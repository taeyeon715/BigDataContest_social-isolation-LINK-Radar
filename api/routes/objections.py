"""이의·정정 기록 경로 (api/services/objections.py).

- POST /objections: 검토 이상. 후보·실행 결과에 대한 이의·정정 (사유 분류 + 가린 자유 글)
- GET /objections: 조회 이상. 도시·상태·후보·실행으로 거름
- POST /objections/{objection_id}/status: 승인 역할. 검토 중 / 반영 / 기각
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from bigcontest.agent.schemas import ObjectionListResponse, ObjectionRequest, ObjectionResponse, ObjectionStatusRequest

from api.auth import Identity, require
from api.errors import ApiError
from api.routes.deps import check_city
from api.services import objections as O

router = APIRouter(dependencies=[Depends(require("조회"))])
STATUSES = ("접수", "검토 중", "반영", "기각")


def _deps(request: Request):
    return request.app.state.store, request.app.state.runs.clock


@router.post("/objections", response_model=ObjectionResponse, status_code=201)
def create_objection(req: ObjectionRequest, request: Request,
                     who: Identity = Depends(require("검토"))) -> ObjectionResponse:
    store, clock = _deps(request)
    return O.record(store, clock, req, who)


@router.get("/objections", response_model=ObjectionListResponse)
def list_objections(request: Request, city: str | None = None, status: str | None = None,
                    candidate_id: str | None = Query(default=None, max_length=100),
                    run_id: str | None = Query(default=None, max_length=100),
                    limit: int = Query(default=100, ge=1, le=500)) -> ObjectionListResponse:
    if city is not None:
        check_city(city)
    if status is not None and status not in STATUSES:
        raise ApiError(400, "INVALID_REQUEST", "상태는 " + ", ".join(STATUSES) + " 중 하나입니다.")
    store, _ = _deps(request)
    return O.listing(store, city, status, candidate_id, run_id, limit)


@router.post("/objections/{objection_id}/status", response_model=ObjectionResponse)
def objection_status(objection_id: str, req: ObjectionStatusRequest, request: Request,
                     who: Identity = Depends(require("승인"))) -> ObjectionResponse:
    store, clock = _deps(request)
    return O.set_status(store, clock, objection_id, req, who)
