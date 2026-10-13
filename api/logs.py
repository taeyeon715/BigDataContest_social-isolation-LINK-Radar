"""구조화 로그: 요청마다 JSON 한 줄. 질문 원문·메모·토큰은 남기지 않는다.

남기는 칸: ts, level, logger, msg, request_id, method, path, status, ms, run_id, error_code, actor
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone

from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOGGER = "linkradar"
_FIELDS = ("request_id", "method", "path", "status", "ms", "run_id", "error_code", "actor")
_REQ_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in _FIELDS:
            v = getattr(record, k, None)
            if v is not None:
                out[k] = v
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out, ensure_ascii=False)


def setup() -> logging.Logger:
    """linkradar 로거에 JSON 처리기를 한 번만 붙인다."""
    log = logging.getLogger(LOGGER)
    if not any(getattr(h, "_linkradar", False) for h in log.handlers):
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        h._linkradar = True  # type: ignore[attr-defined]
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


class RequestLog:
    """순수 ASGI 미들웨어: request_id를 정하고 (X-Request-ID 헤더가 알맞으면 그대로), 끝나면 한 줄 기록."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.log = logging.getLogger(LOGGER + ".request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = given if _REQ_ID.fullmatch(given) else secrets.token_hex(8)
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                msg.setdefault("headers", [])
                msg["headers"] = list(msg["headers"]) + [(b"x-request-id", rid.encode())]
            await send(msg)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            run_id = state.get("run_id") or (scope.get("path_params") or {}).get("run_id")
            self.log.info("request", extra={
                "request_id": rid, "method": scope.get("method"), "path": scope.get("path"),
                "status": status["code"], "ms": round((time.perf_counter() - start) * 1000, 1),
                "run_id": run_id, "error_code": state.get("error_code"), "actor": state.get("actor"),
            })
