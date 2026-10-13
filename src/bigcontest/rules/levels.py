"""등급 규칙 (configs/rules.yaml levels·caps). 축 사실 → 등급 + rule_id + 사유. LLM 과 무관하고 결정적이다.

입력은 근거에서 뽑은 축 사실(`Axes`)이다. 근거에서 축을 뽑는 일은 agent.tools.signal.assign_level 이 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bigcontest.rules import LEVEL_ORDER, load_rules, rules_version

ACCESS_CURRENT = "현재 원장 기준 접근성"


@dataclass
class StructFact:
    adm_cd: str
    value: float
    rank: int
    n: int
    population: str  # 순위 모집단 설명 (도시·기준월·동 수)
    top: bool


@dataclass
class Axes:
    """후보 하나의 축 사실."""

    candidate_type: str
    input_status: str
    missing_inputs: list[str] = field(default_factory=list)
    counter_result: str = "해당 없음"
    counter_reason: str | None = None
    trigger: str | None = None
    structure: StructFact | None = None  # 예정된 점검: 그 동 / 이탈: 먼저 확인할 동 중 첫 상위 동
    access_km: float | None = None
    access_counts: bool = False  # 등급 축으로 셈 (거리 기준 이상 + 기준일에 확인된 접근성)
    access_current: bool = False  # 공개 시점 미상 현재 원장 (표시)
    access_ref_only: bool = False  # 거리 기준 이상이지만 strict 라 세지 않음
    second_adm_axis: bool = False  # 이탈: 먼저 확인할 동 중 구조 상위 + 접근성인 동이 있음
    seasonal_reproduced: bool = False
    deviation_drop: float | None = None  # 이탈 크기 (양수 = 하락 비율)
    confidence: str = "보통"
    confidence_why: list[str] = field(default_factory=list)


@dataclass
class Decision:
    level: str
    rule_id: str
    reason: str
    caps: list[str] = field(default_factory=list)


def _struct_text(s: StructFact) -> str:
    return f"구조 취약성 상위(1인세대 비율 {s.value:g}, {s.population} 중 {s.rank}/{s.n}위)"


def _access_text(a: Axes) -> str:
    if a.access_current:
        return f"접근성 근거({ACCESS_CURRENT}: 관측 거점까지 직선 {a.access_km:.1f}km, 과거 당시 확인 안 됨)"
    return f"접근성 근거(관측 거점까지 직선 {a.access_km:.1f}km)"


def _cap(level: str, max_level: str) -> str:
    if level not in LEVEL_ORDER:
        return level
    return level if LEVEL_ORDER[level] <= LEVEL_ORDER[max_level] else max_level


def decide(a: Axes, rules: dict | None = None) -> Decision:
    """rules.yaml levels 를 위에서부터 적용하고 caps 로 상한을 건다."""
    rules = rules or load_rules()
    th = rules["thresholds"]
    far = th["access"]["far_km"]
    conf_ok = a.confidence in ("보통", "높음")
    s_top = a.structure is not None and a.structure.top

    if a.input_status == "보류":
        d = Decision("판단 보류", "HOLD-INPUT", f"필수 근거 결측: {', '.join(a.missing_inputs)}")
    elif a.counter_result == "보류":
        d = Decision("판단 보류", "HOLD-COUNTER", f"반증 보류: {a.counter_reason}")
    elif a.counter_result == "기각":
        d = Decision("Level 1", "L1-REJECTED", f"반증에서 기각: {a.counter_reason}")
    elif a.candidate_type == "unexpected_activity_deviation" and a.deviation_drop is not None \
            and a.deviation_drop < th["deviation"]["min_drop"]:
        d = Decision("Level 1", "L1-DEV-SMALL",
                     f"이탈 크기 {a.deviation_drop:.1%}가 절대 최소값 {th['deviation']['min_drop']:.0%} 미만")
    elif a.candidate_type == "scheduled_access_review":
        parts = []
        if s_top:
            parts.append(_struct_text(a.structure))
        if a.access_counts:
            parts.append(_access_text(a))
        if parts:
            parts.append(f"예정된 트리거({a.trigger})")
            why = " + ".join(parts)
            if s_top and a.access_counts and a.input_status == "충족" and conf_ok:
                d = Decision("Level 3", "L3-STRUCT-ACCESS-TRIGGER", why)
            elif s_top:
                d = Decision("Level 2", "L2-STRUCT-TRIGGER", why)
            else:
                d = Decision("Level 2", "L2-ACCESS-TRIGGER", why)
            if s_top and a.access_counts and a.input_status == "충족" and not conf_ok:
                d.caps.append("CAP-CONF-LOW")
            if s_top and a.access_counts and a.input_status == "부분 판정":
                d.caps.append("CAP-PARTIAL")
        else:
            d = Decision("Level 1", "L1-TRIGGER-ONLY",
                         f"근거 하나만: 예정된 트리거({a.trigger}). 구조·접근성 기준 미달 또는 결측")
        if a.access_ref_only:
            d.reason += (f" / 접근성 참고({ACCESS_CURRENT}, 관측 거점까지 직선 {a.access_km:.1f}km): 과거 당시 확인 안 된 "
                         "원장이라 strict_as_of 등급에 쓰지 않음")
        if s_top and a.access_km is not None and a.access_km < far:
            d.reason += f" / 거리 공백 근거 약함(관측 거점까지 {a.access_km:.1f}km)"
    elif a.counter_result == "통과":
        if s_top:
            why = f"{_struct_text(a.structure)} (먼저 확인할 동, 공유 맥락) + 반증 통과 이탈"
            d = Decision("Level 2", "L2-STRUCT-DEVIATION", why)
            if a.second_adm_axis:
                # 카드 이탈은 도시 단위라 동을 직접 가리키지 않음 → 동의 두 번째 근거가 있어도 Level 2 상한
                d.caps.append("CAP-SHARED-CONTEXT")
                d.reason += " / 먼저 확인할 동에 접근성 근거도 있으나 이탈은 도시 단위(공유 맥락)라 Level 3 불가"
        else:
            d = Decision("Level 1", "L1-DEVIATION-ONLY", "근거 하나만: 반증 통과 이탈 (구조 상위 동 없음)")
    else:
        d = Decision("Level 0", "L0-NONE", "규칙 해당 없음")

    # 상한 (caps): 위에서 이미 Level 3 을 막은 경우도 기록
    for cap in rules["caps"]:
        hit = (cap["id"] == "CAP-PARTIAL" and a.input_status == "부분 판정") or \
              (cap["id"] == "CAP-CONF-LOW" and a.confidence == "낮음")
        if hit:
            new = _cap(d.level, cap["max_level"])
            if new != d.level or cap["id"] in d.caps:
                d.level = new
                if cap["id"] not in d.caps:
                    d.caps.append(cap["id"])
    if a.input_status == "부분 판정":
        d.reason += f" (부분 판정: {', '.join(a.missing_inputs)} 없음, Level 3 불가)"
    if a.confidence == "낮음" and d.level not in ("판단 보류",):
        d.reason += f" (근거 신뢰도 낮음: {'; '.join(a.confidence_why) or '-'}, Level 3 불가)"
    return d


def rule_label(d: Decision, rules: dict | None = None) -> str:
    """level_reason 끝에 붙는 표시: [rules-v1.0 L2-STRUCT-TRIGGER+CAP-PARTIAL]."""
    return f"[{rules_version(rules)} {full_rule_id(d)}]"


def full_rule_id(d: Decision) -> str:
    return "+".join([d.rule_id, *d.caps])


# ---------------------------------------------------------------- 조건별 충족 여부 (explain_level 입력)


def _flip(a: Axes, rules: dict, **change) -> str | None:
    """조건 하나만 충족으로 바꿨을 때의 등급 (지금보다 높아질 때만)."""
    from dataclasses import replace

    now = decide(a, rules).level
    new = decide(replace(a, **change), rules).level
    if new in LEVEL_ORDER and (now not in LEVEL_ORDER or LEVEL_ORDER[new] > LEVEL_ORDER[now]):
        return new
    return None


def rule_checks(a: Axes, rules: dict | None = None) -> list[dict]:
    """등급 규칙 조건 목록 (schemas.RuleCheck 칸). met: True/False/None(자료 없음).
    level_if_met: 그 조건 하나만 (지금과 반대로) 충족되면 가능한 등급 (decide 를 다시 돌려 구함)."""
    rules = rules or load_rules()
    th = rules["thresholds"]
    s = a.structure
    from bigcontest.rules.candidates import top_cut

    out: list[dict] = []
    top_struct = StructFact(s.adm_cd if s else "-", max(s.value if s else 0, th["structure"]["abs_min"]),
                            1, s.n if s else 1, s.population if s else "-", True)
    if a.candidate_type == "scheduled_access_review":
        cut = top_cut(s.n, rules) if s else None
        out.append({"condition": f"구조 상위 (도시 안 상위 {th['structure']['top_share']:.0%})",
                    "met": None if s is None else s.rank <= cut, "value": s.rank if s else None,
                    "threshold": cut, "comparator": "<=", "unit": "위", "input": "structure_vulnerability",
                    "level_if_met": None if (s and s.top) else _flip(a, rules, structure=top_struct)})
        out.append({"condition": "1인세대 비율 최소값", "met": None if s is None else s.value >= th["structure"]["abs_min"],
                    "value": s.value if s else None, "threshold": th["structure"]["abs_min"], "comparator": ">=",
                    "input": "structure_vulnerability"})
        far = th["access"]["far_km"]
        out.append({"condition": "관측 거점까지 직선거리", "met": None if a.access_km is None else a.access_counts,
                    "value": a.access_km, "threshold": far, "comparator": ">=", "unit": "km", "input": "access_gap",
                    "level_if_met": None if a.access_counts else
                    _flip(a, rules, access_counts=True, access_km=max(a.access_km or 0.0, far), access_ref_only=False)})
        if a.access_ref_only:
            out.append({"condition": "접근성 자료가 기준일에 확인됨", "met": False, "value": ACCESS_CURRENT,
                        "input": "access_gap"})
        out.append({"condition": "예정된 트리거", "met": bool(a.trigger), "value": a.trigger})
    else:
        out.append({"condition": "반증 통과", "met": a.counter_result == "통과", "value": a.counter_result,
                    "input": "holiday_expectation",
                    "level_if_met": None if a.counter_result == "통과" else
                    _flip(a, rules, counter_result="통과", counter_reason="반증 통과")})
        md = th["deviation"]["min_drop"]
        out.append({"condition": "이탈 크기 최소값", "met": None if a.deviation_drop is None else a.deviation_drop >= md,
                    "value": a.deviation_drop, "threshold": md, "comparator": ">=", "unit": "비율",
                    "input": "cohort_deviation",
                    "level_if_met": None if a.deviation_drop is None or a.deviation_drop >= md else
                    _flip(a, rules, deviation_drop=md)})
        out.append({"condition": "먼저 확인할 동이 구조 상위", "met": bool(s and s.top), "value": s.adm_cd if s else None,
                    "input": "structure_vulnerability",
                    "level_if_met": None if (s and s.top) else _flip(a, rules, structure=top_struct)})
    out.append({"condition": "필수 근거 모두 있음", "met": a.input_status == "충족", "value": a.input_status,
                "input": (a.missing_inputs[0].split(" ")[0] if a.missing_inputs else None),
                "level_if_met": None if a.input_status == "충족" else
                _flip(a, rules, input_status="충족", missing_inputs=[])})
    out.append({"condition": "근거 신뢰도 보통 이상", "met": a.confidence in ("보통", "높음"), "value": a.confidence,
                "level_if_met": None if a.confidence != "낮음" else _flip(a, rules, confidence="보통", confidence_why=[])})
    return out
