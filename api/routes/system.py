"""GET /health, GET /me, GET /snapshots/active, GET /data/capabilities

/health 만 토큰 없이 열려 있다 (상태 확인용). 나머지는 조회 권한.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends

from bigcontest.agent.schemas import CapabilitiesResponse, HealthResponse, MeResponse, SnapshotInfo
from bigcontest.contracts.grain import get_data_capabilities

from api.auth import Identity, require
from api.routes.deps import snapshots
from api.services.fake_data import KST
from api.services.snapshots import SnapshotService

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(snaps: SnapshotService = Depends(snapshots)) -> HealthResponse:
    sid = snaps.active_id
    return HealthResponse(status="ok" if sid else "degraded", active_snapshot_id=sid, time=datetime.now(KST))


@router.get("/me", response_model=MeResponse)
def me(who: Identity = Depends(require("조회"))) -> MeResponse:
    return MeResponse(name=who.record_name(), role=who.role, shared=who.shared, authenticated=who.authenticated)


@router.get("/snapshots/active", response_model=SnapshotInfo, dependencies=[Depends(require("조회"))])
def active_snapshot(snaps: SnapshotService = Depends(snapshots)) -> SnapshotInfo:
    return snaps.active()


@router.get("/data/capabilities", response_model=CapabilitiesResponse, dependencies=[Depends(require("조회"))])
def capabilities() -> CapabilitiesResponse:
    # 설정 파일(configs/grain_contract.yaml)에서 만듦. 가짜 값이 아님.
    return get_data_capabilities()
