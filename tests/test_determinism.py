"""결정론 테스트 (에이전트 설계 §8·§10).

- LLM 분류·도구 선택·설명을 바꿔도 등급이 같고, LLM 없이도 같음
- 필수 근거 도구는 LLM 이 고르지 않아도 항상 실행
- 같은 입력이면 같은 경로·같은 결과 (가짜 도구 기준)
"""

import pytest

from bigcontest.agent.graph import build_graph
from bigcontest.agent.nodes import required_signal_tools
from bigcontest.agent.state import initial_state
from bigcontest.agent.tools import EXTRA_TOOLS

AS_OF = "2025-09-26"
LLMS = ["none", "fake", "fake_no_tools", "fake_all_tools", "fake_error", "fake_wrong_fact_once"]


def run(**kw) -> dict:
    return build_graph().invoke(initial_state("det", "monitoring", AS_OF, **{"tools_backend": "stub", **kw}), {"recursion_limit": 100})


def levels(state: dict) -> dict[str, tuple]:
    return {
        c["candidate_id"]: (c["candidate_type"], c["candidate_level"], c["input_status"], c["counter_result"])
        for c in state["candidates"]
    }


@pytest.mark.parametrize("scenario", ["normal", "holiday_rejection", "missing_required", "city_common"])
def test_llm_choice_does_not_change_levels(scenario):
    base = levels(run(scenario=scenario))
    assert base
    for llm in LLMS[1:]:
        assert levels(run(scenario=scenario, llm=llm)) == base, llm


@pytest.mark.parametrize("llm", LLMS)
def test_required_tools_always_run(llm):
    out = run(llm=llm)
    required = required_signal_tools()
    assert out["tools_run"][: len(required)] == required
    extra = out["tools_run"][len(required):]
    assert all(t in EXTRA_TOOLS for t in extra)  # 화이트리스트 밖 이름은 버림
    assert "run_sql" not in out["tools_run"]


def test_required_tools_cover_candidate_inputs():
    # 후보 종류별 required_inputs 도구 (configs/candidate_inputs.yaml)
    assert set(required_signal_tools()) == {
        "get_calendar_triggers", "check_request_contract", "get_structure", "get_access_gap",
        "calc_cohort_deviation", "get_card_cohort_series", "calc_expected_activity", "check_confounders",
    }


def test_qa_classifier_variants_same_levels(tmp_path):
    from bigcontest.agent.graph import Agent

    agent = Agent(tmp_path / "det.db", tools_backend="stub")
    try:
        got = []
        for i, llm in enumerate(["none", "fake", "fake_error"]):
            st = agent.start(f"q{i}", "qa", AS_OF, question="이번 추석 전에 춘천 어르신 어디부터 확인해야 해?", llm=llm)
            assert st["values"]["route"] == "investigate"
            got.append(levels(st["values"]))
        assert got[0] == got[1] == got[2]
    finally:
        agent.close()


def test_same_input_same_path_and_result():
    a, b = run(llm="fake"), run(llm="fake")
    for key in ("path", "candidates", "evidence", "report", "guard", "resources", "published"):
        assert a[key] == b[key], key


def test_llm_call_log_shape():
    out = run(llm="fake")
    slots = [r["slot"] for r in out["llm_log"]]
    assert slots == ["choose_extra_tools", "explain"]
    for r in out["llm_log"]:
        assert r["model_id"] == "fake" and r["prompt_version"] == "fake-v0" and r["ok"]
        assert {"input_tokens", "output_tokens", "cost_usd", "latency_ms", "attempts"} <= set(r)
    # LLM 실패: 1회 재시도 후 규칙·템플릿
    err = run(llm="fake_error")
    assert all(not r["ok"] and r["attempts"] == 2 for r in err["llm_log"])
    assert err["report"]["narrative"] is None and err["path"][-1] == "publish_candidates"
    # LLM 없음: 기록도 없음
    assert run()["llm_log"] == []
