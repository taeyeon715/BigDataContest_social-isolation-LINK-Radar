"""조사·선택 도구 근거의 Guard 실패는 격리 (외부 점검 K-05).

- 조사 루프 근거가 신호 Guard 에 걸리면(작은 칸·폐기 지표·도구 오류) 실행 전체를 보류하지 않고 그 근거만 뺌.
  질문은 "확인 못 함"(quarantined) + 사유 코드, 판단 기록(decision_trace)에 남김. 작은 칸 값은 어디에도 나오지 않음
- LLM 이 그 도구를 고르든 안 고르든 등급·끝 상태가 같음 (실패 주입 결정론)
- 필수(기본) 근거의 작은 칸은 지금처럼 바로 보류
"""

from __future__ import annotations

import json

import pytest

from bigcontest.agent import nodes
from bigcontest.agent.graph import build_graph
from bigcontest.agent.state import initial_state, report_of
from bigcontest.agent.tools import stubs

AS_OF = "2025-09-26"
LLMS = ["none", "fake", "fake_no_tools", "fake_all_tools", "fake_error", "fake_wrong_fact_once",
        "fake_wrong_level_once", "fake_diagnosis_once"]
TOOL = "calc_holiday_gap"  # 선택 도구이자 조사 루프 질문(연휴 회복)의 도구
SMALL_METRIC = "추가 관측 인원 (주입)"


def _failure(kind: str, ctx):
    if kind == "small":
        return [stubs._ev(ctx, "INV-SMALL", metric=SMALL_METRIC, value=5, unit="명", n_obs=5,
                          applied_scope=ctx.city, measured_grain="집단")]
    if kind == "deprecated":
        return [stubs._ev(ctx, "INV-DEP", metric=stubs.DEPRECATED_METRIC, value=0.3, unit="비율",
                          n_obs=stubs.STUB_STAY_POP, applied_scope=ctx.city)]
    return [stubs._error(ctx, "INV-ERR", "연휴 결제 회복 (주입)", "TOOL_ERROR", "주입한 도구 오류")]


@pytest.fixture
def inject(monkeypatch):
    """선택한 추가 도구(calc_holiday_gap)에 실패를 넣음. where='investigate' 면 조사 루프에서만, 'any' 면 수집 때도."""
    orig_call, orig_inv = nodes.call_tool, nodes.investigate
    phase = {"inv": False}
    cfg = {"kind": None, "where": "investigate"}

    def call(tool, ctx):
        if tool == TOOL and cfg["kind"] and (phase["inv"] or cfg["where"] == "any"):
            return _failure(cfg["kind"], ctx)
        return orig_call(tool, ctx)

    def inv(state):
        phase["inv"] = True
        try:
            return orig_inv(state)
        finally:
            phase["inv"] = False

    monkeypatch.setattr(nodes, "call_tool", call)
    monkeypatch.setattr(nodes, "investigate", inv)

    def set_(kind, where="investigate"):
        cfg.update(kind=kind, where=where)

    return set_


def run(llm: str, scenario: str = "normal", city: str | None = "춘천") -> dict:
    return build_graph().invoke(initial_state("q", "monitoring", AS_OF, tools_backend="stub", llm=llm,
                                              scenario=scenario, city=city), {"recursion_limit": 100})


def outcome(s: dict) -> tuple:
    levels = tuple(sorted((c["candidate_id"], c["candidate_type"], c["candidate_level"], c["input_status"],
                           c["counter_result"], c.get("queue_rank"), c.get("selected_for_review"),
                           tuple(c.get("evidence_ids") or [])) for c in s["candidates"]))
    return s["path"][-1], s["verdict"], s.get("abstain_reason"), levels


@pytest.mark.parametrize("where", ["investigate", "any"])
@pytest.mark.parametrize("kind", ["small", "deprecated", "error"])
def test_failure_in_selected_tool_keeps_levels_and_end_state(inject, kind, where):
    baseline = outcome(run("none"))  # 실패 주입 없이
    assert baseline[0] == "publish_candidates" and baseline[3]
    inject(kind, where)
    picked = set()
    for llm in LLMS:
        s = run(llm)
        assert outcome(s) == baseline, (kind, where, llm)
        items = (report_of(s).investigation.items if report_of(s).investigation else [])
        picked.add(any(i.tool == TOOL for i in items))
        for i in items:
            if i.tool == TOOL:
                assert i.status == "quarantined" and i.evidence_ids == [] and i.reason_code
                assert i.finding.startswith("확인 못 함")
    assert picked == {True, False}  # 그 도구를 고른 변형과 안 고른 변형이 모두 있음 (그래도 같은 끝 상태)


def test_small_cell_never_reaches_output(inject):
    inject("small", "any")
    for llm in ("none", "fake_all_tools"):
        s = run(llm)
        rep = report_of(s)
        # 근거 표·인용·답 어디에도 그 근거(값)가 없음. 판단 기록에는 ID·지표·규칙만 (값 없음)
        assert all(not e.evidence_id.endswith("INV-SMALL") for e in rep.evidence_card.evidence)
        assert all(not i.endswith("INV-SMALL") for it in rep.investigation.items for i in it.evidence_ids)
        assert SMALL_METRIC not in json.dumps([rep.fact_sentences, rep.narrative, s.get("answer")], ensure_ascii=False)
        for t in s["decision_trace"]:
            for x in (t.get("inputs") or {}).get("quarantined") or []:
                assert set(x) == {"evidence_id", "metric", "tool", "rule_ids"}
        assert all(e["evidence_id"] != "EV-20250926-INV-SMALL" for e in s["evidence"] + s["investigation_evidence"])
        q = [x for x in s["quarantine"] if x["evidence_id"].endswith("INV-SMALL")]
        assert q and all(x["rule_ids"] == ["G-08"] for x in q) and all("value" not in x for x in q)


def test_quarantine_recorded_in_trace(inject):
    inject("small")
    s = run("none")  # 규칙 고르기: 우선순위 1 (연휴 회복) → calc_holiday_gap
    item = next(i for i in report_of(s).investigation.items if i.tool == TOOL)
    assert item.status == "quarantined" and item.reason_code == "G-08" and "작은 칸" in item.finding
    step = next(t for t in s["decision_trace"] if t["node"] == "investigate" and (t["inputs"] or {}).get("quarantined"))
    assert "G-08" in step["rule_ids"] and "격리" in step["reason"]
    assert step["inputs"]["quarantined"][0]["evidence_id"] == "EV-20250926-INV-SMALL"
    guard = [g for g in s["guard"] if g.get("stage") in (None, "guard_signal", "신호")]
    assert all("INV-SMALL" not in json.dumps(g, ensure_ascii=False) for g in guard)


def test_error_and_deprecated_reason_codes(inject):
    for kind, code in (("error", "TOOL_ERROR"), ("deprecated", "G-11")):
        inject(kind)
        item = next(i for i in report_of(run("none")).investigation.items if i.tool == TOOL)
        assert item.status == "quarantined" and item.reason_code == code


def test_required_evidence_small_cell_still_abstains():
    """기본(필수) 근거의 작은 칸은 기존 정책: 바로 판단 보류."""
    s = run("none", scenario="small_cell", city=None)
    assert "abstain_report" in s["path"] and "G-08" in (s.get("abstain_reason") or "")
