"""(c) 원본 없는 시연: LINK-Sim 합성 자료로 API(실제 그래프 경로)를 띄운다.

    uv run python -m bigcontest.sim.serve            # http://127.0.0.1:8000 (LLM 없음)
    uv run python -m bigcontest.sim.serve --port 8001

- data/interim(대회 제공 원본 정리본) 없이 돈다. 카드·통신 표는 LINK-Sim 합성, 공개 자료는 레포 파일.
- 활성 스냅샷 = "sim-…" (SimDataSource 를 register_source 로 등록하고 API 의 활성 스냅샷으로 발행).
- 모든 응답 머리 X-Data-Source 를 "synthetic" 으로 바꾼다 (API 코드는 고치지 않고 바깥에 미들웨어 하나를 씌움).
  화면 출처 띠에는 이 값이 그대로 보인다. 합성 화면을 실제 분석 결과처럼 캡처·발표하지 않는다.
- 상태 저장소는 기본 data/sim/api_state.sqlite (실제 백엔드 저장소와 섞지 않음).
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bigcontest.config import ROOT


class MarkSynthetic:
    """응답 머리 X-Data-Source 를 synthetic 으로 바꾼다 (설정 경로 /data/capabilities 는 그대로)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/data/capabilities":
            await self.app(scope, receive, send)
            return

        async def wrapped(msg):
            if msg["type"] == "http.response.start":
                headers = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"x-data-source"]
                headers.append((b"x-data-source", b"synthetic"))
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, wrapped)


def build_app(db_path: str | None = None, seed: int | None = None):
    """합성 스냅샷을 활성으로 둔 FastAPI 앱. (앱, snapshot_id)"""
    import sys

    if str(ROOT) not in sys.path:  # api 패키지는 레포 뿌리에 있음
        sys.path.insert(0, str(ROOT))
    from api.app import create_app
    from bigcontest.agent.schemas import DATA_END, DATA_START, SnapshotInfo
    from bigcontest.agent.tools.common import RULE_VERSION
    from bigcontest.sim import params as P
    from bigcontest.sim.adapter import SimDataSource, register

    ds = SimDataSource(**({} if seed is None else {"seed": seed}))
    sid = register(ds)
    db = db_path or str(ROOT / "data" / "sim" / "api_state.sqlite")
    if db != ":memory:":
        Path(db).parent.mkdir(parents=True, exist_ok=True)
    app = create_app(db_path=db, backend="real", tools_backend="real", llm=os.environ.get("LINKRADAR_LLM", "none"),
                     delay_s=0)
    app.state.snapshots.publish(SnapshotInfo(
        snapshot_id=sid, created_at=datetime.now(timezone(timedelta(hours=9))), data_from=DATA_START, data_to=DATA_END,
        cleaning_rule_version=f"{P.SIM_VERSION}/synthetic", rule_version=RULE_VERSION,
        ledger_version=f"ledger@sha256:{ds.hashes['resources'][:12]}", previous_snapshot_id=None))
    app.add_middleware(MarkSynthetic)
    return app, sid


def main() -> None:
    ap = argparse.ArgumentParser(description="LINK-Sim 합성 자료로 API 띄우기 (원본 없는 시연)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    import uvicorn

    app, sid = build_app(a.db)
    print(f"LINK-Sim 합성 스냅샷 {sid} (X-Data-Source: synthetic). 실제 분석 결과가 아닙니다.")
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
