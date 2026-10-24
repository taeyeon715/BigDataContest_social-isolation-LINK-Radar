"""rules-v2.0 등급 판정 (configs/rules_v2.yaml levels). LLM 과 무관하고 결정적이다.

입력은 칸(동 × 성 × 연령) 하나의 사실(`CellFacts`)이다. 판단 보류·반증·이탈 크기·트리거 순서는 v1.1 levels.decide 와 같다.

근거는 두 갈래로 센다.
- 제공 데이터 신호(신한카드·SK 통신): H 행동 고립(SK), W 겨울 체류 감소(SK, 읍·면), C 카드 근거(신한, 그 성·연령)
- 외부 확인(공공 통계·자원 원장): S 1인 비율(주민등록), V 경제 취약(수급 통계), A 거점 원거리(자원 원장)

Level 3 = 예정된 트리거 + 제공 데이터 신호 2개 이상(서로 다른 신호) + 외부 확인 1개 이상 + 필수 근거 충족 + 신뢰도 보통 이상.
Level 2 이상에는 동을 가리키는 통신 신호(H 또는 W)가 하나는 있어야 한다. 카드(C)는 도시 단위라 '누구'만 정한다.
통신 신호가 없으면 Level 1 (카드 신호만, 외부 확인만으로는 오르지 않는다).
이탈(도시 × 코호트, 카드)은 Level 2 상한이고, 먼저 확인할 칸에 SK 신호가 있어야 Level 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from bigcontest.contracts import load_yaml

AXIS_NAME = {"H": "행동 고립", "W": "겨울 체류 감소", "C": "카드 결제 공백",
             "S": "혼자 사는 구조", "V": "경제 취약", "A": "거점 원거리"}
DATA_AXES = ("H", "W", "C")  # 신한·SK
DONG_AXES = ("H", "W")  # 동을 가리키는 통신 신호
EXTERNAL_AXES = ("S", "V", "A")  # 공공 통계·자원 원장
ORDER = DATA_AXES + EXTERNAL_AXES


@lru_cache
def load_rules_v2() -> dict:
    return load_yaml("rules_v2.yaml")


@dataclass
class CellFacts:
    candidate_type: str  # scheduled_access_review / unexpected_activity_deviation
    input_status: str = "충족"  # 충족 / 부분 판정 / 보류
    missing_inputs: list[str] = field(default_factory=list)
    trigger: str | None = None
    axes: dict[str, bool] = field(default_factory=dict)  # 신호 → 섰나 (결측 신호는 넣지 않음)
    axis_why: dict[str, str] = field(default_factory=dict)  # 신호 → 사유 문장
    counter_result: str = "해당 없음"  # 통과 / 보류 / 기각 / 해당 없음
    counter_reason: str | None = None
    holiday_travel: bool = False
    deviation_drop: float | None = None
    confidence: str = "보통"  # 낮음 / 보통 / 높음
    confidence_why: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)  # 방향 미확인 신호 (Level 3 근거에서 뺌)


@dataclass
class DecisionV2:
    level: str
    rule_id: str
    reason: str
    axes_on: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)


def axes_on(f: CellFacts) -> list[str]:
    return [a for a in ORDER if f.axes.get(a)]


def split(on: list[str]) -> tuple[list[str], list[str]]:
    """선 신호 → (제공 데이터, 외부 확인)."""
    return [a for a in on if a in DATA_AXES], [a for a in on if a in EXTERNAL_AXES]


def _why(f: CellFacts, on: list[str]) -> str:
    return " + ".join(f"{AXIS_NAME[a]}({f.axis_why.get(a, '')})".replace("()", "") for a in on)


def decide_v2(f: CellFacts, rules: dict | None = None) -> DecisionV2:
    rules = rules or load_rules_v2()
    lv = rules["levels"]
    on = axes_on(f)
    data, ext = split(on)
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
        if data:
            d = DecisionV2("Level 2", "V2-L2-DEV-DATA", f"반증 통과 카드 이탈 + 먼저 확인할 칸의 근거: {_why(f, on)}", on)
            d.caps.append("CAP-SHARED-CONTEXT")
            return d
        return DecisionV2("Level 1", "L1-DEVIATION-ONLY", "반증 통과 이탈이지만 먼저 확인할 칸에 통신 신호가 없음", on)

    # 예정된 점검
    trig = f"예정된 트리거({f.trigger})"
    if not any(a in DONG_AXES for a in data):
        what = "카드 신호만(동을 가리키지 않음)" if data else (f"외부 확인만({_why(f, ext)})" if ext else "근거 없음")
        return DecisionV2("Level 1", "L1-TRIGGER-ONLY", f"{trig}. 동을 가리키는 통신 신호 없음, {what}", on)
    why = f"{_why(f, on)} + {trig}"
    d = DecisionV2("Level 2", "V2-L2-DATA-TRIGGER", why, on)
    data3 = [a for a in data if a not in f.unverified]
    ext3 = [a for a in ext if a not in f.unverified]
    if len(data) < lv["level3_min_data"]:
        d.caps.append("CAP-DATA-ONE")  # 신한·SK 신호 하나
    if len(ext) < lv["level3_min_external"]:
        d.caps.append("CAP-NO-EXTERNAL")  # 외부 확인 없음
    if not d.caps and (len(data3) < lv["level3_min_data"] or len(ext3) < lv["level3_min_external"]):
        d.caps.append("CAP-SIGNAL-UNVERIFIED")  # 방향 미확인 신호를 빼면 Level 3 조건 미달
        d.reason += " · 방향 미확인 신호(" + "·".join(AXIS_NAME[a] for a in on if a in f.unverified) + ")는 Level 3 근거에서 뺌"
    if f.input_status != "충족":
        d.caps.append("CAP-PARTIAL")
    if not conf_ok:
        d.caps.append("CAP-CONF-LOW")
    if not d.caps:
        return DecisionV2("Level 3", "V2-L3-DATA2-EXT1", why, on)
    return d


def cell_type(on: list[str]) -> str:
    """위험 유형 (rules_v2.yaml types). H(행동)와 S·V(구조·경제)로 정한다."""
    h = "H" in on
    sv = bool({"S", "V"} & set(on))
    if h and sv:
        return "이중 위험"
    if h:
        return "관계 단절형"
    if sv:
        return "구조·경제 취약형"
    return "해당 없음"
