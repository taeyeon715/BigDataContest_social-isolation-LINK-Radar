"""실행·검토·승인·질의응답 경로.

권한 (api/auth.py): 이 라우터 전체가 조회 이상. 실행 만들기·질의응답·이어 묻기(그래프·LLM 비용을 씀)와
검토 저장·현장 결과는 검토 이상, 승인은 승인 역할만. 실행 만들기·질의응답·이어 묻기는 사용자마다 횟수 한도
(api/limits.py, 넘으면 429).
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request

from bigcontest.agent.schemas import (
    ApproveRequest,
    ApproveResponse,
    EvidenceResponse,
    FollowUpListResponse,
    FollowupRequest,
    FollowupResponse,
    OutcomeListResponse,
    OutcomeRequest,
    OutcomeResponse,
    OutcomeSummaryResponse,
    QARequest,
    QAResponse,
    ReviewRequest,
    ReviewResponse,
    RunAccepted,
    RunCreateRequest,
    RunResourcesResponse,
    RunStatusResponse,
)

from api.auth import Identity, require
from api.models import ReportResponse, RunHistoryResponse, RunListResponse
from api.errors import ApiError
from api.routes.deps import check_city, runs
from api.services.runs import RunService

router = APIRouter(dependencies=[Depends(require("조회"))])


def _limit(request: Request, who: Identity) -> None:
    """사용자별 횟수 한도 (꺼져 있으면 통과)."""
    limiter = getattr(request.app.state, "run_limiter", None)
    if limiter is not None:
        limiter.hit(who.actor())


@router.post("/runs", response_model=RunAccepted, status_code=202)
def create_run(req: RunCreateRequest, request: Request, svc: RunService = Depends(runs),
               who: Identity = Depends(require("검토"))) -> RunAccepted:
    _limit(request, who)
    out = svc.create(req, who)
    request.state.run_id = out.run_id  # 구조화 로그용
    return out


@router.get("/runs", response_model=RunListResponse)
def list_runs(candidate_id: str | None = None, limit: int = Query(default=50, ge=1, le=200),
              svc: RunService = Depends(runs)) -> RunListResponse:
    """명세에 없는 조회: 실행 기록 페이지, 후보별 실행 찾기."""
    return svc.list(candidate_id, limit)


@router.get("/followups", response_model=FollowUpListResponse)
def followups(city: str | None = None, week: date | None = Query(default=None, description="이 날부터 7일"),
              date_from: date | None = Query(default=None, alias="from"),
              date_to: date | None = Query(default=None, alias="to"),
              as_of: date | None = Query(default=None, description="주면 지남 / 이번 주 / 나중 표시"),
              svc: RunService = Depends(runs)) -> FollowUpListResponse:
    """후속 확인 모아 보기 (후보마다 가장 최근 조사 실행의 마지막 후속 확인일). 화면이 실행마다 /history 를 부르지 않게."""
    if city is not None:
        check_city(city)
    if week is not None:
        if date_from is not None or date_to is not None:
            raise ApiError(400, "INVALID_REQUEST", "week 와 from·to 는 함께 쓸 수 없습니다.")
        date_from, date_to = week, week + timedelta(days=6)
    if date_from and date_to and date_from > date_to:
        raise ApiError(400, "INVALID_REQUEST", "from 은 to 보다 늦을 수 없습니다.")
    return svc.followups(city, date_from, date_to, as_of)


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def run_status(run_id: str, svc: RunService = Depends(runs)) -> RunStatusResponse:
    return svc.status(run_id)


@router.get("/runs/{run_id}/evidence", response_model=EvidenceResponse)
def run_evidence(run_id: str, svc: RunService = Depends(runs)) -> EvidenceResponse:
    return svc.evidence(run_id)


@router.get("/runs/{run_id}/report", response_model=ReportResponse)
def run_report(run_id: str, svc: RunService = Depends(runs)) -> ReportResponse:
    """명세에 없는 조회: 지금 판의 보고서 문장."""
    return svc.report(run_id)


@router.get("/runs/{run_id}/resources", response_model=RunResourcesResponse)
def run_resources(run_id: str, svc: RunService = Depends(runs)) -> RunResourcesResponse:
    """이 실행에 묶인 추천 자원 (실행 스냅샷·원장으로 고른 것 + 담당자 판정)."""
    return svc.resources(run_id)


@router.post("/runs/{run_id}/followup", response_model=FollowupResponse)
def followup(run_id: str, req: FollowupRequest, request: Request, svc: RunService = Depends(runs),
             who: Identity = Depends(require("검토"))) -> FollowupResponse:
    """이어 묻기: 이 실행 근거 카드 안에서만 답함 (새 도구 실행 없음). LLM 을 부를 수 있어 검토 이상."""
    _limit(request, who)
    return svc.followup(run_id, req, who)


@router.get("/runs/{run_id}/history", response_model=RunHistoryResponse)
def run_history(run_id: str, svc: RunService = Depends(runs)) -> RunHistoryResponse:
    """명세에 없는 조회: 보고서 판·검토·승인 기록."""
    return svc.history(run_id)


@router.post("/runs/{run_id}/review", response_model=ReviewResponse)
def review(run_id: str, req: ReviewRequest, svc: RunService = Depends(runs),
           who: Identity = Depends(require("검토"))) -> ReviewResponse:
    return svc.review(run_id, req, who)


@router.post("/runs/{run_id}/approve", response_model=ApproveResponse)
def approve(run_id: str, req: ApproveRequest, svc: RunService = Depends(runs),
            who: Identity = Depends(require("승인"))) -> ApproveResponse:
    return svc.approve(run_id, req, who)


@router.post("/runs/{run_id}/outcomes", response_model=OutcomeResponse)
def record_outcome(run_id: str, req: OutcomeRequest, svc: RunService = Depends(runs),
                   who: Identity = Depends(require("검토"))) -> OutcomeResponse:
    """승인된 조사 실행의 후보(동·집단)마다 현장 확인 건수. 건수만 받음 (개인 정보·메모 없음)."""
    return svc.record_outcome(run_id, req, who)


@router.get("/runs/{run_id}/outcomes", response_model=OutcomeListResponse)
def run_outcomes(run_id: str, svc: RunService = Depends(runs)) -> OutcomeListResponse:
    """이 실행의 현장 확인 결과 (이 실행 상세 화면용이라 10 미만도 그대로)."""
    return svc.outcomes(run_id)


@router.get("/outcomes/summary", response_model=OutcomeSummaryResponse)
def outcome_summary(city: str | None = None, by: str = Query(default="trigger", description="trigger / event"),
                    svc: RunService = Depends(runs)) -> OutcomeSummaryResponse:
    """현장 확인 결과 모아 보기 (도시 × 트리거 또는 사건). 합계 10 미만 칸은 숫자 없이."""
    if city is not None:
        check_city(city)
    if by not in ("trigger", "event"):
        raise ApiError(400, "INVALID_REQUEST", "by 는 trigger 또는 event 입니다.")
    return svc.outcome_summary(city, by)


@router.post("/qa", response_model=QAResponse)
def qa(req: QARequest, request: Request, svc: RunService = Depends(runs),
       who: Identity = Depends(require("검토"))) -> QAResponse:
    _limit(request, who)
    out = svc.qa(req, who)
    request.state.run_id = out.run_id
    return out
