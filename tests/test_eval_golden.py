"""골든 질문 평가 (AG-08~10). 가짜 도구 + LLM 없음/가짜로 비용 0, 결과 고정.

- 골든 질문 20개: 경로·대상(도시·동·집단)·끝점 정확도
- 이어 묻기 골든 15개: 종류·상태(answered / NO_TARGET / NO_DATA) 정확도
- 설명 지표 계산(인용률·1회 Guard 통과율·대상 충실도)이 실제로 차이를 잡는지 (틀린 사실을 넣는 가짜 LLM → 1회 통과율 0)
"""

import pytest

from bigcontest.agent.evaluation import explanation_metrics, load_golden, run_golden, sentence_faithful, table


@pytest.fixture(scope="module")
def none_run(tmp_path_factory):
    return run_golden("none", db_dir=tmp_path_factory.mktemp("g0"))


def test_golden_set_shape():
    g = load_golden()
    assert len(g["cases"]) == 20 and len({c["id"] for c in g["cases"]}) == 20
    assert len(g["followups"]["cases"]) == 15
    assert {c["expect"]["endpoint"] for c in g["cases"]} == {"await_approval", "answer", "abstain_answer"}
    intents = [c["intent"] for c in g["followups"]["cases"]]
    assert all(intents.count(i) == 3 for i in ("why_level", "resources", "compare", "data_limits", "other"))


def test_golden_rule_only_all_correct(none_run):
    m = none_run["metrics"]
    bad = [(r["id"], r["want"], r["got"]) for r in none_run["rows"]
           if not (r["route_ok"] and r["target_ok"] and r["endpoint_ok"])]
    assert not bad
    assert m["route_accuracy"] == m["target_accuracy"] == m["endpoint_accuracy"] == 1.0
    fbad = [(f["q"], f["got_intent"], f["got_status"]) for f in none_run["followups"]
            if not (f["intent_ok"] and f["status_ok"] and f["contains_ok"])]
    assert not fbad
    assert m["first_pass_guard_rate"] == 1.0  # 템플릿만이면 한 번에 통과


def test_golden_fake_llm_same_targets(tmp_path):
    m = run_golden("fake", db_dir=tmp_path)["metrics"]
    assert m["target_accuracy"] == 1.0 and m["route_accuracy"] == 1.0


def test_metrics_catch_wrong_facts(tmp_path):
    m = run_golden("fake_wrong_fact_once", db_dir=tmp_path)["metrics"]
    assert m["first_pass_guard_rate"] == 0.0  # 첫 보고서는 모두 사실 검사에 걸림 (REVISE 뒤 통과)
    assert m["target_accuracy"] == 1.0


def test_target_faithfulness_rule():
    cands = {"C-20250926-001": {"evidence_ids": ["EV-20250926-STR-32010350"]}}
    ev = {"EV-20250926-STR-32010350": {"applied_scope": "춘천 남면 (32010350)"},
          "EV-20250926-STR-32010630": {"applied_scope": "춘천 효자1동 (32010630)"},
          "EV-20250926-TRG": {"applied_scope": "춘천"}}
    assert sentence_faithful("C-20250926-001 은 구조 근거가 있음 [EV-20250926-STR-32010350]", cands, ev) == [True]
    assert sentence_faithful("C-20250926-001 [EV-20250926-STR-32010630]", cands, ev) == [False]  # 다른 동 근거
    assert sentence_faithful("C-20250926-001 [EV-20250926-TRG]", cands, ev) == [True]  # 도시 근거
    assert sentence_faithful("후보 없이 [EV-20250926-TRG]", cands, ev) == []
    # 후보 ID 가 없으면 문장이 말한 동 이름으로
    assert sentence_faithful("남면은 구조 근거 [EV-20250926-STR-32010350]", cands, ev) == [True]
    assert sentence_faithful("남면은 [EV-20250926-STR-32010630]", cands, ev) == [False]
    assert explanation_metrics([])["citation_coverage"] is None


def test_table_prints(none_run):
    out = table({"none": none_run["metrics"]})
    assert "route_accuracy" in out and "100%" in out
