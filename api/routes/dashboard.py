"""GET /overview, GET /map, GET /candidates, GET /resources

- 가짜 백엔드: api/services/fake_data.py
- 실제 백엔드: api/services/dashboard_real.py. 활성 스냅샷의 발행된 정기 감시 실행(없으면 요청 안에서 한 번 돌림)과
  원장 v2 에서 읽는다. 후보 ID 는 POST /runs 가 받는 ID 와 같다 (API 명세 §5)
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from bigcontest.agent.schemas import (
    CandidateListResponse,
    MapResponse,
    OverviewResponse,
    ResourceListResponse,
)

from api.auth import Identity, require
from api.errors import ApiError
from api.routes.deps import check_as_of, check_city, check_mode, snapshots
from api.services import fake_data as fd
from api.services.snapshots import SnapshotService

router = APIRouter(dependencies=[Depends(require("조회"))])

TYPES = ("scheduled_access_review", "unexpected_activity_deviation")
LEVELS = ("Level 0", "Level 1", "Level 2", "Level 3", "판단 보류")


def real(request: Request):
    """실제 백엔드면 RealDashboard, 가짜면 None."""
    return getattr(request.app.state, "dashboard", None)


@router.get("/overview", response_model=OverviewResponse)
def overview(as_of: date, city: str, analysis_mode: str = "simulated_replay",
             snaps: SnapshotService = Depends(snapshots), dash=Depends(real),
             who: Identity = Depends(require("조회"))) -> OverviewResponse:
    city, as_of, mode = check_city(city), check_as_of(as_of), check_mode(analysis_mode)
    if dash is not None:
        return dash.overview(as_of, city, mode, who)
    return fd.overview(city, as_of, mode, snaps.active().snapshot_id)


@router.get("/map", response_model=MapResponse)
def map_view(as_of: date, city: str, analysis_mode: str = "simulated_replay",
             snaps: SnapshotService = Depends(snapshots), dash=Depends(real),
             who: Identity = Depends(require("조회"))) -> MapResponse:
    city, as_of, mode = check_city(city), check_as_of(as_of), check_mode(analysis_mode)
    if dash is not None:
        return dash.map_view(as_of, city, mode, who)
    return fd.map_view(city, as_of, snaps.active().snapshot_id)


@router.get("/candidates", response_model=CandidateListResponse)
def candidates(as_of: date, city: str, type: str | None = None, level: str | None = None,
               analysis_mode: str = Query(default="simulated_replay", description="실제 백엔드의 정기 감시 모드"),
               snaps: SnapshotService = Depends(snapshots), dash=Depends(real),
               who: Identity = Depends(require("조회"))) -> CandidateListResponse:
    city, as_of, mode = check_city(city), check_as_of(as_of), check_mode(analysis_mode)
    if type is not None and type not in TYPES:
        raise ApiError(400, "INVALID_REQUEST",
                       "후보 종류는 scheduled_access_review, unexpected_activity_deviation 중 하나입니다.")
    if level is not None and level not in LEVELS:
        raise ApiError(400, "INVALID_REQUEST", "등급은 Level 0~3 또는 판단 보류입니다.")
    if dash is not None:
        out = dash.candidates(as_of, city, mode, who)
    else:
        out = CandidateListResponse(as_of=as_of, city=city, snapshot_id=snaps.active().snapshot_id,
                                    selection_budget=fd.SELECTION_BUDGET[city], items=fd.candidates_for(city, as_of))
    items = [c for c in out.items if (type is None or c.candidate_type == type)
             and (level is None or c.candidate_level == level)]
    return out.model_copy(update={"items": items})


@router.get("/resources", response_model=ResourceListResponse)
def resources(
    adm_cd: str | None = None,
    need_type: str | None = None,
    group: str | None = None,
    city: str | None = Query(default=None, description="adm_cd 없이 도시로 거를 때"),
    limit: int = Query(default=50, ge=1, le=200, description="실제 백엔드에서 돌려줄 최대 수 (total 은 전체 수)"),
    cause_type: str | None = Query(default=None, max_length=100,
                                   description="G 추가: 원인 유형 ('+'로 이음). 그 원인의 필요 유형 자원만 (후보 목록의 cause_type)"),
    dash=Depends(real),
) -> ResourceListResponse:
    if adm_cd is not None and adm_cd not in fd.areas():
        raise ApiError(400, "INVALID_REQUEST", "행정동 코드가 강남·춘천 목록에 없습니다.")
    if dash is not None:
        from api.services.dashboard_real import need_types

        needs = need_types()
    else:
        needs = fd.NEED_TYPES
    if need_type is not None and need_type not in needs:
        raise ApiError(400, "INVALID_REQUEST", "필요 유형은 " + ", ".join(needs) + " 중 하나입니다.")
    if group is not None and group not in fd.GROUPS:
        raise ApiError(400, "INVALID_REQUEST", "집단은 " + ", ".join(fd.GROUPS) + " 중 하나입니다.")
    if city is not None:
        check_city(city)
    from api.services.dashboard_real import needs_of

    try:
        cause_needs = needs_of(cause_type) if cause_type else []
    except KeyError:
        raise ApiError(400, "INVALID_REQUEST", "모르는 원인 유형입니다 (configs/need_types.yaml cause_types).") from None
    if dash is not None:
        return dash.resources(city, adm_cd, need_type, group, limit, cause_type)
    items = fd.resources_for(city, adm_cd, need_type, group, needs=cause_needs or None)
    return ResourceListResponse(adm_cd=adm_cd, need_type=need_type, group=group, items=items,
                                cause_type=cause_type, need_types=[need_type] if need_type else cause_needs,
                                total=len(items),
                                scope_total=len(fd.resources_for(city, adm_cd, None, group)) if cause_needs else None)
