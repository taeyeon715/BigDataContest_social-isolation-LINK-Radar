"""SQLite 저장소. 상태를 쓰는 곳은 FastAPI 하나 (docs/데이터_모델.md).

- 표는 데이터 모델 §3 DDL을 따른다.
- `run_results`, `run_jobs` 두 표는 가짜 백엔드 전용이다. 실제 그래프(agent_runs.py)는 쓰지 않고 LangGraph 체크포인트
  (`<이름>.agent.sqlite`)가 이 역할을 한다.
  - `run_results`: 실행이 끝났을 때 보여 줄 경로·Guard·후보·첫 보고서 (시작할 때 고정한 스냅샷으로 미리 만듦)
  - `run_jobs`: "언제 다음 상태로 넘어가나" 예약. 조회할 때 기한이 지난 것을 처리하므로 API를 다시 켜도 이어진다.

동시성
- 체크포인터(agent/checkpoint.py)와 같게 WAL + busy_timeout 30초.
- 연결 하나를 RLock으로 나눠 쓴다. uvicorn 워커는 1개로 띄운다 (`--workers 1`, 기본값).
  여러 워커는 WAL·busy_timeout으로 잠금 오류는 줄지만, 가짜 예약 처리(advance)가 겹칠 수 있다.
- 조회(GET)는 처리할 예약이 없으면 쓰기 트랜잭션을 열지 않는다 (RunService.advance).

ID: `COUNT+1` 대신 시각 + 무작위 값(`new_id`). 행을 지워도 겹치지 않는다.

개인정보: 질문(runs.question, run_metadata 의 inputs.question)·검토 메모·자원 판정 메모·승인 메모·승인 의도 메모는
저장하기 전에 `mask_text`(guard.mask_pii: 전화번호·주민번호·이메일·상세 주소·이름+호칭)로 가린다. 원문은 저장하지
않는다. 보존 기한이 지난 글은 api/retention.py 가 지운다.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL CHECK (mode IN ('monitoring','qa')),
  question TEXT, candidate_id TEXT,
  as_of TEXT NOT NULL,
  analysis_mode TEXT NOT NULL CHECK (analysis_mode IN ('retrospective','simulated_replay','strict_as_of')),
  snapshot_id TEXT NOT NULL,
  status TEXT NOT NULL,
  route TEXT,
  evidence_revision INTEGER NOT NULL DEFAULT 0,
  report_revision INTEGER NOT NULL DEFAULT 0,
  report_stale INTEGER NOT NULL DEFAULT 0,
  extra_rounds INTEGER NOT NULL DEFAULT 0 CHECK (extra_rounds <= 2),
  abstain_reason TEXT, error_code TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_metadata (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  snapshot_id TEXT NOT NULL,
  code_commit TEXT, code_dirty INTEGER,
  cleaning_rule_version TEXT NOT NULL,
  rule_version TEXT, ledger_version TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_revisions (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  report_revision INTEGER NOT NULL,
  evidence_revision INTEGER NOT NULL,
  snapshot_id TEXT NOT NULL,
  body TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, report_revision)
);
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  reviewer TEXT NOT NULL,
  checked_evidence_ids TEXT NOT NULL DEFAULT '[]',
  memo TEXT, follow_up_on TEXT,
  report_revision_before INTEGER NOT NULL, report_revision_after INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_judgements (
  review_id TEXT NOT NULL REFERENCES reviews(review_id),
  resource_id TEXT NOT NULL,
  judgement TEXT NOT NULL CHECK (judgement IN ('적합','보류','부적합')),
  memo TEXT,
  PRIMARY KEY (review_id, resource_id)
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  idempotency_key TEXT NOT NULL UNIQUE,
  decision TEXT NOT NULL CHECK (decision IN ('approved','held','more')),
  report_revision INTEGER NOT NULL,
  approver TEXT NOT NULL, memo TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finalizations (
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  step TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, step)
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  run_id TEXT, detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL, node TEXT NOT NULL,
  model_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER,
  status TEXT NOT NULL CHECK (status IN ('ok','error','fallback')), error_code TEXT,
  created_at TEXT NOT NULL
);
-- 현장 확인 결과. 승인된 조사 실행의 후보(동·집단)마다 건수만. 고쳐 적으면 새 행 (최근 행이 기준)
CREATE TABLE IF NOT EXISTS field_outcomes (
  outcome_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  candidate_id TEXT NOT NULL,
  city TEXT NOT NULL, adm_cd TEXT, group_label TEXT,
  trigger_code TEXT NOT NULL, event_id TEXT,
  checked_households INTEGER NOT NULL CHECK (checked_households >= 0),
  contacted INTEGER NOT NULL CHECK (contacted >= 0),
  absent INTEGER NOT NULL CHECK (absent >= 0),
  new_links INTEGER NOT NULL CHECK (new_links >= 0),
  crises_found INTEGER NOT NULL CHECK (crises_found >= 0),
  recorded_by TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,  -- 표시용 시각. 순서는 seq
  seq INTEGER                -- 저장 순서 (1, 2, …). 같은 시각에 고쳐 적어도 나중 행이 최신
);
CREATE INDEX IF NOT EXISTS idx_outcomes_run ON field_outcomes(run_id);
-- 승인 의도. 그래프를 재개하기 전에 API 가 먼저 적어 둠 (두 DB 를 한 트랜잭션으로 묶지 않음).
-- 재시작하면 이 표와 그래프 확정 기록(run_records.finalize)을 맞춰 빠진 승인·확정 기록을 원래 값으로 채움.
-- focus 는 추가 조사(more)에서만: 검증한 후보·도구·반증 + 받을 때 정한 계획 (메모 원문은 없음)
CREATE TABLE IF NOT EXISTS approval_intents (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  approval_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved','held','more')),
  report_revision INTEGER NOT NULL,
  approver TEXT NOT NULL, memo TEXT,
  actor TEXT NOT NULL, audit_extra TEXT NOT NULL DEFAULT '{}',
  focus TEXT,
  state TEXT NOT NULL CHECK (state IN ('pending','applied','rejected')),
  result TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_run ON approval_intents(run_id, state);
CREATE INDEX IF NOT EXISTS idx_reviews_follow_up ON reviews(follow_up_on);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);

-- 가짜 백엔드 전용
CREATE TABLE IF NOT EXISTS run_results (
  run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  action TEXT NOT NULL,
  due_at TEXT NOT NULL
);
"""


def new_id(prefix: str, now: datetime) -> str:
    """시각 순으로 정렬되는 ID. 예: run-20261013-100000-3fa9c2d1 (같은 초에도 무작위 32비트로 구분)."""
    return f"{prefix}-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"


def mask_text(text: str | None) -> str | None:
    """저장 전 개인정보 가리기 (가린 글만 저장). 이미 가린 글을 다시 넣어도 같음."""
    if not text:
        return text
    from bigcontest.agent.guard import mask_pii

    return mask_pii(text)[0]


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(DDL)
        self._migrate()
        self._lock = threading.RLock()
        self._depth = 0

    def _migrate(self) -> None:
        """예전 파일에 없는 칸 추가. field_outcomes.seq 는 예전 행의 rowid(넣은 순서)로 채움."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(field_outcomes)")}
        if "seq" not in cols:
            self._conn.execute("ALTER TABLE field_outcomes ADD COLUMN seq INTEGER")
        self._conn.execute("UPDATE field_outcomes SET seq = rowid WHERE seq IS NULL")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_seq ON field_outcomes(seq)")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[None]:
        """한 번에 하나씩. 바깥 tx 안에서 다시 열면 같은 트랜잭션."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def _one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, args).fetchone())

    def _all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _exec(self, sql: str, *args: Any) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    # ---------------------------------------------------------------- runs

    def next_run_id(self, now: datetime) -> str:
        return new_id("run", now)

    def insert_run(self, **row: Any) -> None:
        if row.get("question"):
            row["question"] = mask_text(row["question"])
        cols = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        self._exec(f"INSERT INTO runs ({cols}) VALUES ({qs})", *row.values())

    def update_run(self, run_id: str, now: datetime, **fields: Any) -> None:
        fields["updated_at"] = now.isoformat()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE runs SET {sets} WHERE run_id = ?", *fields.values(), run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM runs WHERE run_id = ?", run_id)

    def runs_with_status(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        qs = ",".join("?" for _ in statuses)
        return self._all(f"SELECT * FROM runs WHERE status IN ({qs}) ORDER BY created_at, run_id", *statuses)

    def list_runs(self, candidate_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if candidate_id:
            return self._all("SELECT * FROM runs WHERE candidate_id = ? ORDER BY created_at DESC, run_id DESC LIMIT ?",
                             candidate_id, limit)
        return self._all("SELECT * FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?", limit)

    def monitoring_runs(self, as_of: str, analysis_mode: str | None = None) -> list[dict[str, Any]]:
        """기준일의 정기 감시 실행 (최근 것부터). 현황·지도·후보 목록이 읽을 실행을 고를 때 씀."""
        if analysis_mode:
            return self._all("SELECT * FROM runs WHERE mode = 'monitoring' AND as_of = ? AND analysis_mode = ?"
                             " ORDER BY created_at DESC, run_id DESC", as_of, analysis_mode)
        return self._all("SELECT * FROM runs WHERE mode = 'monitoring' AND as_of = ? ORDER BY created_at DESC, run_id DESC",
                         as_of)

    def candidate_runs(self) -> list[dict[str, Any]]:
        """후보를 열어 조사한 실행 (최근 것부터). 후속 확인 모아 보기용."""
        return self._all("SELECT * FROM runs WHERE candidate_id IS NOT NULL ORDER BY created_at DESC, run_id DESC")

    def last_follow_up(self, run_id: str) -> dict[str, Any] | None:
        """이 실행의 검토 중 후속 확인일이 있는 마지막 것."""
        return self._one("SELECT * FROM reviews WHERE run_id = ? AND follow_up_on IS NOT NULL"
                         " ORDER BY created_at DESC, rowid DESC LIMIT 1", run_id)

    # ---------------------------------------------------------------- run_metadata (한 번만)

    def insert_run_metadata(self, run_id: str, snapshot_id: str, cleaning_rule_version: str, rule_version: str | None,
                            ledger_version: str | None, body: dict, now: datetime) -> None:
        if (body.get("inputs") or {}).get("question"):
            body = {**body, "inputs": {**body["inputs"], "question": mask_text(body["inputs"]["question"])}}
        self._exec(
            "INSERT INTO run_metadata (run_id, snapshot_id, code_commit, code_dirty, cleaning_rule_version, rule_version,"
            " ledger_version, body, created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO NOTHING",
            run_id, snapshot_id, body.get("code_commit"), int(bool(body.get("code_dirty"))), cleaning_rule_version,
            rule_version, ledger_version, json.dumps(body, ensure_ascii=False), now.isoformat())

    def get_run_metadata(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM run_metadata WHERE run_id = ?", run_id)

    # ---------------------------------------------------------------- 가짜 실행 결과·예약

    def put_result(self, run_id: str, body: dict) -> None:
        self._exec("INSERT OR REPLACE INTO run_results (run_id, body) VALUES (?, ?)", run_id,
                   json.dumps(body, ensure_ascii=False, default=str))

    def get_result(self, run_id: str) -> dict | None:
        r = self._one("SELECT body FROM run_results WHERE run_id = ?", run_id)
        return json.loads(r["body"]) if r else None

    def add_job(self, run_id: str, action: str, due_at: datetime) -> None:
        self._exec("INSERT INTO run_jobs (run_id, action, due_at) VALUES (?, ?, ?)", run_id, action, due_at.isoformat())

    def clear_jobs(self, run_id: str, action: str) -> None:
        self._exec("DELETE FROM run_jobs WHERE run_id = ? AND action = ?", run_id, action)

    def due_jobs(self, run_id: str, now: datetime) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM run_jobs WHERE run_id = ? AND due_at <= ? ORDER BY due_at, id", run_id,
                         now.isoformat())

    def has_jobs(self, run_id: str, action: str) -> bool:
        return self._one("SELECT 1 AS x FROM run_jobs WHERE run_id = ? AND action = ?", run_id, action) is not None

    def delete_job(self, job_id: int) -> None:
        self._exec("DELETE FROM run_jobs WHERE id = ?", job_id)

    # ---------------------------------------------------------------- 보고서 판

    def insert_report_revision(self, run_id: str, report_revision: int, evidence_revision: int, snapshot_id: str,
                               body: dict, reason: str, now: datetime) -> None:
        self._exec(
            "INSERT INTO report_revisions (run_id, report_revision, evidence_revision, snapshot_id, body, reason,"
            " created_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id, report_revision) DO NOTHING",
            run_id, report_revision, evidence_revision, snapshot_id, json.dumps(body, ensure_ascii=False, default=str),
            reason, now.isoformat())

    def has_report_revision(self, run_id: str, report_revision: int) -> bool:
        return self._one("SELECT 1 AS x FROM report_revisions WHERE run_id = ? AND report_revision = ?", run_id,
                         report_revision) is not None

    def latest_report(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM report_revisions WHERE run_id = ? ORDER BY report_revision DESC LIMIT 1", run_id)

    def list_report_revisions(self, run_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT run_id, report_revision, evidence_revision, snapshot_id, reason, created_at"
                         " FROM report_revisions WHERE run_id = ? ORDER BY report_revision", run_id)

    # ---------------------------------------------------------------- 검토

    def insert_review(self, review_id: str, run_id: str, reviewer: str, checked: list[str], memo: str | None,
                      follow_up_on: str | None, before: int, after: int, judgements: list[dict], now: datetime) -> None:
        self._exec(
            "INSERT INTO reviews (review_id, run_id, reviewer, checked_evidence_ids, memo, follow_up_on,"
            " report_revision_before, report_revision_after, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            review_id, run_id, reviewer, json.dumps(checked, ensure_ascii=False), mask_text(memo), follow_up_on, before,
            after, now.isoformat())
        for j in judgements:
            self._exec("INSERT INTO resource_judgements (review_id, resource_id, judgement, memo) VALUES (?,?,?,?)",
                       review_id, j["resource_id"], j["judgement"], mask_text(j.get("memo")))

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        # 같은 시각이면 넣은 순서(rowid). review_id 끝자리는 무작위라 순서로 쓰지 않음 (현장 확인 결과 seq 와 같은 이유)
        rows = self._all("SELECT * FROM reviews WHERE run_id = ? ORDER BY created_at, rowid", run_id)
        for r in rows:
            r["checked_evidence_ids"] = json.loads(r["checked_evidence_ids"])
            r["resource_judgements"] = self._all(
                "SELECT resource_id, judgement, memo FROM resource_judgements WHERE review_id = ? ORDER BY resource_id",
                r["review_id"])
        return rows

    def current_judgements(self, run_id: str) -> dict[str, tuple[str, str | None]]:
        """자원마다 가장 최근 판정."""
        out: dict[str, tuple[str, str | None]] = {}
        for r in self.list_reviews(run_id):
            for j in r["resource_judgements"]:
                out[j["resource_id"]] = (j["judgement"], j["memo"])
        return out

    # ---------------------------------------------------------------- 승인·확정

    def get_approval_by_key(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM approvals WHERE idempotency_key = ?", key)

    def insert_approval(self, approval_id: str, run_id: str, key: str, decision: str, report_revision: int,
                        approver: str, memo: str | None, now: datetime) -> None:
        self._exec(
            "INSERT INTO approvals (approval_id, run_id, idempotency_key, decision, report_revision, approver, memo,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            approval_id, run_id, key, decision, report_revision, approver, mask_text(memo), now.isoformat())

    def list_approvals(self, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            return self._all("SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at, rowid", run_id)
        return self._all("SELECT * FROM approvals ORDER BY created_at, rowid")

    def finalize(self, run_id: str, step: str, result: dict, now: datetime) -> dict:
        """부작용 1회 (INSERT ... ON CONFLICT DO NOTHING 후 저장된 행을 읽음)."""
        self._exec("INSERT INTO finalizations (run_id, step, result, created_at) VALUES (?,?,?,?)"
                   " ON CONFLICT(run_id, step) DO NOTHING", run_id, step,
                   json.dumps(result, ensure_ascii=False), now.isoformat())
        r = self._one("SELECT result FROM finalizations WHERE run_id = ? AND step = ?", run_id, step)
        return json.loads(r["result"]) if r else {}

    def list_finalizations(self, run_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM finalizations WHERE run_id = ? ORDER BY created_at", run_id)

    # ---------------------------------------------------------------- 승인 의도

    @staticmethod
    def _intent(r: dict | None) -> dict | None:
        if r is None:
            return None
        r["audit_extra"] = json.loads(r["audit_extra"] or "{}")
        r["focus"] = json.loads(r["focus"]) if r.get("focus") else None
        return r

    def put_intent(self, *, key: str, run_id: str, approval_id: str, decision: str, report_revision: int,
                   approver: str, memo: str | None, actor: str, audit_extra: dict, focus: dict | None,
                   now: datetime) -> dict:
        """승인 의도를 대기(pending)로 적음. 같은 키가 거절(rejected)로 끝나 있었으면 새 요청으로 덮어씀."""
        self._exec("DELETE FROM approval_intents WHERE idempotency_key = ? AND state = 'rejected'", key)
        self._exec(
            "INSERT INTO approval_intents (idempotency_key, run_id, approval_id, decision, report_revision, approver,"
            " memo, actor, audit_extra, focus, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
            key, run_id, approval_id, decision, report_revision, approver, mask_text(memo), actor,
            json.dumps(audit_extra, ensure_ascii=False), json.dumps(focus, ensure_ascii=False) if focus else None,
            now.isoformat(), now.isoformat())
        return self.get_intent(key)

    def get_intent(self, key: str) -> dict | None:
        return self._intent(self._one("SELECT * FROM approval_intents WHERE idempotency_key = ?", key))

    def pending_intents(self, run_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM approval_intents WHERE state = 'pending'" + (" AND run_id = ?" if run_id else "")
        return [self._intent(r) for r in self._all(sql + " ORDER BY seq", *([run_id] if run_id else []))]

    def set_intent(self, key: str, state: str, result: str | None, now: datetime) -> None:
        self._exec("UPDATE approval_intents SET state = ?, result = ?, updated_at = ? WHERE idempotency_key = ?",
                   state, result, now.isoformat(), key)

    # ---------------------------------------------------------------- LLM 호출 (실제 그래프)

    def count_llm_calls(self, run_id: str) -> int:
        """그래프 llm_log 에서 옮긴 행 수 (이어 묻기 행은 그래프 기록이 아니라 셈에서 뺌)."""
        return self._one("SELECT COUNT(*) AS n FROM llm_calls WHERE run_id = ? AND node != 'followup'", run_id)["n"]

    def insert_llm_call(self, run_id: str, node: str, rec: dict, now: datetime) -> None:
        """그래프 llm_log 한 칸 → llm_calls 한 행 (질문·응답 원문은 없음)."""
        status = "ok" if rec.get("ok") else ("fallback" if rec.get("fallback") else "error")
        self._exec(
            "INSERT INTO llm_calls (run_id, node, model_id, prompt_version, input_tokens, output_tokens, cost_usd,"
            " latency_ms, status, error_code, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            run_id, node, str(rec.get("model_id") or "-"), str(rec.get("prompt_version") or "-"),
            rec.get("input_tokens"), rec.get("output_tokens"), rec.get("cost_usd"),
            int(rec.get("latency_ms") or 0), status, rec.get("error_code"), now.isoformat())

    def llm_cost(self, run_id: str | None = None) -> float:
        sql = "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls" + (" WHERE run_id = ?" if run_id else "")
        return float(self._one(sql, *([run_id] if run_id else []))["c"])

    # ---------------------------------------------------------------- 현장 확인 결과

    def insert_outcome(self, **row: Any) -> None:
        """새 기록에 저장 순서 seq 를 붙임 (쓰기 트랜잭션 안이라 겹치지 않음)."""
        with self.tx():
            row["seq"] = self._one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM field_outcomes")["n"]
            cols = ", ".join(row)
            self._exec(f"INSERT INTO field_outcomes ({cols}) VALUES ({', '.join('?' * len(row))})", *row.values())

    def get_outcome_by_key(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM field_outcomes WHERE idempotency_key = ?", key)

    def list_outcomes(self, run_id: str | None = None, city: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM field_outcomes WHERE 1=1", []
        if run_id is not None:
            sql, args = sql + " AND run_id = ?", args + [run_id]
        if city is not None:
            sql, args = sql + " AND city = ?", args + [city]
        return self._all(sql + " ORDER BY seq", *args)

    # ---------------------------------------------------------------- 감사 로그

    def audit(self, actor: str, action: str, run_id: str | None, detail: dict, now: datetime) -> None:
        self._exec("INSERT INTO audit_log (at, actor, action, run_id, detail) VALUES (?,?,?,?,?)", now.isoformat(),
                   actor, action, run_id, json.dumps(detail, ensure_ascii=False))

    def audit_rows(self, run_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM audit_log WHERE run_id = ? ORDER BY id", run_id)
