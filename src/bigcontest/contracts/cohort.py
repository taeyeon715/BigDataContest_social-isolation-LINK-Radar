"""연령 계약: 분석 코호트(데이터가 지원하는 연령)와 서비스 대상 집단(정책 연령)을 나눈다.

- 감시 대상 3집단은 configs/cohorts.yaml 의 groups 에 원천별 코호트를 직접 적어 둠 (설계 §5.3)
- 그 밖의 요청은 원천의 연령 구간 중 겹치는 것을 모아 자동 계산
- 정확히 같지 않으면 경고 문구를 붙이고, 너무 넓게 섞이면(예: 통신 60+로 "70대 이상") 지원하지 않음
"""

from dataclasses import dataclass, field

from bigcontest.contracts import cohorts_config

Band = tuple[str, int, int | None]


@dataclass
class CohortResult:
    source: str
    supported: bool
    analysis_cohort: str | None
    service_eligibility_group: str
    bands: list[str] = field(default_factory=list)
    extra_ages: tuple[int, int] | None = None  # 요청보다 더 들어간 연령 (구간 하나로 요약)
    missing_ages: tuple[int, int] | None = None  # 요청했는데 빠진 연령
    warnings: list[str] = field(default_factory=list)
    message: str | None = None  # 지원하지 않을 때 이유

    @property
    def exact(self) -> bool:
        return self.supported and self.extra_ages is None and self.missing_ages is None


def source_bands(source: str) -> list[Band]:
    cfg = cohorts_config()
    key = cfg["source_bands"].get(source)
    if key is None:
        return []
    spec = cfg["age_bands"][key]
    if isinstance(spec, list):
        return [(name, lo, hi) for name, lo, hi in spec]
    out: list[Band] = []
    step, a = spec["step"], spec["start"]
    while a < spec["open_from"]:
        hi = a + step - 1
        out.append((f"{a}~{hi}세" if step > 1 else f"{a}세", a, hi))
        a += step
    out.append((f"{spec['open_from']}세 이상", spec["open_from"], None))
    return out


def _hi(x: int | None) -> int:
    return cohorts_config()["max_age"] if x is None else x


def _span(ages: set[int]) -> tuple[int, int] | None:
    return (min(ages), max(ages)) if ages else None


def _fmt_span(span: tuple[int, int]) -> str:
    lo, hi = span
    if hi >= cohorts_config()["max_age"]:
        return f"{lo}세 이상"
    return f"{lo}세" if lo == hi else f"{lo}~{hi}세"


def service_label(age_min: int | None, age_max: int | None, sex: str | None = None) -> str:
    lo = age_min or 0
    if age_max is None:
        text = f"{lo}세 이상"
    elif age_min is None:
        text = f"{age_max}세 이하"
    else:
        text = f"{lo}~{age_max}세"
    if sex:
        text += " 남성" if sex == "남" else " 여성"
    return text


def _cohort_label(bands: list[Band]) -> str:
    if len(bands) > 3:  # 1세·5세 단위는 범위로 요약
        lo, hi = bands[0][1], bands[-1][2]
        return _fmt_span((lo, _hi(hi)))
    return "·".join(b[0] for b in bands)


def resolve_cohort(
    source: str,
    age_min: int | None = None,
    age_max: int | None = None,
    sex: str | None = None,
    group: str | None = None,
) -> CohortResult:
    """요청 연령(또는 서비스 대상 집단 이름)을 원천의 분석 코호트로 바꾼다."""
    cfg = cohorts_config()
    all_bands = source_bands(source)
    explicit: list[str] | None = None
    if group is not None:
        g = cfg["groups"].get(group)
        if g is None:
            label = group
            return CohortResult(source, False, None, label, message=f"알 수 없는 서비스 대상 집단: {group}")
        svc = g["service"]
        age_min, age_max, sex = svc["age_min"], svc["age_max"], svc["sex"]
        label = svc["label"]
        explicit = g["cohorts"].get(source)
    else:
        label = service_label(age_min, age_max, sex)

    if not all_bands:
        return CohortResult(source, False, None, label, message=f"{source} 에는 연령 구분이 없음")

    lo, hi = age_min or 0, _hi(age_max)
    want = set(range(lo, hi + 1))
    if explicit is not None:
        chosen = [b for b in all_bands if b[0] in explicit]
    else:
        chosen = [b for b in all_bands if b[1] <= hi and _hi(b[2]) >= lo]
    if not chosen:
        return CohortResult(source, False, None, label, message=f"{source} 연령 구간에 '{label}'과 겹치는 칸이 없음")

    covered: set[int] = set()
    for _, b_lo, b_hi in chosen:
        covered |= set(range(b_lo, _hi(b_hi) + 1))
    extra, missing = _span(covered - want), _span(want - covered)
    cohort = _cohort_label(chosen)
    res = CohortResult(source, True, cohort, label, [b[0] for b in chosen], extra, missing)

    n_extra = len(covered - want)
    if explicit is None and n_extra > cfg["max_extra_years"]:
        res.supported = False
        res.message = (
            f"{source} 의 연령 구간 '{cohort}'로는 '{label}'을 나타낼 수 없음 "
            f"({_fmt_span(extra)}가 섞임). '{cohort}' 그대로만 말할 수 있음"
        )
        return res
    if extra:
        res.warnings.append(
            f"요청 집단({label})과 분석 코호트({source} {cohort})가 정확히 같지 않음: {_fmt_span(extra)}가 섞임"
        )
    if missing:
        res.warnings.append(
            f"요청 집단({label})과 분석 코호트({source} {cohort})가 정확히 같지 않음: {_fmt_span(missing)}가 빠짐"
        )
    return res


def label_allowed(source: str, label_age_min: int, label_age_max: int | None = None) -> bool:
    """이 원천 값을 '{label_age_min}세 이상'(또는 범위)이라고 불러도 되나.

    원천 구간 경계와 맞아야 함. 예: 통신(60+)을 '70대 이상'으로 부르면 False.
    """
    bands = source_bands(source)
    lows = {b[1] for b in bands}
    highs = {_hi(b[2]) for b in bands}
    return label_age_min in lows and _hi(label_age_max) in highs
