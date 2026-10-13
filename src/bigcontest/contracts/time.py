"""시간 계약: 그 기준일(as_of)에 실제로 쓸 수 있던 자료·모델만 쓴다 (설계 §2.4, G-13).

분석 모드
- retrospective: 사후 분석. 모든 자료를 쓰되 기준일 뒤 자료·규칙에는 "사후" 표시
- simulated_replay: 실제 공개일을 모르면 "관측일 + 가정한 공급 지연"으로 재생. 결과에 가정 표시가 자동으로 붙음
- strict_as_of: 실제 공개일이 확인된 자료만. 공개일 미상·기준일 뒤 자료와 모델은 쓰지 않음

공개일은 칸을 나눈다: actual_available_at(확인된 실제 공개일, 모르면 None) /
simulated_available_at(재생용 가정 값) / availability_basis(reported / assumed / unknown).
파일 기준월과 실제 관측 기간(observed_from ~ observed_to)도 따로 적는다.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field

from bigcontest.agent.schemas import (
    ASSUMED_AVAILABILITY_LABEL,
    AnalysisMode,
    AvailabilityBasis,
    ContractIssue,
    ContractResult,
)
from bigcontest.contracts import grain_contract

POST_HOC_LABEL = "사후 참고: 기준일 뒤 자료로 만든 규칙·모델이라 그 시점의 판단 근거가 아님"
POST_HOC_DATA_LABEL = "사후 분석: 기준일 뒤에 관측·공개된 자료 포함"
BACKGROUND_LABEL = "공개일을 모르는 자료라 그 시점에 쓸 수 있던 근거가 아니라 배경 자료로만 표시"


def _month_start(ym: str, shift: int = 0) -> date:
    y, m = int(ym[:4]), int(ym[4:6]) + shift
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return date(y, m, 1)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


class DataAvailability(BaseModel):
    """자료 한 덩어리(파일·월·일)의 관측 기간과 공개 시점."""

    source: str
    base_month: str | None = None  # 파일 기준월 (YYYYMM)
    observed_from: date
    observed_to: date
    window_status: str = "확인"  # 관측 기간 확인 상태 (확인 / 미확인)
    actual_available_at: date | None = None
    simulated_available_at: date | None = None
    availability_basis: AvailabilityBasis = "unknown"


def observed_window(source: str, base_month: str | None = None, day: date | None = None) -> tuple[date, date, str]:
    """파일 기준월(또는 날짜) → 실제 관측 기간과 확인 상태."""
    t = grain_contract()["sources"][source]["time"]
    kind = t["observed"]
    if kind in ("same_day", "verified_date"):
        if day is None:
            raise ValueError(f"{source}: 일 단위 자료는 day 가 필요함")
        return day, day, "확인"
    if base_month is None:
        raise ValueError(f"{source}: 월 단위 자료는 base_month 가 필요함")
    if kind in ("same_month", "month_end"):
        start = _month_start(base_month)
        return start, _month_end(start), "확인"
    if kind == "months_before":
        lo, hi = t["window_months"]
        return _month_start(base_month, lo), _month_end(_month_start(base_month, hi)), t.get("window_status", "미확인")
    raise ValueError(f"알 수 없는 관측 방식: {kind}")


def availability(
    source: str,
    base_month: str | None = None,
    day: date | None = None,
    actual_available_at: date | None = None,
    assume: bool = True,
) -> DataAvailability:
    """관측 기간과 공개 시점. 실제 공개일을 모르면 설정의 가정 지연으로 simulated_available_at 을 채운다."""
    t = grain_contract()["sources"][source]["time"]
    start, end, status = observed_window(source, base_month, day)
    sim = None
    if assume:
        anchor = _month_end(_month_start(base_month)) if t["anchor"] == "base_month_end" else end
        sim = anchor + timedelta(days=t["lag_days"])
    if actual_available_at is not None:
        basis = "reported"
    else:
        basis = "assumed" if sim is not None else "unknown"
    return DataAvailability(
        source=source,
        base_month=base_month,
        observed_from=start,
        observed_to=end,
        window_status=status,
        actual_available_at=actual_available_at,
        simulated_available_at=sim,
        availability_basis=basis,
    )


@dataclass
class Provenance:
    """규칙·모델이 언제까지의 자료로 만들어졌나."""

    name: str
    trained_until: date | None = None
    calibrated_until: date | None = None
    rule_derived_from: tuple[date, date] | None = None  # 규칙을 만든 근거 기간

    @property
    def latest(self) -> date | None:
        ds = [self.trained_until, self.calibrated_until, self.rule_derived_from[1] if self.rule_derived_from else None]
        ds = [d for d in ds if d is not None]
        return max(ds) if ds else None


class TimeDecision(BaseModel):
    usable: bool  # 그 시점의 근거로 쓸 수 있나
    role: str  # evidence / background / rejected
    code: str | None = None
    message: str | None = None
    labels: list[str] = Field(default_factory=list)


def check_data_time(item: DataAvailability, as_of: date, mode: AnalysisMode) -> TimeDecision:
    """자료 한 덩어리를 이 기준일·모드에서 근거로 쓸 수 있는지."""
    labels: list[str] = []
    if item.window_status != "확인":
        labels.append(f"{item.source}: 관측 기간 {item.window_status} ({item.observed_from}~{item.observed_to}로 가정)")

    if mode == "retrospective":
        if item.observed_to > as_of:
            labels.append(POST_HOC_DATA_LABEL)
        return TimeDecision(usable=True, role="evidence", labels=labels)

    if item.observed_to > as_of:
        return TimeDecision(
            usable=False,
            role="rejected",
            code="TIME_DATA_AFTER_AS_OF",
            message=f"{item.source}: 관측 기간 끝({item.observed_to})이 기준일({as_of}) 뒤",
            labels=labels,
        )

    if mode == "strict_as_of":
        if item.actual_available_at is None:
            return TimeDecision(
                usable=False,
                role="background",
                code="TIME_AVAILABILITY_UNKNOWN",
                message=f"{item.source}: 실제 공개일을 모름. strict_as_of 에서는 근거로 쓰지 않음",
                labels=labels + [BACKGROUND_LABEL],
            )
        available = item.actual_available_at
    else:  # simulated_replay
        available = item.actual_available_at or item.simulated_available_at
        if available is None:
            return TimeDecision(
                usable=False,
                role="background",
                code="TIME_AVAILABILITY_UNKNOWN",
                message=f"{item.source}: 실제·가정 공개일이 모두 없음",
                labels=labels + [BACKGROUND_LABEL],
            )
        if item.actual_available_at is None:
            labels.append(ASSUMED_AVAILABILITY_LABEL)

    if available > as_of:
        return TimeDecision(
            usable=False,
            role="rejected",
            code="TIME_DATA_AFTER_AS_OF",
            message=f"{item.source}: 공개일({available})이 기준일({as_of}) 뒤",
            labels=labels,
        )
    return TimeDecision(usable=True, role="evidence", labels=labels)


def check_model_time(prov: Provenance, as_of: date, mode: AnalysisMode) -> TimeDecision:
    """규칙·모델이 기준일 뒤 자료로 만들어졌으면 strict_as_of 에서 차단, 다른 모드는 사후 참고 표시."""
    latest = prov.latest
    if latest is None:
        if mode == "strict_as_of":
            return TimeDecision(
                usable=False,
                role="rejected",
                code="TIME_MODEL_PROVENANCE_UNKNOWN",
                message=f"{prov.name}: 언제까지의 자료로 만들었는지 기록이 없음",
            )
        return TimeDecision(usable=True, role="evidence", labels=[f"{prov.name}: 만든 시점 기록 없음"])
    if latest <= as_of:
        return TimeDecision(usable=True, role="evidence")
    if mode == "strict_as_of":
        return TimeDecision(
            usable=False,
            role="rejected",
            code="TIME_MODEL_AFTER_AS_OF",
            message=f"{prov.name}: {latest}까지의 자료로 만듦. 기준일({as_of}) 뒤",
        )
    return TimeDecision(usable=True, role="evidence", labels=[f"{prov.name}: {POST_HOC_LABEL}"])


@dataclass
class TimeCheck:
    result: ContractResult
    usable: list[DataAvailability] = field(default_factory=list)
    background: list[DataAvailability] = field(default_factory=list)


def check_time_contract(
    items: list[DataAvailability],
    as_of: date,
    mode: AnalysisMode,
    models: list[Provenance] | None = None,
    required_sources: set[str] | None = None,
) -> TimeCheck:
    """check_availability 의 시간 계약 부분.

    - 쓸 수 없는 자료는 빼고, 그 때문에 필수 원천이 하나도 안 남으면 ERROR (G-13)
    - strict_as_of 에서 기준일 뒤 모델·규칙은 ERROR (G-13 ABSTAIN)
    - 가정·사후 표시는 warnings 로
    """
    issues: list[ContractIssue] = []
    warnings: list[str] = []
    out = TimeCheck(result=ContractResult(status="OK"))
    for it in items:
        d = check_data_time(it, as_of, mode)
        warnings += [w for w in d.labels if w not in warnings]
        if d.usable:
            out.usable.append(it)
        elif d.role == "background":
            out.background.append(it)
            warnings.append(d.message or "")
        else:
            warnings.append(d.message or "")
    for m in models or []:
        d = check_model_time(m, as_of, mode)
        warnings += [w for w in d.labels if w not in warnings]
        if not d.usable:
            issues.append(ContractIssue(code=d.code or "TIME", rule="G-13", message=d.message or ""))
    for src in sorted(required_sources or set()):
        if not any(it.source == src for it in out.usable):
            issues.append(
                ContractIssue(
                    code="TIME_NO_USABLE_DATA",
                    rule="G-13",
                    message=f"{src}: 기준일 {as_of}({mode})에 쓸 수 있는 자료가 없음",
                )
            )
    out.result = ContractResult(status="ERROR" if issues else "OK", issues=issues, warnings=warnings)
    return out


def can_join_same_month(a: str, b: str, base_month: str) -> bool:
    """두 원천의 같은 기준월 자료를 '같은 달'로 결합해도 되나.

    관측 기간이 같고 둘 다 확인된 경우만 True. 시민생활은 관측 기간이 미확인이라 False.
    """
    wa, wb = observed_window(a, base_month), observed_window(b, base_month)
    return wa == wb and wa[2] == "확인"
