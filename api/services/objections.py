"""이의·정정 기록 (행정안전부 「공공부문 AI 윤리기준」(2026-10)의 사후관리·피해구제에 대응).

담당자가 "이 후보는 현장 사정과 다르다", "근거 자료가 틀렸다"를 사유와 함께 남긴다.
- 누가: 토큰으로 정한 이름 (요청 본문의 이름은 믿지 않음). 남기기는 검토 이상, 상태 바꾸기는 승인 역할
- 사유 분류 + 자유 글 (저장 전 개인정보 가림, 보존 기한 뒤 비움: api/retention.py)
- 상태: 접수 → 검토 중 → 반영 / 기각. 반영·기각 뒤에는 바꾸지 않음 (새 기록을 남김)
- 모든 기록·상태 변경·거절은 감사 로그 (objection_recorded / objection_status / objection_rejected)
- 반영된 기록은 다음 규칙 평가 때 검토 목록으로 쓴다 (규칙·등급을 자동으로 바꾸지 않음. 사람이 규칙을 고침)
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from bigcontest.agent.schemas import (
    ObjectionListResponse,
    ObjectionRecord,
    ObjectionRequest,
    ObjectionResponse,
    ObjectionStatusRequest,
)

from api.auth import Identity
from api.errors import ApiError
from api.store import Store, new_id

FINAL = ("반영", "기각")
NEXT = {"접수": ("검토 중", "반영", "기각"), "검토 중": ("반영", "기각")}


def _record(r: dict) -> ObjectionRecord:
    return ObjectionRecord(**{k: r[k] for k in ObjectionRecord.model_fields})


def record(store: Store, clock: Callable[[], datetime], req: ObjectionRequest, who: Identity) -> ObjectionResponse:
    """이의·정정 남기기. 같은 멱등 키 + 같은 내용 → 첫 기록(duplicate), 다른 내용 → 409."""
    now = clock()
    body = req.model_dump(exclude={"idempotency_key"})
    rejected: ApiError | None = None
    with store.tx():
        first = store.get_objection_by_key(req.idempotency_key)
        if first is not None:
            from api.store import mask_text

            same = all(first[k] == (mask_text(v) if k == "text" else v) for k, v in body.items())
            if same:
                return ObjectionResponse(objection=_record(first), duplicate=True)
            rejected = ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 멱등 키로 다른 이의·정정을 보냈습니다.")
        elif req.target_type == "run" and not req.run_id:
            rejected = ApiError(400, "INVALID_REQUEST", "실행 결과에 대한 이의에는 run_id 가 필요합니다.")
        elif req.target_type == "candidate" and not req.candidate_id:
            rejected = ApiError(400, "INVALID_REQUEST", "후보에 대한 이의에는 candidate_id 가 필요합니다.")
        elif req.run_id and store.get_run(req.run_id) is None:
            rejected = ApiError(404, "RUN_NOT_FOUND", f"실행을 찾을 수 없습니다: {req.run_id}")
        if rejected is not None:
            store.audit(who.actor(), "objection_rejected", req.run_id,
                        {"error_code": rejected.code, "candidate_id": req.candidate_id}, now)
        else:
            oid = new_id("obj", now)
            store.insert_objection(objection_id=oid, status="접수", submitted_by=who.record_name(),
                                   idempotency_key=req.idempotency_key, created_at=now.isoformat(),
                                   updated_at=now.isoformat(), **body)
            # 감사 로그에는 사유 분류만 (자유 글은 넣지 않음)
            store.audit(who.actor(), "objection_recorded", req.run_id,
                        {"objection_id": oid, "target_type": req.target_type, "candidate_id": req.candidate_id,
                         "city": req.city, "adm_cd": req.adm_cd, "reason_category": req.reason_category}, now)
            saved = store.get_objection(oid)
    if rejected is not None:
        raise rejected
    return ObjectionResponse(objection=_record(saved))


def set_status(store: Store, clock: Callable[[], datetime], objection_id: str, req: ObjectionStatusRequest,
               who: Identity) -> ObjectionResponse:
    now = clock()
    with store.tx():
        cur = store.get_objection(objection_id)
        if cur is None:
            raise ApiError(404, "OBJECTION_NOT_FOUND", f"이의·정정 기록을 찾을 수 없습니다: {objection_id}")
        if req.status not in NEXT.get(cur["status"], ()):
            store.audit(who.actor(), "objection_rejected", cur["run_id"],
                        {"objection_id": objection_id, "error_code": "OBJECTION_CLOSED", "from": cur["status"],
                         "to": req.status}, now)
            rejected = ApiError(409, "OBJECTION_CLOSED",
                                f"'{cur['status']}' 상태에서 '{req.status}'(으)로 바꿀 수 없습니다.")
        else:
            rejected = None
            store.update_objection(objection_id, req.status, req.resolution, who.record_name(), now)
            store.audit(who.actor(), "objection_status", cur["run_id"],
                        {"objection_id": objection_id, "from": cur["status"], "to": req.status}, now)
    if rejected is not None:
        raise rejected
    return ObjectionResponse(objection=_record(store.get_objection(objection_id)))


def listing(store: Store, city: str | None, status: str | None, candidate_id: str | None, run_id: str | None,
            limit: int) -> ObjectionListResponse:
    rows = store.list_objections(city, status, candidate_id, run_id, limit)
    return ObjectionListResponse(items=[_record(r) for r in rows],
                                 total=store.count_objections(city, status, candidate_id, run_id))
