"""비교안 A~D: 같은 날짜·도시·집단·K 에서 쓰는 자료만 바꿔 후보·등급을 다시 낸다.

| 비교안 | 쓰는 근거 |
|---|---|
| A | 구조(주민등록 1인세대) + 달력 + 대상 집단 |
| B | A + 통신(구성·계절: 체류 구성, 춘천 읍·면 겨울 변화) |
| C | B + 카드(시점·이탈: 결제 시계열, 기대값, 이탈 경보, 반증) |
| D | C + 자원(관측 거점까지 직선거리) |

시민생활은 어느 비교안에도 입력으로 넣지 않는다 (탐색적 검증 참고, 설계 §7.1).
도구는 실제 도구(고정 스냅샷)를 한 번만 부르고, 비교안마다 근거를 걸러 같은 규칙 엔진에 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from bigcontest.agent.schemas import Candidate, NormalizedRequest, ToolResult
from bigcontest.agent.tools import call_tool
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.stubs import ToolContext
from bigcontest.rules import LEVEL_ORDER, load_rules

BASE_INPUTS = {"calendar_trigger", "target_group", "structure_vulnerability"}
VARIANT_INPUTS: dict[str, set[str]] = {
    "A": BASE_INPUTS,
    "B": BASE_INPUTS | {"mobility_layer", "winter_contraction"},
    "C": BASE_INPUTS | {"mobility_layer", "winter_contraction", "card_cohort_series", "expected_activity",
                        "cohort_deviation", "holiday_expectation", "city_common_factor", "weather_asos",
                        "academic_calendar"},
}
VARIANT_INPUTS["D"] = VARIANT_INPUTS["C"] | {"access_gap"}
VARIANT_NAME = {"A": "주민등록 + 달력", "B": "A + 통신(구성·계절)", "C": "B + 카드(시점·이탈)", "D": "C + 자원(접근성)"}

TOOLS = ("get_calendar_triggers", "check_request_contract", "get_structure", "get_mobility_layer",
         "calc_winter_contraction", "get_card_cohort_series", "calc_expected_activity", "calc_cohort_deviation",
         "check_confounders", "get_access_gap", "get_validation_reference")


def ctx_for(snapshot_id: str, city: str, as_of: date, group: str, mode: str = "simulated_replay") -> ToolContext:
    req = NormalizedRequest(city=city, metric="카드결제", group=group, period_to=as_of)
    return ToolContext(scenario="normal", as_of=as_of, analysis_mode=mode, snapshot_id=snapshot_id, request=req,
                       backend="real")


def collect(ctx: ToolContext) -> list[ToolResult]:
    """비교에 쓰는 모든 도구 결과 (한 번만 부름)."""
    out: list[ToolResult] = []
    for name in TOOLS:
        if name == "calc_winter_contraction" and ctx.as_of.month not in S.WINTER_MONTHS:
            continue
        out += call_tool(name, ctx)
    return out


def input_key(e: ToolResult) -> str | None:
    if e.metric == S.FORECAST_METRIC:
        return "expected_activity"
    return S.input_of(e)


def filter_evidence(evidence: list[ToolResult], allowed: set[str]) -> list[ToolResult]:
    return [e for e in evidence if input_key(e) in allowed]


@dataclass
class RunResult:
    candidates: list[Candidate]
    evidence: list[ToolResult]
    k: int
    notes: list[str] = field(default_factory=list)


def run_rules(evidence: list[ToolResult], req: NormalizedRequest, as_of: date, k: int,
              rules: dict | None = None) -> RunResult:
    """근거 → 후보 → 반증 → 등급 → 배정 (그래프와 같은 함수, LLM 없음)."""
    rules = rules or load_rules()
    cands = S.classify_candidate_type(evidence, req, as_of, cities=[req.city], rules=rules)
    judged = []
    for c in cands:
        if c.candidate_type == "unexpected_activity_deviation":
            res, why = S.judge_counter(c, S.confounders_for(c, evidence))
            c = c.model_copy(update={"counter_result": res, "counter_reason": why})
        judged.append(c)
    leveled = S.assign_level(judged, evidence, rules, as_of=as_of)
    return RunResult(S.assign_queue(leveled, evidence, k, rules), evidence, k)


def run_variant(evidence: list[ToolResult], variant: str, req: NormalizedRequest, as_of: date, k: int,
                rules: dict | None = None, drop: set[str] | None = None) -> RunResult:
    allowed = VARIANT_INPUTS[variant] - (drop or set())
    return run_rules(filter_evidence(evidence, allowed), req, as_of, k, rules)


# ---------------------------------------------------------------- 지표


def key_of(c: Candidate) -> str:
    """후보 구성 비교용 열쇠 (후보 ID 는 실행마다 다를 수 있어 쓰지 않음)."""
    where = c.adm_cd or "도시"
    return f"{c.city}|{'점검' if c.candidate_type == 'scheduled_access_review' else '이탈'}|{where}|{c.analysis_cohort}"


def top_k(cands: list[Candidate], k: int) -> list[Candidate]:
    """검토 순서 상위 K 중 Level 2·3 (selected 와 같은 정의, K 를 바꿔도 등급은 그대로)."""
    q = sorted(cands, key=lambda c: c.queue_rank or 10**9)
    return [c for c in q[:k] if c.candidate_level in ("Level 2", "Level 3")]


def jaccard(a: set, b: set) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / len(a | b)


def wrong_unit_claims(cands: list[Candidate], evidence: list[ToolResult]) -> int:
    """잘못된 단위 주장 수: 도시 단위(공유 맥락) 근거로 동 하나를 지목한 후보 + 공유 맥락 표시 없는 이탈 후보."""
    by_id = {e.evidence_id: e for e in evidence}
    n = 0
    for c in cands:
        if c.adm_cd and any(by_id.get(i) is not None and by_id[i].is_shared_context and S.input_of(by_id[i]) in
                            ("cohort_deviation", "card_cohort_series") for i in c.evidence_ids):
            n += 1
        if c.candidate_type == "unexpected_activity_deviation" and (c.adm_cd or not c.is_shared_context):
            n += 1
    return n


def level_counts(cands: list[Candidate]) -> dict[str, int]:
    out = {lv: 0 for lv in (*LEVEL_ORDER, "판단 보류")}
    for c in cands:
        out[c.candidate_level or "Level 0"] += 1
    return out
