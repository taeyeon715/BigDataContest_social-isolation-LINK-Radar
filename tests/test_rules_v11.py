"""rules-v1.1 (H3): 폭염 특보 단계, 명절 묶음, 카드 맥락(외지 손님·업종 다양성), 청년 남성 도시 단위 안내,
연휴 이동 반증 맥락, 수급자 비율·1인세대 순증(표시만). 앞쪽은 자료 없이 도는 단위 시험, 뒤쪽은 dev 스냅샷 시험."""

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bigcontest.agent.schemas import Candidate, ToolResult
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import dev_available, dev_snapshot_id
from bigcontest.rules import candidates as RC
from bigcontest.rules import context as X
from bigcontest.rules import load_rules
from bigcontest.rules.heat import apparent_temp, heat_stage
from bigcontest.rules.levels import Axes, StructFact, decide

dev = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")


# ---------------------------------------------------------------- 폭염 특보 단계


def test_apparent_temperature_kma_summer_formula():
    # 습도가 높을수록 체감온도가 높고, 기온 33℃·습도 50% 는 33℃ 안팎 (기상청 여름철 식)
    assert 32.0 < apparent_temp(33, 50) < 33.0
    assert apparent_temp(33, 70) > apparent_temp(33, 50) > apparent_temp(33, 30)
    assert apparent_temp(None, 50) is None and apparent_temp(30.0, float("nan")) is None


@pytest.mark.parametrize("days, stage", [
    ([(33.0, 31.0), (33.2, 31.0)], "폭염주의보"),       # 체감 33 이상 2일
    ([(33.0, 31.0), (32.9, 31.0)], None),                # 하루만
    ([(35.1, 33.0), (35.0, 34.0)], "폭염경보"),          # 체감 35 이상 2일
    ([(35.5, 33.0), (34.0, 33.0)], "폭염주의보"),        # 35 는 하루, 33 은 2일
    ([(38.0, 35.0), (30.0, 29.0)], "폭염중대경보"),      # 체감 38 하루라도
    ([(36.0, 39.0), (None, None)], "폭염중대경보"),      # 기온 39 하루라도
    ([(None, None), (34.0, 33.0)], None),
])
def test_heat_stage(days, stage):
    assert heat_stage(days) == stage


def test_stage_of_trigger_text():
    assert S.stage_of_trigger("R4 폭염경보 [상황 시연(미래 실측 사용)] (…)") == "폭염경보"
    assert S.stage_of_trigger("R4 폭염중대경보 …") == "폭염중대경보"
    assert S.stage_of_trigger("R1 추석 연휴 D-7") is None


# ---------------------------------------------------------------- 명절 묶음


BLOCKS = [(date(2025, 8, 15), date(2025, 8, 17), "광복절"), (date(2025, 10, 3), date(2025, 10, 9), "추석")]
NAMES = {pd.Timestamp("2025-08-15"): "광복절", pd.Timestamp("2025-10-06"): "추석", pd.Timestamp("2025-10-05"): "추석연휴"}


def test_holiday_bundle_only_for_myeongjeol_window():
    assert X.holiday_bundle(BLOCKS, NAMES, date(2025, 8, 10)) is None  # 광복절은 명절 아님
    assert X.holiday_bundle(BLOCKS, NAMES, date(2025, 9, 25)) is None  # D-8
    assert X.holiday_bundle(BLOCKS, NAMES, date(2025, 10, 20)) is None  # 연휴 뒤 11일
    b = X.holiday_bundle(BLOCKS, NAMES, date(2025, 9, 26))
    assert b[:3] == (date(2025, 10, 3), date(2025, 10, 9), "추석") and b[3] == [date(2025, 10, 6)]


def test_holiday_timeline_three_steps():
    b = X.holiday_bundle(BLOCKS, NAMES, date(2025, 9, 26))
    bid, steps = X.holiday_timeline(b, date(2025, 9, 26), "춘천", True)
    assert bid == "춘천:명절:20251003"
    assert [(s["phase"], s["code"], s["status"]) for s in steps] == [("전", "R1", "지금"), ("중", "R2", "예정"), ("후", "R3", "예정")]
    assert steps[1]["start"] == date(2025, 10, 6) and steps[1]["end"] == date(2025, 10, 7)
    assert steps[2]["start"] == date(2025, 10, 10) and steps[2]["applies"]
    _, steps = X.holiday_timeline(b, date(2025, 10, 12), "강남", False)
    assert [s["status"] for s in steps] == ["지남", "지남", "지금"] and not steps[2]["applies"]  # R3 은 춘천 고령층만


# ---------------------------------------------------------------- 카드 맥락 계산


def test_expected_categories_volume_free():
    a = np.array([10, 10, 0, 5])
    assert X.expected_categories(a, 200) == pytest.approx(X.expected_categories(a * 7, 200))  # 결제량과 무관
    assert X.expected_categories(np.array([1, 1]), 200) == pytest.approx(2.0)
    assert X.expected_categories(np.array([100, 1]), 1) == pytest.approx(1.0)
    assert np.isnan(X.expected_categories(np.array([0, 0]), 10))


def _visitors(hol_share: float, base_share: float) -> pd.DataFrame:
    days = pd.date_range("2025-08-01", "2025-10-12")
    rows = []
    for d in days:
        sh = hol_share if date(2025, 10, 3) <= d.date() <= date(2025, 10, 9) else base_share
        rows.append({"date": d, "city": "춘천", False: 1000 * sh, True: 1000 * (1 - sh)})
    return pd.DataFrame(rows).set_index(["date", "city"])


def test_visitor_context_surge_and_cutoff():
    rules = load_rules()
    block = (date(2025, 10, 3), date(2025, 10, 9), "추석")
    v = X.visitor_context(_visitors(0.30, 0.20), "춘천", block, set(), date(2025, 10, 12), rules)
    assert v.verdict == "이동과 일관" and v.diff == pytest.approx(0.10) and v.n_holiday_days == 7
    v = X.visitor_context(_visitors(0.22, 0.20), "춘천", block, set(), date(2025, 10, 12), rules)
    assert v.verdict == "이동 신호 없음"
    v = X.visitor_context(_visitors(0.30, 0.20), "춘천", block, set(), date(2025, 10, 1), rules)  # 기준일 카드에 연휴 없음
    assert v.verdict == "관측 전"
    assert X.visitor_context(_visitors(0.3, 0.2), "강남", block, set(), date(2025, 10, 12), rules).verdict == "관측 전"


# ---------------------------------------------------------------- 등급 규칙 (v1.1)


def test_city_guidance_level2_no_level3():
    a = Axes(candidate_type="scheduled_access_review", input_status="충족", trigger="R1 추석 연휴 D-7", city_guidance=True)
    d = decide(a)
    assert (d.level, d.rule_id, d.caps) == ("Level 2", "L2-CITY-GUIDANCE", ["CAP-SHARED-CONTEXT"])
    dev_ = Axes(candidate_type="unexpected_activity_deviation", input_status="충족", counter_result="통과",
                deviation_drop=0.2, city_guidance=True)
    assert decide(dev_).rule_id == "L2-CITY-GUIDANCE"
    assert decide(replace(dev_, deviation_drop=0.05)).rule_id == "L1-DEV-SMALL"  # 이탈 크기 규칙이 먼저
    assert decide(replace(dev_, counter_result="기각", counter_reason="x")).rule_id == "L1-REJECTED"
    assert RC.response_type("Level 2", "20~34세 남성", rule_id="L2-CITY-GUIDANCE+CAP-SHARED-CONTEXT") == "안내 강화"
    assert RC.city_guidance("청년 남성") and RC.city_guidance("20~34세 남성") and not RC.city_guidance("고령층")


def test_holiday_travel_hold_keeps_level_changes_next_step():
    a = Axes(candidate_type="unexpected_activity_deviation", input_status="충족", counter_result="보류",
             counter_reason=S.HOLIDAY_TRAVEL_NOTE + ": …", holiday_travel=True)
    d = decide(a)
    assert (d.level, d.rule_id) == ("판단 보류", "HOLD-HOLIDAY-TRAVEL")
    assert RC.response_type(d.level, "65세 이상", rule_id=d.rule_id) == "연휴 뒤 회복 확인"
    assert decide(replace(a, holiday_travel=False)).rule_id == "HOLD-COUNTER"
    assert RC.response_type("판단 보류", "65세 이상", rule_id="HOLD-COUNTER") == "추가 자료 확보"


def _env(eid, metric, value, warnings=(), status="OK"):
    return ToolResult(evidence_id=eid, metric=metric, value=value, analysis_mode="simulated_replay",
                      as_of=date(2025, 10, 15), snapshot_id="stub", warnings=list(warnings), status=status)


def _cand(**kw):
    base = dict(candidate_id="C-1", candidate_type="unexpected_activity_deviation", city="춘천", analysis_cohort="70대 여성",
                input_status="충족", counter_result="보류")
    return Candidate(**{**base, **kw})


def test_judge_counter_uses_visitor_context_without_rejecting():
    c = _cand()
    conf = {"holiday_expectation": _env("EV-1-CF1", "반증: 공휴일 기대값", "설명 일부", ["설명"]),
            "city_common_factor": _env("EV-1-CF2", "반증: 도시 공통요인", "공통 변화", ["공통"]),
            "weather_asos": _env("EV-1-CF3", "반증: 강수", "통과", ["비"])}
    res, why = S.judge_counter(c, conf)
    assert res == "보류" and why == S.CITY_COMMON_NOTE  # 외지 손님 근거가 없으면 v1.0 그대로
    surge = _env("EV-1-CF5", S.VISITOR_METRIC, "이동과 일관", ["추석 연휴 … +7.7%p: 이동과 일관"])
    res, why = S.judge_counter(c, {**conf, "holiday_visitor_context": surge})
    assert res == "보류" and why.startswith(S.HOLIDAY_TRAVEL_NOTE) and "기각하지 않고" in why
    flat = _env("EV-1-CF5", S.VISITOR_METRIC, "이동 신호 없음", ["추석 연휴 … +1.6%p: 이동 신호 없음"])
    res, why = S.judge_counter(c, {**conf, "holiday_visitor_context": flat})
    assert res == "보류" and not why.startswith(S.HOLIDAY_TRAVEL_NOTE) and "근거도 약함" in why
    # 반증 통과 이탈은 그대로 통과 (맥락 문장만 붙음)
    ok = {**conf, "city_common_factor": _env("EV-1-CF2", "반증: 도시 공통요인", "통과", ["x"]),
          "holiday_expectation": _env("EV-1-CF1", "반증: 공휴일 기대값", "통과", ["x"]), "holiday_visitor_context": surge}
    res, why = S.judge_counter(c, ok)
    assert res == "통과" and "외지 손님" in why


def test_target_population_is_display_only_by_default():
    items = [("A", "강남", 0.60, []), ("B", "강남", 0.55, []), ("C", "강남", 0.30, []), ("D", "강남", 0.20, []),
             ("E", "강남", 0.10, [])]
    targets = {"A": (0.10, 0.20, "40~64세 남성"), "B": (0.25, 0.20, "40~64세 남성")}
    facts = RC.structure_facts(items, load_rules(), targets)
    assert facts["A"].top and facts["A"].target_ok is False  # 기본: 표시만 (상위 판정 그대로)
    strict = RC.structure_facts(items, load_rules({"thresholds.structure.target_population.apply_to_structure": True}),
                                targets)
    assert not strict["A"].top and strict["A"].rate_top and strict["B"].top  # 초안 조건 (리포트 7-1 비교용)


def test_economic_cause_from_context():
    c = _cand(candidate_type="scheduled_access_review", counter_result="해당 없음", adm_cd="11230750",
              context=[{"name": "기초생활수급자 비율", "text": "수급자 비율 9.0% → 원인 유형에 '경제 취약형' 추가", "role": "구조 별도 칸"}])
    assert S.economic_vulnerable(c)
    assert not S.economic_vulnerable(c.model_copy(update={"context": []}))


# ---------------------------------------------------------------- 실제 자료 (dev 스냅샷)


def _ctx(city, as_of, group, **kw):
    from bigcontest.rules.compare import ctx_for

    c = ctx_for(dev_snapshot_id(), city, as_of, group)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run(city, as_of, group):
    from bigcontest.rules.compare import collect, run_variant

    c = _ctx(city, as_of, group)
    return run_variant(collect(c), "D", c.request, as_of, 10).candidates


@dev
def test_recipients_and_net_change_are_display_only():
    from bigcontest.agent.tools import call_tool
    from bigcontest.external import seoul_recipients

    rec = seoul_recipients()
    assert len(rec) == 22 and rec["adm_cd"].notna().all() and rec["year"].eq(2024).all()
    gn = call_tool("get_structure", _ctx("강남", date(2025, 9, 26), "고령층"))
    assert sum(e.metric == "1인세대 비율" for e in gn) == 22
    recs = [e for e in gn if S.input_of(e) == "recipient_rate"]
    assert len(recs) == 22 and all(0 < e.value < 0.5 and "등급 근거" in e.forbidden_claims for e in recs)
    assert sum(S.recipient_top(e) for e in recs) == 7  # 22개 동의 상위 30%
    assert len([e for e in gn if S.input_of(e) == "one_person_net_change"]) == 22
    cc = call_tool("get_structure", _ctx("춘천", date(2025, 9, 26), "고령층"))
    assert [e.status for e in cc if S.input_of(e) == "recipient_rate"] == ["EMPTY"]
    # strict_as_of: 2024년 수급자 통계(2025-12-01 공개)는 그 전 기준일에 쓰지 않음
    strict = call_tool("get_structure", _ctx("강남", date(2025, 9, 26), "고령층", analysis_mode="strict_as_of"))
    assert all(e.status == "EMPTY" for e in strict if S.input_of(e) == "recipient_rate")


@dev
def test_d1_unchanged_and_young_men_city_guidance():
    d1 = [c for c in _run("강남", date(2025, 9, 26), "중장년 남성 1인가구") if c.candidate_type == "scheduled_access_review"]
    assert len(d1) == 7 and {c.candidate_level for c in d1} == {"Level 2"}  # 대상 인구 조건은 표시만 → D1 그대로
    assert all(c.event_bundle_id == "강남:명절:20251003" and len(c.event_timeline) == 3 for c in d1)
    assert all(any(x.name == "40~64세 남성 인구 비중" and not x.affects_level for x in c.context) for c in d1)
    for city in ("강남", "춘천"):
        young = [c for c in _run(city, date(2025, 9, 26), "청년 남성") if c.candidate_type == "scheduled_access_review"]
        assert len(young) == 1 and young[0].adm_cd is None and young[0].rule_id == "L2-CITY-GUIDANCE+CAP-SHARED-CONTEXT"
        assert young[0].response_type == "안내 강화" and young[0].input_status == "충족"
    assert _run("춘천", date(2025, 12, 20), "청년 남성") == []  # 동절기(W)는 도시 단위 안내에 쓰지 않음


@dev
def test_holiday_travel_changes_next_step_only_in_chuncheon():
    cc = [c for c in _run("춘천", date(2025, 10, 15), "고령층") if c.candidate_type == "unexpected_activity_deviation"]
    travel = [c for c in cc if c.rule_id == "HOLD-HOLIDAY-TRAVEL"]
    assert travel and all(c.candidate_level == "판단 보류" and c.response_type == "연휴 뒤 회복 확인" for c in travel)
    assert all(any(x.role == "반증 맥락" and x.value == "이동과 일관" for x in c.context) for c in travel)
    gn = [c for c in _run("강남", date(2025, 10, 15), "고령층") if c.candidate_type == "unexpected_activity_deviation"]
    assert gn and not any(c.rule_id == "HOLD-HOLIDAY-TRAVEL" for c in gn)  # 강남은 외지 손님 급증 없음
    assert any("이동 신호 없음" in x.text for c in gn for x in c.context)
    assert not any(c.counter_result == "기각" and "외지 손님" in (c.counter_reason or "")
                   and "기대 경로" not in c.counter_reason and "공휴일 기대값" not in c.counter_reason for c in cc + gn)


@dev
def test_r4_heat_stage_in_scenario_only():
    from bigcontest.agent.tools import call_tool

    c = _ctx("강남", date(2025, 7, 25), "고령층")
    assert not [e for e in call_tool("get_calendar_triggers", c) if str(e.value).startswith("R4")]  # 기본: 예보 없음
    c = _ctx("강남", date(2025, 7, 25), "고령층", allow_scenario=True)
    r4 = [e for e in call_tool("get_calendar_triggers", c) if str(e.value).startswith("R4")]
    assert len(r4) == 1 and "폭염경보" in r4[0].value and S.is_scenario(r4[0])
    c = _ctx("춘천", date(2025, 7, 26), "고령층", allow_scenario=True)
    r4 = [e for e in call_tool("get_calendar_triggers", c) if str(e.value).startswith("R4")]
    assert len(r4) == 1 and "폭염주의보" in r4[0].value
    cands = S.classify_candidate_type(call_tool("get_calendar_triggers", c) + call_tool("check_request_contract", c)
                                      + call_tool("get_structure", c), c.request, c.as_of)
    assert cands and all(x.heat_stage == "폭염주의보" for x in cands)


@dev
def test_economic_cause_on_gangnam_candidate():
    from bigcontest.agent import nodes

    cands = [c for c in _run("강남", date(2025, 9, 26), "고령층") if c.candidate_type == "scheduled_access_review"]
    econ = [c for c in cands if S.economic_vulnerable(c)]
    assert econ and all("경제 취약형" in nodes.candidate_cause(c, []) for c in econ)
    assert all("경제 취약형" not in nodes.candidate_cause(c, []) for c in cands if c not in econ)


@dev
def test_card_rows_missing_is_zero_at_panel_level():
    """'행 없음 = 0' 민감도 (R-1 7-7): 경보 패널(도시 × 연령 × 성 × 일)에는 행 없는 칸이 없다."""
    from bigcontest.agent.tools.datasource import open_datasource

    ds = open_datasource(dev_snapshot_id())
    assert int((~ds.card_observed()).to_numpy().sum()) == 0
