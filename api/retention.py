"""질문·메모 보존 기한 정리.

질문·메모는 저장할 때 이미 개인정보를 가린 글이다 (api/store.py `mask_text`). 그래도 오래 둘 이유가 없으므로
보존 기한(일)이 지난 행의 글 칸만 비운다. 실행·검토·승인·감사 행 자체와 숫자·판정·이름 칸은 그대로 둔다.

- 비우는 칸: runs.question, run_metadata 의 inputs.question, reviews.memo, resource_judgements.memo,
  approvals.memo, approval_intents.memo, objections.text·resolution (이의·정정 글)
- 보존 일수: `LINKRADAR_TEXT_RETENTION_DAYS` (기본 365)
- 실행: `uv run python -m api.retention --db data/state/linkradar.sqlite [--days 365] [--dry-run]`
  (API 를 끄지 않아도 됨. 한 트랜잭션으로 처리). 운영에서는 하루 한 번 예약 작업으로 돌린다
- 한 번 돌릴 때마다 감사 로그에 `text_purged`(행 수만)를 남긴다
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta

from api.services.fake_data import KST
from api.store import Store

DEFAULT_DAYS = 365


def retention_days() -> int:
    return int(os.environ.get("LINKRADAR_TEXT_RETENTION_DAYS", DEFAULT_DAYS))


def purge_old_text(store: Store, days: int | None = None, now: datetime | None = None,
                   dry_run: bool = False) -> dict[str, int]:
    """보존 기한이 지난 질문·메모 글을 비움. 표마다 비운(비울) 행 수를 돌려줌."""
    days = retention_days() if days is None else days
    if days < 0:
        raise ValueError("보존 일수는 0 이상")
    now = now or datetime.now(KST)
    cutoff = (now - timedelta(days=days)).isoformat()
    out: dict[str, int] = {}
    with store.tx():
        simple = [("runs", "question", "created_at", "runs"), ("reviews", "memo", "created_at", "reviews"),
                  ("approvals", "memo", "created_at", "approvals"),
                  ("approval_intents", "memo", "created_at", "approval_intents"),
                  ("objections", "text", "created_at", "objections"),
                  ("objections", "resolution", "created_at", "objection_resolutions")]
        for table, col, at, key in simple:
            where = f"{col} IS NOT NULL AND {at} < ?"
            out[key] = store._one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", cutoff)["n"]
            if not dry_run:
                store._exec(f"UPDATE {table} SET {col} = NULL WHERE {where}", cutoff)
        jw = ("memo IS NOT NULL AND review_id IN (SELECT review_id FROM reviews WHERE created_at < ?)")
        out["resource_judgements"] = store._one(f"SELECT COUNT(*) AS n FROM resource_judgements WHERE {jw}", cutoff)["n"]
        if not dry_run:
            store._exec(f"UPDATE resource_judgements SET memo = NULL WHERE {jw}", cutoff)
        n_meta = 0
        for r in store._all("SELECT run_id, body FROM run_metadata WHERE created_at < ?", cutoff):
            body = json.loads(r["body"])
            if (body.get("inputs") or {}).get("question"):
                n_meta += 1
                if not dry_run:
                    body["inputs"]["question"] = None
                    store._exec("UPDATE run_metadata SET body = ? WHERE run_id = ?",
                                json.dumps(body, ensure_ascii=False), r["run_id"])
        out["run_metadata"] = n_meta
        if not dry_run and any(out.values()):
            store.audit("system", "text_purged", None, {"days": days, "cutoff": cutoff, **out}, now)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="보존 기한이 지난 질문·메모 글 비우기")
    ap.add_argument("--db", required=True, help="API 상태 SQLite 경로 (예: data/state/linkradar.sqlite)")
    ap.add_argument("--days", type=int, default=None, help=f"보존 일수 (기본 LINKRADAR_TEXT_RETENTION_DAYS 또는 {DEFAULT_DAYS})")
    ap.add_argument("--dry-run", action="store_true", help="비우지 않고 개수만")
    args = ap.parse_args(argv)
    store = Store(args.db)
    try:
        print(json.dumps(purge_old_text(store, args.days, dry_run=args.dry_run), ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()
