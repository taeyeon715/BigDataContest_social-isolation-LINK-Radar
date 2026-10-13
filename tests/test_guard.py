"""Guard 테스트 (에이전트 설계 §6, §10): guard_cases.yaml 의 사례마다 기대 판정.

- 사실 검사는 (지표, 지역, 기간, 값, 단위, 방향, 근거 ID) 묶음을 근거 카드와 대조 → 사실 바꿔치기를 모두 잡음
- 표현 검사는 따로 기록
- 규칙 G-01~G-14 마다 사례가 하나 이상 있음
"""

import copy
from datetime import date
from pathlib import Path

import pytest
import yaml

from bigcontest.agent import guard as G
from bigcontest.agent import nodes
from bigcontest.agent.graph import build_graph
from bigcontest.agent.schemas import Candidate, EvidenceCard, Fact, GuardResult, Report, ToolResult
from bigcontest.agent.state import card_dict, initial_state, report_dict

CASES = yaml.safe_load((Path(__file__).parent / "guard_cases.yaml").read_text(encoding="utf-8"))
AS_OF = "2025-10-20"


@pytest.fixture(scope="module")
def base() -> dict:
    """정기 감시 normal 시나리오의 끝 상태 (체크포인터 없이)."""
    app = build_graph()
    out = app.invoke(initial_state("guard-base", "monitoring", AS_OF, tools_backend="stub"), {"recursion_limit": 100})
    assert out["path"][-1] == "publish_candidates"
    # 상태에는 카드·보고서가 근거 ID·카드 ID 로만 들어 있음. 시험은 전체 모양으로 펼쳐서 씀
    return {**out, "evidence_card": card_dict(out), "report": report_dict(out)}


def _apply_evidence(evidence: list[dict], case: dict, mode: str | None) -> list[ToolResult]:
    evidence = copy.deepcopy(evidence)
    if mode:
        for e in evidence:
            e["analysis_mode"] = mode
            if mode != "simulated_replay":
                e["warnings"] = [w for w in e["warnings"] if not w.startswith("가정한 공개 시점")]
    if "evidence_set" in case:
        m = case["evidence_set"]
        for e in evidence:
            if e["evidence_id"].endswith(m["match"]):
                e.update(m["set"])
    if "evidence_add" in case:
        tmpl = {k: v for k, v in evidence[0].items() if k in ("analysis_mode", "as_of", "snapshot_id")}
        evidence.append({**tmpl, **case["evidence_add"]})
    return [ToolResult(**e) for e in evidence]


def _report_case(base: dict, case: dict) -> tuple[GuardResult, GuardResult, Report]:
    report = copy.deepcopy(base["report"])
    sentences = report["fact_sentences"]
    if "replace" in case:
        old, new = case["replace"]
        i = next(i for i, s in enumerate(sentences) if old in s)
        sentences[i] = sentences[i].replace(old, new, 1)
    if "drop" in case:
        report["fact_sentences"] = [s for s in sentences if case["drop"] not in s]
    if "narrative" in case:
        report["narrative"] = "\n".join(filter(None, [report.get("narrative"), case["narrative"]]))
    card = report["evidence_card"]
    if case.get("mode") or "evidence_set" in case:
        card["evidence"] = [e.model_dump(mode="json") for e in _apply_evidence(card["evidence"], case, case.get("mode"))]
        if case.get("mode"):
            card["analysis_mode"] = case["mode"]
            card["time_label"] = None
    rep = Report(**report)
    return G.check_facts(rep, rep.evidence_card), G.check_expression(rep, rep.evidence_card), rep


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] == "report"], ids=lambda c: c["id"])
def test_report_cases(base, case):
    facts, expr, _ = _report_case(base, case)
    got = {"사실": facts, "표현": expr}
    for check, want in case["expect"].items():
        assert got[check].verdict == want, (check, got[check].reasons)
    seen = set(facts.rule_ids) | set(expr.rule_ids)
    for r in case.get("rules", []):
        assert r in seen, (r, facts.reasons, expr.reasons)
    # 사실 검사와 표현 검사는 따로 기록
    assert facts.check == "사실" and expr.check == "표현"


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] == "signal"], ids=lambda c: c["id"])
def test_signal_cases(base, case):
    mode = case.get("mode") or base["analysis_mode"]
    evidence = _apply_evidence(base["evidence"], case, case.get("mode"))
    cands = copy.deepcopy(base["candidates"])
    if "candidate_set" in case:
        for c in cands:
            if c["candidate_type"] == case["candidate_set"]["type"]:
                c.update(case["candidate_set"]["set"])
    gr = G.check_signal(evidence, cands, mode, date.fromisoformat(AS_OF))
    assert gr.verdict == case["expect"]["신호"], gr.reasons
    for r in case.get("rules", []):
        assert r in gr.rule_ids, gr.reasons


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] == "resource"], ids=lambda c: c["id"])
def test_resource_cases(base, case):
    resources = copy.deepcopy(base["resources"]) + [case["resource"]]
    state = {**base, "resources": resources}
    upd = nodes.guard_report(state)
    expr = GuardResult(**upd["guard"][-1])
    assert upd["verdict"] == "PASS" and expr.verdict == case["expect"]["표현"]
    kept = {r["resource_id"] for r in upd["resources"]}
    for rid in case["removed"]:
        assert rid not in kept
        assert any(rid in x and "G-07" in x for x in expr.reasons)
    assert kept  # 나머지 자원은 남음
    nodes.to_resources(upd)  # 남은 자원은 스키마를 통과


@pytest.mark.parametrize("case", [c for c in CASES if c["kind"] == "contract"], ids=lambda c: c["id"])
def test_contract_cases(case):
    req = nodes.NormalizedRequest(**case["request"], period_to=date.fromisoformat(AS_OF))
    state = {"request": req.model_dump(mode="json"), "as_of": AS_OF, "route": "lookup",
             "analysis_mode": case.get("mode", "simulated_replay")}
    upd = nodes.check_availability(state)
    cc = upd["contract_check"]
    if case["expect"]["계약"] == "PASS":
        assert cc["status"] == "OK" and not upd.get("abstain_reason")
        return
    assert cc["status"] == "ERROR" and upd["abstain_reason"]
    assert {i["rule"] for i in cc["issues"]} >= set(case["rules"])
    assert {i["code"] for i in cc["issues"]} >= set(case["codes"])


def test_every_rule_has_a_case():
    covered = {r for c in CASES for r in c.get("rules", [])}
    assert covered == {f"G-{i:02d}" for i in range(1, 15)}
    assert set(G.rules()["rules"]) == {f"G-{i:02d}" for i in range(1, 15)}


def test_verdict_mapping_from_config():
    # 에이전트 설계 §6 "규칙별 처리 → 세 판정"
    assert G.rule_verdict("G-07") == "PASS"
    assert G.rule_verdict("G-08") == "ABSTAIN"
    assert G.rule_verdict("G-10") == "REVISE"
    assert G.rule_verdict("G-13", "simulated_replay") == "REVISE"
    assert G.rule_verdict("G-13", "strict_as_of") == "ABSTAIN"
    assert G.rule_verdict("G-14") == "REVISE"


def test_revise_limit_then_abstain():
    gr = GuardResult(guard="guard_report", check="사실", verdict="REVISE", rule_ids=["G-05"], reasons=["x"])
    assert G.decide(gr, 0) == ("REVISE", None)
    assert G.decide(gr, 1) == ("REVISE", None)
    v, why = G.decide(gr, 2)  # 세 번째 REVISE → ABSTAIN
    assert v == "ABSTAIN" and "REVISE 2회" in why
    v, _ = G.decide(gr, 0, allow_revise=False)  # 조회는 바로 ABSTAIN
    assert v == "ABSTAIN"


def test_fact_roundtrip():
    f = Fact(metric="생필품 결제 평소 대비", region="춘천시 가맹점 · 강원 거주", period="2025-10-07~2025-10-13",
             value=0.78, unit="평소 대비 비율", direction="감소", evidence_id="EV-20251020-DEV")
    assert G.parse_fact(G.render_fact(f)).model_dump() == f.model_dump()
    g = Fact(metric="1인세대 비율", region="강남 역삼1동 (11230640)", period="2025-09", value=0.68, unit="비율",
             direction=None, evidence_id="EV-20251020-STR-1")
    assert G.parse_fact(G.render_fact(g)).model_dump() == g.model_dump()


def test_card_is_source_of_facts(base):
    card = EvidenceCard(**base["evidence_card"])
    assert card.facts and all(f.evidence_id in {e.evidence_id for e in card.evidence} for f in card.facts)
    # 보고서의 사실 문장은 모두 근거 카드에서 나옴
    report = Report(**base["report"])
    parsed = [G.parse_fact(s) for s in report.fact_sentences if G.parse_fact(s)]
    assert len(parsed) == len(card.facts)
    assert all(not G.compare_fact(p, card) for p in parsed)
    assert all(Candidate(**c) for c in base["candidates"])
