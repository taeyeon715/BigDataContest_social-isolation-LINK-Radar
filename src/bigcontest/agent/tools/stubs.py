"""가짜 도구. 진짜 계산 없이 시나리오별 고정 결과를 돌려준다.

- 모든 결과는 실제 도구와 같은 반환 봉투(`ToolResult`)다. 값은 고정값이고 `data_version="stub"`,
  경고에 `STUB_NOTE` 를 붙여 실제 분석 결과와 섞이지 않게 한다.
- 시나리오는 실행 상태의 `scenario` 로 고른다. 테스트가 그래프의 모든 분기를 밟도록 만든 것이다.
- `check_request_contract` 만 진짜 계약 코드(contracts.grain)를 부른다.
- 날짜는 기준일(as_of)에서 거꾸로 잡아 어떤 기준일에도 시간 계약을 지킨다.

경보 시점과 반증 검토 시점은 따로 둔다 (EDA-6 검토 반영).
- 경보 근거에는 최초 경보일(alarm_date), 반증 근거에는 검토 결정일(review_decided_at)을 적는다.
- 검토 결정일이 기준일 뒤면 최종 기각·통과 사유와 회복 정보를 숨기고 "검토 중 / 회복 미관측"으로 돌려준다.
  (봉투 칸은 늘리지 않고 value·warnings·interpretation_status 로 나타낸다.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from bigcontest.agent.schemas import NormalizedRequest, ToolResult
from bigcontest.contracts.grain import check_request_contract

STUB_NOTE = "가짜 도구(stub) 고정값: 실제 분석 결과 아님"
REVIEW_PENDING_NOTE = "검토 중 / 회복 미관측"
CITY_COMMON_NOTE = "도시 공통 변화로 분류 / 확인 필요 (공휴일이 원인이라는 증거 아님)"

SCENARIOS: dict[str, str] = {
    "normal": "추석 D-7 트리거 + 예정된 점검 3곳 + 예상 밖 이탈 1건(반증 통과)",
    "scheduled_only": "트리거만 있고 이탈 경보 없음",
    "empty": "트리거·경보 모두 없음 (EMPTY)",
    "error": "반증 도구 오류 → 이탈 후보만 보류",
    "holiday_rejection": "공휴일 기대값으로 설명되는 이탈 → 기각 (Level 1)",
    "rain_explained": "강수로 일부 설명 → 기각하지 않고 설명 부분·남는 이탈을 함께 기록",
    "city_common": "도시 공통 하락 → 기각 사유로 쓰지 않고 '확인 필요'로 남김",
    "review_pending": "반증 검토 결정일이 기준일 뒤 → 검토 중 / 회복 미관측",
    "missing_required": "구조·접근성 결측(부분 판정) + 카드 시계열 오류(핵심 필수 근거 결측 → 보류)",
    "out_of_area_resource": "관할 밖 자원이 섞여 옴 → 자원 매칭에서 제외",
    "resource_no_source": "출처·대상·확인일이 없는 자원 → G-07 추천 제외",
    "confounder_timeout_once": "반증 도구가 처음 한 번 시간 초과 → G-06 REVISE 후 통과",
    "confounder_timeout_always": "반증 도구가 계속 시간 초과 → REVISE 2회 뒤 ABSTAIN",
    "deprecated_metric": "폐기한 지표가 근거로 들어옴 → G-11",
    "small_cell": "관측 수가 작은 칸 → G-08 출력 차단",
    "lookup_error": "조회 도구 오류",
    "lookup_empty": "조회 결과 없음",
}

DEPRECATED_METRIC = "통신 야간 체류 비율 (폐기 지표)"  # G-11 시험용. configs/guard_rules.yaml 과 같은 이름

# 행정동 고정값: (adm_cd, 이름, 1인세대 비율, 관측 거점까지 직선거리 km). 코드·이름은 ref/adm_cd_names.csv 와 같고 값만 가짜
# 가짜 분모 크기 (G-08 작은 칸 기준과 비교): 동 하나의 전체 세대 수, 생활권 체류인구 합
STUB_HOUSEHOLDS = 5200
STUB_STAY_POP = 48000
ADMS: dict[str, list[tuple[str, str, float, float]]] = {
    "춘천": [
        ("32010350", "남면", 0.47, 12.4),
        ("32010630", "효자1동", 0.52, 0.8),
        ("32010670", "퇴계동", 0.22, 1.5),
    ],
    "강남": [
        ("11230640", "역삼1동", 0.68, 0.6),
        ("11230520", "논현1동", 0.61, 0.9),
        ("11230630", "대치4동", 0.55, 1.1),
    ],
}

# 달력 트리거: (규칙, 이름, 트리거 시작일, 끝일). 2025 추석 연휴는 10-03~10-09
TRIGGERS = [
    ("R1", "추석 연휴 D-7", date(2025, 9, 26), date(2025, 10, 2)),
]

# 자원 고정값 (match_resources 가 ResourceRecommendation 으로 바꿈). region 은 관할 확인용
RESOURCES: dict[str, list[dict]] = {
    "춘천": [
        {"resource_id": "STUB-R01", "name": "남면 행정복지센터 (가짜)", "resource_type": "행정복지센터",
         "region": "춘천시", "city": "춘천", "adm_cd": "32010350", "target_group": "65세 이상",
         "eligibility": "확인 필요", "operating_status": "운영", "distance_km": 0.5, "phone_based": False,
         "source_url": "stub://resources/STUB-R01", "verified_date": "2025-09-01"},
        {"resource_id": "STUB-R02", "name": "전화 안부 상담 (가짜)", "resource_type": "관계형성·여가",
         "region": "춘천시", "city": "춘천", "adm_cd": None, "target_group": "65세 이상",
         "eligibility": "적합", "operating_status": "운영", "distance_km": None, "phone_based": True,
         "source_url": "stub://resources/STUB-R02", "verified_date": "2025-09-01"},
    ],
    "강남": [
        {"resource_id": "STUB-R11", "name": "역삼1동 주민센터 (가짜)", "resource_type": "행정복지센터",
         "region": "강남구", "city": "강남", "adm_cd": "11230640", "target_group": "65세 이상",
         "eligibility": "확인 필요", "operating_status": "운영", "distance_km": 0.3, "phone_based": False,
         "source_url": "stub://resources/STUB-R11", "verified_date": "2025-09-01"},
    ],
}
OUT_OF_AREA_RESOURCE = {
    "resource_id": "STUB-R90", "name": "성동구 거점 (가짜, 관할 밖)", "resource_type": "1인가구지원(청년·중장년)",
    "region": "성동구", "city": None, "adm_cd": None, "target_group": "40~64세",
    "eligibility": "확인 필요", "operating_status": "확인 필요", "distance_km": 9.0, "phone_based": False,
    "source_url": "stub://resources/STUB-R90", "verified_date": "2025-09-01",
}
NO_SOURCE_RESOURCE = {
    "resource_id": "STUB-R91", "name": "출처 없는 자원 (가짜)", "resource_type": "사회복지관",
    "region": "춘천시", "city": "춘천", "adm_cd": None, "target_group": None,
    "eligibility": "확인 필요", "operating_status": "확인 필요", "distance_km": None, "phone_based": False,
    "source_url": "", "verified_date": None,
}


@dataclass
class ToolContext:
    scenario: str
    as_of: date
    analysis_mode: str
    snapshot_id: str
    attempt: int = 1  # 근거 수집 회차 (evidence_revision)
    request: NormalizedRequest | None = None
    backend: str = "stub"  # stub / real (tools.call_tool 이 고름)
    allow_scenario: bool = False  # R4·R6 상황 시연(미래 실측 사용) 허용 (기본 끔)
    tool_log: list | None = None  # call_tool 이 도구마다 {tool, ms, status, n} 을 붙임 (None 이면 안 남김)

    @property
    def city(self) -> str:
        return self.request.city if self.request else "춘천"


class StubToolError(KeyError):
    pass


# ---------------------------------------------------------------- 봉투 만들기


def _ev(ctx: ToolContext, code: str, **kw) -> ToolResult:
    """고정값 근거 봉투. 근거 ID 는 기준일 + 도구 코드로 고정 (같은 입력이면 같은 ID)."""
    warnings = [STUB_NOTE] + kw.pop("warnings", [])
    base = dict(
        evidence_id=f"EV-{ctx.as_of:%Y%m%d}-{code}",
        analysis_mode=ctx.analysis_mode,
        as_of=ctx.as_of,
        snapshot_id=ctx.snapshot_id,
        data_version="stub",
        rule_version="stub-v0",
        availability_basis="assumed",
        interpretation_status="계산 재현",
        status="OK",
        warnings=warnings,
    )
    base.update(kw)
    if base["status"] != "OK" and "value_state" not in kw:
        base["value_state"] = "missing"
    return ToolResult(**base)


def _error(ctx: ToolContext, code: str, metric: str, error_code: str, reason: str) -> ToolResult:
    return _ev(ctx, code, metric=metric, status="ERROR", error_code=error_code, warnings=[reason])


def _empty(ctx: ToolContext, code: str, metric: str, reason: str) -> ToolResult:
    return _ev(ctx, code, metric=metric, status="EMPTY", warnings=[reason])


def _last_month_end(as_of: date, lag_days: int) -> date:
    """기준일에 공개돼 있던 가장 최근 월말 (월말 + 공급 지연 <= 기준일)."""
    d = as_of.replace(day=1) - timedelta(days=1)
    while d + timedelta(days=lag_days) > as_of:
        d = d.replace(day=1) - timedelta(days=1)
    return d


def _card_window(ctx: ToolContext) -> tuple[date, date]:
    """카드 근거 기간: 기준일 7일 전까지 1주 (공급 지연 3일을 넉넉히 넘김)."""
    end = ctx.as_of - timedelta(days=7)
    return end - timedelta(days=6), end


# ---------------------------------------------------------------- Data 도구


def get_structure(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario == "missing_required":
        return [_empty(ctx, "STR", "1인세대 비율", "구조 지표 결측 (가짜 시나리오)")]
    if ctx.scenario == "lookup_error":
        return [_error(ctx, "STR", "1인세대 비율", "TOOL_ERROR", "구조 지표 도구 오류 (가짜 시나리오)")]
    if ctx.scenario == "lookup_empty":
        return [_empty(ctx, "STR", "1인세대 비율", "조회 결과 없음 (가짜 시나리오)")]
    month_end = _last_month_end(ctx.as_of, 10)
    out = []
    if ctx.scenario == "small_cell":
        # 인원이 아주 작은 칸 (G-08 시험용)
        out.append(
            _ev(ctx, "STR-SMALL", metric="80세 이상 1인세대 수", value=2, unit="명", n_obs=1,
                measured_grain="행정동 × 월", applied_scope=f"{ctx.city} {ADMS[ctx.city][0][1]} ({ADMS[ctx.city][0][0]})",
                observed_from=month_end, observed_to=month_end,
                simulated_available_at=month_end + timedelta(days=10), value_state="observed")
        )
    for adm_cd, name, ratio, _km in ADMS[ctx.city]:
        out.append(
            _ev(
                ctx,
                f"STR-{adm_cd}",
                metric="1인세대 비율",
                value=ratio,
                unit="비율",
                denominator="전체 세대수 (주민등록)",
                n_obs=STUB_HOUSEHOLDS,  # 분모 크기 (G-08 작은 칸 기준과 비교)
                measured_grain="행정동 × 월",
                applied_scope=f"{ctx.city} {name} ({adm_cd})",
                observed_from=month_end,
                observed_to=month_end,
                simulated_available_at=month_end + timedelta(days=10),
                interpretation_status="직접 확인",
                valid_claims=["1인세대 비율"],
                forbidden_claims=["1인가구 (개념이 다름)"],
                value_state="observed",
            )
        )
    return out


def get_mobility_layer(ctx: ToolContext) -> list[ToolResult]:
    month_end = _last_month_end(ctx.as_of, 15)
    if ctx.scenario == "deprecated_metric":
        metric = DEPRECATED_METRIC
    else:
        metric = "체류인구 중 60+ 비중"
    return [
        _ev(
            ctx,
            "MOB",
            metric=metric,
            value=0.31,
            unit="비율",
            denominator="생활권 체류인구 전체 (SK 통신 성연령)",
            n_obs=STUB_STAY_POP,  # 분모 크기: 체류인구 합 (G-08)
            measured_grain="50m 칸 × 월 × 성 × 연령(60+ 묶음)",
            applied_scope=ctx.city,
            observed_from=month_end.replace(day=1),
            observed_to=month_end,
            simulated_available_at=month_end + timedelta(days=15),
            valid_claims=["이 생활권에 머무는 사람의 연령 구성"],
            forbidden_claims=["주민 수", "70대 이상·80대 이상 (60+ 묶음)"],
            value_state="observed",
        )
    ]


def get_card_cohort_series(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario == "missing_required":
        return [_error(ctx, "SER", "생필품 결제 일별 시계열", "TOOL_ERROR", "카드 시계열 도구 오류 (가짜 시나리오)")]
    start, end = _card_window(ctx)
    return [
        _ev(
            ctx,
            "SER",
            metric="생필품 결제 일별 시계열",
            value=[412.0, 398.0, 405.0, 371.0, 352.0, 330.0, 321.0],
            unit="건/일",
            denominator="카드 데이터2 생필품 업종 결제 건수",
            n_obs=7,
            measured_grain="도시×성×연령×일",
            applied_scope=f"{ctx.city} (동 목록은 공유 맥락)",
            merchant_region="춘천시" if ctx.city == "춘천" else "강남구",
            customer_residence="강원" if ctx.city == "춘천" else "서울",
            is_shared_context=True,
            analysis_cohort="80대+",
            service_eligibility_group="65세 이상",
            observed_from=start,
            observed_to=end,
            simulated_available_at=end + timedelta(days=3),
            value_state="observed",
        )
    ]


def get_card_time_band(ctx: ToolContext) -> list[ToolResult]:
    """카드 데이터1 시간대 비중 (가짜 고정값). 거주지 없음."""
    start, end = _card_window(ctx)
    return [
        _ev(
            ctx,
            "TOD",
            metric="카드 결제 중 저녁·밤 18~23시 비중 (카드 데이터1 보조)",
            value=0.21,
            unit="같은 연령 결제 중 비율",
            denominator="카드 데이터1 결제건수 (모든 시간대)",
            n_obs=7,
            measured_grain="가맹점 시군구 × 일 × 시간대(4구간) × 성 × 연령 (카드 데이터1)",
            applied_scope=f"{ctx.city} 가맹점 (거주지 모름)",
            merchant_region="춘천시" if ctx.city == "춘천" else "강남구",
            is_shared_context=True,
            analysis_cohort="60대",
            observed_from=start,
            observed_to=end,
            simulated_available_at=end + timedelta(days=3),
            value_state="observed",
            warnings=["카드 데이터1(보조): 고객 거주지 칸이 없어 결제한 사람이 이 도시 주민인지 알 수 없음"],
        )
    ]


def get_validation_reference(ctx: ToolContext) -> list[ToolResult]:
    if ctx.city != "강남":
        return [_empty(ctx, "VAL", "시민생활 지표", "시민생활 자료는 강남만 있음")]
    month_end = _last_month_end(ctx.as_of, 20)
    return [
        _ev(
            ctx,
            "VAL",
            metric="시민생활 '소통 적음' 비중 (탐색적 참고)",
            value=0.18,
            unit="비율",
            measured_grain="행정동 × 성 × 5세 연령 × 파일 기준월",
            applied_scope="강남 전체",
            observed_from=month_end.replace(day=1),
            observed_to=month_end,
            simulated_available_at=month_end + timedelta(days=20),
            interpretation_status="가설",
            warnings=["시민생활은 탐색적 검증 참고. 등급 입력 아님"],
            value_state="observed",
        )
    ]


def check_request_contract_tool(ctx: ToolContext) -> list[ToolResult]:
    """대상 집단 확인. 진짜 계약 코드를 부른다."""
    if ctx.request is None:
        return [_empty(ctx, "TGT", "대상 집단 계약", "정규화한 요청이 없음")]
    res = check_request_contract(ctx.request)
    if res.status != "OK":
        return [_error(ctx, "TGT", "대상 집단 계약", res.reason_codes[0], res.issues[0].message)]
    return [
        _ev(
            ctx,
            "TGT",
            metric="대상 집단 계약",
            value=res.analysis_cohort or "",
            measured_grain=res.measured_grain,
            applied_scope=ctx.city,
            analysis_cohort=res.analysis_cohort,
            service_eligibility_group=res.service_eligibility_group,
            interpretation_status="직접 확인",
            warnings=list(res.warnings),
        )
    ]


# ---------------------------------------------------------------- Signal 도구


def get_calendar_triggers(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario == "empty":
        return [_empty(ctx, "TRG", "달력 트리거", "기준일에 해당하는 트리거 없음")]
    out = []
    for rule, name, start, end in TRIGGERS:
        if start <= ctx.as_of <= end:
            out.append(
                _ev(
                    ctx,
                    f"TRG-{rule}",
                    metric="달력 트리거",
                    value=f"{rule} {name}",
                    measured_grain="날짜",
                    applied_scope=ctx.city,
                    observed_from=start,
                    observed_to=start,
                    simulated_available_at=start,
                    interpretation_status="직접 확인",
                )
            )
    return out or [_empty(ctx, "TRG", "달력 트리거", "기준일에 해당하는 트리거 없음")]


def calc_expected_activity(ctx: ToolContext) -> list[ToolResult]:
    start, end = _card_window(ctx)
    return [
        _ev(
            ctx,
            "EXP",
            metric="생필품 결제 기대값",
            value=405,
            unit="건/일",
            denominator="같은 코호트 최근 같은 요일 4주 평균 (카드 데이터2)",
            n_obs=4,
            measured_grain="도시×성×연령×일",
            applied_scope=f"{ctx.city} (동 목록은 공유 맥락)",
            merchant_region="춘천시" if ctx.city == "춘천" else "강남구",
            customer_residence="강원" if ctx.city == "춘천" else "서울",
            is_shared_context=True,
            analysis_cohort="80대+",
            service_eligibility_group="65세 이상",
            observed_from=start,
            observed_to=end,
            simulated_available_at=end + timedelta(days=3),
            trained_until=start - timedelta(days=1),
            value_state="observed",
        )
    ]


def calc_cohort_deviation(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario in ("empty", "scheduled_only"):
        return [_empty(ctx, "DEV", "생필품 결제 평소 대비", "이탈 경보 없음")]
    start, alarm_date = _card_window(ctx)
    warnings = [f"경보 최초일 {alarm_date}"]
    if ctx.scenario == "review_pending":
        # 회복일이 기준일 뒤라 그 시점에는 회복을 알 수 없음
        recovered_on = ctx.as_of + timedelta(days=5)
        warnings.append("회복 미관측" if recovered_on > ctx.as_of else f"회복 관측일 {recovered_on}")
    return [
        _ev(
            ctx,
            "DEV",
            metric="생필품 결제 평소 대비",
            value=0.78,
            unit="평소 대비 비율",
            denominator="같은 코호트 최근 같은 요일 4주 평균 (카드 데이터2)",
            n_obs=4,
            direction="감소",
            measured_grain="도시×성×연령×일",
            applied_scope=f"{ctx.city} (동 목록은 공유 맥락)",
            merchant_region="춘천시" if ctx.city == "춘천" else "강남구",
            customer_residence="강원" if ctx.city == "춘천" else "서울",
            is_shared_context=True,
            analysis_cohort="80대+",
            service_eligibility_group="65세 이상",
            observed_from=start,
            observed_to=alarm_date,
            simulated_available_at=alarm_date + timedelta(days=3),
            trained_until=start - timedelta(days=1),
            calibrated_until=start - timedelta(days=1),
            rule_derived_from="2025-07~08",
            valid_claims=[f"{'강원' if ctx.city == '춘천' else '서울'} 거주 80대+ 고객의 {ctx.city} 가맹점 생필품 결제가 "
                          "비교 기준보다 낮음"],
            forbidden_claims=[f"{ctx.city} 거주 80대의 결제 감소", "이 동의 결제가 줄었다", "고립 발생"],
            warnings=warnings,
            value_state="observed",
        )
    ]


# 반증 결과 고정값: 근거 이름 → (결과, 메모). 결과는 통과 / 기각 / 설명 일부 / 공통 변화
_CONFOUNDERS = {
    "holiday_expectation": ("CF1", "반증: 공휴일 기대값"),
    "city_common_factor": ("CF2", "반증: 도시 공통요인"),
    "weather_asos": ("CF3", "반증: 강수"),
    "academic_calendar": ("CF4", "반증: 학사일정"),
}


def _confounder_outcomes(scenario: str) -> dict[str, tuple[str, str]]:
    out = {
        "holiday_expectation": ("통과", "공휴일 기대값으로 설명되지 않음"),
        "city_common_factor": ("통과", "다른 코호트는 평소 수준"),
        "weather_asos": ("통과", "강수일 아님"),
        "academic_calendar": ("통과", "고령층 코호트라 학사일정 해당 없음"),
    }
    if scenario == "holiday_rejection":
        out["holiday_expectation"] = ("기각", "연휴 앞 주의 평소 감소 범위 안 (공휴일 기대값으로 설명됨)")
    if scenario == "rain_explained":
        out["weather_asos"] = ("설명 일부", "날씨로 설명되는 부분 0.06, 보정 후 남는 이탈 0.16")
    if scenario == "city_common":
        out["city_common_factor"] = ("공통 변화", CITY_COMMON_NOTE)
    return out


def check_confounders(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario in ("empty", "scheduled_only"):
        return []
    if ctx.scenario == "error":
        return [_error(ctx, code, metric, "TOOL_ERROR", "반증 도구 오류 (가짜 시나리오)") for code, metric in _CONFOUNDERS.values()]
    timeout = ctx.scenario == "confounder_timeout_always" or (ctx.scenario == "confounder_timeout_once" and ctx.attempt == 1)
    if timeout:
        return [_error(ctx, code, metric, "TOOL_TIMEOUT", "반증 도구 시간 초과 (가짜 시나리오)") for code, metric in _CONFOUNDERS.values()]

    start, alarm_date = _card_window(ctx)
    decided = ctx.as_of + timedelta(days=3) if ctx.scenario == "review_pending" else alarm_date + timedelta(days=2)
    out = []
    for key, (outcome, note) in _confounder_outcomes(ctx.scenario).items():
        code, metric = _CONFOUNDERS[key]
        if decided > ctx.as_of:
            # 미래 결론을 소급하지 않음: 결과·사유를 숨김
            out.append(_ev(ctx, code, metric=metric, value="검토 중", interpretation_status="미확인",
                           observed_from=start, observed_to=alarm_date, warnings=[REVIEW_PENDING_NOTE]))
            continue
        out.append(
            _ev(ctx, code, metric=metric, value=outcome, observed_from=start, observed_to=alarm_date,
                simulated_available_at=decided, warnings=[note, f"검토 결정일 {decided}"])
        )
    return out


def calc_holiday_gap(ctx: ToolContext) -> list[ToolResult]:
    return [_empty(ctx, "HOL", "연휴 깊이·회복", "기준일 전에 끝난 연휴 없음 (가짜)")]


def calc_winter_contraction(ctx: ToolContext) -> list[ToolResult]:
    return [_empty(ctx, "WIN", "춘천 읍·면 겨울 변화", "동절기 전 (가짜)")]


def calc_heat_response(ctx: ToolContext) -> list[ToolResult]:
    return [_empty(ctx, "HEAT", "폭염 반응 (탐색)", "폭염 예보 없음 (가짜)")]


# ---------------------------------------------------------------- Welfare 도구


def get_access_gap(ctx: ToolContext) -> list[ToolResult]:
    if ctx.scenario == "missing_required":
        return [_empty(ctx, "ACC", "관측 거점까지 직선거리", "접근성 근거 결측 (가짜 시나리오)")]
    out = []
    for adm_cd, name, _ratio, km in ADMS[ctx.city]:
        out.append(
            _ev(
                ctx,
                f"ACC-{adm_cd}",
                metric="관측 거점까지 직선거리",
                value=km,
                unit="km",
                measured_grain="행정동 중심점 → 관측 거점",
                applied_scope=f"{ctx.city} {name} ({adm_cd})",
                interpretation_status="계산 재현",
                valid_claims=["관측된 거점까지 직선거리가 큼"],
                forbidden_claims=["서비스 공백", "서비스 공급 부족"],
                value_state="observed",
            )
        )
    return out


def _dong_scope(city: str, adm_cd: str | None) -> str:
    """실제 도구와 같은 적용 범위 모양: "강남 역삼1동 (11230640)". 동이 없으면 도시만."""
    if not adm_cd:
        return city
    name = next((n for cd, n, *_ in ADMS.get(city, []) if cd == adm_cd), "")
    return f"{city} {name} ({adm_cd})" if name else f"{city} ({adm_cd})"


def find_resources(ctx: ToolContext, adm_cd: str | None, group: str | None) -> tuple[ToolResult, list[dict]]:
    """자원 후보. 봉투는 요약(건수)이고 자원 상세는 따로 돌려준다."""
    items = [dict(r) for r in RESOURCES[ctx.city]]
    if ctx.scenario == "out_of_area_resource":
        items.append(dict(OUT_OF_AREA_RESOURCE))
    if ctx.scenario == "resource_no_source":
        items.append(dict(NO_SOURCE_RESOURCE))
    env = _ev(
        ctx,
        f"RES-{adm_cd or ctx.city}",
        metric="복지 자원 후보",
        value=len(items),
        unit="건",
        applied_scope=_dong_scope(ctx.city, adm_cd),
        service_eligibility_group=group,
        interpretation_status="직접 확인",
        valid_claims=["출처·확인일이 있는 자원 후보"],
        forbidden_claims=["두 도시 직접 비교"],
        value_state="observed",
    )
    return env, items


# ---------------------------------------------------------------- 호출


_TOOLS = {
    "get_structure": get_structure,
    "get_mobility_layer": get_mobility_layer,
    "get_card_cohort_series": get_card_cohort_series,
    "get_card_time_band": get_card_time_band,
    "get_validation_reference": get_validation_reference,
    "check_request_contract": check_request_contract_tool,
    "get_calendar_triggers": get_calendar_triggers,
    "calc_expected_activity": calc_expected_activity,
    "calc_cohort_deviation": calc_cohort_deviation,
    "check_confounders": check_confounders,
    "calc_holiday_gap": calc_holiday_gap,
    "calc_winter_contraction": calc_winter_contraction,
    "calc_heat_response": calc_heat_response,
    "get_access_gap": get_access_gap,
}


def call_tool(name: str, ctx: ToolContext) -> list[ToolResult]:
    """도구 이름으로 가짜 도구를 부른다. 없는 이름이면 StubToolError."""
    if ctx.scenario not in SCENARIOS:
        raise StubToolError(f"알 수 없는 시나리오: {ctx.scenario}")
    fn = _TOOLS.get(name)
    if fn is None:
        raise StubToolError(f"가짜 도구가 없음: {name}")
    return fn(ctx)
