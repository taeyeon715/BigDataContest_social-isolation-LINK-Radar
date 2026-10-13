"""그래프 경로 테스트 (에이전트 설계 §4·§10).

- 다섯 끝점(답변, 보류 답변, 후보 목록 발행, 확정, 보류)에 모두 도달
- 조회도 계약 검사를 거침: "강남 60대의 밤 활동" → 보류 답변
- 후보별 반증·등급: 기각 후보는 Level 1 로 보고서에 남고, 후보 하나의 보류가 실행 전체를 보류하지 않음
- REVISE 3회째에 ABSTAIN, 추가 조사 2회 넘으면 보류 기록
가짜 도구(stub)만 쓴다.
"""

import pytest

from bigcontest.agent.graph import Agent, build_graph
from bigcontest.agent.schemas import Candidate, Report
from bigcontest.agent.state import initial_state, report_of
from bigcontest.agent.tools.stubs import CITY_COMMON_NOTE, REVIEW_PENDING_NOTE

AS_OF = "2025-09-26"  # 추석 연휴 D-7
INVESTIGATE_Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"


def run(run_id="r", mode="monitoring", as_of=AS_OF, **kw) -> dict:
    """체크포인터 없이 끝까지 (승인 대기가 없는 경로용)."""
    app = build_graph()
    return app.invoke(initial_state(run_id, mode, as_of, **{"tools_backend": "stub", **kw}), {"recursion_limit": 100})


def by_type(state: dict, t: str) -> list[dict]:
    return [c for c in state["candidates"] if c["candidate_type"] == t]


def deviation(state: dict) -> dict:
    (c,) = by_type(state, "unexpected_activity_deviation")
    return c


def report_text(state: dict) -> str:
    r = state["report"]
    return "\n".join(r["fact_sentences"] + ([r["narrative"]] if r.get("narrative") else []))


@pytest.fixture
def agent(tmp_path):
    a = Agent(tmp_path / "runs.db", tools_backend="stub")
    yield a
    a.close()


# ---------------------------------------------------------------- 다섯 끝점


def test_endpoint_answer_lookup_goes_through_contract():
    out = run(mode="qa", as_of="2025-10-20", question="강남 1인세대 비율 상위 동은?")
    assert out["route"] == "lookup"
    assert out["path"][-1] == "answer" and out["answer"]["status"] == "answered"
    assert out["path"].index("check_availability") < out["path"].index("lookup")
    assert out["contract_check"]["status"] == "OK"
    assert "[EV-20251020-STR-11230640]" in out["answer"]["text"]
    assert [g["check"] for g in out["guard"]] == ["신호", "사실", "표현"]


def test_endpoint_abstain_answer_night_activity_of_60s():
    out = run(mode="qa", as_of="2025-10-20", question="강남 60대의 밤 활동은 어때?")
    assert out["route"] == "lookup"
    assert out["path"] == ["intake", "classify", "check_availability", "abstain_report", "abstain_answer"]
    assert out["answer"]["status"] == "abstained"
    assert out["contract_check"]["issues"][0]["code"] == "GRAIN_FORBIDDEN_COMBO"
    assert out["abstain_reason"].startswith("GRAIN_FORBIDDEN_COMBO")
    assert out["answer"]["needed_data"]
    assert report_of(out).is_abstain


def test_endpoint_abstain_answer_out_of_scope():
    out = run(mode="qa", question="오늘 점심 뭐 먹지?")
    assert out["route"] == "out_of_scope"
    assert out["path"] == ["intake", "classify", "abstain_report", "abstain_answer"]
    assert out["abstain_reason"].startswith("OUT_OF_SCOPE")


def test_endpoint_publish_candidates():
    out = run()
    assert out["route"] == "investigate"
    assert "classify" not in out["path"] and "check_availability" in out["path"]
    assert out["path"][-1] == "publish_candidates"
    # rules-v1.0: 구조 상위·접근성 기준을 못 넘는 동(가짜 퇴계동)은 예정된 점검 후보로 만들지 않음
    assert len(out["published"]["candidates"]) == 3
    levels = [c["candidate_level"] for c in out["candidates"]]
    assert levels == ["Level 3", "Level 2", "Level 2"]
    assert all(c["rule_id"] and c["rule_version"] == "rules-v1.0" for c in out["candidates"])
    # 예정된 점검은 반증 대상이 아님
    assert all(c["counter_result"] == "해당 없음" for c in by_type(out, "scheduled_access_review"))
    # K=2: 등급은 그대로, 배정만 2건
    assert sum(c["selected_for_review"] for c in out["candidates"]) == 2
    dev = deviation(out)
    assert dev["is_shared_context"] and dev["check_first_adm"] and dev["adm_cd"] is None


def test_endpoint_finalize_approved_and_held(agent):
    st = agent.start("inv-ok", "qa", AS_OF, question=INVESTIGATE_Q)
    assert st["waiting"] and st["values"]["path"][-1] == "guard_report"
    assert st["interrupt"]["report_revision"] == 1
    res = agent.approve("inv-ok", "approved", report_revision=1)
    assert res["status"] == "accepted" and res["record"]["kind"] == "확정"
    assert agent.status("inv-ok")["values"]["path"][-2:] == ["await_approval", "finalize"]

    agent.start("inv-held", "qa", AS_OF, question=INVESTIGATE_Q)
    res = agent.approve("inv-held", "held", report_revision=1)
    assert res["record"]["kind"] == "보류"


def test_extra_investigation_limit_then_held(agent):
    agent.start("more", "qa", AS_OF, question=INVESTIGATE_Q)
    assert agent.approve("more", "more", report_revision=1)["status"] == "accepted"
    assert agent.approve("more", "more", report_revision=2)["status"] == "accepted"
    res = agent.approve("more", "more", report_revision=3)  # 세 번째 추가 조사 → 보류 기록
    assert res["record"]["kind"] == "보류"
    values = agent.status("more")["values"]
    assert values["extra_rounds"] == 2
    assert values["path"].count("collect_signals") == 3
    assert values["evidence_revision"] == 3 and values["report_revision"] == 3


# ---------------------------------------------------------------- 후보별 반증·등급


def test_rejected_candidate_stays_level1_in_report():
    out = run(scenario="holiday_rejection")
    dev = deviation(out)
    assert dev["counter_result"] == "기각" and dev["candidate_level"] == "Level 1"
    assert "반증에서 기각" in dev["level_reason"] and "공휴일 기대값" in dev["level_reason"]
    assert dev["candidate_id"] in report_text(out)
    assert out["path"][-1] == "publish_candidates" and not out["report"]["is_abstain"]
    # 다른 후보 등급은 그대로
    assert [c["candidate_level"] for c in by_type(out, "scheduled_access_review")] == ["Level 3", "Level 2"]


def test_rain_is_recorded_not_auto_rejected():
    dev = deviation(run(scenario="rain_explained"))
    assert dev["counter_result"] == "통과"
    assert "날씨로 설명되는 부분" in dev["counter_reason"] and "보정 후 남는 이탈" in dev["counter_reason"]


def test_city_common_decline_is_not_rejection():
    out = run(scenario="city_common")
    dev = deviation(out)  # 후보가 사라지지 않음
    assert dev["counter_result"] == "보류" and dev["counter_result"] != "기각"
    assert dev["counter_reason"] == CITY_COMMON_NOTE
    assert dev["candidate_level"] == "판단 보류"
    assert dev["candidate_id"] in report_text(out) and "확인 필요" in report_text(out)
    assert not out["report"]["is_abstain"]


def test_review_decided_after_as_of_hides_conclusion():
    out = run(scenario="review_pending")
    dev = deviation(out)
    assert dev["counter_result"] == "보류" and REVIEW_PENDING_NOTE in dev["counter_reason"]
    conf = [e for e in out["evidence"] if e["metric"].startswith("반증")]
    assert conf and all(e["value"] == "검토 중" and e["interpretation_status"] == "미확인" for e in conf)
    # 미래 결론(기각·통과 사유, 검토 결정일)을 소급하지 않음
    assert not any(w.startswith("검토 결정일") for e in conf for w in e["warnings"])
    alarm = next(e for e in out["evidence"] if e["metric"] == "생필품 결제 평소 대비")
    assert any(w.startswith("경보 최초일") for w in alarm["warnings"]) and "회복 미관측" in alarm["warnings"]


def test_candidate_hold_does_not_abstain_run():
    out = run(scenario="error")
    dev = deviation(out)
    assert dev["counter_result"] == "보류" and "반증 자료 결측" in dev["counter_reason"]
    assert dev["candidate_level"] == "판단 보류"
    assert out["errors"] and all(e["error_code"] == "TOOL_ERROR" for e in out["errors"])
    assert out["abstain_reason"] is None and not out["report"]["is_abstain"]
    assert [c["candidate_level"] for c in by_type(out, "scheduled_access_review")] == ["Level 3", "Level 2"]


def test_missing_required_inputs_per_candidate():
    out = run(scenario="missing_required")
    (sch,) = by_type(out, "scheduled_access_review")
    assert sch["input_status"] == "부분 판정" and sch["adm_cd"] is None
    assert sch["missing_inputs"] == ["structure_vulnerability 또는 access_gap"]
    assert sch["candidate_level"] == "Level 1"
    dev = deviation(out)
    assert dev["input_status"] == "보류" and "card_cohort_series" in dev["missing_inputs"]
    assert dev["candidate_level"] == "판단 보류"
    assert out["path"][-1] == "publish_candidates"


def test_empty_scenario_publishes_empty_list():
    out = run(scenario="empty")
    assert out["candidates"] == [] and out["path"][-1] == "publish_candidates"


def test_candidates_validate():
    for c in run()["candidates"]:
        Candidate(**c)


# ---------------------------------------------------------------- Guard 분기


def test_signal_revise_then_pass():
    out = run(scenario="confounder_timeout_once")
    assert out["path"].count("collect_signals") == 2
    assert [g["verdict"] for g in out["guard"] if g["guard"] == "guard_signal"] == ["REVISE", "PASS"]
    assert deviation(out)["counter_result"] == "통과"


def test_signal_revise_third_time_abstains():
    out = run(scenario="confounder_timeout_always")
    sig = [g["verdict"] for g in out["guard"] if g["guard"] == "guard_signal"]
    assert sig == ["REVISE", "REVISE", "REVISE"]  # 세 번째 REVISE 판단 → 한도 넘어 ABSTAIN
    assert out["path"].count("collect_signals") == 3
    assert "REVISE 2회 넘음" in out["abstain_reason"]
    assert out["path"][-2:] == ["abstain_report", "publish_candidates"]
    assert out["published"]["is_abstain"]


def test_report_revise_once_then_pass():
    out = run(llm="fake_wrong_fact_once")
    assert out["path"].count("write_report") == 2 and out["report_revision"] == 2
    facts = [g["verdict"] for g in out["guard"] if g["check"] == "사실"]
    assert facts == ["REVISE", "PASS"]
    assert out["path"][-1] == "publish_candidates" and not out["report"]["is_abstain"]


def test_report_revise_third_time_abstains():
    out = run(llm="fake_wrong_fact_always")
    assert out["path"].count("write_report") == 3
    assert [g["verdict"] for g in out["guard"] if g["check"] == "사실"] == ["REVISE"] * 3
    assert out["report"]["is_abstain"] and "REVISE 2회 넘음" in out["abstain_reason"]
    assert out["path"][-2:] == ["abstain_report", "publish_candidates"]


def test_report_expression_revise():
    out = run(llm="fake_diagnosis_once")
    expr = [g for g in out["guard"] if g["check"] == "표현"]
    assert expr[0]["verdict"] == "REVISE" and "G-04" in expr[0]["rule_ids"]
    assert expr[-1]["verdict"] == "PASS"


def test_lookup_small_cell_blocked():
    out = run(mode="qa", as_of="2025-10-20", question="강남 1인세대 비율 상위 동은?", scenario="small_cell")
    assert out["path"][-1] == "abstain_answer" and "G-08" in out["abstain_reason"]


def test_lookup_revise_goes_straight_to_abstain():
    out = run(mode="qa", as_of="2025-10-20", question="강남 체류인구는?", scenario="deprecated_metric")
    assert out["path"].count("lookup") == 1 and out["path"][-1] == "abstain_answer"
    assert "G-11" in out["abstain_reason"]


@pytest.mark.parametrize("scenario,code", [("lookup_error", "TOOL_ERROR"), ("lookup_empty", "EMPTY")])
def test_lookup_tool_error_and_empty_are_distinct(scenario, code):
    out = run(mode="qa", as_of="2025-10-20", question="강남 1인세대 비율 상위 동은?", scenario=scenario)
    assert out["path"][-1] == "abstain_answer" and out["abstain_reason"].startswith(code)


def test_strict_as_of_without_reported_dates_abstains():
    out = run(mode="qa", as_of="2025-10-20", question="강남 1인세대 비율 상위 동은?", analysis_mode="strict_as_of")
    assert out["path"][-1] == "abstain_answer"
    assert "TIME_NO_USABLE_DATA" in [i["code"] for i in out["contract_check"]["issues"]]


# ---------------------------------------------------------------- 자원


def test_out_of_area_resource_excluded():
    out = run(scenario="out_of_area_resource")
    assert "STUB-R90" not in {r["resource_id"] for r in out["resources"]}
    res_ev = [e for e in out["evidence"] if e["metric"] == "복지 자원 후보"]
    assert any("관할 밖 자원 제외: STUB-R90" in w for e in res_ev for w in e["warnings"])


def test_resource_without_source_removed_with_pass():
    out = run(scenario="resource_no_source")
    assert "STUB-R91" not in {r["resource_id"] for r in out["resources"]}
    expr = [g for g in out["guard"] if g["check"] == "표현"][-1]
    assert expr["verdict"] == "PASS" and any("G-07" in x for x in expr["reasons"])
    assert out["path"][-1] == "publish_candidates"


def test_all_five_endpoints_reached(agent):
    ends = set()
    ends.add(run(mode="qa", as_of="2025-10-20", question="강남 1인세대 비율 상위 동은?")["path"][-1])
    ends.add(run(mode="qa", question="오늘 점심 뭐 먹지?")["path"][-1])
    ends.add(run()["path"][-1])
    kinds = set()
    for rid, decision in (("e1", "approved"), ("e2", "held")):
        agent.start(rid, "qa", AS_OF, question=INVESTIGATE_Q)
        kinds.add(agent.approve(rid, decision, report_revision=1)["record"]["kind"])
    assert ends == {"answer", "abstain_answer", "publish_candidates"}
    assert kinds == {"확정", "보류"}
