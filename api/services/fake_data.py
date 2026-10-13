"""가짜 데이터. 분석 결과가 아님.

- 모든 응답은 `bigcontest.agent.schemas`의 Pydantic 모델로 만든다 (명세와 모양이 어긋나지 않게).
- 행정동 이름·중심점은 ref/adm_cd_names.csv 에서 읽는다. 1인세대 비율·후보·등급·자원은 지어낸 값이다.
- 시연 흐름 D1~D4 (docs/화면_설계.md §5)가 클릭으로 재현되도록 값을 맞췄다.
- 기본 백엔드(fake)용이다. `LINKRADAR_BACKEND=real` 이면 스냅샷 읽기 + 에이전트 그래프를 쓴다.
"""

from __future__ import annotations

import csv
import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from bigcontest.agent.schemas import (
    Candidate,
    CandidateCountRange,
    CandidateListResponse,
    ContractResult,
    EvidenceCard,
    Fact,
    GuardResult,
    MapArea,
    MapResource,
    MapResponse,
    OverviewResponse,
    ReleasedCandidate,
    Report,
    ResourceRecommendation,
    SnapshotInfo,
    ToolResult,
    TraceStep,
    TriggerInfo,
    ValueState,
)
from bigcontest.agent.tools import report as R

ROOT = Path(__file__).resolve().parents[2]
KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 스냅샷 (가짜)

FAKE_SNAPSHOTS: dict[str, SnapshotInfo] = {
    "snap-fake-20261012-180000-9c0d": SnapshotInfo(
        snapshot_id="snap-fake-20261012-180000-9c0d",
        created_at=datetime(2026, 10, 12, 18, 0, tzinfo=KST),
        data_from=date(2025, 7, 1),
        data_to=date(2025, 12, 31),
        cleaning_rule_version="clean-v1",
        rule_version="rules-v0.1",
        ledger_version="ledger@sha256:fake0a11ce00",
        previous_snapshot_id=None,
        row_counts={"candidates.parquet": 38},
    ),
    "snap-fake-20261013-101500-a1b2": SnapshotInfo(
        snapshot_id="snap-fake-20261013-101500-a1b2",
        created_at=datetime(2026, 10, 13, 10, 15, tzinfo=KST),
        data_from=date(2025, 7, 1),
        data_to=date(2025, 12, 31),
        cleaning_rule_version="clean-v1",
        rule_version="rules-v0.1",
        ledger_version="ledger@sha256:fake3f9a1c0b",
        previous_snapshot_id="snap-fake-20261012-180000-9c0d",
        row_counts={"candidates.parquet": 42, "analysis.duckdb:card_cohort_daily": 165600},
    ),
}
DEFAULT_ACTIVE_SNAPSHOT = "snap-fake-20261013-101500-a1b2"

# 검토 예산 K (도시별, 가짜)
SELECTION_BUDGET = {"강남": 3, "춘천": 2}

# ---------------------------------------------------------------- 행정동


@dataclass(frozen=True)
class Area:
    city: str
    adm_cd: str
    adm_nm: str
    adm_type: str
    lon: float
    lat: float


@lru_cache(maxsize=1)
def areas() -> dict[str, Area]:
    out: dict[str, Area] = {}
    with open(ROOT / "ref" / "adm_cd_names.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["adm_cd"]] = Area(r["city"], r["adm_cd"], r["adm_nm"], r["adm_type"], float(r["lon"]), float(r["lat"]))
    return out


def area_by_name(city: str, name: str) -> Area:
    for a in areas().values():
        if a.city == city and a.adm_nm == name:
            return a
    raise KeyError(name)


def city_areas(city: str) -> list[Area]:
    return [a for a in areas().values() if a.city == city]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# 가짜 1인세대 비율. 이름이 있는 동만 값을 정하고 나머지는 이름 해시로 낮은 값.
_STRUCTURE = {
    "강남": {"역삼1동": 0.68, "논현1동": 0.61, "논현2동": 0.55, "대치4동": 0.52, "신사동": 0.50, "삼성2동": 0.47,
             "역삼2동": 0.44, "청담동": 0.42, "삼성1동": 0.40, "개포3동": None},
    "춘천": {"효자1동": 0.52, "조운동": 0.50, "남면": 0.47, "북산면": 0.46, "근화동": 0.45, "사북면": 0.44,
             "동산면": 0.42, "약사명동": 0.41, "소양동": 0.40, "교동": 0.39},
}


def structure_value(a: Area) -> float | None:
    table = _STRUCTURE[a.city]
    if a.adm_nm in table:
        return table[a.adm_nm]
    h = int(hashlib.sha256(a.adm_cd.encode()).hexdigest()[:6], 16)
    return round(0.22 + (h % 1600) / 10000, 2)  # 0.22~0.38


def structure_ranks(city: str) -> dict[str, int]:
    vals = [(structure_value(a), a.adm_cd) for a in city_areas(city)]
    ranked = sorted([v for v in vals if v[0] is not None], key=lambda x: -x[0])
    return {cd: i + 1 for i, (_, cd) in enumerate(ranked)}


# ---------------------------------------------------------------- 자원 (가짜)


@dataclass(frozen=True)
class FakeResource:
    resource_id: str
    name: str
    resource_type: str
    city: str | None  # None = 전국 전화형
    adm_cd: str | None
    lat: float | None
    lon: float | None
    target_group: str
    groups: tuple[str, ...]
    need_types: tuple[str, ...]
    eligibility: str = "적합"
    operating_status: str = "운영"
    phone_based: bool = False
    hub: bool = False  # 거점 직선거리 계산에 쓰는 관측 거점
    warnings: tuple[str, ...] = ()
    source_url: str = "https://www.data.go.kr/"
    verified_date: date = date(2026, 3, 18)


_ALL = ("고령층", "중장년 남성 1인가구", "청년 남성")
RESOURCES: list[FakeResource] = [
    FakeResource("R0739", "외로움안녕120", "상담", None, None, None, None, "전 연령", _ALL,
                 ("안부·관계", "정신건강 상담"), phone_based=True),
    # 춘천
    FakeResource("R0412", "춘천시 노인복지관", "노인복지", "춘천", "32010530", 37.8781, 127.7319, "노년", ("고령층",),
                 ("안부·관계", "생필품"), eligibility="확인 필요", operating_status="확인 필요", hub=True,
                 warnings=("연휴 운영 여부 미확인",)),
    FakeResource("R0420", "춘천종합사회복지관", "사회복지관", "춘천", "32010670", 37.8541, 127.7335, "전 연령", _ALL,
                 ("생필품", "안부·관계"), hub=True),
    FakeResource("R0415", "남면 행정복지센터", "행정복지센터", "춘천", "32010350", 37.7401, 127.5850, "전 연령", _ALL,
                 ("생필품", "이동 지원")),
    FakeResource("R0418", "북산면 보건진료소", "보건진료소", "춘천", "32010390", 37.9668, 127.9230, "전 연령", _ALL,
                 ("의료 접근",), eligibility="적합", operating_status="확인 필요", warnings=("동절기 운영 시간 미확인",)),
    FakeResource("R0431", "춘천 순회 안부 확인반", "노인맞춤돌봄", "춘천", None, None, None, "노년", ("고령층",),
                 ("안부·관계",), eligibility="확인 필요", warnings=("위치 확인 필요: 주소가 원장에 없음",
                                                                  "자격 확인 필요: 노인맞춤돌봄 대상 기준")),
    FakeResource("R0440", "강원대 학생상담센터", "상담/정신건강", "춘천", "32010520", 37.8689, 127.7440, "청년",
                 ("청년 남성",), ("정신건강 상담",), eligibility="확인 필요", warnings=("재학생만 이용 가능할 수 있음",)),
    # 강남
    FakeResource("R0105", "강남노인종합복지관", "노인복지", "강남", "11230720", 37.4818, 127.0846, "노년", ("고령층",),
                 ("안부·관계", "생필품"), hub=True),
    FakeResource("R0106", "논현노인종합복지관", "노인복지", "강남", "11230520", 37.5110, 127.0283, "노년", ("고령층",),
                 ("안부·관계", "생필품"), operating_status="확인 필요", hub=True, warnings=("연휴 운영 여부 미확인",)),
    FakeResource("R0110", "수서종합사회복지관", "사회복지관", "강남", "11230750", 37.4872, 127.1012, "전 연령", _ALL,
                 ("생필품", "안부·관계"), hub=True),
    FakeResource("R0101", "강남구 중장년 1인가구 자조모임", "1인가구지원(청년·중장년)", "강남", "11230520", 37.5139,
                 127.0301, "중장년", ("중장년 남성 1인가구",), ("안부·관계",), eligibility="확인 필요",
                 warnings=("자격 확인 필요: 40~64세 강남구 거주",)),
    FakeResource("R0102", "역삼1동 주민센터", "행정복지센터", "강남", "11230640", 37.5007, 127.0369, "전 연령", _ALL,
                 ("생필품", "이동 지원")),
    FakeResource("R0103", "강남구 정신건강복지센터", "상담/정신건강", "강남", "11230600", 37.4932, 127.0572, "전 연령",
                 _ALL, ("정신건강 상담",)),
    FakeResource("R0104", "서울잇다플레이스", "1인가구지원(청년·중장년)", "강남", None, None, None, "청년·중장년",
                 ("중장년 남성 1인가구", "청년 남성"), ("안부·관계",), eligibility="확인 필요",
                 operating_status="확인 필요",
                 warnings=("권역 외: 강남구 밖 성동구 거점", "위치 확인 필요: 위치·연락처 미확인")),
]
LOCAL_TYPES = ("행정복지센터", "보건진료소")
GROUPS = ("고령층", "중장년 남성 1인가구", "청년 남성")
NEED_TYPES = ("의료 접근", "생필품", "안부·관계", "정신건강 상담", "이동 지원")


def nearest_hub_km(a: Area) -> float | None:
    hubs = [r for r in RESOURCES if r.hub and r.city == a.city and r.lat is not None]
    if not hubs:
        return None
    return round(min(haversine_km(a.lat, a.lon, r.lat, r.lon) for r in hubs), 1)


def resources_for(city: str | None, adm_cd: str | None, need_type: str | None, group: str | None,
                  needs: list[str] | None = None) -> list[ResourceRecommendation]:
    """관할 → 대상 → 필요 유형 → (거리/전화형) 순. 출처·확인일이 없는 자원은 원장에 없음."""
    base = areas().get(adm_cd) if adm_cd else None
    if base is not None:
        city = base.city
    out = []
    for r in RESOURCES:
        if r.city is not None and city is not None and r.city != city:
            continue
        if group and group not in r.groups:
            continue
        if need_type and need_type not in r.need_types:
            continue
        if needs and not set(needs) & set(r.need_types):  # 원인 유형의 필요 유형 중 하나라도
            continue
        # 동 창구(행정복지센터·보건진료소)는 그 동에만 추천
        if r.resource_type in LOCAL_TYPES and base is not None and r.adm_cd != base.adm_cd:
            continue
        dist = None
        if base is not None and r.lat is not None and not r.phone_based:
            dist = round(haversine_km(base.lat, base.lon, r.lat, r.lon), 1)
        out.append(ResourceRecommendation(
            resource_id=r.resource_id, name=r.name, resource_type=r.resource_type,
            city=r.city or city or "강남", adm_cd=r.adm_cd, target_group=r.target_group,
            eligibility=r.eligibility, operating_status=r.operating_status, distance_km=dist,
            phone_based=r.phone_based, source_url=r.source_url, verified_date=r.verified_date,
            warnings=list(r.warnings),
        ))
    # 전화형 먼저, 그다음 가까운 순, 거리 모르는 것은 뒤
    out.sort(key=lambda x: (not x.phone_based, x.distance_km is None, x.distance_km or 0))
    return out


# ---------------------------------------------------------------- 트리거 (가짜 달력)

CHUSEOK_START = date(2025, 10, 3)


@dataclass(frozen=True)
class TriggerWindow:
    key: str
    rule: str
    name: str
    cities: tuple[str, ...]
    active_from: date
    active_to: date
    starts_on: date
    note: str


TRIGGERS = [
    TriggerWindow("R4", "R4", "폭염 예보", ("강남", "춘천"), date(2025, 7, 25), date(2025, 8, 15), date(2025, 7, 25),
                  "최고 33℃ 이상 예보. 과거 재생은 ASOS 실측을 예보로 가정"),
    TriggerWindow("R1", "R1", "추석 연휴 D-7", ("강남", "춘천"), date(2025, 9, 26), date(2025, 10, 2), CHUSEOK_START,
                  "3일 이상 연휴"),
    TriggerWindow("R3", "R3", "추석 연휴 후 회복 확인", ("춘천",), date(2025, 10, 10), date(2025, 10, 19),
                  date(2025, 10, 10), "연휴 끝난 뒤 10일 (춘천 고령층)"),
    TriggerWindow("W", "W", "동절기 (춘천 읍·면)", ("춘천",), date(2025, 11, 1), date(2025, 12, 31), date(2025, 11, 1),
                  "11~2월 계절 점검. 관광형 면은 따로 표시"),
]


def triggers_for(city: str, as_of: date) -> list[TriggerInfo]:
    out = []
    for t in TRIGGERS:
        if city in t.cities and t.active_from <= as_of <= t.active_to:
            out.append(TriggerInfo(trigger_id=f"TR-{t.active_from:%Y%m%d}-{t.key}", rule=t.rule, name=t.name,
                                   city=city, starts_on=t.starts_on, note=t.note))
    return out


# ---------------------------------------------------------------- 후보 틀


@dataclass(frozen=True)
class Tmpl:
    candidate_id: str
    city: str
    kind: str  # scheduled_access_review / unexpected_activity_deviation
    visible_from: date
    visible_to: date
    level: str
    level_reason: str
    group: str  # 서비스 대상 집단 이름
    cohort: str  # 분석 코호트
    dong: str | None = None
    check_first: tuple[str, ...] = ()
    trigger_key: str | None = None
    input_status: str = "충족"
    missing: tuple[str, ...] = ()
    counter: str = "해당 없음"
    counter_reason: str | None = None
    # 근거 재료
    payment_ratio: float | None = None  # 이탈: 평소 대비 결제
    holiday_ratio: float | None = None  # 이탈: 공휴일 기대값 대비
    city_ratio: float | None = None  # 이탈: 도시 전체 평소 대비
    ref_note: tuple[str, float] | None = None  # 외부 참고 (시민생활)
    extra_limits: tuple[str, ...] = ()


SERVICE_GROUP = {"고령층": "65세 이상", "중장년 남성 1인가구": "40~64세 남성", "청년 남성": "20~34세 남성"}
RESIDENT_COHORT = {"고령층": "65세 이상 (주민등록 1세 단위)", "중장년 남성 1인가구": "40~64세 남성 (주민등록 1세 단위)",
                   "청년 남성": "20~34세 남성 (주민등록 1세 단위)"}

D = date
TEMPLATES: list[Tmpl] = [
    # ---- R4 폭염 (7/25~8/15)
    Tmpl("C-20250725-501", "강남", "scheduled_access_review", D(2025, 7, 25), D(2025, 8, 15), "Level 2",
         "구조 취약성 상위 + 예정된 트리거(폭염)", "고령층", "", dong="일원본동", trigger_key="R4"),
    Tmpl("C-20250725-502", "강남", "scheduled_access_review", D(2025, 7, 25), D(2025, 8, 15), "Level 1",
         "트리거만 있음 (구조 취약성 중간)", "고령층", "", dong="개포1동", trigger_key="R4"),
    Tmpl("C-20250725-001", "춘천", "scheduled_access_review", D(2025, 7, 25), D(2025, 8, 15), "Level 2",
         "구조 취약성 상위 + 예정된 트리거(폭염)", "고령층", "", dong="효자1동", trigger_key="R4"),
    # ---- 강남 청년 남성 이탈 (통과)
    Tmpl("C-20250818-511", "강남", "unexpected_activity_deviation", D(2025, 8, 18), D(2025, 9, 30), "Level 2",
         "반증 통과 이탈 + 구조 취약성 상위 동 있음", "청년 남성", "20대·30대 남성",
         check_first=("역삼1동", "논현1동"), counter="통과", counter_reason="공휴일·도시 공통·강수로 설명되지 않음",
         payment_ratio=0.81, holiday_ratio=0.83, city_ratio=0.99),
    # ---- R1 추석 D-7 (9/26~10/2)
    Tmpl("C-20250926-501", "강남", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 2",
         "구조 취약성 상위(1인세대 비율 2위) + 예정된 트리거", "중장년 남성 1인가구", "", dong="논현1동",
         trigger_key="R1", ref_note=("중장년 남성 1인가구 '소통 적음' 남녀 비 (여성 대비)", 1.8)),
    Tmpl("C-20250926-502", "강남", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 2",
         "구조 취약성 상위(1인세대 비율 1위) + 예정된 트리거", "청년 남성", "", dong="역삼1동", trigger_key="R1"),
    Tmpl("C-20250926-503", "강남", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 2",
         "구조 취약성 상위 + 예정된 트리거", "중장년 남성 1인가구", "", dong="대치4동", trigger_key="R1"),
    Tmpl("C-20250926-504", "강남", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 1",
         "트리거만 확인. 구조 지표 품질 의심으로 부분 판정", "고령층", "", dong="개포3동", trigger_key="R1",
         input_status="부분 판정", missing=("resident_structure (개포3동 7개 지표 0, 품질 의심)",)),
    Tmpl("C-20250926-001", "춘천", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 2",
         "접근성 근거(관측 거점까지 직선거리) + 예정된 트리거", "고령층", "60대·70대·80대+", dong="남면",
         trigger_key="R1"),
    Tmpl("C-20250926-002", "춘천", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 2",
         "접근성 근거(관측 거점까지 직선거리) + 예정된 트리거", "고령층", "60대·70대·80대+", dong="북산면",
         trigger_key="R1"),
    Tmpl("C-20250926-003", "춘천", "scheduled_access_review", D(2025, 9, 26), D(2025, 10, 2), "Level 1",
         "트리거 + 접근성 근거 하나. 구조 취약성은 중간", "고령층", "60대·70대·80대+", dong="사북면",
         trigger_key="R1"),
    Tmpl("C-20250926-004", "춘천", "unexpected_activity_deviation", D(2025, 9, 20), D(2025, 10, 2), "판단 보류",
         "반증 보류", "고령층", "80대+", check_first=("효자1동", "남면"), input_status="부분 판정",
         missing=("weather_asos",), counter="보류", counter_reason="강수 반증 자료 없음", payment_ratio=0.84,
         holiday_ratio=None, city_ratio=0.98),
    # ---- 추석 뒤 (10/10~)
    Tmpl("C-20251010-501", "강남", "unexpected_activity_deviation", D(2025, 10, 10), D(2025, 10, 24), "Level 1",
         "반증에서 기각됨 (기각 사유 기록)", "중장년 남성 1인가구", "40대·50대 남성", counter="기각",
         counter_reason="추석 연휴 감소가 공휴일 기대값으로 설명됨 (R5 반증 달력)", payment_ratio=0.72,
         holiday_ratio=0.98, city_ratio=0.74),
    Tmpl("C-20251010-502", "강남", "unexpected_activity_deviation", D(2025, 10, 10), D(2025, 10, 31), "Level 1",
         "도시 공통 변화 / 확인 필요 (평소 수준 미회복, 도시 전체도 낮음)", "고령층", "70대·80대+",
         check_first=("논현1동", "역삼1동"), counter="보류",
         counter_reason="도시 공통 변화 / 확인 필요: 평소 수준으로 돌아오지 않았지만 도시 전체도 함께 낮음",
         payment_ratio=0.86, holiday_ratio=0.9, city_ratio=0.88),
    Tmpl("C-20251010-503", "강남", "unexpected_activity_deviation", D(2025, 10, 10), D(2025, 10, 19), "판단 보류",
         "반증 보류 (검토 중)", "고령층", "60대", check_first=("역삼1동",), counter="보류",
         counter_reason="검토 중: 연휴 뒤 회복 판정 기간(10일)이 아직 지나지 않음", payment_ratio=0.89,
         holiday_ratio=0.93, city_ratio=0.95),
    Tmpl("C-20251010-001", "춘천", "scheduled_access_review", D(2025, 10, 10), D(2025, 10, 19), "Level 2",
         "접근성 근거 + 예정된 트리거(연휴 후 회복)", "고령층", "60대·70대·80대+", dong="신북읍", trigger_key="R3"),
    Tmpl("C-20251010-002", "춘천", "scheduled_access_review", D(2025, 10, 10), D(2025, 10, 19), "Level 1",
         "트리거만 있음", "고령층", "60대·70대·80대+", dong="동면", trigger_key="R3"),
    Tmpl("C-20251012-011", "춘천", "unexpected_activity_deviation", D(2025, 10, 12), D(2025, 10, 31), "Level 1",
         "도시 공통 변화 / 확인 필요", "고령층", "70대", check_first=("남면", "북산면"), counter="보류",
         counter_reason="도시 공통 변화 / 확인 필요: 평소 수준으로 돌아오지 않았지만 도시 전체도 함께 낮음",
         payment_ratio=0.85, holiday_ratio=0.91, city_ratio=0.87),
    # ---- W 동절기 (11~12월, 춘천 읍·면)
    Tmpl("C-20251101-001", "춘천", "scheduled_access_review", D(2025, 11, 1), D(2025, 12, 31), "Level 3",
         "구조 취약성 상위 + 접근성 근거 + 재현된 계절 수축 + 필수 근거 모두", "고령층", "60대·70대·80대+",
         dong="북산면", trigger_key="W"),
    Tmpl("C-20251101-002", "춘천", "scheduled_access_review", D(2025, 11, 1), D(2025, 12, 31), "Level 3",
         "구조 취약성 상위 + 접근성 근거 + 필수 근거 모두", "고령층", "60대·70대·80대+", dong="남면",
         trigger_key="W"),
    Tmpl("C-20251101-003", "춘천", "scheduled_access_review", D(2025, 11, 1), D(2025, 12, 31), "Level 2",
         "접근성 근거 + 예정된 트리거", "고령층", "60대·70대·80대+", dong="동산면", trigger_key="W"),
    Tmpl("C-20251101-004", "춘천", "scheduled_access_review", D(2025, 11, 1), D(2025, 12, 31), "Level 2",
         "접근성 근거 + 예정된 트리거", "고령층", "60대·70대·80대+", dong="사북면", trigger_key="W"),
    Tmpl("C-20251101-005", "춘천", "scheduled_access_review", D(2025, 11, 1), D(2025, 12, 31), "Level 1",
         "관광형 면: 여름 방문객 비중이 커 겨울 감소가 과장될 수 있음", "고령층", "60대·70대·80대+", dong="서면",
         trigger_key="W", input_status="부분 판정", missing=("seasonal_baseline (관광형 면 별도 기준선)",)),
    Tmpl("C-20251110-021", "춘천", "unexpected_activity_deviation", D(2025, 11, 10), D(2025, 11, 30), "Level 2",
         "반증 통과 이탈 + 접근성 근거 있는 면", "고령층", "80대+", check_first=("북산면", "남면", "효자1동"),
         counter="통과", counter_reason="공휴일·도시 공통·강수로 설명되지 않음", payment_ratio=0.79,
         holiday_ratio=0.8, city_ratio=1.0),
    Tmpl("C-20251215-031", "춘천", "unexpected_activity_deviation", D(2025, 12, 15), D(2025, 12, 31), "Level 1",
         "반증에서 기각됨 (기각 사유 기록)", "청년 남성", "10대·20대", counter="기각",
         counter_reason="종강·방학 학사일정으로 설명됨 (가설: 대학 일정과의 연결은 확인 전)", payment_ratio=0.7,
         holiday_ratio=None, city_ratio=0.97),
]
TEMPLATE_BY_ID = {t.candidate_id: t for t in TEMPLATES}


def _visible(t: Tmpl, city: str, as_of: date) -> bool:
    return t.city == city and t.visible_from <= as_of <= t.visible_to


def _trigger_label(key: str | None) -> str | None:
    if key is None:
        return None
    t = next(x for x in TRIGGERS if x.key == key)
    return f"{t.rule} {t.name}"


def _evidence_ids(t: Tmpl) -> list[str]:
    stem = t.candidate_id.removeprefix("C-")
    if t.kind == "scheduled_access_review":
        n = 2 + (1 if t.city == "춘천" else 0) + (1 if t.ref_note else 0)
    else:
        n = 3
    return [f"EV-{stem}-{k}" for k in range(1, n + 1)]


def _base_candidate(t: Tmpl) -> dict:
    is_dev = t.kind == "unexpected_activity_deviation"
    adm = None if is_dev else area_by_name(t.city, t.dong).adm_cd  # type: ignore[arg-type]
    return dict(
        candidate_id=t.candidate_id, candidate_type=t.kind, city=t.city, adm_cd=adm,
        check_first_adm=[area_by_name(t.city, n).adm_cd for n in t.check_first],
        analysis_cohort=t.cohort or RESIDENT_COHORT[t.group], service_eligibility_group=SERVICE_GROUP[t.group],
        trigger=_trigger_label(t.trigger_key), input_status=t.input_status, missing_inputs=list(t.missing),
        counter_result=t.counter, counter_reason=t.counter_reason, candidate_level=t.level,
        level_reason=t.level_reason, evidence_ids=_evidence_ids(t), is_shared_context=bool(t.check_first),
        **_rule_fields(t),
    )


FAKE_RULE_VERSION = "rules-v0.1 (가짜)"
STRUCTURE_TOP_SHARE, STRUCTURE_MIN, ACCESS_FAR_KM = 0.3, 0.35, 5.0


def _rule_fields(t: Tmpl) -> dict:
    """가짜 등급 규칙 기록 (화면용). 실제 값은 등급 엔진(assign_level)이 채운다."""
    if t.counter == "기각":
        return dict(rule_id="L1-REJECTED", rule_version=FAKE_RULE_VERSION, rule_checks=[
            {"condition": "반증 통과", "met": False, "value": t.counter_reason}])
    if t.kind == "unexpected_activity_deviation":
        ranks = structure_ranks(t.city)
        n = len(ranks)
        top = any(ranks.get(area_by_name(t.city, d).adm_cd, n + 1) <= max(1, math.ceil(STRUCTURE_TOP_SHARE * n))
                  for d in t.check_first)
        passed = t.counter == "통과"
        return dict(rule_id="DEV-L2" if t.level == "Level 2" else "DEV-HOLD", rule_version=FAKE_RULE_VERSION,
                    rule_checks=[
                        {"condition": "반증 통과", "met": passed if t.counter != "보류" else None,
                         "value": t.counter, "input": "confounders" if t.counter == "보류" else None,
                         "level_if_met": None if passed else "Level 2"},
                        {"condition": "먼저 확인할 동 중 구조 상위 30%", "met": top, "threshold": "상위 30%",
                         "level_if_met": None if top else "Level 2"},
                    ])
    a = area_by_name(t.city, t.dong)  # type: ignore[arg-type]
    v, km = structure_value(a), nearest_hub_km(a)
    ranks = structure_ranks(t.city)
    rank, n = ranks.get(a.adm_cd), len(ranks)
    top = rank is not None and rank <= max(1, math.ceil(STRUCTURE_TOP_SHARE * n))
    far = km is not None and km >= ACCESS_FAR_KM
    checks = [
        {"condition": "구조 상위 30%", "met": None if v is None else top,
         "value": f"{v:g} ({rank}/{n}위)" if v is not None else None, "threshold": "상위 30%",
         "input": "structure_vulnerability", "level_if_met": None if top else "Level 2"},
        {"condition": "1인세대 비율 최소값", "met": None if v is None else v >= STRUCTURE_MIN, "value": v,
         "threshold": STRUCTURE_MIN, "comparator": ">="},
        {"condition": "관측 거점까지 직선거리", "met": None if km is None else far, "value": km,
         "threshold": ACCESS_FAR_KM, "comparator": ">=", "unit": "km", "input": "access_gap",
         "level_if_met": None if far else ("Level 3" if top else "Level 2")},
        {"condition": "예정된 트리거", "met": True, "value": _trigger_label(t.trigger_key)},
    ]
    if t.input_status != "충족":
        checks.append({"condition": "필수 근거 모두", "met": None, "value": ", ".join(t.missing),
                       "input": t.missing[0].split(" ")[0] if t.missing else None, "level_if_met": "Level 2"})
    rid = {"Level 3": "SCHED-L3", "Level 2": "SCHED-L2"}.get(t.level, "SCHED-L1")
    return dict(rule_id=rid, rule_version=FAKE_RULE_VERSION, rule_checks=checks)


_LEVEL_ORDER = {"Level 3": 0, "Level 2": 1, "Level 1": 2, "Level 0": 3, "판단 보류": 4}


def candidates_for(city: str, as_of: date) -> list[Candidate]:
    """기준일에 보이는 후보 + 검토 배정(K). 등급은 바꾸지 않고 배정만 매김."""
    ts = [t for t in TEMPLATES if _visible(t, city, as_of)]
    k = SELECTION_BUDGET[city]
    rankable = [t for t in ts if t.counter != "기각"]
    rankable.sort(key=lambda t: (_LEVEL_ORDER[t.level], t.kind != "scheduled_access_review", t.candidate_id))
    rank = {t.candidate_id: i + 1 for i, t in enumerate(rankable)}
    out = []
    for t in ts:
        d = _base_candidate(t)
        r = rank.get(t.candidate_id)
        d.update(queue_rank=r, selected_for_review=bool(r and r <= k), selection_budget=k)
        out.append(_with_cause(Candidate(**d)))
    out.sort(key=lambda c: (c.queue_rank is None, c.queue_rank or 0, c.candidate_id))
    return out


def _with_cause(c: Candidate) -> Candidate:
    """원인 유형·필요 유형 (자원 미리 보기용). 가짜는 거리를 보지 않고 트리거만."""
    from bigcontest.agent.tools.welfare import cause_for
    from bigcontest.resources import match as M

    cause = cause_for(c.trigger if c.candidate_type == "scheduled_access_review" else None, None, 0.0)
    needs, _ = M._needs_for(None, cause, M.load_needs())
    return c.model_copy(update={"cause_type": cause, "need_types": list(needs or [])})


def find_candidate(candidate_id: str, as_of: date | None = None) -> Candidate | None:
    t = TEMPLATE_BY_ID.get(candidate_id)
    if t is None:
        return None
    day = as_of if as_of and t.visible_from <= as_of <= t.visible_to else t.visible_from
    return next(c for c in candidates_for(t.city, day) if c.candidate_id == candidate_id)


def group_of(candidate_id: str) -> str | None:
    t = TEMPLATE_BY_ID.get(candidate_id)
    return t.group if t else None


# ---------------------------------------------------------------- 현황·지도


def overview(city: str, as_of: date, mode: str, snapshot_id: str) -> OverviewResponse:
    now = candidates_for(city, as_of)
    prev_day = as_of - timedelta(days=7)
    old = {c.candidate_id: c for c in candidates_for(city, prev_day)}
    before = set(old)
    ids = {c.candidate_id for c in now}
    counts = {}
    for kind in ("scheduled_access_review", "unexpected_activity_deviation"):
        n = sum(1 for c in now if c.candidate_type == kind and c.counter_result != "기각")
        counts[kind] = CandidateCountRange(count=n, low=max(0, n - 1), high=n + 2)
    released = []
    for cid in sorted(before - ids):  # 해제 사유 (가짜: 후보 틀의 보이는 기간이 끝남)
        c, t = old[cid], TEMPLATE_BY_ID[cid]
        why = (f"트리거 기간이 끝남 ({c.trigger}, ~{t.visible_to:%m/%d})" if c.trigger
               else f"이탈 경보 기간이 끝남 (~{t.visible_to:%m/%d})")
        released.append(ReleasedCandidate(
            candidate_id=cid, candidate_type=c.candidate_type, city=c.city, adm_cd=c.adm_cd,
            check_first_adm=c.check_first_adm, analysis_cohort=c.analysis_cohort,
            service_eligibility_group=c.service_eligibility_group, candidate_level=c.candidate_level,
            trigger=c.trigger, last_seen_as_of=prev_day, release_reason=why))
    return OverviewResponse(
        as_of=as_of, city=city, snapshot_id=snapshot_id, analysis_mode=mode, triggers=triggers_for(city, as_of),
        candidate_counts=counts, new_candidate_ids=sorted(ids - before), released_candidate_ids=sorted(before - ids),
        time_label=_time_label(mode), released_candidates=released, compared_as_of=prev_day,
    )


def _time_label(mode: str) -> str | None:
    from bigcontest.agent.schemas import ASSUMED_AVAILABILITY_LABEL

    return ASSUMED_AVAILABILITY_LABEL if mode == "simulated_replay" else None


def map_view(city: str, as_of: date, snapshot_id: str) -> MapResponse:
    cands = candidates_for(city, as_of)
    scheduled = {c.adm_cd for c in cands if c.candidate_type == "scheduled_access_review"}
    shared = {cd for c in cands if c.candidate_type == "unexpected_activity_deviation" and c.counter_result != "기각"
              for cd in c.check_first_adm}
    ranks = structure_ranks(city)
    out = []
    for a in sorted(city_areas(city), key=lambda a: a.adm_cd):
        v = structure_value(a)
        if a.adm_cd in scheduled:
            sig = "예정된 점검"
        elif a.adm_cd in shared:
            sig = "이탈 경보(공유 맥락)"
        else:
            sig = "없음"
        out.append(MapArea(
            adm_cd=a.adm_cd, adm_nm=a.adm_nm, structure_value=v, structure_rank=ranks.get(a.adm_cd),
            behavior_signal=sig, is_shared_context=sig == "이탈 경보(공유 맥락)",
            nearest_hub_km=nearest_hub_km(a),
            value_state=ValueState.QUALITY_SUSPECT if v is None else ValueState.OBSERVED,
        ))
    res = [MapResource(resource_id=r.resource_id, name=r.name, resource_type=r.resource_type, lat=r.lat, lon=r.lon,
                       phone_based=r.phone_based)
           for r in RESOURCES if r.city in (city, None)]
    return MapResponse(as_of=as_of, city=city, snapshot_id=snapshot_id, structure_metric="주민등록 1인세대 비율",
                       areas=out, resources=res)


# ---------------------------------------------------------------- 근거 카드


@dataclass
class _Ev:
    items: list[ToolResult] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


def _common(as_of: date, mode: str, snapshot_id: str, observed_to: date) -> dict:
    return dict(analysis_mode=mode, as_of=as_of, snapshot_id=snapshot_id, rule_version="rules-v0.1",
                simulated_available_at=min(as_of, observed_to + timedelta(days=3)),
                availability_basis="assumed", status="OK", value_state="observed")


def candidate_evidence(c: Candidate, as_of: date, mode: str, snapshot_id: str) -> _Ev:
    t = TEMPLATE_BY_ID[c.candidate_id]
    ids = iter(c.evidence_ids)
    ev = _Ev()
    period_to = min(as_of, t.visible_to) - timedelta(days=4)
    period_from = period_to - timedelta(days=6)
    if t.kind == "scheduled_access_review":
        a = area_by_name(t.city, t.dong)  # type: ignore[arg-type]
        v = structure_value(a)
        month_end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
        eid = next(ids)
        rank = structure_ranks(t.city).get(a.adm_cd)
        ev.items.append(ToolResult(
            evidence_id=eid, metric="1인세대 비율", value=v, unit="비율", denominator="전체 세대수 (주민등록)", n_obs=1,
            measured_grain="행정동 × 월", applied_scope=f"{t.city} {a.adm_nm}", observed_from=month_end,
            observed_to=month_end, data_version="resident@sha256:fake71d0/clean-v1",
            interpretation_status="직접 확인" if v is not None else "미확인",
            valid_claims=[f"{a.adm_nm} 주민등록 1인세대 비율" + (f" ({t.city} 내 {rank}위)" if rank else "")],
            forbidden_claims=["1인가구 비율 (개념이 다름)", "고립 위험이 높은 동"],
            **{**_common(as_of, mode, snapshot_id, month_end),
               "value_state": "observed" if v is not None else "quality_suspect"},
        ))
        if v is not None:
            ev.facts.append(Fact(metric="1인세대 비율", region=f"{t.city} {a.adm_nm}", period=f"{month_end:%Y-%m}",
                                 value=v, unit="비율", evidence_id=eid))
        eid = next(ids)
        tw = next(x for x in TRIGGERS if x.key == t.trigger_key)
        ev.items.append(ToolResult(
            evidence_id=eid, metric="달력·계절 트리거", value=f"{tw.rule} {tw.name}", unit=None,
            denominator=None, n_obs=None, measured_grain="도시 × 일", applied_scope=t.city,
            observed_from=tw.active_from, observed_to=min(as_of, tw.active_to),
            interpretation_status="직접 확인", valid_claims=[f"{tw.name} 기간"],
            forbidden_claims=["활동 위축이 확인된 후보"], data_version="calendar@fake-v1",
            **_common(as_of, mode, snapshot_id, tw.active_from),
        ))
        if t.city == "춘천":
            eid = next(ids)
            km = nearest_hub_km(a)
            ev.items.append(ToolResult(
                evidence_id=eid, metric="관측 거점까지 직선거리", value=km, unit="km",
                denominator="행정동 중심점 → 가장 가까운 관측 거점 (노인복지관·사회복지관)", n_obs=1,
                measured_grain="행정동", applied_scope=f"춘천 {a.adm_nm}", observed_from=None, observed_to=None,
                interpretation_status="계산 재현", data_version="ledger@sha256:fake3f9a1c0b",
                valid_claims=[f"{a.adm_nm} 중심점에서 관측 거점까지 직선거리 {km}km"],
                forbidden_claims=["실제 이동 시간", "서비스 공급 부족"],
                warnings=["직선거리이며 실제 이동 시간이나 서비스 공급 부족이 아님"],
                **_common(as_of, mode, snapshot_id, as_of),
            ))
            ev.facts.append(Fact(metric="관측 거점까지 직선거리", region=f"춘천 {a.adm_nm}", period=f"{as_of}",
                                 value=km, unit="km", evidence_id=eid))
        if t.ref_note:
            eid = next(ids)
            label, val = t.ref_note
            ev.items.append(ToolResult(
                evidence_id=eid, metric=label, value=val, unit="배", denominator="같은 연령 여성의 '소통 적음' 비율",
                n_obs=None, measured_grain="행정동 × 성 × 5세 연령 × 월 (강남 전체로 합침)",
                applied_scope="강남 전체 · 탐색적", is_shared_context=True, observed_from=date(2025, 7, 1),
                observed_to=date(2025, 8, 31), interpretation_status="가설", data_version="seoul_living@fake",
                valid_claims=["강남 전체에서 중장년 남성 1인가구의 '소통 적음'이 여성보다 많음 (탐색적 참고)"],
                forbidden_claims=["논현1동 중장년 남성이 고립됨", "등급 입력으로 사용"],
                warnings=["검증 참고 자료이며 등급 입력이 아님"],
                **_common(as_of, mode, snapshot_id, date(2025, 8, 31)),
            ))
    else:
        region = f"{t.city}{'구' if t.city == '강남' else '시'} 가맹점 · {'서울' if t.city == '강남' else '강원'} 거주"
        merchant = "강남구" if t.city == "강남" else "춘천시"
        sido = "서울" if t.city == "강남" else "강원"
        card = dict(merchant_region=merchant, customer_residence=sido, analysis_cohort=t.cohort,
                    service_eligibility_group=SERVICE_GROUP[t.group], measured_grain="도시×성×연령×일",
                    is_shared_context=bool(t.check_first), observed_from=period_from, observed_to=period_to,
                    data_version="card2@sha256:fake7c1e/clean-v1", trained_until=period_from - timedelta(days=1),
                    rule_derived_from="2025-07~08")
        eid = next(ids)
        ev.items.append(ToolResult(
            evidence_id=eid, metric="생필품 결제 평소 대비", value=t.payment_ratio, unit="평소 대비 비율",
            denominator="같은 코호트 최근 같은 요일 4주 평균 (카드 데이터2)", n_obs=4, direction="감소",
            applied_scope=f"{t.city} (동 목록은 공유 맥락)" if t.check_first else t.city,
            interpretation_status="계산 재현",
            valid_claims=[f"{sido} 거주 {t.cohort} 고객의 {merchant} 가맹점 생필품 결제가 비교 기준보다 낮음"],
            forbidden_claims=[f"{t.city} 거주 {t.cohort}의 결제 감소", "이 동의 결제가 줄었다 (도시 신호를 동에 공유한 근거)",
                              "고립 발생"],
            missing_inputs=list(t.missing), **card, **_common(as_of, mode, snapshot_id, period_to),
        ))
        ev.facts.append(Fact(metric="생필품 결제 평소 대비", region=region, period=f"{period_from}~{period_to}",
                             value=t.payment_ratio, unit="평소 대비 비율", direction="감소", evidence_id=eid))
        eid = next(ids)
        if t.holiday_ratio is not None:
            ev.items.append(ToolResult(
                evidence_id=eid, metric="공휴일 기대값 대비 결제", value=t.holiday_ratio, unit="기대값 대비 비율",
                denominator="같은 코호트의 지난 연휴 패턴으로 만든 기대값", n_obs=1,
                direction="변화 없음" if t.holiday_ratio >= 0.95 else "감소", applied_scope=t.city,
                interpretation_status="계산 재현",
                valid_claims=["공휴일 기대값과 비교한 값"], forbidden_claims=["연휴가 원인이라는 확정"],
                **card, **_common(as_of, mode, snapshot_id, period_to),
            ))
            ev.facts.append(Fact(metric="공휴일 기대값 대비 결제", region=region, period=f"{period_from}~{period_to}",
                                 value=t.holiday_ratio, unit="기대값 대비 비율",
                                 direction="변화 없음" if t.holiday_ratio >= 0.95 else "감소", evidence_id=eid))
        else:
            ev.items.append(ToolResult(
                evidence_id=eid, metric="강수 반증" if "weather_asos" in t.missing else "학사일정 반증",
                value=None, unit=None, applied_scope=t.city, analysis_mode=mode, as_of=as_of,
                snapshot_id=snapshot_id, status="ERROR" if "weather_asos" in t.missing else "OK",
                error_code="SOURCE_MISSING" if "weather_asos" in t.missing else None,
                value_state="missing", interpretation_status="미확인" if "weather_asos" in t.missing else "가설",
                missing_inputs=list(t.missing),
                warnings=["강수 자료(ASOS)가 이 기간에 없음"] if "weather_asos" in t.missing
                else ["대학 일정과의 연결은 가설"],
            ))
        eid = next(ids)
        ev.items.append(ToolResult(
            evidence_id=eid, metric="도시 전체 결제 평소 대비", value=t.city_ratio, unit="평소 대비 비율",
            denominator="도시 전 연령 최근 같은 요일 4주 평균", n_obs=4,
            direction="변화 없음" if (t.city_ratio or 1) >= 0.95 else "감소", measured_grain="도시×일",
            applied_scope=t.city, merchant_region=merchant, customer_residence=sido, observed_from=period_from,
            observed_to=period_to, interpretation_status="계산 재현", valid_claims=["도시 공통 요인 비교값"],
            forbidden_claims=["도시 공통 하락이 공휴일 때문이라는 확정"],
            data_version="card2@sha256:fake7c1e/clean-v1", **_common(as_of, mode, snapshot_id, period_to),
        ))
        ev.facts.append(Fact(metric="도시 전체 결제 평소 대비", region=region, period=f"{period_from}~{period_to}",
                             value=t.city_ratio, unit="평소 대비 비율",
                             direction="변화 없음" if (t.city_ratio or 1) >= 0.95 else "감소", evidence_id=eid))
    return ev


def _limitations(c: Candidate) -> list[str]:
    out = ["개인 판단 아님", "동 단위 카드 소비는 알 수 없음"]
    if c.counter_reason and c.counter_result == "보류":
        out.append(c.counter_reason)
    for m in c.missing_inputs:
        out.append(f"빠진 근거: {m}")
    return out


_KIND_KO = {"scheduled_access_review": "예정된 점검", "unexpected_activity_deviation": "예상 밖 이탈"}


def candidate_title(c: Candidate) -> str:
    t = TEMPLATE_BY_ID[c.candidate_id]
    where = f"{c.city} {t.dong}" if t.dong else f"{c.city} 가맹점 · {c.analysis_cohort}"
    trig = f" · {c.trigger}" if c.trigger else ""
    return f"{c.candidate_level} · {_KIND_KO[c.candidate_type]} · {where} · {t.group}{trig}"


def evidence_card_for(c: Candidate, run_id: str, as_of: date, mode: str, snapshot_id: str, revision: int = 1) -> EvidenceCard:
    ev = candidate_evidence(c, as_of, mode, snapshot_id)
    return EvidenceCard(
        card_id=f"CARD-{run_id}-{revision}", run_id=run_id, candidate_id=c.candidate_id, title=candidate_title(c),
        snapshot_id=snapshot_id, analysis_mode=mode, as_of=as_of, evidence_revision=revision, facts=ev.facts,
        evidence=ev.items, limitations=_limitations(c),
    )


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def build_report(card: EvidenceCard, revision: int, judgements: dict[str, tuple[str, str | None]] | None = None,
                 names: dict[str, str] | None = None) -> Report:
    def unit(f: Fact) -> str:
        return "" if not f.unit or f.unit == "비율" else f" {f.unit}"

    sentences = [f"{f.period} {f.region}의 {f.metric}은(는) {_fmt(f.value)}{unit(f)}입니다 [{f.evidence_id}]."
                 for f in card.facts]
    narrative = ("이 보고서는 집단·지역 단위 근거로 먼저 확인할 곳을 제안합니다. 개인을 판정하지 않으며 "
                 "최종 판단은 담당자가 합니다.")
    if judgements:
        parts = [f"{(names or {}).get(rid, rid)} {j}" + (f"({m})" if m else "") for rid, (j, m) in judgements.items()]
        narrative += " 담당자 자원 판정: " + ", ".join(parts) + "."
    cands = _report_candidates(card)
    actions = [a for a in (_action_for(c, card.as_of) for c in cands) if a]
    sentences += [a.action_plan.text for a in actions]
    return Report(report_revision=revision, evidence_card=card, fact_sentences=sentences, narrative=narrative,
                  actions=actions, level_explanations=[R.explain_level(c) for c in cands if c.rule_checks],
                  decision_trace=fake_trace(_path_of(card), cands))


def _report_candidates(card: EvidenceCard) -> list[Candidate]:
    """보고서가 다루는 후보: 후보 카드면 그 후보, 정기 감시 카드면 근거 ID 로 찾은 후보 (가짜 근거 ID = EV-<후보 줄기>-k)."""
    if card.candidate_id:
        c = find_candidate(card.candidate_id, card.as_of)
        return [c] if c else []
    ids = list(dict.fromkeys("C-" + e.evidence_id.removeprefix("EV-").rsplit("-", 1)[0] for e in card.evidence))
    return [c for c in (find_candidate(i, card.as_of) for i in ids if i in TEMPLATE_BY_ID) if c]


def _action_for(c: Candidate, as_of: date):
    """Level 2·3 후보의 담당자용 출력 (실제 에이전트와 같은 함수: tools.report.build_candidate_action)."""
    t = TEMPLATE_BY_ID[c.candidate_id]
    adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
    res = [{**r.model_dump(mode="json"), "candidate_id": c.candidate_id}
           for r in resources_for(c.city, adm, None, t.group)]
    km = nearest_hub_km(areas()[adm]) if adm in areas() else None
    return R.build_candidate_action(c, res, ACCESS_FAR_KM, km, as_of)


def _path_of(card: EvidenceCard) -> list[str]:
    if card.candidate_id:
        return INVESTIGATE_PATH
    return MONITOR_PATH if card.title.startswith("정기 감시") else LOOKUP_PATH


_NEXT = {"intake", "classify", "check_availability", "guard_signal", "guard_report"}  # 분기 노드


def fake_trace(path: list[str], cands: list[Candidate] | None = None) -> list[TraceStep]:
    """가짜 판단 기록 (화면용). 보고서 사본이라 시각·소요 시간 없음. 실제 값은 graph._node 가 남긴다."""
    cands = cands or []
    counts: dict[str, int] = {}
    for c in cands:
        counts[c.candidate_level or "-"] = counts.get(c.candidate_level or "-", 0) + 1
    levels = " · ".join(f"{k} {n}" for k, n in sorted(counts.items()))
    selected = ", ".join(c.candidate_id for c in cands if c.selected_for_review) or "없음"
    choice = {
        "intake": ("요청 정규화 됨", "rule", None),
        "classify": ("investigate" if "collect_signals" in path else "lookup", "llm", "가짜: 규칙과 같은 경로"),
        "check_availability": ("OK", "rule", None),
        "collect_signals": ("필수 도구 9개 + 추가 1개", "llm", "가짜: 첫 번째 추가 도구"),
        "lookup": ("조회 도구 1개", "rule", None),
        "classify_candidate": (f"후보 {len(cands)}건", "rule", None),
        "counter_evidence": ("반증 " + (", ".join(c.counter_result for c in cands) or "-"), "rule", None),
        "guard_signal": ("PASS", "guard", None),
        "assign_level": (levels or "-", "rule", None),
        "assign_queue": (f"검토 배정 {selected}", "rule", None),
        "match_resources": ("자원 매칭", "rule", None),
        "build_card": ("근거 카드", "template", None),
        "write_report": ("보고서 1판", "llm", "사실·대응안은 템플릿, 설명 문장만 LLM"),
        "guard_report": ("PASS", "guard", None),
    }
    out = []
    for i, node in enumerate(path):
        if node not in choice:
            continue
        ch, src, why = choice[node]
        nxt = path[i + 1] if node in _NEXT and i + 1 < len(path) else None
        rule_ids = sorted({c.rule_id for c in cands if c.rule_id}) if node == "assign_level" else []
        out.append(TraceStep(node=node, choice=ch, next=nxt, source=src, reason=why, rule_ids=rule_ids))
    return out


def standard_guard(revise_first: bool = False) -> list[GuardResult]:
    g = [GuardResult(guard="guard_signal", check="신호", verdict="PASS")]
    if revise_first:
        g.append(GuardResult(guard="guard_report", check="사실", verdict="REVISE", rule_ids=["G-03"],
                             reasons=["'춘천 거주'는 카드로 말할 수 없음 ('강원 거주'로 고침)"]))
        g += [GuardResult(guard="guard_report", check="사실", verdict="PASS", attempt=2),
              GuardResult(guard="guard_report", check="표현", verdict="PASS", attempt=2)]
    else:
        g += [GuardResult(guard="guard_report", check="사실", verdict="PASS"),
              GuardResult(guard="guard_report", check="표현", verdict="PASS")]
    return g


def contract_for(c: Candidate) -> ContractResult:
    if c.candidate_type == "unexpected_activity_deviation":
        warnings = [f"요청 집단({c.service_eligibility_group})과 분석 코호트(card2 {c.analysis_cohort})가 정확히 같지 않음"]
        return ContractResult(status="OK", source="card2", analysis_cohort=c.analysis_cohort,
                              service_eligibility_group=c.service_eligibility_group,
                              measured_grain="가맹점 시군구 × 일 × 업종 × 고객 거주 시도 × 성 × 연령", warnings=warnings)
    return ContractResult(status="OK", source="resident", analysis_cohort=c.analysis_cohort,
                          service_eligibility_group=c.service_eligibility_group, measured_grain="행정동 × 월 × 1세 연령")


INVESTIGATE_PATH = ["intake", "classify", "check_availability", "collect_signals", "classify_candidate",
                    "counter_evidence", "guard_signal", "assign_level", "assign_queue", "match_resources",
                    "build_card", "write_report", "guard_report", "await_approval"]
MONITOR_PATH = ["intake", "check_availability", "collect_signals", "classify_candidate", "counter_evidence",
                "guard_signal", "assign_level", "assign_queue", "match_resources", "build_card", "write_report",
                "guard_report", "publish_candidates"]
LOOKUP_PATH = ["intake", "classify", "check_availability", "lookup", "guard_signal", "build_card", "write_report",
               "guard_report"]
ABSTAIN_PATH = ["intake", "classify", "check_availability", "abstain_report"]
