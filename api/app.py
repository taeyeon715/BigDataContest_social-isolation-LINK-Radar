"""LINK-Radar API 앱 만들기. 실행은 api/main.py 참고.

두 가지 백엔드 (`LINKRADAR_BACKEND`)
- fake (기본): 미리 만든 가짜 응답. 모든 응답에 `X-Data-Source: fake`
- real: 실행·검토·승인·질의응답·이어 묻기를 LangGraph 에이전트(agent/graph.py)로 처리
  (api/services/agent_runs.py). 현황·지도·후보 목록·자원 조회(`/overview`·`/map`·`/candidates`·`/resources`)는
  활성 스냅샷의 발행된 정기 감시 실행과 원장 v2 에서 읽음 (api/services/dashboard_real.py). 후속 확인 모아 보기
  `/followups`·현장 결과 모아 보기 `/outcomes/summary` 는 저장소에서. 이 경로들과 `/runs`·`/qa`·`/health`·`/snapshots`
  응답은 `X-Data-Source: real` (tests/test_source_header.py 가 모든 경로를 확인).
`/data/capabilities`는 설정 파일에서 만들어 `config`.

환경 변수
- LINKRADAR_BACKEND: fake / real (기본 fake)
- LINKRADAR_TOOLS: real 백엔드의 도구 real / stub (기본 real = 고정 dev 스냅샷의 실제 도구)
- LINKRADAR_LLM: real 백엔드의 LLM none / claude / 가짜 변형 이름 (기본 none = 규칙·템플릿)
- LINKRADAR_STATE_DB: SQLite 경로 (기본 fake 는 data/state/linkradar_fake.sqlite, real 은 data/state/linkradar.sqlite).
  real 의 그래프 체크포인트는 같은 폴더의 <이름>.agent.sqlite
- LINKRADAR_FAKE_DELAY: 가짜 실행이 끝나는 데 걸리는 초 (기본 1.5). real 에서 0 이면 그래프를 요청 안에서 바로 돌림
- LINKRADAR_WEB_ORIGINS: 허용할 화면 주소 (쉼표로 구분, 기본 http://localhost:5173,http://127.0.0.1:5173)
- LINKRADAR_ENV: `prod`면 /docs·/redoc·/openapi.json 을 끄고, 토큰 설정이 없으면 시작하지 않음 (기본 dev)
- LINKRADAR_API_TOKEN / LINKRADAR_API_USER / LINKRADAR_API_ROLE / LINKRADAR_API_TOKENS_FILE: 인증 (api/auth.py)

운영 메모
- uvicorn 워커는 1개로 띄운다 (SQLite 저장소, api/store.py).
- 요청 본문은 64KB까지 (넘으면 413 PAYLOAD_TOO_LARGE). 앞단 프록시를 두면 그쪽에도 같은 한도를 둔다.
- 요청마다 JSON 로그 한 줄 (api/logs.py). 응답 헤더 X-Request-ID.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api import errors, logs
from api.auth import Auth
from api.routes import dashboard, runs, system
from api.services.fake_data import KST
from api.services.runs import RunService
from api.services.snapshots import SnapshotService
from api.store import Store

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "state" / "linkradar_fake.sqlite"
DEFAULT_REAL_DB = ROOT / "data" / "state" / "linkradar.sqlite"
REAL_PREFIXES = ("/runs", "/qa", "/health", "/snapshots", "/overview", "/map", "/candidates", "/resources", "/followups",
                 "/outcomes")
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


class BodyLimit:
    """요청 본문 크기 한도. Content-Length가 크면 바로 413, 없으면 읽으면서 센다."""

    def __init__(self, app: ASGIApp, max_bytes: int = errors.MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.max_bytes):
            scope.setdefault("state", {})["error_code"] = "PAYLOAD_TOO_LARGE"
            resp = JSONResponse(status_code=413, content=errors._body("PAYLOAD_TOO_LARGE", errors.TOO_LARGE_MESSAGE))
            await resp(scope, receive, send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(status_code=413)  # FastAPI 본문 읽기가 그대로 다시 냄
            return msg

        await self.app(scope, limited, send)


class MarkSource:
    """모든 응답에 X-Data-Source (fake / real / config). real 백엔드에서도 REAL_PREFIXES 밖 경로는 fake.

    BaseHTTPMiddleware 를 쓰지 않는다: 그 안에서 난 413 이 ExceptionGroup 으로 감싸져 400 이 되기 때문.
    """

    def __init__(self, app: ASGIApp, backend: str = "fake"):
        self.app = app
        self.backend = backend

    def value(self, path: str) -> bytes:
        if path == "/data/capabilities":
            return b"config"
        if self.backend == "real" and path.startswith(REAL_PREFIXES):
            return b"real"
        return b"fake"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        value = self.value(scope.get("path") or "")

        async def send_wrapper(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                msg["headers"] = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"x-data-source"]
                msg["headers"].append((b"x-data-source", value))
            await send(msg)

        await self.app(scope, receive, send_wrapper)


def create_app(
    db_path: str | Path | None = None,
    delay_s: float | None = None,
    origins: list[str] | None = None,
    clock: Callable[[], datetime] | None = None,
    auth: Auth | None = None,
    env: str | None = None,
    backend: str | None = None,
    tools_backend: str | None = None,
    llm: str | None = None,
) -> FastAPI:
    backend = (backend or os.environ.get("LINKRADAR_BACKEND", "fake")).strip().lower()
    if backend not in ("fake", "real"):
        raise RuntimeError("LINKRADAR_BACKEND 는 fake 또는 real 입니다")
    db_path = db_path or os.environ.get("LINKRADAR_STATE_DB") or (DEFAULT_REAL_DB if backend == "real" else DEFAULT_DB)
    delay_s = float(os.environ.get("LINKRADAR_FAKE_DELAY", "1.5")) if delay_s is None else delay_s
    origins = origins or [o.strip() for o in os.environ.get("LINKRADAR_WEB_ORIGINS", DEFAULT_ORIGINS).split(",") if o]
    env = (env or os.environ.get("LINKRADAR_ENV", "dev")).strip().lower()
    auth = auth if auth is not None else Auth.from_env()
    prod = env == "prod"
    if prod and not auth.enabled:
        raise RuntimeError("LINKRADAR_ENV=prod 에서는 LINKRADAR_API_TOKEN 또는 LINKRADAR_API_TOKENS_FILE 이 필요합니다")

    logs.setup()
    app = FastAPI(
        title="LINK-Radar API" + (" (실제 그래프)" if backend == "real" else " (가짜 응답)"),
        version="0.6.2" if backend == "real" else "0.6.1",
        docs_url=None if prod else "/docs", redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    store = Store(db_path)
    app.state.store = store
    app.state.auth = auth
    app.state.env = env
    app.state.backend = backend
    clock = clock or (lambda: datetime.now(KST))
    if backend == "real":
        from bigcontest.agent.graph import Agent

        from api.services.agent_runs import AgentRunService
        from api.services.snapshots import AgentSnapshotService

        tools_backend = tools_backend or os.environ.get("LINKRADAR_TOOLS", "real")
        llm = llm or os.environ.get("LINKRADAR_LLM", "none")
        agent_db = Path(str(db_path)).with_suffix(".agent.sqlite") if str(db_path) != ":memory:" else ":memory:"
        snapshots = AgentSnapshotService(tools_backend)
        agent = Agent(agent_db, tools_backend=tools_backend, durability="exit")
        svc = AgentRunService(store, snapshots, agent, clock, sync=delay_s <= 0, llm=llm, tools_backend=tools_backend)
        app.state.runs = svc
        from api.services.dashboard_real import RealDashboard

        app.state.dashboard = RealDashboard(svc)
        svc.recover()  # 재시작: 끝나지 않은 실행·다시 만들던 보고서를 이어 감
    else:
        snapshots = SnapshotService()
        app.state.runs = RunService(store, snapshots, delay_s, clock)
    app.state.snapshots = snapshots

    errors.install(app)

    app.add_middleware(BodyLimit)
    app.add_middleware(MarkSource, backend=backend)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Data-Source", "X-Request-ID"],
    )
    app.add_middleware(logs.RequestLog)  # 가장 바깥: 413·CORS 거절도 기록

    app.include_router(system.router)
    app.include_router(dashboard.router)
    app.include_router(runs.router)
    return app
