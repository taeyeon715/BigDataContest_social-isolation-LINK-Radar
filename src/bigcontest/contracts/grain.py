"""단위(grain) 계약: 요청한 차원을 한 원천 표 안에서 모두 지원하는지 본다.

지원하지 않는 요청은 조용히 틀린 값을 내지 않고 status=ERROR + 사유 코드로 돌려준다.
- G-01 DIMENSION_NOT_AVAILABLE / CITY_NOT_COVERED: 원천에 그 차원·도시가 없음
- G-02 GRAIN_FORBIDDEN_COMBO: 서로 다른 표를 결합해야만 답할 수 있음 (예: 연령 × 시간대)
- COHORT_NOT_SUPPORTED: 요청 연령을 원천 연령 구간으로 나타낼 수 없음 (연령 계약)
"""

from bigcontest.agent.schemas import (
    DATA_END,
    DATA_START,
    CapabilitiesResponse,
    ContractIssue,
    ContractResult,
    NormalizedRequest,
    SourceCapability,
)
from bigcontest.contracts import grain_contract
from bigcontest.contracts.cohort import resolve_cohort, source_bands

def _dim_ko(dims) -> str:
    names = grain_contract()["dimensions"]
    return " × ".join(names.get(d, d) for d in dims)


def forbidden_combo(metric: str, dims: set[str]) -> dict | None:
    for combo in grain_contract()["forbidden_combos"]:
        if metric in combo["metrics"] and set(combo["dims"]) <= dims:
            return combo
    return None


def check_request_contract(req: NormalizedRequest) -> ContractResult:
    """요청이 단위·연령 계약에 맞는지 검사. 시간 계약은 contracts.time 에서 따로 본다."""
    gc = grain_contract()
    sources = gc["metrics"][req.metric]
    dims = set(req.dimensions) | {"city"}
    issues: list[ContractIssue] = []

    combo = forbidden_combo(req.metric, dims)
    if combo:
        single = len(combo["dims"]) == 1  # 차원 하나가 아예 없는 경우는 G-01
        issues.append(
            ContractIssue(
                code="DIMENSION_NOT_AVAILABLE" if single else "GRAIN_FORBIDDEN_COMBO",
                rule="G-01" if single else "G-02",
                message=f"{req.metric}: {_dim_ko(combo['dims'])} {'차원' if single else '조합'}은 지원하지 않음. {combo['reason']}",
            )
        )
        return ContractResult(status="ERROR", issues=issues)

    in_city = [s for s in sources if req.city in gc["sources"][s]["cities"]]
    if not in_city:
        issues.append(
            ContractIssue(
                code="CITY_NOT_COVERED",
                rule="G-01",
                message=f"{req.metric} 자료는 {req.city}에 없음 ({', '.join(sources)})",
            )
        )
        return ContractResult(status="ERROR", issues=issues)

    ok = [s for s in in_city if dims <= set(gc["sources"][s]["dims"])]
    if not ok:
        missing = sorted(dims - set().union(*(gc["sources"][s]["dims"] for s in in_city)))
        if missing:
            msg = f"{req.metric} 자료에 없는 차원: {_dim_ko(missing)}"
        else:
            msg = f"{req.metric}: {_dim_ko(sorted(dims))}를 한 표에서 함께 지원하는 자료가 없음 (표끼리 결합하지 않음)"
        code, rule = ("DIMENSION_NOT_AVAILABLE", "G-01") if missing else ("GRAIN_FORBIDDEN_COMBO", "G-02")
        issues.append(ContractIssue(code=code, rule=rule, message=msg))
        return ContractResult(status="ERROR", issues=issues)

    source = ok[0]
    spec = gc["sources"][source]
    warnings: list[str] = []
    if source == "card1":
        warnings.append("카드 데이터1(보조) 사용: 고객 거주지 없음, 데이터2와 합산 금지")
    if "seoul_living" == source:
        warnings.append("시민생활은 탐색적 검증 참고. 등급 입력 아님")

    cohort = None
    service = None
    if "age" in dims or req.group:
        res = resolve_cohort(source, req.age_min, req.age_max, req.sex, req.group)
        service = res.service_eligibility_group
        if not res.supported:
            issues.append(ContractIssue(code="COHORT_NOT_SUPPORTED", rule="G-01", message=res.message or ""))
            return ContractResult(
                status="ERROR", source=source, issues=issues, service_eligibility_group=service
            )
        cohort = res.analysis_cohort
        warnings += res.warnings

    return ContractResult(
        status="OK",
        source=source,
        analysis_cohort=cohort,
        service_eligibility_group=service,
        measured_grain=spec["measured_grain"],
        warnings=warnings,
    )


def get_data_capabilities() -> CapabilitiesResponse:
    """데이터별 지원 차원·기간 (GET /data/capabilities, 도구 get_data_capabilities)."""
    gc = grain_contract()
    items = []
    for name, s in gc["sources"].items():
        items.append(
            SourceCapability(
                source=name,
                name=s["name"],
                dimensions=s["dims"],
                cities=s["cities"],
                measured_grain=s["measured_grain"],
                period_from=DATA_START,
                period_to=DATA_END,
                age_bands=[b[0] for b in source_bands(name)] if len(source_bands(name)) <= 20 else [],
                observed_window_status=s["time"].get("window_status"),
            )
        )
    return CapabilitiesResponse(
        grain_version=gc["version"], sources=items, forbidden_combos=gc["forbidden_combos"]
    )
