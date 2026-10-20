"""rules-v2.0 등급 판정 (configs/rules_v2.yaml, reports/r3_규칙개정/변경계획.md 3·4절). LLM 과 무관하고 결정적이다.

입력은 칸(동 × 성 × 연령) 하나의 사실(`CellFacts`)이다. 판단 보류·반증·이탈 크기·트리거 순서는 v1.1 levels.decide 와 같고,
구조 조건만 "서로 다른 근거 축 개수"로 바뀐다.
- 근거 축: S(혼자 사는 구조), H(행동 고립), V(경제 취약), A(접근성).
- Level 3 = 예정된 트리거 + 축 2개 이상(단 {S, V}만은 제외) + 필수 근거 충족 + 근거 신뢰도 보통 이상.
- 이탈(도시 × 코호트)은 Level 2 상한 (v1.1 CAP-SHARED-CONTEXT 와 같음).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from bigcontest.contracts import load_yaml

AXIS_NAME = {"S": "혼자 사는 구조", "H": "행동 고립", "V": "경제 취약", "A": "접근성"}


@lru_cache
def load_rules_v2() -> dict:
    return load_yaml("rules_v2.yaml")


@dataclass
class CellFacts:
    candidate_type: str  # scheduled_access_review / unexpected_activity_deviation
    input_status: str = "충족"  # 충족 / 부분 판정 / 보류
    missing_inputs: list[str] = field(default_factory=list)
    trigger: str | None = None
    axes: dict[str, bool] = field(default_factory=dict)  # 축 → 섰나 (결측 축은 넣지 않음)
    axis_why: dict[str, str] = field(default_factory=dict)  # 축 → 사유 문장
    counter_result: str = "해당 없음"  # 통과 / 보류 / 기각 / 해당 없음
    counter_reason: str | None = None
    holiday_travel: bool = False
    deviation_drop: float | None = None
    confidence: str = "보통"  # 낮음 / 보통 / 높음
    confidence_why: list[str] = field(default_factory=list)


@dataclass
class DecisionV2:
    level: str
    rule_id: str
    reason: str
    axes_on: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)


def axes_on(f: CellFacts) -> list[str]:
    return [a for a in ("S", "H", "V", "A") if f.axes.get(a)]


def level3_ok(on: list[str], rules: dict) -> bool:
    lv = rules["levels"]
    if len(on) < lv["level3_min_axes"]:
        return False
    return sorted(on) not in [sorted(s) for s in lv["level3_excluded_sets"]]


def _why(f: CellFacts, on: list[str]) -> str:
    return " + ".join(f"{AXIS_NAME[a]}({f.axis_why.get(a, '')})".replace("()", "") for a in on)


def decide_v2(f: CellFacts, rules: dict | None = None) -> DecisionV2:
    rules = rules or load_rules_v2()
    on = axes_on(f)
    conf_ok = f.confidence in ("보통", "높음")
    min_drop = rules["deviation"]["min_drop"]

    if f.input_status == "보류":
        return DecisionV2("판단 보류", "HOLD-INPUT", f"필수 근거 결측: {', '.join(f.missing_inputs)}", on)
    if f.counter_result == "보류":
        rid = "HOLD-HOLIDAY-TRAVEL" if f.holiday_travel else "HOLD-COUNTER"
        return DecisionV2("판단 보류", rid, f"반증 보류: {f.counter_reason}", on)
    if f.counter_result == "기각":
        return DecisionV2("Level 1", "L1-REJECTED", f"반증에서 기각: {f.counter_reason}", on)

    if f.candidate_type == "unexpected_activity_deviation":
        if f.deviation_drop is not None and f.deviation_drop < min_drop:
            return DecisionV2("Level 1", "L1-DEV-SMALL",
                              f"이탈 크기 {f.deviation_drop:.1%}가 최소 하락폭 {min_drop:.0%} 미만", on)
        if on:
            d = DecisionV2("Level 2", "V2-L2-DEV-AXIS", f"반증 통과 이탈 + 먼저 확인할 칸의 근거: {_why(f, on)}", on)
            d.caps.append("CAP-SHARED-CONTEXT")
            return d
        return DecisionV2("Level 1", "L1-DEVIATION-ONLY", "반증 통과 이탈이지만 근거 축이 선 칸 없음", on)

    # 예정된 점검
    trig = f"예정된 트리거({f.trigger})"
    if not on:
        return DecisionV2("Level 1", "L1-TRIGGER-ONLY", f"{trig}만. 근거 축 없음", on)
    why = f"{_why(f, on)} + {trig}"
    if level3_ok(on, rules):
        if f.input_status == "충족" and conf_ok:
            return DecisionV2("Level 3", "V2-L3-AXES2-TRIGGER", why, on)
        d = DecisionV2("Level 2", "V2-L2-AXES-TRIGGER", why, on)
        d.caps.append("CAP-CONF-LOW" if not conf_ok else "CAP-PARTIAL")
        return d
    d = DecisionV2("Level 2", "V2-L2-AXES-TRIGGER", why, on)
    if len(on) >= 2:
        d.caps.append("CAP-SV-ONLY")  # {S, V}만: 입력이 겹쳐 두 축으로 세지 않음
    return d


def cell_type(on: list[str]) -> str:
    """위험 유형 (rules_v2.yaml types)."""
    h = "H" in on
    sv = bool({"S", "V"} & set(on))
    if h and sv:
        return "이중 위험"
    if h:
        return "관계 단절형"
    if sv:
        return "구조·경제 취약형"
    return "해당 없음"
