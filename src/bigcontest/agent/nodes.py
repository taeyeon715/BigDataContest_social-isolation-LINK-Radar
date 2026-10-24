"""그래프 노드 (에이전트 설계 §3). LangGraph 와 무관한 일반 함수다.

- 노드는 상태(dict)를 받아 바꿀 칸만 dict 로 돌려준다. 상태 안의 모델은 JSON 형태라 꺼낼 때 Pydantic 으로 검증한다.
- 연결(분기)은 graph.py 한 곳에만 있다. 승인 대기의 `interrupt` 도 graph.py 에서 부르고,
  여기에는 요청 만들기(`approval_request`)와 응답 처리(`apply_approval`)만 둔다.
- 도구는 `tools.call_tool` 로 부른다. 상태의 `tools_backend` 가 real 이면 실제 도구(고정 스냅샷), stub 이면 가짜 도구.
- 후보 분류·반증 판정·등급은 tools.signal 에 있다. 등급은 configs/rules.yaml (rules-v1.1, bigcontest.rules).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bigcontest.agent import budget as B
from bigcontest.agent import guard as G
from bigcontest.agent import investigation as INV
from bigcontest.agent.llm import call_llm, get_llm_safe, llm_config
from bigcontest.agent.periods import RELATIVE as PERIOD_LABELS
from bigcontest.agent.periods import find_period, window
from bigcontest.agent.places import Place, by_code, dongs, resolve_place
from bigcontest.agent.schemas import (
    DATA_END,
    DATA_START,
    DISCLAIMER,
    Candidate,
    ContractIssue,
    ContractResult,
    EvidenceCard,
    NormalizedRequest,
    Report,
    ResourceRecommendation,
    ToolResult,
)
from bigcontest.agent.state import EvidenceIdConflict, card_of, pack_card, pack_report, report_of
from bigcontest.agent.tools import EXTRA_TOOLS, call_tool, find_resources
from bigcontest.agent.tools import report as R
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.common import city_of_evidence, req_with_city
from bigcontest.agent.tools.stubs import ToolContext
from bigcontest.agent.tools.welfare import ACCESS_METRIC, cause_for
from bigcontest.contracts import grain_contract
from bigcontest.contracts.grain import check_request_contract
from bigcontest.contracts.inputs import required_tools
from bigcontest.contracts.time import Provenance, availability, check_time_contract

# 예전 이름 (테스트·API 가 씀). 값은 configs/rules.yaml 임계값 (rules-v1.1)
STUB_LEVEL_RULES = S.STUB_LEVEL_RULES
CITY_COMMON_NOTE = S.CITY_COMMON_NOTE
REVIEW_PENDING_NOTE = S.REVIEW_PENDING_NOTE
INPUT_OF_METRIC = S.INPUT_OF_METRIC
REQUIRED_CONFOUNDERS = S.REQUIRED_CONFOUNDERS
fact_of = R.fact_of
CITIES = ("강남", "춘천")

ROUTES = ("lookup", "investigate", "out_of_scope")
CANDIDATE_TYPES = ("scheduled_access_review", "unexpected_activity_deviation")
TYPE_KO = {"scheduled_access_review": "예정된 점검", "unexpected_activity_deviation": "예상 밖 이탈"}
MAX_EXTRA_ROUNDS = 2
# 카드 거주지 한계 (G-03). 이 템플릿 문장 한 곳에서만 말하고 LLM 설명은 다시 쓰지 않음 (explain_v4)
CARD1_NOTE = ("한계: 카드 데이터1(보조)은 가맹점 기준 결제를 시간대별로 담은 자료라 고객이 사는 곳이 없고, "
              "결제 시간대만으로 실제 체류·외출은 알 수 없음. 같은 연령 안의 비중만 비교함")
RESIDENCE_NOTE = "한계: 카드 자료는 고객이 사는 곳을 시도 단위까지만 담고 있어 시군구·동 단위로는 알 수 없음"

# 정기 감시 요청: 도시는 실행 요청(RunCreateRequest.city)으로, 비우면 두 도시 모두. 집단·지표는 고정
MONITORING_REQUEST = {"metric": "카드결제", "group": "고령층"}
# 동절기(춘천 읍·면) 점검일 때 필수 근거에 더해지는 도구 (candidate_inputs.yaml variants)
WINTER_TOOLS = ("calc_winter_contraction",)
# 도시별로 항상 같이 모으는 선택 맥락 (등급 입력 아님): 강남 시민생활은 탐색적 참고
CONTEXT_TOOLS = {"강남": ("get_validation_reference",)}
# 연휴 주의 기간(연휴 전 lead_days ~ 연휴 끝, 춘천은 연휴 뒤까지)에만 도는 고정 도구 (rules_v2.yaml holiday_watch)
HOLIDAY_WATCH_TOOL = "get_holiday_watch"
WINTER_CELL_TOOL = "get_winter_cells"

# 조회 경로: 지표 → 조회 도구
LOOKUP_TOOL = {
    "1인세대비율": "get_structure",
    "주민등록인구": "get_structure",
    "체류인구": "get_mobility_layer",
    "카드결제": "get_card_cohort_series",
    "시민생활지표": "get_validation_reference",
}

# 카드 결제 × 시간대 조회는 카드 데이터1(보조)로 (계약이 card1 을 고름)
CARD1_LOOKUP_TOOL = "get_card_time_band"

# 보류 사유 코드 → 더 확보하면 되는 것
NEEDED_DATA = {
    "OUT_OF_SCOPE": "도시(강남·춘천)와 지표가 들어간 질문",
    "OUT_OF_SCOPE_INDIVIDUAL": "동·집단 단위로 바꾼 질문 (예: \"강남 1인세대 비율 상위 동은?\", \"춘천 고령층 결제 추이는?\")",
    "GRAIN_FORBIDDEN_COMBO": "요청한 차원을 한 표에서 함께 지원하는 자료 (표끼리 결합하지 않음)",
    "DIMENSION_NOT_AVAILABLE": "요청한 차원이 있는 원천 자료",
    "CITY_NOT_COVERED": "그 도시의 원천 자료",
    "COHORT_NOT_SUPPORTED": "요청 연령을 나타낼 수 있는 연령 구간의 자료",
    "TIME_NO_USABLE_DATA": "기준일에 공개일이 확인된 자료 (또는 가정 공개 시점 재생 모드)",
    "TIME_MODEL_AFTER_AS_OF": "기준일 전 자료로 만든 모델·규칙",
    "TIME_MODEL_PROVENANCE_UNKNOWN": "모델·규칙을 만든 자료 기간 기록",
    "AMBIGUOUS_PLACE": "어느 동인지 정한 질문 (보류 사유의 선택지 중 하나로 다시 묻기)",
    "TIME_PERIOD_AFTER_AS_OF": "기준일 전 기간을 묻는 질문 (또는 기준일을 바꾼 새 실행)",
    "TIME_PERIOD_OUT_OF_DATA": "자료 기간(2025-07-01~2025-12-31) 안의 기간을 묻는 질문",
    "TOOL_ERROR": "도구 오류 해결 후 다시 조회",
    "EMPTY": "조회 조건에 맞는 자료",
    "G-05": "근거 카드와 맞는 보고서 문장",
    "G-06": "반증 자료 (공휴일 기대값·도시 공통요인·강수)",
    "G-08": "작은 칸을 묶은 집계",
    "G-11": "폐기하지 않은 지표",
    "G-13": "기준일에 쓸 수 있던 자료",
    "G-14": "지표 원래 뜻대로 쓴 문장",
}


# ---------------------------------------------------------------- 공통


def _as_of(state: dict) -> date:
    return date.fromisoformat(state["as_of"])


def _req(state: dict) -> NormalizedRequest | None:
    r = state.get("request")
    return NormalizedRequest(**r) if r else None


def _evidence(state: dict) -> list[ToolResult]:
    return [ToolResult(**e) for e in state.get("evidence") or []]


def _candidates(state: dict) -> list[Candidate]:
    return [Candidate(**c) for c in state.get("candidates") or []]


def _dump(items) -> list[dict]:
    return [x.model_dump(mode="json") for x in items]


def _backend(state: dict) -> str:
    return state.get("tools_backend") or "stub"


def _ctx(state: dict, attempt: int | None = None, city: str | None = None) -> ToolContext:
    req = _req(state)
    if req is not None and city:
        req = req_with_city(req, city)
    return ToolContext(
        scenario=state.get("scenario", "normal"),
        as_of=_as_of(state),
        analysis_mode=state["analysis_mode"],
        snapshot_id=state["snapshot_id"],
        attempt=attempt or max(1, state.get("evidence_revision") or 1),
        request=req,
        backend=_backend(state),
        allow_scenario=bool(state.get("allow_scenario")),
        tool_log=[],
    )


MAX_TIMINGS = 400  # 상태에 남기는 도구 호출 기록 수


def _timings(state: dict, *ctxs: ToolContext, phase: str | None = None) -> list[dict]:
    """도구 호출 기록(ms·상태)을 상태에 더함. 회차는 근거 판 번호. phase 는 조사 루프(investigate)·다시 시도(retry) 표시."""
    rnd = (state.get("evidence_revision") or 0) + 1
    new = [{**e, "round": rnd, **({"phase": phase} if phase else {})} for c in ctxs for e in (c.tool_log or [])]
    return (list(state.get("tool_timings") or []) + new)[-MAX_TIMINGS:]


def _cities(state: dict) -> list[str]:
    """이 실행의 도시. 정기 감시는 monitor_cities(비우면 두 도시), 질의응답은 요청 도시."""
    req = _req(state)
    if state.get("mode") == "monitoring":
        return list(state.get("monitor_cities") or _default_monitor_cities(state))
    return [req.city] if req else []


def _default_monitor_cities(state: dict) -> tuple[str, ...]:
    # 가짜 도구는 근거 ID 에 도시가 없어 두 도시를 한 실행에 담을 수 없음 → 춘천 하나
    return CITIES if _backend(state) == "real" else ("춘천",)


def _log(state: dict, *records) -> list[dict]:
    return list(state.get("llm_log") or []) + [r for r in records if r]


def _llm(state: dict):
    """상태의 LLM. 초기화에 실패하면 None (규칙·템플릿) + 기록 하나 (같은 실행에서 처음 한 번만)."""
    llm, rec = get_llm_safe(state.get("llm"))
    if rec and any(r.get("slot") == "init" for r in state.get("llm_log") or []):
        rec = None
    return llm, rec


def _errors(state: dict, evidence: list[ToolResult]) -> list[dict]:
    out = list(state.get("errors") or [])
    for e in evidence:
        if e.status == "ERROR":
            item = {"evidence_id": e.evidence_id, "metric": e.metric, "error_code": e.error_code or ""}
            if item not in out:
                out.append(item)
    return out


EVIDENCE_ID_CONFLICT = "EVIDENCE_ID_CONFLICT"


def same_content(a: dict | ToolResult, b: dict | ToolResult) -> bool:
    """같은 근거 ID 의 두 봉투가 같은 계산 결과인지 (JSON 모양으로 비교)."""
    da = a.model_dump(mode="json") if isinstance(a, ToolResult) else a
    db = b.model_dump(mode="json") if isinstance(b, ToolResult) else b
    return da == db


def id_conflicts(*groups) -> list[str]:
    """여러 근거 목록에서 같은 ID 인데 내용이 다른 것 (K-01: 같은 ID 로 다른 계산을 가리키면 안 됨)."""
    seen: dict[str, dict] = {}
    out: list[str] = []
    for group in groups:
        for e in group or []:
            d = e.model_dump(mode="json") if isinstance(e, ToolResult) else e
            prev = seen.setdefault(d["evidence_id"], d)
            if prev is not d and prev != d and d["evidence_id"] not in out:
                out.append(d["evidence_id"])
    return out


def _conflict_errors(errors: list[dict], ids: list[str], where: str) -> list[dict]:
    """근거 ID 충돌을 오류로 남김 (조용히 버리지 않음)."""
    out = list(errors or [])
    for i in ids:
        item = {"evidence_id": i, "metric": "", "error_code": f"{EVIDENCE_ID_CONFLICT}: 같은 ID 에 다른 내용 ({where})"}
        if item not in out:
            out.append(item)
    return out


def quarantine_split(results: list[ToolResult], check=None) -> tuple[list[ToolResult], list[tuple[ToolResult, list]]]:
    """참고용 근거 → (쓸 근거, [(격리한 근거, [(규칙·오류 코드, 사유)])]). 격리한 근거의 값은 어디에도 싣지 않는다."""
    check = check or G.quarantine_reasons
    keep, bad = [], []
    for r in results:
        why = check(r)
        if why:
            bad.append((r, why))
        else:
            keep.append(r)
    return keep, bad


def _quarantine_records(bad: list[tuple[ToolResult, list]], phase: str, tool_of: dict[str, str]) -> list[dict]:
    """격리 기록 (값 없이 ID·지표·규칙만)."""
    return [{"evidence_id": r.evidence_id, "metric": r.metric, "tool": tool_of.get(r.evidence_id), "phase": phase,
             "rule_ids": list(dict.fromkeys(c for c, _ in why)), "reason": "; ".join(dict.fromkeys(w for _, w in why))}
            for r, why in bad]


def _adm_of(ev: ToolResult) -> str | None:
    return S.adm_of(ev)


# ---------------------------------------------------------------- intake · classify

_INVESTIGATE_WORDS = ("어디부터", "확인해야", "점검", "후보", "조사")
_METRIC_WORDS = (
    ("1인세대", "1인세대비율"),
    ("1인가구", "1인세대비율"),
    ("결제", "카드결제"),
    ("소비", "카드결제"),
    ("시민생활", "시민생활지표"),
    ("체류", "체류인구"),
    ("활동", "체류인구"),
    ("유동", "체류인구"),
    ("인구", "주민등록인구"),
)
_GROUP_WORDS = (("어르신", "고령층"), ("고령", "고령층"), ("노인", "고령층"), ("중장년", "중장년 남성 1인가구"), ("청년", "청년 남성"))
_TIME_WORDS = (("새벽", "00_05"), ("아침", "06_11"), ("오전", "06_11"), ("낮", "12_17"), ("오후", "12_17"), ("밤", "18_23"), ("저녁", "18_23"), ("야간", "18_23"))
_SEX_WORDS = (("여성", "여"), ("여자", "여"), ("남성", "남"), ("남자", "남"))


def _is_investigate(question: str) -> bool:
    return any(w in question for w in _INVESTIGATE_WORDS)


def _group_service(group: str) -> dict:
    from bigcontest.contracts import load_yaml

    return load_yaml("cohorts.yaml")["groups"][group]["service"]


def read_question(question: str, as_of: date) -> tuple[NormalizedRequest | None, Place]:
    """질문 → (정규화한 요청 또는 None, 장소 읽기 결과). 장소가 애매하면 요청 None + place.ambiguous."""
    place = resolve_place(question)
    if place.ambiguous:
        return None, place
    return normalize_question(question, as_of, place), place


def normalize_question(question: str, as_of: date, place: Place | None = None) -> NormalizedRequest | None:
    """질문 → 화이트리스트 값으로 정규화한 요청 (규칙 기반). 못 하면 None (범위 밖).
    조사 질문의 지표는 카드결제(신호)로 둔다. 구조 지표는 조사 경로가 항상 같이 모은다.

    장소: 동 이름 사전으로 동을 먼저 찾고 그 동으로 도시를 정한다 ("춘천 강남동" → 춘천, places.resolve_place).
    찾은 동은 adm_cds 에 두고, 경로에 맞게 쓰는 법은 scope_request 가 정한다. 애매하면 None.
    기간: 기간 말은 period_label 에 두고 창은 scope_request 가 정한다 (periods.py 규칙).

    대상 조건: 질문에 적힌 성별·연령을 먼저 지킨다.
    - 감시 집단 별칭(어르신 → 고령층, 청년 → 청년 남성, 중장년 → 중장년 남성 1인가구)은 질문에 연령이 따로 없고
      적힌 성별이 그 집단의 성별과 같을 때만(또는 성별을 적지 않았을 때만) 쓴다
    - 맞지 않으면 다른 집단으로 바꾸지 않고, 적힌 연령(없으면 집단 말의 연령 범위)과 성별을 그대로 요청에 넣는다.
      지원하지 않는 조합이면 check_availability 가 보류한다
    """
    place = place or resolve_place(question)
    if place.ambiguous or place.city is None:
        return None
    city = place.city
    investigate = _is_investigate(question)
    metric = "카드결제" if investigate else next((m for w, m in _METRIC_WORDS if w in question), None)
    if metric is None:
        return None
    fields: dict[str, Any] = {"city": city, "metric": metric, "period_to": as_of, "adm_cds": place.adm_cds,
                              "period_label": find_period(question)}
    m = re.search(r"([1-9])0\s*대", question)
    group = next((g for w, g in _GROUP_WORDS if w in question), None)
    sex = next((s for w, s in _SEX_WORDS if w in question), None)
    if m:
        fields["age_min"] = int(m.group(1)) * 10
        fields["age_max"] = None if "이상" in question else int(m.group(1)) * 10 + 9
    elif group is not None:
        svc = _group_service(group)
        if sex is None or svc.get("sex") == sex:
            fields["group"] = group
        else:  # 집단 별칭과 성별이 다름 → 집단 말의 연령 범위 + 적힌 성별 (다른 집단으로 바꾸지 않음)
            fields["age_min"], fields["age_max"] = svc.get("age_min"), svc.get("age_max")
    elif investigate and sex is None:
        fields["group"] = "고령층"
    if sex and "group" not in fields:
        fields["sex"] = sex
    tod = next((t for w, t in _TIME_WORDS if w in question), None)
    if tod:
        fields["time_of_day"] = tod
    try:
        return NormalizedRequest(**fields)
    except ValidationError:
        return None


def scope_request(request: dict, route: str, as_of: date) -> tuple[dict, str | None]:
    """경로가 정해진 뒤 동·기간을 요청에 반영. (요청, 보류 사유 또는 None).

    - 조사 경로: 등급은 도시 전체·실행 기준일로 계산 (정기 감시와 같음). 동은 adm_cds 로만 남겨
      assign_queue 가 등급·순서를 정한 뒤 그 동 후보만 남긴다. 기간 창은 쓰지 않음 (기간 말은 기록만)
    - 조회 경로: 동이 하나면 adm_cd, 여럿이면 adm_cd 차원 (카드는 동 단위가 없어 계약이 보류).
      기간 말은 periods.window 로 창을 정하고 기준일까지로 자름. 창 시작이 기준일 뒤거나 자료 기간 밖이면 보류
    """
    req = dict(request)
    dims = [d for d in req.get("dimensions") or [] if d != "adm_cd"]
    req["adm_cd"] = None
    reason = None
    if route == "investigate":
        req["period_from"], req["period_to"] = str(DATA_START), str(as_of)
    elif route == "lookup":
        adms = list(req.get("adm_cds") or [])
        if len(adms) == 1:
            req["adm_cd"] = adms[0]
        if adms:
            dims.append("adm_cd")
        label = req.get("period_label")
        win = window(label, as_of) if label else None
        if label and win is None:
            reason = f"TIME_PERIOD_OUT_OF_DATA: 질문 기간({label})을 자료 기간에서 찾지 못함"
        elif win:
            lo, hi = win
            if lo > as_of:
                reason = f"TIME_PERIOD_AFTER_AS_OF: 질문 기간({label}, {lo}~{hi})이 기준일 {as_of} 뒤"
            else:
                lo, hi = max(lo, DATA_START), min(hi, as_of, DATA_END)
                if lo > hi:
                    reason = f"TIME_PERIOD_OUT_OF_DATA: 질문 기간({label})이 자료 기간({DATA_START}~{DATA_END}) 밖"
                else:
                    req["period_from"], req["period_to"] = str(lo), str(hi)
    req["dimensions"] = dims
    return NormalizedRequest(**req).model_dump(mode="json"), reason


def rule_route(question: str | None, request: dict | None) -> str:
    """규칙 기반 경로 (LLM 이 없거나 실패할 때)."""
    if request is None:
        return "out_of_scope"
    return "investigate" if _is_investigate(question or "") else "lookup"


def intake(state: dict) -> dict:
    as_of = _as_of(state)
    if state["mode"] == "monitoring":
        cities = list(state.get("monitor_cities") or _default_monitor_cities(state))
        req = NormalizedRequest(**MONITORING_REQUEST, city=cities[0], period_to=as_of)
        return {"request": req.model_dump(mode="json"), "route": "investigate", "monitor_cities": cities}
    question = state.get("question") or ""
    # 개인 지목 질문은 LLM 없이 막음: 정규화도 LLM 호출도 하지 않음
    marks = G.individual_target(question)
    if marks:
        return {"request": None, "route": "out_of_scope",
                "abstain_reason": f"{INDIVIDUAL_CODE}: {G.rules()['individual']['message']} (걸린 표현 {len(marks)}개)"}
    req, place = read_question(question, as_of)
    if place.ambiguous:  # 동을 고를 수 없음: 짐작하지 않고 선택지를 돌려줌 (LLM 도 부르지 않음)
        return {"request": None, "route": "out_of_scope",
                "abstain_reason": f"{AMBIGUOUS_CODE}: {place.ambiguous}. 선택지: {', '.join(place.options)}"}
    return {"request": req.model_dump(mode="json") if req else None}


INDIVIDUAL_CODE = "OUT_OF_SCOPE_INDIVIDUAL"
AMBIGUOUS_CODE = "AMBIGUOUS_PLACE"
_NO_LLM_CODES = (INDIVIDUAL_CODE, AMBIGUOUS_CODE)
_ROUTE_RANK = {"out_of_scope": 0, "lookup": 1, "investigate": 2}


def llm_fill(request: dict, llm_req: dict, rec: dict | None = None) -> dict:
    """규칙이 비운 칸만 LLM 값으로 채운다: 동(adm_names, 동 이름 enum) · 기간(period, 기간 이름 enum).
    규칙이 읽은 칸은 바꾸지 않는다. LLM 동이 요청 도시와 다르면 버린다. 채운 칸·버린 이유는 호출 기록에."""
    req = dict(request)
    filled, rejected = [], []
    names = [n for n in llm_req.get("adm_names") or [] if isinstance(n, str)]
    if names and not req.get("adm_cds"):
        cds = list(dict.fromkeys(d.adm_cd for n in names for d in dongs() if d.name == n))
        cities = {by_code()[c].city for c in cds}
        if cds and cities == {req.get("city")}:
            req["adm_cds"] = cds
            filled.append("adm_cds")
        else:
            rejected.append("LLM 동이 요청 도시와 다르거나 사전에 없음")
    period = llm_req.get("period")
    if period and not req.get("period_label"):
        if period in PERIOD_LABELS:
            req["period_label"] = period
            filled.append("period_label")
        else:
            rejected.append("LLM 기간이 기간 이름 목록 밖")
    if rec is not None and (filled or rejected):
        rec["llm_filled"] = filled
        if rejected:
            rec["llm_fill_rejected"] = rejected
    return req


def classify(state: dict) -> dict:
    """경로 정하기. LLM 경로는 화이트리스트 값만 받는다.

    - 개인 지목·장소가 애매한 질문(intake 가 막음)은 LLM 을 부르지 않고 out_of_scope 그대로
    - LLM 에는 개인정보를 가린 질문만 보낸다. 가린 개수만 llm_log 에 남기고 원문은 남기지 않음
    - 규칙이 질문을 정규화하지 못했을 때만 LLM 이 낸 요청을 (Pydantic 검증 후) 쓴다
    - 규칙이 정규화한 요청은 LLM 이 바꾸지 못한다. 다만 규칙이 비운 동·기간 칸만 LLM 값(enum)으로 채운다 (llm_fill)
    - 경로는 올리기만(lookup → investigate) 받고, 낮추기(investigate → lookup / out_of_scope)·범위 밖으로 돌리기는
      무시한다. 의견이 다르면 llm_log 에 기록
    - 경로가 정해지면 scope_request 로 동·기간을 경로에 맞게 반영 (조사: 등급은 도시 전체·기준일, 조회: 창)
    """
    question, request = state.get("question") or "", state.get("request")
    if (state.get("abstain_reason") or "").startswith(_NO_LLM_CODES):
        return {"route": "out_of_scope"}
    route = rule_route(question, request)
    llm, init_rec = _llm(state)
    masked, n_masked = G.mask_pii(question)
    out, rec = call_llm(llm, "classify", llm.classify if llm else None, masked, request)
    if rec is not None:
        rec["question_masked"] = n_masked
    upd: dict[str, Any] = {}
    llm_route = out.get("route") if isinstance(out, dict) else None
    llm_req = dict(out.get("request") or {}) if isinstance(out, dict) else {}
    extra = {k: llm_req.pop(k, None) for k in ("adm_names", "period")}
    if request is None and llm_route in ("lookup", "investigate") and llm_req:
        try:
            base = {**llm_req, "period_to": _as_of(state)}
            if not base.get("city") and extra["adm_names"]:  # 도시 없이 동만 → 동의 도시
                cities = {d.city for d in dongs() if d.name in extra["adm_names"]}
                base["city"] = next(iter(cities)) if len(cities) == 1 else None
            req = NormalizedRequest(**base).model_dump(mode="json")
            request = NormalizedRequest(**llm_fill(req, extra, rec)).model_dump(mode="json")
            route = llm_route
        except (ValidationError, TypeError):
            pass
    elif request is not None:
        if any(extra.values()):
            request = NormalizedRequest(**llm_fill(request, extra, rec)).model_dump(mode="json")
        if llm_route in ROUTES and llm_route != route:
            adopted = llm_route if _ROUTE_RANK[llm_route] > _ROUTE_RANK[route] else route
            if rec is not None:
                rec["route_disagreement"] = {"rule": route, "llm": llm_route, "adopted": adopted,
                                             "why": "올리기만 받음" if adopted == llm_route else "낮추기는 무시 (규칙 경로 유지)"}
            route = adopted
    if request is not None and route in ("lookup", "investigate"):
        request, why = scope_request(request, route, _as_of(state))
        upd["request"] = request
        if why:
            upd["abstain_reason"] = why
    upd.update({"route": route, "llm_log": _log(state, init_rec, rec)})
    if route == "out_of_scope":
        upd["abstain_reason"] = "OUT_OF_SCOPE: 질문을 도시·지표·집단으로 정규화할 수 없음"
    return upd


# ---------------------------------------------------------------- check_availability


def _time_items(source: str, req: NormalizedRequest):
    """기간 안 몇 날짜(달)의 공개 시점. 질문 기간이 있으면 그 기간 첫날도 본다 (지난주처럼 끝이 공급 지연 안일 때)."""
    t = grain_contract()["sources"][source]["time"]
    if t["resolution"] == "month":
        y, m = req.period_from.year, req.period_from.month
        while (y, m) <= (req.period_to.year, req.period_to.month):
            yield availability(source, base_month=f"{y}{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    elif t["resolution"] == "day":
        if req.period_label:
            yield availability(source, day=req.period_from)
        for back in (14, 7, 0):
            d = req.period_to - timedelta(days=back)
            if d >= req.period_from:
                yield availability(source, day=d)


def _models(state: dict) -> list[Provenance]:
    """조사 경로가 쓰는 규칙·모델의 시점 (G-13)."""
    if state.get("route") != "investigate":
        return []
    as_of = _as_of(state)
    if _backend(state) == "stub":
        return [Provenance("가짜 기대 활동 모델 (stub)", trained_until=as_of - timedelta(days=14))]
    # 경보 기준값은 과거만 쓰지만, 임계값(z<-2.5, 7일 중 3일)은 2025-07~12 자료 평가(EDA-6)로 정함 → 사후 참고
    return [Provenance("EDA-6 경보 규칙 임계값", trained_until=as_of - timedelta(days=1),
                       rule_derived_from=(date(2025, 7, 1), date(2025, 12, 31)))]


def check_availability(state: dict) -> dict:
    """모든 경로에서 실행: 차원(G-01)·결합 단위(G-02)·연령 계약·시간 계약(G-13).
    정기 감시가 두 도시면 도시마다 검사하고, 결과가 다르면 사유 앞에 도시를 붙여 합친다."""
    req = _req(state)
    cities = _cities(state) if state.get("mode") == "monitoring" else []
    if len(cities) > 1:
        results = {c: _contract(state, req_with_city(req, c)) for c in cities}
        first = next(iter(results.values()))
        if all(r == first for r in results.values()):
            cc = first
        else:
            issues = [i.model_copy(update={"message": f"[{c}] {i.message}"}) for c, r in results.items() for i in r.issues]
            cc = first.model_copy(update={
                "status": "ERROR" if any(r.status == "ERROR" for r in results.values()) else "OK", "issues": issues,
                "warnings": list(dict.fromkeys(w for r in results.values() for w in r.warnings))})
    else:
        cc = _contract(state, req)
    upd: dict[str, Any] = {"contract_check": cc.model_dump(mode="json")}
    if cc.status == "ERROR":
        upd["abstain_reason"] = "; ".join(f"{i.code}: {i.message}" for i in cc.issues)
    return upd


def _contract(state: dict, req: NormalizedRequest) -> ContractResult:
    as_of, mode = _as_of(state), state["analysis_mode"]
    grain = check_request_contract(req)
    if grain.status == "ERROR":
        cc = grain
    else:
        tc = check_time_contract(list(_time_items(grain.source, req)), as_of, mode, models=_models(state),
                                 required_sources={grain.source} if grain.source != "resources" else set())
        cc = ContractResult(
            status=tc.result.status,
            source=grain.source,
            issues=tc.result.issues,
            analysis_cohort=grain.analysis_cohort,
            service_eligibility_group=grain.service_eligibility_group,
            measured_grain=grain.measured_grain,
            warnings=list(dict.fromkeys(grain.warnings + tc.result.warnings)),
        )
    return cc


# ---------------------------------------------------------------- lookup · collect_signals


def lookup(state: dict) -> dict:
    req = _req(state)
    source = (state.get("contract_check") or {}).get("source")
    tool = CARD1_LOOKUP_TOOL if source == "card1" else LOOKUP_TOOL.get(req.metric)
    rev = (state.get("evidence_revision") or 0) + 1
    if tool is None:
        return {"abstain_reason": f"EMPTY: {req.metric} 조회 도구 없음", "evidence_revision": rev}
    params = {"by_adm": True} if (tool == "get_mobility_layer" and _backend(state) == "real") else {}
    ctx = _ctx(state, rev)
    results = call_tool(tool, ctx, **params)
    if len(req.adm_cds) > 1:  # 질문한 동이 여럿: 그 동들과 도시 단위 근거만
        results = [r for r in results if _adm_of(r) is None or _adm_of(r) in req.adm_cds] or results
    upd: dict[str, Any] = {
        "evidence": _dump(results),
        "evidence_revision": rev,
        "tools_run": [tool],
        "errors": _errors(state, results),
        "tool_timings": _timings(state, ctx),
    }
    if any(r.status == "ERROR" for r in results):
        bad = next(r for r in results if r.status == "ERROR")
        upd["abstain_reason"] = f"{bad.error_code}: 조회 도구 오류 ({bad.metric})"
    elif all(r.status == "EMPTY" for r in results):
        upd["abstain_reason"] = f"EMPTY: 조회 결과 없음 ({results[0].metric})"
    return upd


def required_signal_tools() -> list[str]:
    """후보 종류별 필수 근거 도구 (LLM 선택과 무관하게 항상 실행)."""
    names: list[str] = []
    for t in CANDIDATE_TYPES:
        for n in required_tools(t):
            if n not in names:
                names.append(n)
    return names


def fixed_tools(state: dict) -> list[str]:
    """LLM 과 무관하게 실행하는 도구: 필수 근거 + 동절기 점검 필수(춘천 11~2월) + 도시별 고정 맥락."""
    names = required_signal_tools()
    month = _as_of(state).month
    for city in _cities(state):
        if city == "춘천" and month in S.WINTER_MONTHS:
            names += [t for t in WINTER_TOOLS if t not in names]
        if _backend(state) == "real":
            names += [t for t in CONTEXT_TOOLS.get(city, ()) if t not in names]
    if _backend(state) == "real" and HOLIDAY_WATCH_TOOL not in names and in_holiday_watch(state):
        names.append(HOLIDAY_WATCH_TOOL)
    if _backend(state) == "real" and "춘천" in _cities(state) and month in S.WINTER_MONTHS:
        names.append(WINTER_CELL_TOOL)  # rules-v2.0 동절기: 읍·면 60대 이상 칸
    return names


def in_holiday_watch(state: dict) -> bool:
    """기준일이 어느 도시든 연휴 주의 기간인가 (rules_v2.yaml holiday_watch)."""
    from bigcontest.agent.tools.datasource import open_datasource
    from bigcontest.holiday_watch import holiday_phase

    blocks = S.holiday_blocks(open_datasource(state["snapshot_id"]))
    return any(holiday_phase(blocks, _as_of(state), c) for c in _cities(state))


def collect_signals(state: dict) -> dict:
    """필수 근거 도구는 항상. 추가 도구는 첫 회차엔 LLM 이 고르고(없으면 없음), 추가 조사 회차엔
    plan_more 가 만든 계획(담당자 focus)을 실행한다. 계획의 도구는 집중 후보의 도시에서만 돈다.
    추가 조사 회차에는 지난 회차의 추가 도구도 같은 도시에서 다시 돌린다 (지난 근거가 말없이 빠지지 않게).
    담당자가 요청한 도구(계획)가 낸 근거 ID 는 따로 남겨 후보 카드에 넣는다 (scoped_evidence)."""
    rev = (state.get("evidence_revision") or 0) + 1
    required = fixed_tools(state)
    available = [t for t in EXTRA_TOOLS if t not in required]
    plan = state.get("extra_plan") or {}
    init_rec = rec = None
    if plan.get("tools") or plan.get("candidate_ids") or plan.get("counter_checks"):
        extra = [t for t in dict.fromkeys(plan.get("tools") or []) if t in available]
        requested = set(extra)
    else:
        llm, init_rec = _llm(state)
        chosen, rec = call_llm(llm, "choose_extra_tools", llm.choose_extra_tools if llm else None,
                               state.get("request"), available, required)
        # 화이트리스트 밖 이름·필수 도구 이름은 버림 (필수 도구는 이미 실행 목록에 있음)
        extra = [t for t in dict.fromkeys(chosen or []) if t in available]
        requested = set()
    plan_cities = set(plan.get("cities") or [])
    cities = _cities(state)
    runs = [{"tool": t, "city": c, "requested": t in requested} for c in cities for t in extra
            if not (t in requested and plan_cities) or c in plan_cities]
    if state.get("prev_round"):  # 추가 조사 회차: 지난 회차 추가 도구를 다시 (요청 표시는 이어받음)
        seen = {(r["tool"], r["city"]): r for r in runs}
        for r in state.get("extra_runs") or []:
            if r["tool"] in available and r["city"] in cities:
                if (r["tool"], r["city"]) in seen:
                    seen[(r["tool"], r["city"])]["requested"] |= bool(r.get("requested"))
                else:
                    seen[(r["tool"], r["city"])] = dict(r)
        runs = list(seen.values())
    tools = required + list(dict.fromkeys(r["tool"] for r in runs))
    evidence: list[ToolResult] = []
    asked: list[str] = []
    ctxs = []
    made_by: dict[str, str] = {}  # 근거 ID → "도구|도시" (Guard 처치: 실패한 도구만 다시)
    quarantined: list[dict] = []
    for city in cities:
        ctx = _ctx(state, rev, city)
        ctxs.append(ctx)
        for name in required:
            got = call_tool(name, ctx)
            evidence += got
            made_by.update({e.evidence_id: f"{name}|{city}" for e in got})
        for r in (r for r in runs if r["city"] == city):
            got = call_tool(r["tool"], ctx)
            # 선택 도구(LLM·담당자가 고른 추가 도구)의 작은 칸은 격리: 그 근거만 빼고 실행은 계속 (K-05).
            # 필수 근거의 작은 칸은 그대로 신호 Guard 가 보류. 폐기 지표 등은 Guard 처치(drop_evidence)가 뺌
            got, bad = quarantine_split(got, small_cell_problems)
            quarantined += _quarantine_records(bad, "collect_signals", {b.evidence_id: r["tool"] for b, _ in bad})
            evidence += got
            made_by.update({e.evidence_id: f"{r['tool']}|{city}" for e in got})
            if r["requested"]:
                asked += [e.evidence_id for e in got]
    return {
        "quarantine": quarantined,
        "evidence": _dump(evidence),
        "evidence_revision": rev,
        "evidence_tools": made_by,
        "remedy": None,
        "tools_run": tools,
        "extra_runs": runs,
        "requested_evidence_ids": list(dict.fromkeys(asked)),
        "errors": _errors(state, evidence),
        "llm_log": _log(state, init_rec, rec),
        "tool_timings": _timings(state, *ctxs),
    }


# ---------------------------------------------------------------- 추가 조사 계획

# 확인할 반증 → 그것을 더 보는 추가 도구 (없으면 필수 반증 결과를 보고서에 다시 보여 줌)
COUNTER_TOOL = {"공휴일 기대값": "calc_holiday_gap"}
COUNTER_CHECKS = ("공휴일 기대값", "도시 공통요인", "강수", "학사일정")
# LLM 이 없을 때 메모 → 도구·반증 (규칙)
MEMO_TOOL_RULES = (
    (r"연휴|명절|추석|설날|공휴일", "calc_holiday_gap"),
    (r"겨울|동절기|한파|추위|읍|면 지역", "calc_winter_contraction"),
    (r"폭염|더위|여름", "calc_heat_response"),
    (r"체류|유동|이동|통신|생활인구|외출", "get_mobility_layer"),
    (r"시민생활|외로움|소통", "get_validation_reference"),
)
MEMO_COUNTER_RULES = (
    (r"강수|비가|비 |눈|날씨", "강수"),
    (r"공휴일|연휴|명절", "공휴일 기대값"),
    (r"도시 전체|도시 공통|공통", "도시 공통요인"),
    (r"학사|방학|개학|학교", "학사일정"),
)
MAX_PLAN_TOOLS = 5


def memo_plan(memo: str | None) -> tuple[list[str], list[str]]:
    """규칙: 메모 낱말 → (추가 도구, 확인할 반증). LLM 이 없거나 실패할 때 씀."""
    memo = memo or ""
    tools = [t for pat, t in MEMO_TOOL_RULES if re.search(pat, memo)]
    checks = [c for pat, c in MEMO_COUNTER_RULES if re.search(pat, memo)]
    return list(dict.fromkeys(tools)), list(dict.fromkeys(checks))


def make_plan(state: dict, focus: dict | None) -> tuple[dict, list[dict]]:
    """담당자 focus(후보·도구·반증·메모) → (추가 조사 계획, LLM 기록). 상태는 바꾸지 않는다.
    메모는 LLM 구조화 출력(화이트리스트만)으로 바꾸고, LLM 이 없거나 실패하면 규칙(memo_plan).
    개인 지목 메모는 쓰지 않는다. 계획에는 메모 원문을 넣지 않는다 (쓴 여부만).
    API 는 추가 조사를 받을 때 이 함수로 계획을 먼저 정해 저장하고, 그래프 plan_more 는 그 계획을 그대로 쓴다."""
    focus = dict(focus or {})
    memo = focus.pop("memo", None)
    tools = [t for t in focus.get("tools") or [] if t in EXTRA_TOOLS]
    checks = [c for c in focus.get("counter_checks") or [] if c in COUNTER_CHECKS]
    source, reason, recs, memo_note = ("human" if (tools or checks) else "none"), None, [], None
    if memo and G.individual_target(memo):
        memo, memo_note = None, "개인 지목 메모는 쓰지 않음"
    if memo:
        llm, init_rec = _llm(state)
        masked, n_masked = G.mask_pii(memo)
        cands = [{k: c.get(k) for k in ("candidate_id", "candidate_type", "city", "candidate_level")}
                 for c in state.get("candidates") or []][:20]
        out, rec = call_llm(llm, "plan_more", llm.plan_more if llm else None, masked, list(EXTRA_TOOLS),
                            list(COUNTER_CHECKS), cands)
        if rec is not None:
            rec["memo_masked"] = n_masked
        recs = [r for r in (init_rec, rec) if r]
        if isinstance(out, dict) and rec and rec.get("ok"):
            tools += [t for t in out.get("tools") or [] if t in EXTRA_TOOLS]  # 화이트리스트 밖 이름은 버림
            checks += [c for c in out.get("counter_checks") or [] if c in COUNTER_CHECKS]
            source, reason = "llm", out.get("reason")
        else:
            mt, mc = memo_plan(memo)
            tools, checks = tools + mt, checks + mc
            source, reason = "rule", "메모 낱말 규칙 (LLM 없음·실패)"
    tools += [COUNTER_TOOL[c] for c in checks if c in COUNTER_TOOL]
    known = {c["candidate_id"]: c for c in state.get("candidates") or []}
    cids = [c for c in focus.get("candidate_ids") or [] if c in known]
    plan = {
        "tools": list(dict.fromkeys(tools))[:MAX_PLAN_TOOLS],
        "counter_checks": list(dict.fromkeys(checks)),
        "candidate_ids": cids,
        "cities": sorted({known[c]["city"] for c in cids}),
        "source": source,
        "reason": str(reason or memo_note or "담당자가 고른 범위")[:200],
        "memo_used": bool(memo),
    }
    return plan, recs


def _checked_plan(state: dict, plan: dict) -> dict:
    """API 가 미리 정한 계획을 다시 화이트리스트·이 실행 후보로 거름 (저장된 값을 그대로 믿지 않음)."""
    known = {c["candidate_id"]: c for c in state.get("candidates") or []}
    cids = [c for c in plan.get("candidate_ids") or [] if c in known]
    return {
        "tools": [t for t in dict.fromkeys(plan.get("tools") or []) if t in EXTRA_TOOLS][:MAX_PLAN_TOOLS],
        "counter_checks": [c for c in dict.fromkeys(plan.get("counter_checks") or []) if c in COUNTER_CHECKS],
        "candidate_ids": cids,
        "cities": sorted({known[c]["city"] for c in cids}),
        "source": plan.get("source") if plan.get("source") in ("human", "llm", "rule", "none") else "none",
        "reason": str(plan.get("reason") or "담당자가 고른 범위")[:200],
        "memo_used": bool(plan.get("memo_used")),
    }


def plan_more(state: dict) -> dict:
    """추가 조사 계획: 담당자 focus → 다음 회차에 더 돌릴 도구 (make_plan).
    API 가 받을 때 정해 둔 계획(focus.plan)이 있으면 그것을 그대로 쓴다 (LLM 을 다시 부르지 않음).
    메모 원문은 상태에서 지운다 (계획에는 쓴 여부만)."""
    focus = dict(state.get("focus") or {})
    given = focus.pop("plan", None)
    given_log = [r for r in focus.pop("plan_llm_log", None) or [] if isinstance(r, dict)]
    if isinstance(given, dict):
        plan, recs = _checked_plan(state, given), given_log
    else:
        plan, recs = make_plan(state, focus)
    return {"extra_plan": plan, "focus": {**focus, "memo": None} if focus else None,
            "llm_log": _log(state, *recs)}


def _ev_key(e: dict) -> str:
    return "|".join(str(e.get(k)) for k in ("metric", "applied_scope", "analysis_cohort", "observed_to"))


def round_summary(state: dict) -> dict:
    """회차 비교용 요약: 근거 열쇠(지표·범위·코호트·관측 끝) → [값, 상태], 후보 열쇠 → 등급, 실행한 도구.
    evidence 는 카드에 넣은 근거(후보로 연 조사면 그 후보 카드), state_evidence 는 상태의 근거 전부."""
    evs = state.get("evidence") or []
    every = {_ev_key(e): [e.get("value"), e.get("status")] for e in evs}
    ids = (state.get("evidence_card") or {}).get("evidence_ids")
    shown = every if ids is None else {_ev_key(e): [e.get("value"), e.get("status")]
                                       for e in evs if e.get("evidence_id") in set(ids)}
    levels = {_cand_key(c): c.get("candidate_level") for c in state.get("candidates") or []}
    return {"round": (state.get("extra_rounds") or 0) + 1, "evidence": shown, "state_evidence": every,
            "levels": levels, "tools": list(state.get("tools_run") or [])}


def _cand_key(c: dict) -> str:
    return "|".join(str(c.get(k)) for k in ("candidate_type", "city", "adm_cd", "analysis_cohort"))


def _cand_label(key: str) -> str:
    ctype, city, adm, cohort = key.split("|", 3)
    where = f"{city} {adm}" if adm not in ("None", "") else f"{city} (도시 단위)"
    return f"{TYPE_KO.get(ctype, ctype)} {where} {cohort}"


def round_changes(state: dict) -> list[str]:
    """지난 회차 대비 달라진 점 (템플릿). 근거 ID 는 넣지 않음 (사실 검사 대상 문장이 아님)."""
    prev = state.get("prev_round")
    if not prev:
        return []
    n = prev["round"]
    plan = state.get("extra_plan") or {}
    cur = round_summary(state)
    new = [k for k in cur["evidence"] if k not in prev["evidence"]]
    changed = [k for k in cur["evidence"] if k in prev["evidence"] and cur["evidence"][k] != prev["evidence"][k]]
    gone = [k for k in prev["evidence"] if k not in cur["evidence"]]
    prev_all = prev.get("state_evidence") or prev["evidence"]
    hidden = [k for k in cur["state_evidence"] if k not in prev_all and k not in cur["evidence"]]
    src = {"llm": "LLM 이 메모를 도구로 바꿈", "rule": "메모 낱말 규칙", "human": "담당자가 고름"}.get(
        plan.get("source"), "범위 지정 없음")
    out = [f"{n}회차 대비 달라진 점: 추가 도구 {', '.join(plan.get('tools') or []) or '없음'} 실행 ({src})"]

    def names(keys: list[str]) -> str:
        metrics = list(dict.fromkeys(k.split("|")[0] for k in keys))
        return f"{', '.join(metrics[:4])}{' 등' if len(metrics) > 4 else ''}"

    if new:
        out.append(f"{n}회차 대비 새 근거 {len(new)}건 (근거 표에 넣음): {names(new)}")
    else:
        out.append(f"{n}회차 대비 새 근거 없음")
    if hidden:
        out.append(f"근거 표에 넣지 않은 새 근거 {len(hidden)}건 (다른 동·다른 코호트, 상태에만 있음): {names(hidden)}")
    if changed:
        out.append(f"{n}회차 대비 값이 바뀐 근거 {len(changed)}건: {names(changed)}")
    if gone:
        why = [f"{k.split('|')[0]} ({'근거 표 범위 밖, 상태에는 있음' if k in cur['state_evidence'] else '이번 회차 도구 결과에 없음'})"
               for k in gone]
        out.append(f"{n}회차 대비 빠진 근거 {len(gone)}건: " + "; ".join(list(dict.fromkeys(why))[:4]))
    moved = [f"{_cand_label(k)} {prev['levels'][k]} → {lv}" for k, lv in cur["levels"].items()
             if k in prev["levels"] and prev["levels"][k] != lv]
    out.append(f"{n}회차 대비 등급이 바뀐 후보: " + "; ".join(moved[:5]) if moved
               else f"{n}회차 대비 등급이 바뀐 후보 없음 (등급은 규칙이 정함)")
    cands = {c["candidate_id"]: c for c in state.get("candidates") or []}
    for cid in plan.get("candidate_ids") or []:
        c = cands.get(cid)
        if c:
            out.append(f"집중 확인 {cid}: {c.get('candidate_level')} ({clip_reason(c.get('level_reason') or '-')})")
    devs = [c for c in cands.values() if c["candidate_type"] == "unexpected_activity_deviation"
            and (not plan.get("candidate_ids") or c["candidate_id"] in plan["candidate_ids"])]
    for chk in plan.get("counter_checks") or []:
        if devs:
            out.append(f"반증 '{chk}' 확인: " + "; ".join(f"{c['candidate_id']} 반증 {c['counter_result']}"
                                                          for c in devs[:4]))
        else:
            out.append(f"반증 '{chk}': 이탈 후보가 없어 해당 없음")
    if plan.get("memo_used"):
        out.append("담당자 메모는 도구 계획으로만 씀 (원문은 보고서에 넣지 않음)")
    return out


# ---------------------------------------------------------------- 후보 · 반증 · 등급


def clip_reason(text: str, limit: int = 120) -> str:
    """등급 이유를 짧게: " + " 로 이은 조각 단위로 자름 (낱말 중간에서 자르지 않음). 뺀 조각이 있으면 " 외"."""
    if len(text) <= limit:
        return text
    parts = text.split(" + ")
    kept: list[str] = []
    for p in parts:
        if kept and len(" + ".join(kept + [p])) > limit:
            break
        kept.append(p)
    out = " + ".join(kept)
    if len(out) > limit:  # 첫 조각부터 길면 마지막 띄어쓰기에서
        cut = out[:limit].rsplit(" ", 1)[0].rstrip(" ,·(")
        return cut + " …"
    return out + (" 외" if len(kept) < len(parts) else "")


def classify_candidate(state: dict) -> dict:
    """예정된 점검 / 예상 밖 이탈 후보 + 필수 근거 충족 여부 (후보마다, tools.signal.classify_candidate_type)."""
    out = S.classify_candidate_type(_evidence(state), _req(state), _as_of(state), state.get("contract_check"),
                                    _cities(state))
    return {"candidates": _dump(out)}


def counter_evidence(state: dict) -> dict:
    """예상 밖 이탈 후보만 반증. 예정된 점검은 '해당 없음' 그대로. 후보마다 자기 경보의 반증 근거로 기록."""
    evidence = _evidence(state)
    out = []
    for c in _candidates(state):
        if c.candidate_type == "unexpected_activity_deviation":
            c.counter_result, c.counter_reason = S.judge_counter(c, S.confounders_for(c, evidence))
        out.append(Candidate(**c.model_dump()))
    return {"candidates": _dump(out)}


def _inv_evidence(state: dict) -> list[ToolResult]:
    return [ToolResult(**e) for e in state.get("investigation_evidence") or []]


def guard_signal(state: dict) -> dict:
    """신호 Guard + 규칙별 처치 (guard_rules.yaml remedy, AG-04). 전체 다시 수집은 하지 않는다.

    - drop_evidence (G-11 폐기 지표, G-14 지표 뜻 바꾸기): 걸린 근거만 빼고 다시 검사. Guard 횟수를 쓰지 않음.
      후보가 등급 근거로 쓴 근거면 뺄 수 없어 판단 보류
    - retry_failed_tool (G-06 다시 시도할 수 있는 반증 오류): 실패한 도구만 다시 (retry_tools). 횟수 한도 max_revise
    - abstain (G-08 작은 칸, G-13 기준일 뒤 자료 등): 바로 판단 보류
    조사 루프 근거(investigation_evidence)도 작은 칸·폐기 지표·지표 뜻 검사를 받는다."""
    used = (state.get("revise_count") or {}).get("guard_signal", 0)
    evidence, extra = _evidence(state), _inv_evidence(state)
    cands = state.get("candidates") or []
    gr = G.check_signal(evidence, cands, state["analysis_mode"], _as_of(state),
                        abstain_reason=state.get("abstain_reason"), attempt=used + 1, extra=extra)
    allow = state.get("route") != "lookup"
    upd: dict[str, Any] = {"guard": list(state.get("guard") or []) + [gr.model_dump(mode="json")], "remedy": None}
    remedy = G.pick_remedy(gr.rule_ids, "guard_signal") if gr.verdict == "REVISE" and allow else None
    why_no = "검사에서 바로 판단 보류"
    if remedy == "drop_evidence":
        targets = G.drop_targets(evidence + extra)
        used_by = {i for c in cands for i in c.get("evidence_ids") or []}
        if targets and not set(targets) & used_by:
            upd.update(verdict="REVISE", remedy={"remedy": remedy, "rule_ids": sorted(set(targets.values())),
                                                 "targets": targets})
            return upd
        remedy = "abstain"
        why_no = "문제 근거 빼기 불가: 후보가 등급 근거로 쓴 근거라 뺄 수 없음"
    elif remedy == "retry_failed_tool":
        _, tool_over = B.state_check(state)
        targets = G.retry_targets(evidence)
        why_no = (f"{B.BUDGET_EXCEEDED}: {tool_over}" if tool_over
                  else "실패한 도구 다시 실행 불가: 다시 시도할 수 있는 오류로 실패한 도구가 없음")
        if targets and not tool_over:
            verdict, why = G.decide(gr, used, allow_revise=True)
            upd["verdict"] = verdict
            if verdict == "REVISE":
                upd["revise_count"] = {**state.get("revise_count", {}), "guard_signal": used + 1}
                upd["remedy"] = {"remedy": remedy, "rule_ids": gr.rule_ids, "targets": targets}
            else:
                upd["abstain_reason"] = state.get("abstain_reason") or why
            return upd
        remedy = "abstain"
    verdict, why = G.decide(gr, used, allow_revise=allow)
    if remedy == "abstain" and verdict == "REVISE":
        verdict, why = "ABSTAIN", f"{why_no}: " + "; ".join(gr.reasons)
    upd["verdict"] = verdict
    if verdict == "REVISE":  # 처치를 정하지 못한 REVISE (예전 체크포인트) → 예전처럼 다시 수집
        upd["revise_count"] = {**state.get("revise_count", {}), "guard_signal": used + 1}
    if verdict == "ABSTAIN":
        upd["abstain_reason"] = state.get("abstain_reason") or why
        if remedy == "abstain":
            upd["remedy"] = {"remedy": "abstain", "rule_ids": gr.rule_ids, "targets": {}}
    return upd


def drop_evidence(state: dict) -> dict:
    """처치 drop_evidence: Guard 가 걸은 근거(폐기 지표 등)만 상태에서 빼고 경고를 남김. 다시 guard_signal 로."""
    targets: dict[str, str] = dict((state.get("remedy") or {}).get("targets") or {})
    keep = [e for e in state.get("evidence") or [] if e["evidence_id"] not in targets]
    keep_inv = [e for e in state.get("investigation_evidence") or [] if e["evidence_id"] not in targets]
    gone = [e for e in (state.get("evidence") or []) + (state.get("investigation_evidence") or [])
            if e["evidence_id"] in targets]
    errors = list(state.get("errors") or [])
    for e in gone:
        item = {"evidence_id": e["evidence_id"], "metric": e.get("metric") or "",
                "error_code": f"{targets[e['evidence_id']]} 처치: 근거에서 뺌"}
        if item not in errors:
            errors.append(item)
    upd: dict[str, Any] = {"evidence": keep, "investigation_evidence": keep_inv, "errors": errors, "verdict": None}
    inv = state.get("investigation")
    if inv:
        items = []
        for it in inv.get("items") or []:
            if set(it.get("evidence_ids") or []) & set(targets):
                rule = next(targets[i] for i in it["evidence_ids"] if i in targets)
                it = {**it, "status": "dropped", "finding": f"Guard {rule} 가 이 근거를 뺌 (폐기 지표·지표 뜻 바꾸기)",
                      "evidence_ids": [i for i in it["evidence_ids"] if i not in targets]}
            items.append(it)
        upd["investigation"] = {**inv, "items": items}
    return upd


def retry_tools(state: dict) -> dict:
    """처치 retry_failed_tool: 다시 시도할 수 있는 오류(시간 초과·입출력)로 실패한 도구만 같은 도시에서 다시 부른다.
    LLM 도구 선택은 다시 하지 않는다. 그 도구가 낸 옛 근거는 새 결과로 바꾼다. 다음은 후보 분류부터 다시 (등급 입력이 바뀜)."""
    rev = (state.get("evidence_revision") or 0) + 1
    made_by = dict(state.get("evidence_tools") or {})
    failed = list(((state.get("remedy") or {}).get("targets")) or G.retry_targets(_evidence(state)))
    pairs = list(dict.fromkeys(made_by[i] for i in failed if i in made_by))
    old = [e for e in state.get("evidence") or [] if made_by.get(e["evidence_id"]) not in pairs]
    new: list[ToolResult] = []
    ctxs = []
    for pair in pairs:
        tool, city = pair.split("|", 1)
        ctx = _ctx(state, rev, city)
        ctxs.append(ctx)
        got = call_tool(tool, ctx)
        new += got
        made_by.update({e.evidence_id: pair for e in got})
    return {
        "evidence": old + _dump(new),
        "evidence_revision": rev,
        "evidence_tools": made_by,
        "errors": _errors(state, new),
        "tool_timings": _timings(state, *ctxs, phase="retry"),
        "verdict": None,
        "remedy": {**(state.get("remedy") or {}), "retried": [p.split("|")[0] for p in pairs]},
    }


# ---------------------------------------------------------------- 조사 루프 (investigation.py, AG-03)


def small_cell_problems(ev: ToolResult) -> list[tuple[str, str]]:
    """선택 도구 근거의 격리 사유: G-08 작은 칸·비공개 값만."""
    return [(r, w) for r, w in G.evidence_problems(ev) if r == "G-08"]


def _inv_cfg() -> dict:
    return dict((llm_config().get("slots") or {}).get("investigate") or {})


def open_questions(state: dict) -> dict:
    """관찰: 후보마다 아직 풀리지 않은 질문을 고정 목록에서 만든다 (코드). 추가 조사 회차가 바뀌면 처음부터.
    Guard 처치(retry_tools)로 다시 들어오면 이미 확인한 질문과 회차 수는 이어서 센다."""
    cfg = _inv_cfg()
    er = state.get("extra_rounds") or 0
    inv = dict(state.get("investigation") or {})
    upd: dict[str, Any] = {}
    if inv.get("extra_round") != er:
        inv = {"extra_round": er, "rounds": [], "items": []}
        upd["investigation_evidence"] = []
    inv["questions"] = INV.questions_for(_focus_candidates(state), _as_of(state), _backend(state),
                                         int(cfg.get("max_open", 8)))
    inv["stopped"] = None
    inv["continue"] = False
    upd["investigation"] = inv
    return upd


def _focus_candidates(state: dict) -> list[Candidate]:
    """조사 루프가 볼 후보: 질문한 동·후보로 연 조사·추가 조사 집중 후보가 있으면 그 후보만 (보고서에 남을 후보).
    등급·순서는 이 거르기와 무관하게 assign_queue 가 도시 전체로 정한다."""
    cands = _candidates(state)
    req = _req(state)
    ids = set(state.get("scope_candidate_ids") or []) | set((state.get("extra_plan") or {}).get("candidate_ids") or [])
    adms = set(req.adm_cds) if req is not None and state.get("mode") == "qa" else set()
    if not ids and not adms:
        return cands
    return [c for c in cands if c.candidate_id in ids or (adms & {c.adm_cd, *c.check_first_adm})]


def _cand_view(state: dict) -> list[dict]:
    keys = ("candidate_id", "candidate_type", "city", "adm_cd", "check_first_adm", "analysis_cohort", "trigger",
            "counter_result", "counter_reason", "input_status")
    return [{k: c.get(k) for k in keys} for c in state.get("candidates") or []][:20]


def investigate(state: dict) -> dict:
    """계획 + 실행: 남은 질문 중 많아야 max_picks 개를 고르고(LLM, 없거나 실패·예산 초과면 규칙) 정해진 도구를 실행한다.
    결과 근거는 investigation_evidence 에만 둔다 (등급·반증 입력인 evidence 에는 넣지 않음)."""
    cfg = _inv_cfg()
    k, max_rounds = int(cfg.get("max_picks", 3)), int(cfg.get("max_rounds", 2))
    inv = dict(state.get("investigation") or {"rounds": [], "items": [], "questions": []})
    rounds, items = list(inv.get("rounds") or []), list(inv.get("items") or [])
    tried = {i["key"] for i in items}
    open_ = [q for q in inv.get("questions") or [] if q["key"] not in tried]
    rnd = len(rounds) + 1
    if rnd > max_rounds:
        return {"investigation": {**inv, "stopped": f"확인 회차 한도 {max_rounds}회"}}
    if not open_:
        return {"investigation": {**inv, "stopped": "남은 질문 없음"}}
    llm_over, tool_over = B.state_check(state)
    if tool_over:
        return {"investigation": {**inv, "stopped": f"{B.BUDGET_EXCEEDED}: {tool_over} → 조사 멈춤 (템플릿)"}}
    already = [{"key": i["key"], "status": i["status"]} for i in items]
    llm, init_rec = (None, None) if llm_over else _llm(state)
    picks, rec, reason = None, None, None
    if llm is not None:
        out, rec = call_llm(llm, "investigate", llm.investigate, open_, _cand_view(state), already, k)
        if rec is not None and rec.get("ok") and isinstance(out, dict):
            picks, reason = INV.clean_picks(out.get("picks"), open_, k), out.get("reason")
    source = "llm" if picks is not None else "rule"
    if picks is None:
        picks = INV.rule_picks(open_, k)
        why = f"LLM 예산 초과({llm_over})" if llm_over else ("LLM 없음" if llm is None else "LLM 실패")
        reason = f"규칙: 우선순위로 고름 ({why})"
    by_key = {q["key"]: q for q in open_}
    cohort_of = {c["candidate_id"]: c.get("analysis_cohort") for c in state.get("candidates") or []}
    req = _req(state)
    new_ev: list[ToolResult] = []
    quarantined = list(state.get("quarantine") or [])
    q_seen = {(x["evidence_id"], x["phase"]) for x in quarantined}
    ctxs, cache = [], {}
    for p in picks:
        q = by_key[p["key"]]
        sig = (q["tool"], q["city"], q.get("adm_cd") if q["tool"] == "find_resources" else None)
        if sig not in cache:
            ctx = _ctx(state, None, q["city"])
            ctxs.append(ctx)
            if q["tool"] == "find_resources":
                env, found = find_resources(ctx, q.get("adm_cd"), req.group if req else None,
                                            need_type=INV.MEDICAL_NEED)
                raw, n = [env], len([r for r in found if r.get("city") == q["city"]])
            else:
                raw, n = call_tool(q["tool"], ctx), None
            # 신호 Guard 에 걸리는 조사 근거(작은 칸·폐기 지표·지표 뜻 바꾸기·도구 오류)는 격리:
            # 그 근거만 쓰지 않고 실행은 계속 (K-05). 값은 어디에도 싣지 않음
            keep, bad = quarantine_split(raw)
            cache[sig] = (keep, n, bad)
            for rec_ in _quarantine_records(bad, "investigate", {b.evidence_id: q["tool"] for b, _ in bad}):
                if (rec_["evidence_id"], rec_["phase"]) not in q_seen:
                    quarantined.append(rec_)
                    q_seen.add((rec_["evidence_id"], rec_["phase"]))
        results, n, bad = cache[sig]
        new_ev += results
        # 질문한 후보의 집단에 드는 근거만 답으로 봄 (예: 20대 남성 연휴 근거는 고령층 후보의 답이 아님)
        cohorts = {cohort_of.get(cid) for cid in q.get("candidate_ids") or []} - {None}
        fit = [r for r in results if not cohorts or any(cohort_fits(r.analysis_cohort, co) for co in cohorts)]
        bad_fit = [(r, why) for r, why in bad
                   if not cohorts or any(cohort_fits(r.analysis_cohort, co) for co in cohorts)]
        hint = INV.BY_ID[q["qid"]].metric_hint
        if bad_fit and not [r for r in fit if not hint or hint in (r.metric or "")]:
            # 답이 될 근거가 모두 격리됨 → "확인 못 함" + 사유 코드
            codes = list(dict.fromkeys(c for _, why in bad_fit for c, _ in why))
            status, finding, ids = INV.quarantined(codes)
            reason_code = codes[0]
        else:
            status, finding, ids = INV.judge(q, fit, n)
            reason_code = None
        items.append({**{f: q.get(f) for f in ("key", "qid", "label", "question", "city", "adm_cd", "candidate_ids",
                                                 "tool")},
                      "round": rnd, "source": source, "why": p.get("why"), "status": status, "finding": finding,
                      "evidence_ids": ids, **({"reason_code": reason_code} if reason_code else {})})
    rounds.append({"round": rnd, "source": source, "reason": str(reason or "")[:200], "picked": [p["key"] for p in picks]})
    merged = list(state.get("investigation_evidence") or [])
    by_id = {e["evidence_id"]: e for e in merged}
    conflicts: list[str] = []
    for e in new_ev:
        d = e.model_dump(mode="json")
        if e.evidence_id not in by_id:
            merged.append(d)
            by_id[e.evidence_id] = d
        elif by_id[e.evidence_id] != d and e.evidence_id not in conflicts:
            conflicts.append(e.evidence_id)  # 같은 ID 에 다른 계산: 버리지 않고 오류로 남김
    # 등급 입력 근거(evidence)와 ID 가 같은데 내용이 다른 것도 오류 (K-01)
    conflicts += [i for i in id_conflicts(state.get("evidence") or [], merged) if i not in conflicts]
    stopped = None if picks else ("LLM 이 더 확인할 질문이 없다고 봄" if source == "llm" else "고를 질문 없음")
    upd = {"investigation": {**inv, "rounds": rounds, "items": items, "stopped": stopped},
           "investigation_evidence": merged, "llm_log": _log(state, init_rec, rec), "quarantine": quarantined,
           "tool_timings": _timings(state, *ctxs, phase="investigate")}
    if conflicts:
        upd["errors"] = _conflict_errors(state.get("errors") or [], conflicts, "조사 근거")
    return upd


def check_findings(state: dict) -> dict:
    """확인: 이번 회차 질문마다 풀림/여전히 모름/자료 없음/도구 오류를 세고, 못 푼 질문이 있고 남은 질문·회차·예산이
    있으면 한 회차 더 (graph.after_check_findings). 결과는 등급·반증에 쓰지 않는다."""
    cfg = _inv_cfg()
    max_rounds = int(cfg.get("max_rounds", 2))
    inv = dict(state.get("investigation") or {})
    rounds, items = inv.get("rounds") or [], inv.get("items") or []
    this = [i for i in items if rounds and i["round"] == len(rounds)]
    unresolved = [i for i in this if i["status"] != "answered"]
    tried = {i["key"] for i in items}
    remaining = [q for q in inv.get("questions") or [] if q["key"] not in tried]
    _, tool_over = B.state_check(state)
    more = bool(inv.get("stopped") is None and this and unresolved and remaining and len(rounds) < max_rounds
                and not tool_over)
    if not more and inv.get("stopped") is None:
        if not this:
            inv["stopped"] = "이번 회차에 확인한 질문 없음"
        elif not unresolved:
            inv["stopped"] = "확인한 질문이 모두 풀림"
        elif not remaining:
            inv["stopped"] = "남은 질문 없음"
        elif tool_over:
            inv["stopped"] = f"{B.BUDGET_EXCEEDED}: {tool_over}"
        else:
            inv["stopped"] = f"확인 회차 한도 {max_rounds}회"
    inv["continue"] = more
    return {"investigation": inv}


def assign_level(state: dict) -> dict:
    """후보마다 등급 (configs/rules.yaml rules-v1.1, tools.signal.assign_level). 기각 → Level 1 + 사유,
    보류 → 판단 보류. 모든 후보에 rule_id·rule_version."""
    evidence = _evidence(state)
    cands = S.assign_level(_candidates(state), evidence, as_of=_as_of(state))
    cands = apply_holiday_watch(cands, evidence, _as_of(state))
    cands = apply_winter_cells(cands, evidence, _as_of(state))
    if _backend(state) == "real":  # 남은 v1.1 판정을 rules-v2.0 칸 판정으로 (가짜 도구는 칸 근거가 없음)
        from bigcontest.agent import v2_levels as V2
        from bigcontest.agent.tools.datasource import open_datasource

        ds, mode, req = open_datasource(state["snapshot_id"]), state["analysis_mode"], _req(state)
        cands = V2.relevel_scheduled(cands, evidence, ds, _as_of(state), mode, req.group if req else None)
        cands = V2.relevel_deviation(cands, ds, _as_of(state), mode)
    return {"candidates": _dump(cands)}


HOLIDAY_TRIGGERS = ("R1", "R2", "R3")
CAP_HOLIDAY_WATCH = "CAP-HOLIDAY-WATCH"


def watch_summaries(evidence: list[ToolResult]) -> list[ToolResult]:
    return [e for e in evidence if e.metric == S.WATCH_SUMMARY_METRIC and e.status == "OK"]


def watch_cells_of(evidence: list[ToolResult]) -> dict[str, dict[str, list[ToolResult]]]:
    """연휴 주의 기간인 도시 → 주의 동 → 그 동의 주의 칸 근거 (get_holiday_watch)."""
    out: dict[str, dict[str, list[ToolResult]]] = {city_of_evidence(e): {} for e in watch_summaries(evidence)}
    for e in evidence:
        if e.metric == S.WATCH_METRIC and e.status == "OK":
            out.setdefault(city_of_evidence(e), {}).setdefault(_adm_of(e), []).append(e)
    return out


def _holiday_scheduled(c: Candidate) -> bool:
    return c.candidate_type == "scheduled_access_review" and (c.trigger or "")[:2] in HOLIDAY_TRIGGERS


def _watch_update(c: Candidate, cells: list[ToolResult]) -> dict:
    """주의 동 후보 → Level 3 (rules-v2.0 holiday_watch). 등급 사유에 칸마다 선 축."""
    from bigcontest.holiday_watch import watch_config

    axes = "; ".join(f"{e.applied_scope.split(') ')[-1]} {e.warnings[0]}" for e in cells)
    return {"candidate_level": "Level 3", "rule_id": "V2-L3-HOLIDAY-WATCH", "rule_version": "rules-v2.0 holiday_watch",
            "rule_checks": [], "evidence_confidence": "보통",
            "level_reason": f"연휴 주의 목록: {axes} + 예정된 트리거({c.trigger}). "
                            f"연휴에 끊기는 연령 근거는 {watch_config()['observed']}"}


def apply_holiday_watch(cands: list[Candidate], evidence: list[ToolResult], as_of: date) -> list[Candidate]:
    """연휴 트리거(R1·R2·R3) 후보를 연휴 주의 목록에 맞춘다 (rules_v2.yaml holiday_watch).
    - 주의 동(60대 이상 칸 근거 축 2개 이상)의 고령층 후보 → Level 3. 그 동 후보가 없으면 같은 트리거로 새로 만든다
    - 목록 밖 연휴 후보의 Level 3 → Level 2 상한 (CAP-HOLIDAY-WATCH)
    주의 기간이 아닌 도시는 그대로."""
    watch = watch_cells_of(evidence)
    if not watch:
        return cands
    out, done = [], set()
    for c in cands:
        if c.city in watch and _holiday_scheduled(c):
            if R.group_key(c) == "고령층" and c.adm_cd in watch[c.city]:
                c = c.model_copy(update=_watch_update(c, watch[c.city][c.adm_cd]))
                done.add((c.city, c.adm_cd))
            elif c.candidate_level == "Level 3":
                c = c.model_copy(update={
                    "candidate_level": "Level 2", "rule_id": f"{c.rule_id}+{CAP_HOLIDAY_WATCH}",
                    "level_reason": f"{c.level_reason or ''} · 연휴 주의 목록 밖(연휴에 끊기는 연령의 근거 축 2개 이상 칸이 "
                                    "아님) → Level 2 상한 [rules-v2.0 holiday_watch]"})
        out.append(c)
    city_level = {e.evidence_id for e in evidence if _adm_of(e) is None}
    used = {c.candidate_id for c in out}
    for city, adms in watch.items():
        tmpl = next((c for c in out if c.city == city and _holiday_scheduled(c) and R.group_key(c) == "고령층"), None)
        if tmpl is None:  # 이 도시에 고령층 연휴 후보가 없음 (다른 집단만 감시) → 블록에만 나옴
            continue
        n = S.ID_BASE.get(city, 0) + 300
        for adm, cells in adms.items():
            if (city, adm) in done:
                continue
            while (cid := f"C-{as_of:%Y%m%d}-{n:03d}") in used:
                n += 1
            used.add(cid)
            base = tmpl.model_copy(update={"candidate_id": cid, "adm_cd": adm, "context": [], "queue_rank": None,
                                           "selected_for_review": False, "input_status": "충족", "missing_inputs": [],
                                           "evidence_ids": [i for i in tmpl.evidence_ids if i in city_level]
                                           + [e.evidence_id for e in cells]})
            out.append(base.model_copy(update=_watch_update(base, cells)))
    return out


LEVEL_RANK = {"Level 1": 1, "Level 2": 2, "Level 3": 3}


def winter_cells_of(evidence: list[ToolResult]) -> dict[str, list[tuple[ToolResult, str, str]]]:
    """동절기 칸 근거 → 동 → [(근거, 등급, 규칙 id)] (get_winter_cells 경고 칸 첫 줄 'Level 3 (V2-...)')."""
    out: dict[str, list[tuple[ToolResult, str, str]]] = {}
    for e in evidence:
        if e.metric == S.WINTER_CELL_METRIC and e.status == "OK":
            m = re.match(r"^(Level \d) \((.+)\)$", e.warnings[0])
            if m:
                out.setdefault(_adm_of(e), []).append((e, m.group(1), m.group(2)))
    return out


def _winter_update(c: Candidate, cells: list[tuple[ToolResult, str, str]]) -> dict:
    e, level, rid = max(cells, key=lambda x: (LEVEL_RANK[x[1]], x[0].value))
    why = "; ".join(f"{x[0].applied_scope.split(') ')[-1]} {x[1]}, {x[0].warnings[1]}, {x[0].warnings[2]}" for x in cells)
    return {"candidate_level": level, "rule_id": rid if rid.startswith("V2-") else f"V2-{rid}",
            "rule_version": "rules-v2.0", "rule_checks": [],
            "evidence_ids": list(dict.fromkeys([*c.evidence_ids, *(x[0].evidence_id for x in cells)])),
            "level_reason": f"동절기 칸(읍·면 60대 이상, 칸 중 가장 높은 등급): {why} + 예정된 트리거({c.trigger})"}


def apply_winter_cells(cands: list[Candidate], evidence: list[ToolResult], as_of: date) -> list[Candidate]:
    """동절기 트리거(W) 후보를 rules-v2.0 칸 판정에 맞춘다 (설계 3절).
    - 고령층 후보: 그 읍·면 60대 이상 칸(남·여) 중 가장 높은 등급. 겨울 체류 감소가 아니면 칸 판정이 Level 2 상한
    - 다른 집단 후보: Level 1 (동절기 트리거는 60대 이상 칸만)
    - Level 3 칸이 있는 읍·면에 고령층 후보가 없으면 같은 트리거로 새로 만든다"""
    win = winter_cells_of(evidence)
    if not win:
        return cands
    out, done = [], set()
    for c in cands:
        if c.city == "춘천" and c.candidate_type == "scheduled_access_review" and (c.trigger or "").startswith("W"):
            if R.group_key(c) == "고령층":
                if c.adm_cd in win:
                    c = c.model_copy(update=_winter_update(c, win[c.adm_cd]))
                    done.add(c.adm_cd)
            else:
                c = c.model_copy(update={
                    "candidate_level": "Level 1", "rule_id": "V2-L1-WINTER-AGE", "rule_version": "rules-v2.0",
                    "rule_checks": [], "level_reason": "동절기 점검은 읍·면 60대 이상 칸에만 건다 [rules-v2.0 동절기]"})
        out.append(c)
    tmpl = next((c for c in out if c.city == "춘천" and (c.trigger or "").startswith("W") and R.group_key(c) == "고령층"),
                None)
    if tmpl is not None:
        city_level = {e.evidence_id for e in evidence if _adm_of(e) is None}
        used = {c.candidate_id for c in out}
        n = S.ID_BASE["춘천"] + 400
        for adm, cells in win.items():
            if adm in done or not any(lv == "Level 3" for _, lv, _ in cells):
                continue
            while (cid := f"C-{as_of:%Y%m%d}-{n:03d}") in used:
                n += 1
            used.add(cid)
            base = tmpl.model_copy(update={"candidate_id": cid, "adm_cd": adm, "context": [], "queue_rank": None,
                                           "selected_for_review": False, "input_status": "충족", "missing_inputs": [],
                                           "evidence_ids": [i for i in tmpl.evidence_ids if i in city_level]})
            out.append(base.model_copy(update=_winter_update(base, cells)))
    return out


def _join_dongs(names: list[str]) -> str:
    """['논현1동', '논현2동'] → '논현1·2동' (같은 이름에 번호만 다른 동을 묶음)."""
    groups: dict[str, list[str]] = {}
    for n in names:
        m = re.match(r"^(\D+?)(\d+)동$", n)
        groups.setdefault(m.group(1) if m else n, []).append(m.group(2) if m else "")
    return "·".join(f"{k}{'·'.join(v)}동" if v[0] else k for k, v in groups.items())


def holiday_watch_lines(evidence: list[ToolResult]) -> list[str]:
    """연휴 주의 블록 (템플릿): 주의 지역 / 근거 / 할 일 / 관측 한계. 연휴 주의 기간이 아니면 빈 목록."""
    from bigcontest.holiday_watch import watch_config

    summ = watch_summaries(evidence)
    if not summ:
        return []
    cfg = watch_config()
    label = summ[0].warnings[0]  # 예: "2025 추석 연휴 전 (10/03~10/09)"
    head = label.split(" 연휴 ")[0]
    parts, n_cells, n_dongs, follow = [], 0, 0, []
    for e in sorted(summ, key=lambda e: CITIES.index(city_of_evidence(e))):
        city = city_of_evidence(e)
        dongs = [d for d in e.warnings[1].removeprefix("동: ").split("·") if d and d != "없음"]
        n_cells += int(e.n_obs or 0)
        n_dongs += len(dongs)
        if dongs:
            parts.append(f"{city} {_join_dongs(dongs)}")
        days = (cfg.get("follow_up_days") or {}).get(city)
        if days:
            follow.append(f"{city}은 연휴 뒤 {days}일까지 이어서 확인")
    where = ", ".join(parts)
    after = " 연휴 뒤 " in label
    title = f"{head} 연휴 뒤 이어서 볼 지역" if after else f"{head} 연휴 주의 지역"
    card_basis = cfg["follow_up_card_basis"] if after else cfg["card_basis"]
    todo = ", ".join(cfg["follow_up_actions"]) if after else (
        ", ".join(cfg["actions"]) + (". " + ". ".join(follow) if follow else ""))
    lines = [f"{title}: {where}의 60대 이상 (Level 3, 주의 칸 {n_cells}개, {n_dongs}개 동, {label})" if parts
             else f"{title}: 근거 축 2개 이상인 60대 이상 칸 없음 ({label})",
             f"근거: {card_basis} + {cfg['cell_basis']}",
             f"할 일: {todo}",
             f"관측 한계: 연휴에 끊기는 연령은 {cfg['observed']}이라 아직 검증 전 ({cfg['confirm']})"]
    return lines


def assign_queue(state: dict) -> dict:
    """검토 예산 K 로 순서·배정 (rules.yaml selection: 등급 → 도시 → 종류 → 집단 → 구조 값 → adm_cd).
    등급은 바꾸지 않는다."""
    k = int(state.get("selection_budget") or 0)
    out = S.assign_queue(_candidates(state), _evidence(state), k)
    upd: dict[str, Any] = {}
    scope = state.get("scope_candidate_ids") or []
    if scope:
        # 후보로 연 조사: 등급·순서는 도시 전체 후보·근거로 정한 뒤(정기 감시와 같음) 지정한 후보만 남김
        kept = [c for c in out if c.candidate_id in scope]
        missing = [cid for cid in scope if cid not in {c.candidate_id for c in kept}]
        if missing:
            upd["errors"] = list(state.get("errors") or []) + [
                {"error_code": "SCOPE_CANDIDATE_MISSING", "candidate_ids": ", ".join(missing)}]
        if kept:
            out = kept
    req = _req(state)
    if req is not None and req.adm_cds and state.get("mode") == "qa" and state.get("route") == "investigate":
        # 질문한 동: 등급·순서는 위에서 도시 전체로 정했고, 여기서는 그 동(또는 먼저 확인할 동) 후보만 남김
        want = set(req.adm_cds)
        kept = [c for c in out if want & {c.adm_cd, *c.check_first_adm}]
        upd["place_scope"] = {"adm_cds": list(req.adm_cds), "names": [R.adm_name(a) for a in req.adm_cds],
                              "city": req.city, "city_total": len(out), "kept": len(kept)}
        out = kept
    upd["candidates"] = _dump([Candidate(**c.model_dump()) for c in out])
    return upd


def scope_sentences(state: dict) -> list[str]:
    """질문한 동·기간을 어떻게 썼는지 (템플릿). 등급은 도시 전체·기준일로 정했다는 것을 함께 적는다."""
    req = _req(state)
    out: list[str] = []
    ps = state.get("place_scope") or {}
    if ps:
        where = "·".join(f"{n} ({a})" for n, a in zip(ps["names"], ps["adm_cds"]))
        head = f"질문한 동: {ps['city']} {where}. 등급·순서는 {ps['city']} 전체 후보 {ps['city_total']}건으로 정한 뒤 이 동 후보만 남김"
        out.append(head + (f" ({ps['kept']}건)" if ps["kept"] else ". 이 기준일에 이 동 후보는 없음"))
    if req is not None and req.period_label:
        if state.get("route") == "investigate":
            out.append(f"질문의 기간 말 '{req.period_label}'은 조사 등급에 쓰지 않음 "
                       f"(등급은 기준일 {state['as_of']}까지 자료로 정함, 정기 감시와 같은 등급)")
        else:
            out.append(f"조회 기간: '{req.period_label}' → {req.period_from}~{req.period_to} "
                       f"(기준일 {state['as_of']}까지로 자름, 공급 지연이 있는 자료는 그보다 앞까지)")
    return out


def candidate_cause(c: Candidate, evidence: list[ToolResult]) -> str:
    """후보 → 원인 유형 (자원 매칭). 예정된 점검은 트리거, 관측 거점이 멀면 거점 원거리형을 앞에.
    정기 감시 화면의 자원 미리 보기(GET /resources cause_type)도 같은 함수로 정한다."""
    access = {_adm_of(e): float(e.value) for e in evidence
              if e.metric == ACCESS_METRIC and e.status == "OK" and isinstance(e.value, (int, float))}
    adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
    cause = cause_for(c.trigger if c.candidate_type == "scheduled_access_review" else None, access.get(adm),
                      S.STUB_LEVEL_RULES["access_far_km"])
    if S.economic_vulnerable(c):  # rules-v1.1: 수급자 비율 도시 안 상위 동 (강남, 표시·자원 매칭만)
        cause += f"+{S.ECONOMIC_CAUSE}"
    return cause


def match_resources(state: dict) -> dict:
    """Level 2·3 후보마다 자원: 관할 → 대상·자격 → 필요 유형 → 운영 상태 → 거리/전화형. 자격 미상은 '확인 필요'."""
    req = _req(state)
    evidence = _evidence(state)
    known = {e.evidence_id for e in evidence}
    resources: list[dict] = []
    ctxs = []
    conflicts: list[str] = []
    for c in _candidates(state):
        if c.candidate_level not in ("Level 2", "Level 3"):
            continue
        ctx = _ctx(state, city=c.city)
        ctxs.append(ctx)
        adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
        cause = candidate_cause(c, evidence)
        env, items = find_resources(ctx, adm, req.group if req else None, cause_type=cause)
        for r in items:
            if r.get("city") != c.city:
                env.warnings.append(f"관할 밖 자원 제외: {r['resource_id']} ({r.get('region')})")
                continue
            r = dict(r)
            if r.get("target_group") and c.service_eligibility_group and r["target_group"] != c.service_eligibility_group \
                    and _backend(state) == "stub":
                r["eligibility"] = "확인 필요"
            r["candidate_id"] = c.candidate_id
            resources.append(r)
        if env.evidence_id not in known:
            evidence.append(env)
            known.add(env.evidence_id)
        else:
            prev = next(e for e in evidence if e.evidence_id == env.evidence_id)
            if not same_content(prev, env) and env.evidence_id not in conflicts:
                conflicts.append(env.evidence_id)  # 같은 ID 에 다른 계산: 버리지 않고 오류로 남김 (K-01)
    upd = {"resources": resources, "evidence": _dump(evidence), "tool_timings": _timings(state, *ctxs)}
    conflicts += [i for i in id_conflicts(evidence, state.get("investigation_evidence") or []) if i not in conflicts]
    if conflicts:
        upd["errors"] = _conflict_errors(state.get("errors") or [], conflicts, "자원 근거")
    return upd


# ---------------------------------------------------------------- 근거 카드 · 보고서


def build_card(state: dict) -> dict:
    """결정론적 근거 카드. 숫자·사실은 여기서만 만든다 (tools.report.build_evidence_card)."""
    req = _req(state)
    as_of = _as_of(state)
    cities = "·".join(_cities(state)) or req.city
    evidence = _evidence(state)
    cands = _candidates(state)
    ps = state.get("place_scope") or {}
    if state.get("route") == "lookup":
        title = f"{req.city} {req.metric} 조회"
    elif ps and not _scoped(state, cands):
        adms = set(ps["adm_cds"])
        evidence = (scoped_evidence(evidence, cands, state.get("requested_evidence_ids") or []) if cands
                    else [e for e in evidence if _adm_of(e) is None or _adm_of(e) in adms])
        title = f"후보 {len(cands)}건 · {ps['city']} {'·'.join(ps['names'])} (질문한 동) · 기준일 {as_of}"
    elif _scoped(state, cands):
        evidence = scoped_evidence(evidence, cands, state.get("requested_evidence_ids") or [])
        title = (scoped_title(cands[0], as_of) if len(cands) == 1
                 else f"후보 {len(cands)}건 (지정) · {cities} · 기준일 {as_of}")
    else:
        title = f"후보 {len(cands)}건 · {cities} · 기준일 {as_of}"
    if state.get("route") != "lookup":
        evidence = evidence + investigation_card_evidence(state, evidence, cands)
    card = R.build_evidence_card(
        state["run_id"], state["snapshot_id"], state["analysis_mode"], as_of,
        max(1, state.get("evidence_revision") or 1), evidence, title,
        limitations=list((state.get("contract_check") or {}).get("warnings", [])),
        sort_desc=state.get("route") == "lookup")
    # 등급·순서와 facts가 확정된 뒤 별도 사후 참고 칸만 붙임. LLM 문장 입력에도 넣지 않음.
    from bigcontest.agent.tools.structural_risk import get_structural_risk

    if state.get("route") != "lookup":
        card.structural_risk = get_structural_risk(_ctx(state), cands)
    upd: dict[str, Any] = {"evidence_card": pack_card(card)}  # 근거는 evidence 에만, 카드에는 근거 ID
    if state.get("run_meta_dir") and _backend(state) == "real":
        meta, created = R.write_run_metadata(_ctx(state), state["run_id"], Path(state["run_meta_dir"]),
                                             thresholds=dict(S.STUB_LEVEL_RULES))
        upd["run_meta"] = {"path": str(Path(state["run_meta_dir"]) / f"{state['run_id']}.json"), "created": created}
    return upd


def investigation_card_evidence(state: dict, evidence: list[ToolResult], cands: list[Candidate]) -> list[ToolResult]:
    """조사 루프 근거 중 보고서 "추가로 확인한 것"에 인용한 것 (남은 후보와 관련된 질문만). 카드에 없는 것만.
    등급·순서가 정해진 뒤 카드에만 붙이므로 등급과 무관하다."""
    sec = INV.section(state.get("investigation"), {c.candidate_id for c in cands})
    want = {i for it in (sec or {}).get("items") or [] for i in it.get("evidence_ids") or []}
    have = {e.evidence_id for e in evidence}
    cohorts = {c.analysis_cohort for c in cands}
    base = {e.evidence_id: e for e in evidence}
    out = []
    for e in _inv_evidence(state):
        if e.evidence_id in want and e.evidence_id in base and not same_content(base[e.evidence_id], e):
            # 같은 ID 로 다른 계산을 인용하면 보고서가 엉뚱한 근거 표를 열게 됨 (K-01). 조용히 넘기지 않음
            raise EvidenceIdConflict(f"조사 근거와 등급 근거의 ID 가 같은데 내용이 다름: {e.evidence_id}")
        if e.evidence_id in want and e.evidence_id not in have \
                and (not cohorts or any(cohort_fits(e.analysis_cohort, co) for co in cohorts)):
            out.append(e)
            have.add(e.evidence_id)
    return out


def investigation_section(state: dict, card: EvidenceCard, cands: list[Candidate]) -> dict | None:
    """보고서 "추가로 확인한 것": 남은 후보와 관련된 질문만, 근거 ID 는 카드에 있는 것만 (화면에서 근거로 이동)."""
    sec = INV.section(state.get("investigation"), {c.candidate_id for c in cands})
    if not sec:
        return None
    ids = {e.evidence_id for e in card.evidence}
    sec["items"] = [{**i, "evidence_ids": [x for x in i["evidence_ids"] if x in ids]} for i in sec["items"]]
    return sec


def _scoped(state: dict, cands: list[Candidate]) -> bool:
    scope = set(state.get("scope_candidate_ids") or [])
    return bool(scope) and bool(cands) and {c.candidate_id for c in cands} <= scope


def scoped_evidence(evidence: list[ToolResult], cands: list[Candidate],
                    requested: list[str] | set[str] = ()) -> list[ToolResult]:
    """후보로 연 조사의 카드 근거: 그 후보가 쓴 근거 + 그 후보 동(먼저 확인할 동)의 근거 + 동이 없는 도시 공통 근거
    (트리거·같은 코호트의 결제 시계열·경보·반증·기대 활동 등) + 자원 묶음. 다른 동의 근거는 상태에만 남고 카드에는 넣지 않음.
    담당자가 추가 조사로 요청한 도구의 근거(requested)는 같은 도시·같은 동(또는 동 없음)이고 코호트가 후보 코호트에
    들어가면(예: 60대·70대·80대+ 후보의 "70대 여성") 넣는다."""
    ids = {i for c in cands for i in c.evidence_ids}
    adms = {a for c in cands for a in ([c.adm_cd] if c.adm_cd else []) + list(c.check_first_adm)}
    cities = {c.city for c in cands}
    cohorts = {c.analysis_cohort for c in cands}
    asked = set(requested)
    out = []
    for e in evidence:
        adm = _adm_of(e)
        same_city = (city_of_evidence(e) or next(iter(cities))) in cities
        if e.evidence_id in ids or (adm is not None and adm in adms):
            out.append(e)
        elif adm is None and same_city and e.analysis_cohort in (None, *cohorts):
            out.append(e)
        elif e.evidence_id in asked and same_city and (adm is None or adm in adms)                 and any(cohort_fits(e.analysis_cohort, c) for c in cohorts):
            out.append(e)
    return out


def _ages(label: str | None) -> set[int]:
    """코호트 이름 → 연령대 (10 단위). "60대 여성"→{60}, "60대·70대·80대+"→{60,70,80}, "60+"·"60대+"→{60,70,80}.
    80대+ 는 80 으로 셈 (카드 연령 구간의 끝)."""
    out: set[int] = set()
    for m in re.finditer(r"(\d)0\s*대?\s*(\+)?", label or ""):
        lo = int(m.group(1)) * 10
        out |= set(range(lo, 90, 10)) if m.group(2) and lo < 80 else {lo}
    return out


def cohort_fits(sub: str | None, cohort: str | None) -> bool:
    """근거 코호트(sub)가 후보 코호트 안에 드는가. 코호트 없는 근거(도시 공통)는 듦."""
    if sub is None or sub == cohort:
        return True
    a, b = _ages(sub), _ages(cohort)
    sa, sb = (set(re.findall(r"남성|여성", x or "")) or {"남성", "여성"} for x in (sub, cohort))
    return bool(a) and bool(b) and a <= b and sa <= sb


def scoped_title(c: Candidate, as_of: date) -> str:
    if c.candidate_type == "scheduled_access_review" and c.adm_cd:
        where = f"{c.city} {R.adm_name(c.adm_cd)}"
    else:
        where = f"{c.city} 가맹점 · {c.analysis_cohort}"
    return f"후보 {c.candidate_id} · {TYPE_KO[c.candidate_type]} · {where} · 기준일 {as_of}"


def candidate_sentence(c: Candidate) -> str:
    def dong(cd: str) -> str:  # "논현1동 (11230520)" (이름은 ref/adm_cd_names.csv, 모르면 코드만)
        name = R.adm_name(cd)
        return f"{name} ({cd})" if name and name != cd else cd

    if c.candidate_type == "scheduled_access_review":
        scope = f"{c.city} {dong(c.adm_cd) if c.adm_cd else '(동 미정)'}"
    else:
        scope = f"{c.city} 가맹점 · 먼저 확인할 동 {', '.join(map(dong, c.check_first_adm)) or '-'} (공유 맥락)"
    s = f"후보 {c.candidate_id} · {TYPE_KO[c.candidate_type]} · {scope} · {c.analysis_cohort}: {c.candidate_level} — {c.level_reason}"
    if c.candidate_type == "unexpected_activity_deviation" and c.counter_result != "기각":
        s += f" / 반증 {c.counter_result}: {c.counter_reason}"
    return s


def report_facts(card: EvidenceCard, cands: list[Candidate], route: str | None, limit: int = 40) -> list:
    """보고서에 넣을 사실: 조회이거나 사실이 limit 개 이하면 전부. 많으면 Level 2·3·판단 보류 후보와 이탈 후보가
    쓴 근거 + 동이 없는(도시 단위) 근거만 (근거 카드에는 모두 남음)."""
    if route == "lookup" or len(card.facts) <= limit:
        return card.facts
    want: set[str] = set()
    for c in cands:
        if c.candidate_level in ("Level 2", "Level 3", "판단 보류") or c.candidate_type == "unexpected_activity_deviation":
            want |= set(c.evidence_ids)
    ev = {e.evidence_id: e for e in card.evidence}
    out = [f for f in card.facts if f.evidence_id in want or _adm_of(ev[f.evidence_id]) is None]
    return out[:limit]


def summary_sentences(cands: list[Candidate], evidence: list[ToolResult]) -> list[str]:
    """후보 요약·접근성·탐색적 참고·한계 문장 (템플릿)."""
    out = []
    low = [c for c in cands if c.candidate_type == "scheduled_access_review" and c.candidate_level in ("Level 0", "Level 1")]
    if low:
        out.append(f"후보 요약: 예정된 점검 중 {len(low)}곳은 동을 가리키는 통신 신호가 없어 Level 1 (공공 자료만으로는 오르지 않음) [rules-v2.0]")
    far = S.STUB_LEVEL_RULES["access_far_km"]
    for city in dict.fromkeys(c.city for c in cands):
        acc = [e for e in evidence if e.metric == ACCESS_METRIC and e.status == "OK"
               and isinstance(e.value, (int, float)) and city_of_evidence(e) == city]
        if acc and all(float(e.value) < far for e in acc):
            out.append(f"접근성: {city}은 모든 동이 관측 거점(안부·관계)까지 직선 {far:g}km 미만이라 거리 공백 근거 약함 "
                       "(관측 거점 기준일 뿐 실제 공급 부족 여부는 아님)")
    if any(S.input_of(e) == "validation_reference" and e.status == "OK" for e in evidence):
        out.append("참고: 시민생활 지표는 탐색적 참고이며 등급 입력이 아님 (같은 공급자 자료, 관측 기간 미확인)")
    if any(e.customer_residence and e.status == "OK" for e in evidence):
        out.append(RESIDENCE_NOTE)
    if any(e.metric.endswith("(카드 데이터1 보조)") and e.status == "OK" for e in evidence):
        out.append(CARD1_NOTE)
    if any(e.metric == S.EXPECTED_METRIC and e.status == "EMPTY" for e in evidence):
        out.append(f"한계: {S.EXPECTED_PENDING}. 이탈 후보는 부분 판정")
    return out


def write_report(state: dict) -> dict:
    """핵심 사실 문장은 근거 카드에서 템플릿으로, 설명 문장만 LLM. LLM 이 없거나 실패하면 템플릿만."""
    card = card_of(state)
    cands = _candidates(state)
    attempt = (state.get("revise_count") or {}).get("guard_report", 0) + 1
    facts = report_facts(card, cands, state.get("route"))
    watch = holiday_watch_lines(card.evidence)  # 연휴 주의 블록을 맨 앞에 (rules_v2.yaml holiday_watch)
    sentences = watch + [G.render_fact(f) for f in facts]
    many = len(cands) > 6
    sentences += [candidate_sentence(c) for c in cands if not (
        many and c.candidate_type == "scheduled_access_review" and c.candidate_level in ("Level 0", "Level 1"))]
    sentences += summary_sentences(cands, card.evidence)
    sentences += scope_sentences(state)
    access = {_adm_of(e): float(e.value) for e in card.evidence
              if e.metric == ACCESS_METRIC and e.status == "OK" and isinstance(e.value, (int, float))}
    kept, dropped = G.check_resources(state.get("resources") or [])  # G-07 을 못 지날 자원은 대응안에 넣지 않음
    review = state.get("review") or {}
    judged = {j["resource_id"]: j["judgement"] for j in review.get("judgements") or []}
    kept = [r for r in kept if judged.get(r.get("resource_id")) != "부적합"]  # 담당자가 부적합으로 본 자원은 대응안에서 뺌
    actions = []
    for c in cands:
        adm = c.adm_cd or (c.check_first_adm[0] if c.check_first_adm else None)
        act = R.build_candidate_action(c, kept, S.STUB_LEVEL_RULES["access_far_km"], access.get(adm), _as_of(state))
        if act:  # 대응 템플릿 5칸 + 체크리스트 + 확인할 영역. 보고서에는 한 줄로
            actions.append(act)
            sentences.append(act.action_plan.text)
    if dropped:
        ids = sorted({re.search(r"추천 제외 (\S+)", d).group(1) for d in dropped if re.search(r"추천 제외 (\S+)", d)})
        res_names = {r.get("resource_id"): r.get("name") for r in state.get("resources") or []}
        shown = [res_names.get(i) or i for i in ids]  # 화면에 ID 대신 자원 이름
        sentences.append(f"자원 제외(G-07): 출처·지역·대상·확인일 중 빠진 것이 있는 자원 {len(ids)}건 "
                         f"({', '.join(shown[:6])}{' 등' if len(ids) > 6 else ''}). 확인일을 날짜로 확인하면 추천 가능")
    changes = round_changes(state)
    sentences += changes
    sentences += review_sentences(state)
    if card.time_label:
        sentences.append(card.time_label)  # G-13 가정 표시
    sentences.append(DISCLAIMER)  # G-10 최종 판단은 담당자
    llm, init_rec = _llm(state)
    brief = state.get("mode") == "monitoring"
    view = card.model_copy(update={"facts": facts, "structural_risk": []})
    feedback = [r for g in (state.get("guard") or [])[-2:] if g["guard"] == "guard_report" and g["verdict"] != "PASS"
                for r in g["reasons"] if not r.startswith("G-07")] if attempt > 1 else []
    lines, rec = call_llm(llm, "explain", llm.explain if llm else None, view,
                          state.get("candidates") or [], attempt, brief, feedback)
    rev = (state.get("report_revision") or 0) + 1
    report = Report(report_revision=rev, evidence_card=card, fact_sentences=sentences,
                    narrative="\n".join(lines) if lines else None, actions=actions,
                    level_explanations=[R.explain_level(c) for c in cands if c.rule_checks],
                    decision_trace=report_trace(state), round_changes=changes,
                    review_ids=list(review.get("review_ids") or []),
                    investigation=investigation_section(state, card, cands), holiday_watch=watch)
    return {"report": pack_report(report), "report_revision": rev, "llm_log": _log(state, init_rec, rec)}


def review_sentences(state: dict) -> list[str]:
    """담당자 검토 반영 문장. 자원 판정·확인한 근거 수·후속 확인일만. 메모 원문은 넣지 않음."""
    review = state.get("review") or {}
    if not review:
        return []
    names = {r.get("resource_id"): r.get("name") for r in state.get("resources") or []}
    out = []
    for j in review.get("judgements") or []:
        name = names.get(j["resource_id"])
        out.append(f"담당자 검토: 자원 {j['resource_id']}{f' ({name})' if name else ''} {j['judgement']}"
                   + (" → 대응안에서 뺌" if j["judgement"] == "부적합" else ""))
    if review.get("checked_evidence_ids"):
        out.append(f"담당자 검토: 확인한 근거 {len(review['checked_evidence_ids'])}건")
    if review.get("follow_up_on"):
        out.append(f"담당자 검토: 후속 확인일 {review['follow_up_on']}")
    return out


def guard_report(state: dict) -> dict:
    """보고서 Guard: 사실 검사와 표현 검사를 따로 돌리고 따로 기록. G-07 자원은 빼고 경고."""
    report = report_of(state)
    card = report.evidence_card
    used = (state.get("revise_count") or {}).get("guard_report", 0)
    facts = G.check_facts(report, card, attempt=used + 1, candidates=state.get("candidates") or [])
    expr = G.check_expression(report, card, attempt=used + 1)
    kept, notes = G.check_resources(state.get("resources") or [])
    expr.reasons = list(dict.fromkeys(expr.reasons + notes))
    verdict, why = G.decide([facts, expr], used)
    upd: dict[str, Any] = {
        "guard": list(state.get("guard") or []) + [facts.model_dump(mode="json"), expr.model_dump(mode="json")],
        "verdict": verdict,
        "resources": kept,
    }
    if verdict == "REVISE":
        upd["revise_count"] = {**state.get("revise_count", {}), "guard_report": used + 1}
    if verdict == "ABSTAIN":
        upd["abstain_reason"] = why
    return upd


def needed_data(state: dict) -> list[str]:
    codes = [i["code"] for i in (state.get("contract_check") or {}).get("issues", [])]
    reason = state.get("abstain_reason") or ""
    codes += re.findall(r"\b([A-Z][A-Z_]{3,})\b", reason)
    codes += re.findall(r"G-\d+", reason)
    out = [NEEDED_DATA[c] for c in dict.fromkeys(codes) if c in NEEDED_DATA]
    alt = alternative_hint(state)
    if alt:
        out.append(alt)
    return out or ["판단 보류 사유를 풀 자료"]


def alternative_hint(state: dict) -> str | None:
    """금지 결합으로 보류할 때 같은 질문을 결제 기준으로 볼 수 있으면 대안 안내 (D4).
    통신은 연령 표와 시간대 표가 따로라 "60대 밤 체류"는 알 수 없지만, 카드 데이터1(보조)에는 연령 × 시간대가 있다.
    다만 가맹점 기준 결제이고 거주지·체류는 모른다."""
    req = state.get("request") or {}
    codes = [i["code"] for i in (state.get("contract_check") or {}).get("issues", [])]
    if "GRAIN_FORBIDDEN_COMBO" not in codes or req.get("metric") != "체류인구" or not req.get("time_of_day"):
        return None
    if "age" not in (req.get("dimensions") or []) and "sex" not in (req.get("dimensions") or []):
        return None
    who = (f"{req['age_min']}대" if req.get("age_min") is not None and req.get("age_max") == req["age_min"] + 9
           else req.get("group") or "그 연령")
    band = {"00_05": "새벽", "06_11": "오전", "12_17": "낮", "18_23": "밤"}[req["time_of_day"]]
    return (f"대안: 결제 기준이면 카드 데이터1(보조)로 볼 수 있음 (예: \"{req['city']} {who} {band} 카드 결제는?\"). "
            "가맹점 기준 결제 시간대일 뿐 그 사람이 어디 사는지·실제 체류·외출은 알 수 없음")


def abstain_report(state: dict) -> dict:
    """판단 보류 보고서. 템플릿만 (LLM 없음, 다시 Guard 하지 않음)."""
    reason = state.get("abstain_reason") or "판단 보류"
    issues = [ContractIssue(**i) for i in (state.get("contract_check") or {}).get("issues", [])]
    needed = needed_data(state)
    evidence = _evidence(state)
    card = EvidenceCard(
        card_id=f"CARD-{state['run_id']}-abstain",
        run_id=state["run_id"],
        title="판단 보류",
        snapshot_id=state["snapshot_id"],
        analysis_mode=state["analysis_mode"],
        as_of=_as_of(state),
        evidence_revision=max(1, state.get("evidence_revision") or 1),
        evidence=evidence,
        limitations=[i.message for i in issues],
    )
    sentences = [f"판단 보류: {reason}"]
    sentences += [f"부족한 것: {i.code} ({i.rule or '-'}) {i.message}" for i in issues]
    sentences += [f"더 확보하면 되는 것: {n}" for n in needed]
    sentences += review_sentences(state)
    sentences.append(DISCLAIMER)
    rev = (state.get("report_revision") or 0) + 1
    report = Report(report_revision=rev, evidence_card=card, fact_sentences=sentences, is_abstain=True,
                    decision_trace=report_trace(state),
                    review_ids=list((state.get("review") or {}).get("review_ids") or []))
    return {"report": pack_report(report), "evidence_card": pack_card(card), "report_revision": rev}


# ---------------------------------------------------------------- 끝점 · 승인


def answer(state: dict) -> dict:
    report = state["report"]
    lines = list(report["fact_sentences"]) + G.split_sentences(report.get("narrative"))
    return {"answer": {"status": "answered", "text": "\n".join(lines), "report_revision": report["report_revision"]}}


def abstain_answer(state: dict) -> dict:
    return {"answer": {"status": "abstained", "abstain_reason": state.get("abstain_reason"),
                       "needed_data": needed_data(state), "report_revision": state.get("report_revision")}}


def publish_candidates(state: dict, store=None) -> dict:
    """정기 감시: 후보 목록 발행 (승인은 후보를 열어 조사할 때). run_id + 단계 키로 한 번만 기록."""
    report = state.get("report") or {}
    payload = {
        "run_id": state["run_id"],
        "snapshot_id": state["snapshot_id"],
        "selection_budget": state.get("selection_budget"),
        "candidates": state.get("candidates") or [],
        "report_revision": state.get("report_revision"),
        "is_abstain": bool(report.get("is_abstain")),
    }
    if store is not None:
        rec, _ = store.record_once(state["run_id"], "publish", "발행", payload)
        payload = rec["payload"]
    return {"published": payload}


def approval_request(state: dict) -> dict:
    """승인 대기에서 담당자에게 보여 줄 내용 (interrupt 값)."""
    report = state.get("report") or {}
    return {
        "run_id": state["run_id"],
        "snapshot_id": state["snapshot_id"],
        "evidence_revision": state.get("evidence_revision"),
        "report_revision": state.get("report_revision"),
        "is_abstain": bool(report.get("is_abstain")),
        "extra_rounds": state.get("extra_rounds", 0),
        "candidate_ids": [c["candidate_id"] for c in state.get("candidates") or []],
        "review_ids": list(report.get("review_ids") or []),
    }


def apply_approval(state: dict, resp: Any) -> dict:
    """담당자 응답 처리. 오래된 report_revision → 거절(decision=None, 다시 대기)."""
    resp = resp if isinstance(resp, dict) else {}
    decision, rev = resp.get("decision"), resp.get("report_revision")
    report = state.get("report") or {}

    def reject(code: str, msg: str) -> dict:
        return {"decision": None, "approval": {"status": "rejected", "code": code, "message": msg, "request": resp}}

    if resp.get("kind") == "review":
        # 검토 반영: 판정을 상태에 두고 "다시 만들어야 함" 표시 → record_review → write_report → 새 판
        if rev is not None and rev != state.get("report_revision"):
            return reject("STALE_REPORT_REVISION",
                          f"보고서 버전이 다름 (검토 {rev}, 현재 {state.get('report_revision')})")
        rv = resp.get("review") or {}
        review = {k: rv.get(k) for k in ("judgements", "checked_evidence_ids", "follow_up_on", "review_ids")}
        return {"decision": None, "review": review, "report": {**report, "needs_regeneration": True},
                "approval": {"status": "review", "review_ids": review.get("review_ids") or [],
                             "request": {"kind": "review", "report_revision": rev}}}

    if decision not in ("approved", "held", "more"):
        return reject("INVALID_DECISION", "decision 은 approved / held / more")
    if rev != state.get("report_revision") or report.get("needs_regeneration"):
        return reject("STALE_REPORT_REVISION",
                      f"보고서 버전이 다름 (요청 {rev}, 현재 {state.get('report_revision')}). 다시 확인 후 승인")

    upd: dict[str, Any] = {}
    note = None
    contract_broken = (state.get("contract_check") or {}).get("status") == "ERROR"
    if decision == "more":
        if state.get("extra_rounds", 0) >= MAX_EXTRA_ROUNDS:
            decision, note = "held", f"추가 조사 {MAX_EXTRA_ROUNDS}회 넘음 → 보류 기록"
        elif contract_broken:
            decision, note = "held", "계약 위반은 추가 조사로 풀리지 않음 → 보류 기록"
        else:
            upd.update(
                extra_rounds=state.get("extra_rounds", 0) + 1,
                revise_count={"guard_signal": 0, "guard_report": 0},
                abstain_reason=None,
                verdict=None,
                focus=resp.get("focus") or None,  # 담당자 범위 → plan_more
                extra_plan=None,
                prev_round=round_summary(state),
            )
    if decision == "approved" and report.get("is_abstain"):
        decision, note = "held", "계약 위반·Guard ABSTAIN 은 승인으로 풀리지 않음 → 보류 기록"
    upd["decision"] = decision
    req = {k: v for k, v in resp.items() if k != "focus"}  # 메모 원문은 승인 기록에 남기지 않음
    if resp.get("focus"):
        f = resp["focus"]
        req["focus"] = {k: v for k, v in f.items() if k not in ("memo", "plan_llm_log")} | {
            "memo_given": bool(f.get("memo") or f.get("memo_given"))}
    upd["approval"] = {"status": "accepted", "decision": decision, "note": note, "request": req}
    return upd


def record_review(state: dict) -> dict:
    """검토 반영: 보고서 Guard 횟수를 다시 세고 보고서를 다시 만들러 감 (graph.after_review)."""
    return {"revise_count": {**(state.get("revise_count") or {}), "guard_report": 0}, "verdict": None}


def finalize(state: dict, store=None) -> dict:
    """확정 또는 보류 기록. run_id + 단계 키로 한 번만 (멱등)."""
    kind = "확정" if state.get("decision") == "approved" else "보류"
    payload = {
        "run_id": state["run_id"],
        "kind": kind,
        "decision": state.get("decision"),
        "snapshot_id": state["snapshot_id"],
        "evidence_revision": state.get("evidence_revision"),
        "report_revision": state.get("report_revision"),
        "approval": state.get("approval"),
    }
    if store is None:
        return {"final_record": {"kind": kind, "payload": payload, "duplicate": False}}
    rec, created = store.record_once(state["run_id"], "finalize", kind, payload)
    return {"final_record": {**rec, "duplicate": not created}}


def to_resources(state: dict) -> list[ResourceRecommendation]:
    """상태의 자원 dict → ResourceRecommendation (G-07 을 지난 자원만 검증됨)."""
    fields = set(ResourceRecommendation.model_fields)
    return [ResourceRecommendation(**{k: v for k, v in r.items() if k in fields}) for r in state.get("resources") or []]


# ---------------------------------------------------------------- 판단 기록 (decision_trace)

MAX_TRACE = 80  # 상태에 남기는 판단 기록 수 (오래된 것부터 버림)
_REASON_MAX = 200


def _short(s: Any) -> str | None:
    return str(s)[:_REASON_MAX] if s else None


def _count(items, key) -> str:
    out: dict[str, int] = {}
    for x in items:
        k = key(x) or "-"
        out[k] = out.get(k, 0) + 1
    return " · ".join(f"{k} {n}" for k, n in out.items()) or "없음"


def _new_llm(before: dict, upd: dict, slot: str) -> dict | None:
    new = (upd.get("llm_log") or [])[len(before.get("llm_log") or []):]
    return next((r for r in reversed(new) if r.get("slot") == slot), None)


def perf_summary(state: dict) -> dict:
    """소요 시간 요약: 노드별(decision_trace ms)·도구별(tool_timings ms·상태) 합계. run_meta["perf"] 로 남김."""
    nodes_: dict[str, dict] = {}
    for t in state.get("decision_trace") or []:
        d = nodes_.setdefault(t["node"], {"calls": 0, "ms": 0.0})
        d["calls"] += 1
        d["ms"] = round(d["ms"] + float(t.get("ms") or 0), 1)
    tools_: dict[str, dict] = {}
    for t in state.get("tool_timings") or []:
        d = tools_.setdefault(t["tool"], {"calls": 0, "ms": 0.0, "status": {}})
        d["calls"] += 1
        d["ms"] = round(d["ms"] + float(t.get("ms") or 0), 1)
        d["status"][t["status"]] = d["status"].get(t["status"], 0) + 1
    return {"node_ms": round(sum(d["ms"] for d in nodes_.values()), 1),
            "tool_ms": round(sum(d["ms"] for d in tools_.values()), 1), "nodes": nodes_, "tools": tools_}


def report_trace(state: dict) -> list[dict]:
    """보고서에 싣는 판단 기록: 시작 시각·소요 시간을 뺀 사본 (같은 입력이면 같은 보고서, 결정론 테스트)."""
    return [{k: v for k, v in t.items() if k not in ("started_at", "ms")} for t in state.get("decision_trace") or []]


def _trace_quarantine(step: dict, before: dict, upd: dict, phase: str) -> None:
    """이 노드에서 새로 격리한 근거를 판단 기록에 남김 (값 없이 ID·지표·규칙, K-05)."""
    old = {(x["evidence_id"], x["phase"]) for x in before.get("quarantine") or []}
    new = [x for x in upd.get("quarantine") or [] if x["phase"] == phase and (x["evidence_id"], x["phase"]) not in old]
    if not new:
        return
    step["rule_ids"] = list(dict.fromkeys(list(step.get("rule_ids") or []) + [c for x in new for c in x["rule_ids"]]))
    step["inputs"] = {**(step.get("inputs") or {}), "quarantined": [
        {k: x.get(k) for k in ("evidence_id", "metric", "tool", "rule_ids")} for x in new][:8]}
    note = f"격리 {len(new)}건: 신호 Guard 에 걸린 참고 근거만 빼고 계속 (등급 입력 아님)"
    step["reason"] = _short(f"{step['reason']} · {note}" if step.get("reason") else note)


def trace_step(name: str, before: dict, upd: dict, nxt: str | None, started_at: str, ms: float) -> dict:
    """노드 한 번의 판단 기록 (질문 원문·개인정보 없음). graph._node 가 부른다.
    {node, choice, next, source, rule_ids, reason, inputs, changed, started_at, ms}"""
    st = {**before, **upd}
    step: dict[str, Any] = {"node": name, "choice": None, "next": nxt, "source": "rule", "rule_ids": [],
                            "reason": None, "inputs": {}, "changed": sorted(k for k in upd if k != "path"),
                            "started_at": started_at, "ms": round(ms, 1)}
    req = st.get("request") or {}
    if name == "intake":
        step["inputs"] = ({k: req.get(k) for k in ("city", "metric", "group", "adm_cds", "period_label") if req.get(k)}
                          | {"mode": st.get("mode")})
        step["choice"] = "요청 정규화 " + ("됨" if req else "못 함")
        if (st.get("abstain_reason") or "").startswith(INDIVIDUAL_CODE):
            step["rule_ids"], step["reason"] = [INDIVIDUAL_CODE], "개인 지목 질문 (LLM 부르지 않음)"
        elif (st.get("abstain_reason") or "").startswith(AMBIGUOUS_CODE):
            step["rule_ids"], step["reason"] = [AMBIGUOUS_CODE], "동을 고를 수 없음: 선택지를 돌려줌 (LLM 부르지 않음)"
        elif req.get("adm_cds"):
            step["reason"] = "동 이름 사전으로 동을 먼저 찾고 그 동으로 도시를 정함"
    elif name == "classify":
        step["choice"] = st.get("route")
        rec = _new_llm(before, upd, "classify")
        if rec and rec.get("llm_filled"):
            step["inputs"] = {"llm_filled": rec["llm_filled"]}
        if (st.get("abstain_reason") or "").startswith(INDIVIDUAL_CODE):
            step["rule_ids"], step["reason"] = [INDIVIDUAL_CODE], "개인 지목 질문: LLM 부르지 않고 범위 밖"
        elif (st.get("abstain_reason") or "").startswith(AMBIGUOUS_CODE):
            step["rule_ids"], step["reason"] = [AMBIGUOUS_CODE], "동을 고를 수 없음: LLM 부르지 않고 되묻기"
        elif rec and rec.get("ok"):
            step["source"] = "llm"
            dis = rec.get("route_disagreement")
            step["reason"] = _short(rec.get("reason") or (f"규칙 {dis['rule']} · LLM {dis['llm']} → {dis['why']}" if dis
                                                          else "LLM 이 규칙과 같은 경로"))
        else:
            step["reason"] = "규칙: 조사 낱말이 있으면 investigate, 정규화되면 lookup"
    elif name == "check_availability":
        cc = st.get("contract_check") or {}
        step["choice"] = cc.get("status")
        step["rule_ids"] = list(dict.fromkeys([i["code"] for i in cc.get("issues", [])]
                                              + [i["rule"] for i in cc.get("issues", []) if i.get("rule")]))
        step["reason"] = _short("; ".join(i["message"] for i in cc.get("issues", [])))
        step["inputs"] = {"source": cc.get("source"), "analysis_mode": st.get("analysis_mode")}
    elif name in ("lookup", "collect_signals"):
        tools = list(upd.get("tools_run") or [])
        step["inputs"] = {"evidence_revision": upd.get("evidence_revision")}
        if name == "collect_signals":
            required = fixed_tools(before)
            extra = [t for t in tools if t not in required]
            step["choice"] = f"필수 도구 {len(required)}개 + 추가 {len(extra)}개"
            step["inputs"] |= {"extra_tools": extra}
            rec = _new_llm(before, upd, "choose_extra_tools")
            if rec and rec.get("ok"):
                step["source"] = "llm"
                step["reason"] = _short(rec.get("reason") or "LLM 이 추가 도구를 고름 (이유 없음)")
            else:
                step["reason"] = "필수 근거 도구만 (LLM 없음·실패)"
        else:
            step["choice"] = ", ".join(tools)
        step["inputs"] |= {"errors": len(upd.get("errors") or []) - len(before.get("errors") or [])}
        if name == "collect_signals":
            _trace_quarantine(step, {}, upd, "collect_signals")
        if upd.get("abstain_reason"):
            step["reason"] = _short(upd["abstain_reason"])
    elif name in ("classify_candidate", "counter_evidence"):
        cands = st.get("candidates") or []
        if name == "classify_candidate":
            step["choice"] = _count(cands, lambda c: TYPE_KO.get(c["candidate_type"]))
            step["inputs"] = {"input_status": _count(cands, lambda c: c["input_status"])}
        else:
            dev = [c for c in cands if c["candidate_type"] == "unexpected_activity_deviation"]
            step["choice"] = "반증 " + _count(dev, lambda c: c["counter_result"]) if dev else "이탈 후보 없음"
    elif name in ("guard_signal", "guard_report"):
        new = (upd.get("guard") or [])[len(before.get("guard") or []):]
        step["source"] = "guard"
        step["choice"] = upd.get("verdict")
        step["rule_ids"] = sorted({r for g in new for r in g.get("rule_ids", [])})
        step["reason"] = _short(next((r for g in new for r in g.get("reasons", [])), None))
        step["inputs"] = {"checks": [f"{g['check']} {g['verdict']}" for g in new]}
        rem = upd.get("remedy") or {}
        if name == "guard_signal" and rem.get("remedy"):
            step["choice"] = f"{upd.get('verdict')} → 처치 {rem['remedy']}"
            step["inputs"]["remedy"] = rem["remedy"]
            step["inputs"]["targets"] = sorted(rem.get("targets") or [])[:5]
        elif name == "guard_report" and upd.get("verdict") == "REVISE":
            step["inputs"]["remedy"] = "rewrite"
    elif name == "drop_evidence":
        rem = before.get("remedy") or {}
        step["source"] = "guard"
        step["rule_ids"] = list(rem.get("rule_ids") or [])
        step["choice"] = f"근거 {len(rem.get('targets') or {})}건 뺌 → 다시 검사 (Guard 횟수 안 씀)"
        step["inputs"] = {"evidence_ids": sorted(rem.get("targets") or [])[:5]}
    elif name == "retry_tools":
        rem = upd.get("remedy") or {}
        step["source"] = "guard"
        step["rule_ids"] = list(rem.get("rule_ids") or [])
        tried = rem.get("retried") or []
        step["choice"] = f"실패한 도구만 다시 {len(tried)}개: {', '.join(tried) or '없음'}"
        step["inputs"] = {"evidence_revision": upd.get("evidence_revision"), "tools": tried}
    elif name == "open_questions":
        qs = (upd.get("investigation") or {}).get("questions") or []
        step["choice"] = f"미해결 질문 {len(qs)}개 (고정 목록)"
        step["inputs"] = {"questions": [f"{q['label']} · {q['city']}{' ' + q['adm_cd'] if q.get('adm_cd') else ''}"
                                        for q in qs][:8]}
    elif name == "investigate":
        inv = upd.get("investigation") or {}
        rounds = inv.get("rounds") or []
        b_rounds = (before.get("investigation") or {}).get("rounds") or []
        if len(rounds) > len(b_rounds):
            r = rounds[-1]
            picked = [i for i in inv.get("items") or [] if i["round"] == r["round"]]
            step["source"] = "llm" if r["source"] == "llm" else "rule"
            step["choice"] = f"{r['round']}회차: " + (", ".join(f"{i['label']} → {i['tool']}" for i in picked) or "고르지 않음")
            step["reason"] = _short(r.get("reason"))
            step["inputs"] = {"picked": r["picked"], "evidence_ids_new": [e for i in picked for e in i["evidence_ids"]][:5]}
        else:
            step["choice"] = "건너뜀"
            step["reason"] = _short(inv.get("stopped"))
        _trace_quarantine(step, before, upd, "investigate")
    elif name == "check_findings":
        inv = upd.get("investigation") or {}
        items = inv.get("items") or []
        step["choice"] = (_count(items, lambda i: INV.STATUS_KO.get(i["status"], i["status"])) if items else "확인한 것 없음")
        step["reason"] = _short(None if inv.get("continue") else inv.get("stopped"))
        step["inputs"] = {"rounds": len(inv.get("rounds") or []), "items": [
            {k: i.get(k) for k in ("label", "question", "city", "adm_cd", "tool", "round", "source", "why", "status",
                                   "finding", "evidence_ids", "reason_code")} for i in items][:8]}
    elif name == "assign_level":
        cands = st.get("candidates") or []
        step["choice"] = _count(cands, lambda c: c.get("candidate_level"))
        ids = sorted({c["rule_id"] for c in cands if c.get("rule_id")})
        from bigcontest.agent.tools.common import RULE_VERSION

        step["rule_ids"] = ids or [RULE_VERSION]
    elif name == "assign_queue":
        cands = st.get("candidates") or []
        sel = [c["candidate_id"] for c in sorted(cands, key=lambda c: c.get("queue_rank") or 0)
               if c.get("selected_for_review")]
        step["choice"] = f"검토 배정 {len(sel)}곳" + (f": {', '.join(sel)}" if sel else "")
        step["inputs"] = {"selection_budget": st.get("selection_budget")}
    elif name == "match_resources":
        res = upd.get("resources") or []
        step["choice"] = f"자원 {len(res)}건 · 후보 {len({r.get('candidate_id') for r in res})}곳"
    elif name == "build_card":
        card = upd.get("evidence_card") or {}
        step["choice"] = f"사실 {len(card.get('facts') or [])}개 · 근거 {len(card.get('evidence_ids') or [])}개"
        step["source"] = "template"
    elif name == "write_report":
        rec = _new_llm(before, upd, "explain") or _new_llm(before, upd, "brief")
        rep = upd.get("report") or {}
        step["choice"] = f"보고서 {upd.get('report_revision')}판 · 대응안 {len(rep.get('actions') or [])}건"
        step["source"] = "llm" if rec and rec.get("ok") else "template"
        step["reason"] = "사실·대응안은 템플릿, 설명 문장만 LLM" if step["source"] == "llm" else "템플릿만 (LLM 없음·실패)"
    elif name == "abstain_report":
        reason = st.get("abstain_reason") or ""
        step["choice"], step["source"] = "판단 보류", "template"
        step["rule_ids"] = list(dict.fromkeys(re.findall(r"\b([A-Z][A-Z_]{3,}|G-\d+)\b", reason)))
        step["reason"] = _short(reason)
    elif name == "await_approval":
        ap = upd.get("approval") or {}
        step["source"] = "human"
        step["choice"] = ap.get("decision") or ap.get("status")
        step["reason"] = _short(ap.get("note") or ap.get("message"))
        step["rule_ids"] = [ap["code"]] if ap.get("code") else []
    elif name == "plan_more":
        plan = upd.get("extra_plan") or {}
        step["source"] = {"llm": "llm", "rule": "rule"}.get(plan.get("source"), "human")
        step["choice"] = f"추가 도구 {len(plan.get('tools') or [])}개: {', '.join(plan.get('tools') or []) or '없음'}"
        step["inputs"] = {"candidate_ids": plan.get("candidate_ids") or [],
                          "counter_checks": plan.get("counter_checks") or [], "memo_used": plan.get("memo_used", False)}
        step["reason"] = _short(plan.get("reason"))
    elif name == "record_review":
        rv = st.get("review") or {}
        step["source"] = "human"
        step["choice"] = f"검토 반영: 자원 판정 {len(rv.get('judgements') or [])}건 → 보고서 다시 만듦"
    elif name == "finalize":
        step["choice"] = (upd.get("final_record") or {}).get("kind")
    elif name in ("answer", "abstain_answer"):
        step["choice"] = (upd.get("answer") or {}).get("status")
    elif name == "publish_candidates":
        step["choice"] = f"후보 {len((upd.get('published') or {}).get('candidates') or [])}건 발행"
    return step
