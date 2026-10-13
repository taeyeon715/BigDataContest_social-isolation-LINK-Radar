"""판단 기록 decision_trace + 노드 소요 시간. 가짜 도구 기준 (자료 없이 돎).

- `_node` 래퍼가 노드마다 {노드, 고른 것, 다음 노드, 규칙 ID 또는 LLM 이유, 입력 요약, 시작 시각, ms} 를 남김
- LLM 의 classify·choose_extra_tools 이유를 버리지 않음
- 질문 원문은 기록에 남기지 않음, 기록 수는 상한이 있음
- 보고서(Report)와 API 실행 상태(RunStatusResponse)에 실림
"""

import json

from bigcontest.agent import nodes
from bigcontest.agent.graph import Agent, build_graph
from bigcontest.agent.llm import FakeLLM, call_llm
from bigcontest.agent.schemas import Report, RunStatusResponse, TraceStep
from bigcontest.agent.state import initial_state, report_of

AS_OF = "2025-09-26"


def run(mode="monitoring", **kw) -> dict:
    init = initial_state("tr", mode, AS_OF, **{"tools_backend": "stub", **kw})
    return build_graph().invoke(init, {"recursion_limit": 100})


def test_trace_matches_path_and_validates():
    out = run()
    tr = out["decision_trace"]
    assert [t["node"] for t in tr] == out["path"]
    steps = [TraceStep(**t) for t in tr]
    assert all(s.ms >= 0 and s.started_at for s in steps)
    by = {s.node: s for s in steps}
    assert by["intake"].next == "check_availability"  # 정기 감시는 classify 를 건너뜀
    assert by["guard_signal"].source == "guard" and by["guard_signal"].choice == "PASS"
    assert by["guard_signal"].next == "assign_level"
    assert by["assign_level"].source == "rule" and by["assign_level"].rule_ids  # 규칙 버전 (엔진 rule_id 가 오면 그것)
    assert by["assign_level"].choice and "Level" in by["assign_level"].choice
    assert by["guard_report"].next == "publish_candidates"
    assert "report" in by["write_report"].changed  # 바뀐 칸


def test_trace_in_report():
    out = run()
    rep = report_of(out)
    # 보고서에는 보고서를 만들기 전까지의 기록
    assert [t.node for t in rep.decision_trace][-1] == "build_card"
    assert all(t.started_at is None and t.ms is None for t in rep.decision_trace)  # 보고서 사본은 시각 없음
    assert all(TraceStep(**t).ms is not None for t in out["decision_trace"])  # 실행 상태에는 있음
    assert rep.distribution == "내부용(대외비)"


def test_llm_reasons_are_kept():
    out = run(llm="fake")
    by = {t["node"]: t for t in out["decision_trace"]}
    assert by["collect_signals"]["source"] == "llm" and by["collect_signals"]["reason"] == "가짜: 첫 번째 추가 도구"
    assert by["collect_signals"]["inputs"]["extra_tools"]
    rec = next(r for r in out["llm_log"] if r["slot"] == "choose_extra_tools")
    assert rec["reason"] == "가짜: 첫 번째 추가 도구"
    # LLM 실패 → 규칙으로 대신, 이유는 규칙 쪽
    err = {t["node"]: t for t in run(llm="fake_error")["decision_trace"]}
    assert err["collect_signals"]["source"] == "rule"


def test_call_llm_records_reason():
    llm = FakeLLM("fake_flip_route")
    out, rec = call_llm(llm, "classify", llm.classify, "강남 1인세대 비율은?", {"city": "강남"})
    assert rec["reason"] == "가짜: 경로 뒤집기"
    out, rec = call_llm(llm, "choose_extra_tools", llm.choose_extra_tools, None, ["a"], [])
    assert rec["reason"] == "가짜: 첫 번째 추가 도구"


def test_classify_trace_no_raw_question(tmp_path):
    q = "추석 전에 춘천 어르신 어디부터 확인해야 해? 연락처 010-1234-5678"
    agent = Agent(tmp_path / "t.db", tools_backend="stub")
    try:
        st = agent.start("tr-qa", "qa", AS_OF, question=q, llm="fake_flip_route")
        tr = st["values"]["decision_trace"]
        dump = json.dumps(tr, ensure_ascii=False)
        assert q not in dump and "010-1234-5678" not in dump and "어디부터" not in dump
        cls = next(t for t in tr if t["node"] == "classify")
        assert cls["rule_ids"] == ["OUT_OF_SCOPE_INDIVIDUAL"] and cls["choice"] == "out_of_scope"

        q = "추석 전에 춘천 어르신 어디부터 확인해야 해?"
        st = agent.start("tr-qa2", "qa", AS_OF, question=q, llm="fake_flip_route")
        tr = st["values"]["decision_trace"]
        assert q not in json.dumps(tr, ensure_ascii=False)
        cls = next(t for t in tr if t["node"] == "classify")
        assert cls["source"] == "llm" and cls["reason"] == "가짜: 경로 뒤집기"
        assert cls["choice"] == "investigate"  # 낮추기는 무시
        # 승인 대기 중이면 await_approval 은 아직 기록 없음. 승인하면 사람 판단으로 남음
        assert tr[-1]["node"] == "guard_report" and tr[-1]["next"] == "await_approval"
        agent.approve("tr-qa2", "held", st["values"]["report_revision"])
        tr2 = agent.status("tr-qa2")["values"]["decision_trace"]
        ap = next(t for t in tr2 if t["node"] == "await_approval")
        assert ap["source"] == "human" and ap["choice"] == "held" and ap["next"] == "finalize"
        assert tr2[-1]["node"] == "finalize" and tr2[-1]["choice"] == "보류"
    finally:
        agent.close()


def test_abstain_trace_has_rule_ids():
    out = run("qa", question="강남 60대의 밤 활동은 어때?")
    tr = {t["node"]: t for t in out["decision_trace"]}
    assert tr["check_availability"]["choice"] == "ERROR" and tr["check_availability"]["rule_ids"]
    assert tr["abstain_report"]["rule_ids"]
    assert report_of(out).decision_trace  # 보류 보고서에도 실림


def test_trace_is_bounded():
    big = {"decision_trace": [{"node": f"n{i}"} for i in range(nodes.MAX_TRACE + 10)]}
    from bigcontest.agent.graph import _node

    upd = _node("build_card", lambda s: {})(big)
    assert len(upd["decision_trace"]) == nodes.MAX_TRACE
    assert upd["decision_trace"][-1]["node"] == "build_card"


def test_run_status_response_accepts_trace():
    out = run()
    r = RunStatusResponse(run_id="tr", status="published", mode="monitoring", as_of=AS_OF,
                          analysis_mode="simulated_replay", snapshot_id=out["snapshot_id"],
                          decision_trace=out["decision_trace"])
    assert r.decision_trace[0].node == "intake"
