"""경로가 쓰는 서비스 꺼내기와 공통 쿼리 검사."""

from __future__ import annotations

from datetime import date

from fastapi import Request

from bigcontest.agent.schemas import CITIES, DATA_END, DATA_START

from api.errors import ApiError
from api.services.runs import RunService
from api.services.snapshots import SnapshotService

MODES = ("retrospective", "simulated_replay", "strict_as_of")


def runs(request: Request) -> RunService:
    return request.app.state.runs


def snapshots(request: Request) -> SnapshotService:
    return request.app.state.snapshots


def check_city(city: str) -> str:
    if city not in CITIES:
        raise ApiError(400, "INVALID_REQUEST", "도시는 강남, 춘천만 고를 수 있습니다.")
    return city


def check_as_of(as_of: date) -> date:
    if not (DATA_START <= as_of <= DATA_END):
        raise ApiError(400, "INVALID_REQUEST", "기준일은 2025-07-01 ~ 2025-12-31 안의 날짜여야 합니다.")
    return as_of


def check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ApiError(400, "INVALID_REQUEST", "분석 모드는 retrospective, simulated_replay, strict_as_of 중 하나입니다.")
    return mode
