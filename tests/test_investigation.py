"""조사 루프 · Guard 처치 · 도구 시간 제한 · 예산 (H4: AG-03~06).

- 반증 뒤 open_questions → investigate ⇄ check_findings (많아야 2회차, 회차마다 질문 3개) → guard_signal
- 결과는 보고서 "추가로 확인한 것"에만 (근거 ID 포함), 등급·반증 입력에는 넣지 않음 (test_determinism 도 함께 봄)
- Guard 처치: G-11 폐기 지표 → 그 근거만 빼기 (다시 수집 없음), G-06 시간 초과 → 실패한 도구만 다시, G-08 → 바로 보류
- call_tool 시간 제한 (느린 가짜 도구) · 오류 분류 (다시 시도 가능 / 아님)
- 예산: 도구 시간 한도 → 조사 멈춤(템플릿), LLM 한도 → 규칙으로 고름
가짜 도구(stub)만 쓴다. 유료 API 를 부르지 않는다.
"""

import time

import pytest

from bigcontest.agent import nodes
from bigcontest.agent.graph import Agent, build_graph
from bigcontest.agent.schemas import Report
from bigcontest.agent.state import initial_state, report_of
from bigcontest.agent.tools import call_tool, error_code_of, is_retriable, stubs, tool_limits
from bigcontest.agent.tools.datasource import DataUnavailable
from bigcontest.agent.tools.stubs import ToolContext

AS_OF = "2025-09-26"


def run(**kw) -> dict:
    return build_graph().invoke(initial_state("inv", "monitoring", AS_OF, **{"tools_backend": "stub", **kw}),
                                {"recursion_limit": 100})


def signal_calls(out: dict) -> int:
    """신호 수집 도구 호출 수 (조사 루프·자원 찾기 제외)."""
    return sum(1 for t in out["tool_timings"] if t["tool"] != "find_resources" and t.get("phase") != "investigate")


# ---------------------------------------------------------------- 조사 루프


def test_loop_runs_after_counter_and_before_guard():
    out = run(llm="fake")
    p = out["path"]
    assert p.index("counter_evidence") < p.index("open_questions") < p.index("investigate") \
        < p.index("check_findings") < p.index("guard_signal")
    inv = report_of(out).investigation
    assert inv is not None and inv.items and inv.rounds >= 1
    for it in inv.items:
        assert it.status in ("answered", "unanswered", "no_data", "error", "dropped")
        assert it.evidence_ids or it.status in ("no_data", "dropped")
        assert it.source == "llm" and it.why
    # 근거 ID 는 보고서 카드에 있음 (화면에서 근거로 이동)
    card_ids = {e.evidence_id for e in report_of(out).evidence_card.evidence}
    assert {i for it in inv.items for i in it.evidence_ids} <= card_ids
    # 조사 근거는 등급 입력(evidence)과 따로
    assert out["investigation_evidence"]


def test_trace_records_each_loop_step():
    out = run(llm="fake")
    steps = {t["node"]: t for t in out["decision_trace"]}
    assert steps["open_questions"]["choice"].startswith("미해결 질문")
    inv = steps["investigate"]
    assert inv["source"] == "llm" and inv["reason"] and inv["inputs"]["picked"]
    chk = steps["check_findings"]
    assert chk["next"] in ("investigate", "guard_signal") and chk["inputs"]["items"]


def test_at_most_three_picks_per_round_and_two_rounds():
    out = run(llm="fake_all_tools", scenario="rain_explained")
    inv = out["investigation"]
    assert len(inv["rounds"]) <= 2
    for r in inv["rounds"]:
        assert len(r["picked"]) <= 3
    keys = [i["key"] for i in inv["items"]]
    assert len(keys) == len(set(keys))  # 같은 질문 두 번 안 함
    assert all("RUN_SQL" not in k for k in keys)  # 목록 밖 key 는 버림
    # 숫자·진단 말이 든 LLM 이유는 싣지 않음 (Guard 표현 검사)
    assert all("고독사" not in (i["why"] or "") and "Guard 검사" in i["why"] for i in inv["items"])


def test_rule_fallback_when_llm_missing_or_failing():
    for llm in ("none", "fake_error"):
        out = run(llm=llm)
        rounds = out["investigation"]["rounds"]
        assert rounds and all(r["source"] == "rule" for r in rounds)
        step = next(t for t in out["decision_trace"] if t["node"] == "investigate")
        assert step["source"] == "rule"


def test_llm_may_pick_nothing():
    out = run(llm="fake_no_tools")
    inv = out["investigation"]
    assert inv["items"] == [] and "LLM" in inv["stopped"]
    assert report_of(out).investigation is not None  # 칸은 남고 비어 있음


def test_second_round_only_when_something_unresolved():
    out = run(llm="none")  # 규칙: 1회차에 연휴 회복(가짜 도구는 값 없음) → 2회차
    inv = out["investigation"]
    first = [i for i in inv["items"] if i["round"] == 1]
    assert any(i["status"] != "answered" for i in first)
    assert len(inv["rounds"]) == 2
    out = run(llm="fake")  # 뒤쪽부터 고르면 1회차에 모두 풀려 멈춤
    assert len(out["investigation"]["rounds"]) == 1
    assert out["investigation"]["stopped"] == "확인한 질문이 모두 풀림"


def test_lookup_route_has_no_loop():
    out = build_graph().invoke(initial_state("lk", "qa", "2025-10-20", question="강남 1인세대 비율 상위 동은?",
                                             tools_backend="stub"), {"recursion_limit": 100})
    assert "open_questions" not in out["path"] and report_of(out).investigation is None


def test_new_more_round_restarts_loop(tmp_path):
    agent = Agent(tmp_path / "inv.db", tools_backend="stub")
    try:
        st = agent.start("m1", "qa", AS_OF, question="이번 추석 전에 춘천 어르신 어디부터 확인해야 해?", llm="fake")
        first = st["values"]["investigation"]
        assert first["extra_round"] == 0
        agent.approve("m1", "more", st["values"]["report_revision"])
        v = agent.status("m1")["values"]
        assert v["investigation"]["extra_round"] == 1 and v["investigation"]["rounds"]
        assert report_of(v).investigation is not None
    finally:
        agent.close()


# ---------------------------------------------------------------- Guard 처치 (AG-04)


def test_g11_drops_evidence_instead_of_recollecting():
    out = run(scenario="deprecated_metric", llm="fake")
    assert out["path"].count("collect_signals") == 1  # 예전: 3번 (27회 도구 호출)
    assert "drop_evidence" in out["path"] and out["verdict"] == "PASS"
    assert signal_calls(out) == 9  # 필수 8 + LLM 추가 1, 다시 모으지 않음
    assert not any(e["metric"].endswith("(폐기 지표)") for e in out["evidence"])
    assert any(e.get("error_code", "").startswith("G-11 처치") for e in out["errors"])
    assert out["candidates"] and all(c["candidate_level"] for c in out["candidates"])  # 보류가 아니라 등급까지
    assert sum(1 for r in out["llm_log"] if r["slot"] == "choose_extra_tools") == 1
    step = next(t for t in out["decision_trace"] if t["node"] == "guard_signal")
    assert step["inputs"]["remedy"] == "drop_evidence" and step["next"] == "drop_evidence"


def test_g06_timeout_retries_only_failed_tool():
    out = run(scenario="confounder_timeout_once", llm="fake")
    assert out["path"].count("collect_signals") == 1 and out["path"].count("retry_tools") == 1
    retried = [t for t in out["tool_timings"] if t.get("phase") == "retry"]
    assert {t["tool"] for t in retried} == {"check_confounders"}
    assert out["verdict"] == "PASS" and out["path"][-1] == "publish_candidates"
    assert sum(1 for r in out["llm_log"] if r["slot"] == "choose_extra_tools") == 1  # LLM 선택 다시 안 함
    dev = [c for c in out["candidates"] if c["candidate_type"] == "unexpected_activity_deviation"]
    assert dev and dev[0]["counter_result"] == "통과"


def test_g06_always_timeout_abstains_after_two_retries():
    out = run(scenario="confounder_timeout_always")
    assert out["path"].count("retry_tools") == 2 and out["path"].count("collect_signals") == 1
    assert "abstain_report" in out["path"] and "REVISE 2회 넘음" in out["abstain_reason"]


def test_g08_abstains_immediately():
    out = run(scenario="small_cell")
    assert "retry_tools" not in out["path"] and "drop_evidence" not in out["path"]
    assert out["path"].count("guard_signal") == 1 and "abstain_report" in out["path"]


def test_non_retriable_error_is_not_retried():
    out = run(scenario="error")  # 반증 도구 TOOL_ERROR: 다시 해도 같음 → 이탈 후보만 보류, 다시 시도 없음
    assert "retry_tools" not in out["path"] and out["path"].count("collect_signals") == 1


def test_remedy_config_per_rule():
    from bigcontest.agent import guard as G

    assert G.remedy_of("G-11", "guard_signal") == "drop_evidence"
    assert G.remedy_of("G-06", "guard_signal") == "retry_failed_tool"
    assert G.remedy_of("G-08", "guard_signal") == "abstain"
    assert G.remedy_of("G-14", "guard_signal") == "drop_evidence"
    assert G.remedy_of("G-14", "guard_report") == "rewrite"
    assert G.remedy_of("G-05", "guard_report") == "rewrite"
    assert G.pick_remedy(["G-06", "G-11"], "guard_signal") == "drop_evidence"  # 빼기 먼저, 그다음 다시 시도
    assert G.pick_remedy(["G-08", "G-11"], "guard_signal") == "abstain"
    for rid, r in G.rules()["rules"].items():
        assert r.get("remedy") or r.get("remedy_by_where"), rid


# ---------------------------------------------------------------- 도구 시간 제한 · 오류 분류 (AG-05)


def _ctx(scenario="normal") -> ToolContext:
    from datetime import date

    return ToolContext(scenario=scenario, as_of=date(2025, 9, 26), analysis_mode="simulated_replay",
                       snapshot_id="snap-stub-0001", tool_log=[])


@pytest.fixture
def short_timeout(monkeypatch):
    lim = {**tool_limits(), "timeout_s": {"calc_holiday_gap": 0.2, "check_confounders": 0.2}}
    monkeypatch.setattr("bigcontest.agent.tools.tool_limits", lambda: lim)
    return lim


def test_slow_stub_gives_tool_timeout(monkeypatch, short_timeout):
    def slow(ctx):
        time.sleep(1.5)
        return []

    monkeypatch.setitem(stubs._TOOLS, "calc_holiday_gap", slow)
    ctx = _ctx()
    t0 = time.perf_counter()
    (ev,) = call_tool("calc_holiday_gap", ctx)
    assert time.perf_counter() - t0 < 1.0  # 기다리지 않음
    assert ev.status == "ERROR" and ev.error_code == "TOOL_TIMEOUT" and any("다시 시도 가능" in w for w in ev.warnings)
    assert ctx.tool_log[-1]["error_code"] == "TOOL_TIMEOUT" and ctx.tool_log[-1]["retriable"] is True


def test_slow_counter_tool_once_goes_through_retry_path(monkeypatch, short_timeout):
    """실제 시간 제한(느린 반증 도구 한 번) → TOOL_TIMEOUT → G-06 실패한 도구만 다시 → 통과."""
    real = stubs._TOOLS["check_confounders"]
    calls = {"n": 0}

    def slow_once(ctx):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(1.0)
        return real(ctx)

    monkeypatch.setitem(stubs._TOOLS, "check_confounders", slow_once)
    out = run()
    assert out["path"].count("retry_tools") == 1 and out["verdict"] == "PASS"
    first = next(t for t in out["tool_timings"] if t["tool"] == "check_confounders")
    assert first["error_code"] == "TOOL_TIMEOUT"


def test_error_classification(monkeypatch):
    assert error_code_of(OSError("disk")) == "TOOL_IO" and is_retriable("TOOL_IO")
    assert error_code_of(ValueError("bad")) == "TOOL_ERROR" and not is_retriable("TOOL_ERROR")
    assert error_code_of(DataUnavailable("없음")) == "DATA_UNAVAILABLE" and not is_retriable("DATA_UNAVAILABLE")

    def boom(ctx):
        raise ValueError("계산 오류")

    monkeypatch.setitem(stubs._TOOLS, "calc_heat_response", boom)
    (ev,) = call_tool("calc_heat_response", _ctx())  # 가짜 도구 예외도 봉투로
    assert ev.status == "ERROR" and ev.error_code == "TOOL_ERROR" and any("다시 시도해도 같은 결과" in w for w in ev.warnings)
    with pytest.raises(KeyError):
        call_tool("run_sql", _ctx())  # 화이트리스트 밖은 그대로 예외
    with pytest.raises(stubs.StubToolError):
        call_tool("get_structure", _ctx("없는 시나리오"))  # 코드 잘못은 그대로


# ---------------------------------------------------------------- 예산 (AG-06)


def test_tool_time_budget_stops_loop(monkeypatch):
    from bigcontest.agent import budget

    monkeypatch.setattr(budget, "tool_limit_ms", lambda: 0.0)
    out = run(llm="fake")
    inv = out["investigation"]
    assert inv["items"] == [] and inv["stopped"].startswith("BUDGET_EXCEEDED")
    assert out["path"][-1] == "publish_candidates" and out["candidates"]  # 등급은 그대로


def test_llm_budget_falls_back_to_rule_picks():
    from bigcontest.agent.budget import run_budget

    with run_budget(max_calls=1):  # 도구 선택 1번으로 다 씀 → 조사 고르기는 규칙
        out = run(llm="fake")
    r = out["investigation"]["rounds"][0]
    assert r["source"] == "rule" and "예산" in r["reason"]


def test_state_budget_counts_llm_log(monkeypatch):
    from bigcontest.agent import budget

    monkeypatch.setattr(budget, "limits", lambda: (2, None))
    over, _ = budget.state_check({"llm_log": [{"slot": "classify"}, {"slot": "explain"}]})
    assert over and "2회" in over
    assert budget.state_check({"llm_log": [{"slot": "init"}]})[0] is None


def test_report_section_schema_roundtrip():
    out = run(llm="fake")
    rep = Report(**report_of(out).model_dump(mode="json"))
    assert rep.investigation.note.startswith("추가로 확인한 것은 등급")
    assert nodes.INV.STATUS_KO["answered"] == "풀림"
