"""오류 응답: 오류 코드와 쉬운 사유만. 내부 예외 문장은 보내지 않는다 (API 명세 §4)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from bigcontest.agent.schemas import ErrorResponse

log = logging.getLogger("linkradar.api")

MAX_BODY_BYTES = 64 * 1024  # 요청 본문 한도. 넘으면 413


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


# 승인 대기가 아닐 때 지금 상태를 쉬운 말로 (RUN_NOT_AWAITING_APPROVAL)
_STATUS_TEXT = {
    "queued": "아직 조사 중", "running": "아직 조사 중", "approved": "이미 승인됨", "held": "이미 보류됨",
    "published": "정기 감시 발행 (승인 단계 없음)", "answered": "질의응답 답변 (승인 단계 없음)",
    "abstained": "판단 보류로 끝남", "failed": "실행 실패",
}


def not_awaiting(status: str | None, action: str) -> ApiError:
    """409 RUN_NOT_AWAITING_APPROVAL. 메시지에 지금 상태를 적는다. action 예: "검토를 저장할", "승인·보류·추가 조사를 할"."""
    now = _STATUS_TEXT.get(status or "", status or "알 수 없음")
    return ApiError(409, "RUN_NOT_AWAITING_APPROVAL", f"승인 대기 상태가 아닙니다 (지금: {now}). {action} 수 없습니다.")


def _body(code: str, message: str) -> dict:
    return ErrorResponse(error_code=code, message=message).model_dump(mode="json")


# 검증 실패를 쉬운 문장으로 (칸 이름 → 안내)
_FIELD_HINT = {
    "city": "도시는 강남, 춘천만 고를 수 있습니다.",
    "as_of": "기준일은 2025-07-01 ~ 2025-12-31 안의 날짜여야 합니다.",
    "analysis_mode": "분석 모드는 retrospective, simulated_replay, strict_as_of 중 하나입니다.",
    "type": "후보 종류는 scheduled_access_review, unexpected_activity_deviation 중 하나입니다.",
    "level": "등급은 Level 0~3 또는 판단 보류입니다.",
    "report_revision": "보고서 판 번호(report_revision)가 필요합니다.",
    "idempotency_key": "멱등 키(idempotency_key)가 필요합니다.",
    "decision": "결정은 approved, held, more 중 하나입니다.",
    "judgement": "자원 판정은 적합, 보류, 부적합 중 하나입니다.",
    "question": "질문은 500자까지 쓸 수 있습니다.",
    "memo": "메모는 2000자까지 쓸 수 있습니다.",
    "reviewer": "검토자 이름은 1~50자입니다.",
    "approver": "승인자 이름은 1~50자입니다.",
    "candidate_id": "후보 ID 형식이 맞지 않습니다.",
    "checked_evidence_ids": "확인한 근거는 50개까지, ID는 100자까지입니다.",
    "resource_judgements": "자원 판정은 50개까지입니다.",
    "focus": "추가 조사 범위(focus)는 decision=more 일 때만 씁니다. 도구는 추가 도구 목록, 반증은 공휴일 기대값·도시 공통요인·강수·학사일정 중에서 고릅니다.",
}

# 길이·개수 검사에서만 칸 안내를 쓰는 칸. 나머지 검사 실패는 아래 문장 규칙을 따른다.
_LIMIT_FIELDS = {"question", "memo", "reviewer", "approver", "candidate_id", "checked_evidence_ids",
                 "resource_judgements"}


def _validation_message(exc: RequestValidationError) -> str:
    for err in exc.errors():
        for part in reversed([str(p) for p in err.get("loc", ())]):
            if part == "idempotency_key":
                return "멱등 키(idempotency_key)는 영문·숫자·:._- 로 100자까지입니다."
            if part in _LIMIT_FIELDS and err.get("type") not in ("too_long", "too_short", "string_too_long",
                                                                  "string_too_short", "string_pattern_mismatch"):
                continue
            if part in _FIELD_HINT:
                return _FIELD_HINT[part]
        msg = str(err.get("msg", ""))
        if "as_of" in msg:
            return _FIELD_HINT["as_of"]
        if "question" in msg or "candidate_id" in msg:
            return "질문(question)이나 후보 ID(candidate_id) 중 하나가 필요합니다."
    return "요청 형식이 맞지 않습니다."


TOO_LARGE_MESSAGE = f"요청이 너무 큽니다 ({MAX_BODY_BYTES // 1024}KB까지)."


def respond(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request.state.error_code = code  # 구조화 로그에 남김 (api/logs.py)
    return JSONResponse(status_code=status, content=_body(code, message))


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        resp = respond(request, exc.status, exc.code, exc.message)
        for k, v in (getattr(exc, "headers", None) or {}).items():  # 예: 429 Retry-After
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        return respond(request, 400, "INVALID_REQUEST", _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return respond(request, 404, "NOT_FOUND", "없는 주소입니다.")
        if exc.status_code == 405:
            return respond(request, 405, "METHOD_NOT_ALLOWED", "이 주소에서 쓸 수 없는 방식입니다.")
        if exc.status_code == 413:
            return respond(request, 413, "PAYLOAD_TOO_LARGE", TOO_LARGE_MESSAGE)
        return respond(request, exc.status_code, "HTTP_ERROR", "요청을 처리할 수 없습니다.")

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception):
        log.exception("internal error", extra={"request_id": getattr(request.state, "request_id", None)})  # 사유는 로그에만
        return respond(request, 500, "INTERNAL_ERROR", "처리 중 문제가 생겼습니다.")
