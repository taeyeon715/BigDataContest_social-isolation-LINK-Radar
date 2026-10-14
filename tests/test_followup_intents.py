"""이어 묻기 질문 종류 5가지 + 직전 대상 기억 (AG-07).

- 종류: 등급 이유 / 자원·대응 / 비교 / 자료 한계 / 기타. 규칙 먼저, 못 정하면 LLM(enum), 없으면 기타
- 자원 질문은 그 후보의 G-07 을 지난 자원 이름·확인일과 대응안으로 답함
- 카드에 없는 비교(지난주 등)는 지어내지 않고 NO_DATA + "기준일을 바꾼 새 조사" 안내
- "그 동네·거기"는 같은 실행의 직전 대상(후보 ID·동 코드)으로 풀고, 질문 원문은 기억하지 않음
가짜 도구만 쓴다.
"""

import json

import pytest

from bigcontest.agent.followup import INTENTS, classify_intent, refers_back, rule_intent
from bigcontest.agent.graph import Agent
from bigcontest.agent.llm import FakeLLM

AS_OF = "2025-09-26"
Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"


@pytest.fixture
def agent(tmp_path):
    a = Agent(tmp_path / "fu.db", tools_backend="stub")
    a.start("fu", "qa", AS_OF, question=Q)
    yield a
    a.close()


@pytest.mark.parametrize("q,intent", [
    ("남면은 왜 Level 3야?", "why_level"), ("어떤 근거로 골랐어?", "why_level"), ("등급 이유 알려줘", "why_level"),
    ("연결할 자원은?", "resources"), ("어떤 서비스로 연계해?", "resources"), ("대응은 뭘 해야 해?", "resources"),
    ("지난주랑 비교하면?", "compare"), ("지난달보다 줄었어?", "compare"), ("전주 대비 어때?", "compare"),
    ("자료 한계는?", "data_limits"), ("이 데이터 믿을 만해?", "data_limits"), ("빠진 근거 있어?", "data_limits"),
    ("요약해 줘", None), ("고마워", None), ("한 줄로 알려 줘", None),
])
def test_rule_intent_15(q, intent):
    assert rule_intent(q) == intent


def test_baseline_compare_is_not_compare():
    assert rule_intent("평소 대비 결제가 왜 낮아?") == "why_level"  # 카드에 있는 이탈 근거


def test_llm_intent_only_when_rule_unsure():
    assert classify_intent("왜 이 등급이야?", FakeLLM("fake"))[:2] == ("why_level", "rule")
    intent, src, rec = classify_intent("요약해 줘", FakeLLM("fake"))
    assert intent in INTENTS and src == "llm" and rec["slot"] == "followup_intent"
    assert classify_intent("요약해 줘", FakeLLM("fake_error"))[:2] == ("other", "default")
    assert classify_intent("요약해 줘", None)[:2] == ("other", "default")


def test_resources_answer_names_resources(agent):
    r = agent.followup("fu", "남면에 연결할 자원은?")
    assert r["status"] == "answered" and r["intent"] == "resources"
    assert "남면 행정복지센터" in r["answer"] and "확인일" in r["answer"] and "대응안" in r["answer"]
    lines = r["answer"].splitlines()
    assert sum(x.startswith("자원: 남면 행정복지센터") for x in lines) == 1  # 여러 후보에 같은 자원이어도 한 줄


def test_why_level_uses_rule_explanation(agent):
    r = agent.followup("fu", "효자1동은 왜 이 등급이야?")
    assert r["status"] == "answered" and r["intent"] == "why_level"
    assert "등급 이유: 적용 규칙" in r["answer"] and "[EV-" in r["answer"]


def test_compare_without_evidence_is_no_data(agent):
    r = agent.followup("fu", "남면 지난주랑 비교하면?")
    assert r["status"] == "abstained" and r["intent"] == "compare"
    assert r["abstain_reason"].startswith("NO_DATA") and "새 조사" in r["needed_data"][0]
    assert r["answer"] is None and r["cited_evidence_ids"] == []


def test_data_limits_lists_card_limits(agent):
    r = agent.followup("fu", "자료 한계는?")
    assert r["status"] == "answered" and r["intent"] == "data_limits" and "한계:" in r["answer"]


def test_that_place_uses_last_target_only(agent):
    first = agent.followup("fu", "그 동네 자원은?")  # 앞선 대상 없음 → 짐작하지 않음
    assert first["status"] == "abstained" and first["abstain_reason"].startswith("NO_TARGET")
    r1 = agent.followup("fu", "효자1동은 왜 이 등급이야?")
    assert r1["target"]["adm_cds"] == ["32010630"]
    r2 = agent.followup("fu", "거기 연결할 자원은?")
    assert r2["status"] == "answered" and r2["target"]["candidate_ids"] == r1["target"]["candidate_ids"]
    assert refers_back("그 동네는?") and refers_back("거기는?") and not refers_back("그래서?")
    # 기억에는 대상 조건만 (질문 원문 없음)
    mem = agent.followup_memory["fu"]
    assert set(mem) == {"candidate_ids", "adm_cds"}
    assert "연결할" not in json.dumps(mem, ensure_ascii=False) and "효자1동은" not in json.dumps(mem, ensure_ascii=False)


def test_general_question_does_not_change_memory(agent):
    agent.followup("fu", "남면은 왜 이 등급이야?")
    before = dict(agent.followup_memory["fu"])
    r = agent.followup("fu", "요약해 줘")
    assert r["target"] is None and agent.followup_memory["fu"] == before


def test_followup_response_schema_has_intent():
    from bigcontest.agent.schemas import FollowupResponse

    FollowupResponse(run_id="r", followup_id="f", status="answered", intent="resources",
                     target={"candidate_ids": ["C-20250926-001"], "adm_cds": []})
