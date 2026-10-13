"""체크포인터와 멱등 기록 (에이전트 설계 §7).

- 체크포인터: LangGraph SqliteSaver. thread_id = run_id. 프로세스가 다시 켜져도 같은 DB·같은 run_id 로 이어서 실행
- 멱등 기록(`RecordStore`): 확정·보류·발행 같은 부작용은 (run_id, 단계) 키로 한 번만 기록.
  같은 승인을 두 번 보내거나 동시에 보내도 기록은 1건이다.
  체크포인트와 같은 SQLite 파일에 표 하나(`run_records`)를 둔다.
- 재개 키(`run_resume_keys`): 같은 멱등 키로 재개(추가 조사)를 두 번 하지 않게 먼저 차지한다.
  부작용 기록(`run_records`)과 섞이지 않게 표를 따로 둔다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def connect(path: str | Path) -> sqlite3.Connection:
    # SqliteSaver 는 자체 잠금을 쓰므로 여러 스레드에서 같은 연결을 써도 됨 (langgraph 문서)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def open_checkpointer(path: str | Path) -> SqliteSaver:
    saver = SqliteSaver(connect(path))
    saver.setup()
    return saver


class RecordStore:
    """(run_id, 단계) 키로 한 번만 쓰는 기록 표."""

    def __init__(self, path: str | Path):
        self.conn = connect(path)
        self.lock = threading.Lock()
        with self.lock, self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS run_records (
                    run_id TEXT NOT NULL,
                    step TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, step)
                )"""
            )
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS run_resume_keys (
                    run_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, idempotency_key)
                )"""
            )

    def claim_resume(self, run_id: str, key: str, payload: dict) -> tuple[dict, bool]:
        """재개 키를 처음 쓰면 (기록, True), 이미 쓴 키면 (처음 기록, False)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO run_resume_keys (run_id, idempotency_key, payload, created_at) VALUES (?, ?, ?, ?)",
                (run_id, key, json.dumps(payload, ensure_ascii=False, default=str), now),
            )
            created = cur.rowcount == 1
            row = self.conn.execute(
                "SELECT payload, created_at FROM run_resume_keys WHERE run_id = ? AND idempotency_key = ?", (run_id, key)
            ).fetchone()
        return {"run_id": run_id, "idempotency_key": key, "payload": json.loads(row[0]), "created_at": row[1]}, created

    def get_resume(self, run_id: str, key: str) -> dict | None:
        """이미 차지한 재개 키의 기록 (없으면 None)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT payload, created_at FROM run_resume_keys WHERE run_id = ? AND idempotency_key = ?", (run_id, key)
            ).fetchone()
        if row is None:
            return None
        return {"run_id": run_id, "idempotency_key": key, "payload": json.loads(row[0]), "created_at": row[1]}

    def record_once(self, run_id: str, step: str, kind: str, payload: dict) -> tuple[dict, bool]:
        """처음이면 쓰고 (기록, True), 이미 있으면 있던 기록을 그대로 (기록, False)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO run_records (run_id, step, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, step, kind, json.dumps(payload, ensure_ascii=False, default=str), now),
            )
            created = cur.rowcount == 1
        return self.get(run_id, step), created

    def get(self, run_id: str, step: str) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT run_id, step, kind, payload, created_at FROM run_records WHERE run_id = ? AND step = ?",
                (run_id, step),
            ).fetchone()
        if row is None:
            return None
        return {"run_id": row[0], "step": row[1], "kind": row[2], "payload": json.loads(row[3]), "created_at": row[4]}

    def count(self, run_id: str | None = None, step: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM run_records WHERE 1=1", []
        if run_id is not None:
            sql, args = sql + " AND run_id = ?", args + [run_id]
        if step is not None:
            sql, args = sql + " AND step = ?", args + [step]
        with self.lock:
            return self.conn.execute(sql, args).fetchone()[0]

    def close(self) -> None:
        self.conn.close()
