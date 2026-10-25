"""질문 이해 회귀 시험 (시스템 점검 2 AG-01·AG-02·AG-08, 심사 D4): 동 이름·기간 말·LLM 빈칸 채우기·프롬프트 판.

- 동 이름 사전(ref/adm_cd_names.csv)으로 동을 먼저 찾고 그 동으로 도시를 정한다 ("춘천 강남동" → 춘천)
- 여러 동을 가리키는 줄임(역삼동)·도시와 맞지 않는 동(강남 남면)은 고르지 않고 선택지로 되묻는다
- 기간 말("지난주", "이번 달", "추석 전", 날짜)은 periods.py 규칙 하나로 창을 정한다
- LLM 은 규칙이 비운 동·기간 칸만 enum 값으로 채운다
- llm_log 의 prompt_version 은 자리별 프롬프트 판(classify_v3), 설정 판(llm-v1)이 아님
가짜 도구(stub) + LLM 없음 / 가짜 Claude 클라이언트. 네트워크 없음.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from bigcontest.agent import guard as G
from bigcontest.agent import nodes
from bigcontest.agent.graph import Agent, build_graph
from bigcontest.agent.llm import AnthropicLLM, call_llm, classify_schema
from bigcontest.agent.periods import find_period, window
from bigcontest.agent.places import cities_in, resolve_place
from bigcontest.agent.state import initial_state

AS_OF = date(2025, 10, 20)  # 월요일


# ---------------------------------------------------------------- 장소


@pytest.mark.parametrize("q,city,adms", [
    ("춘천 강남동 어르신 어디부터 확인해야 해?", "춘천", ["32010680"]),  # 강남구가 아님
    ("강남동 어르신 결제는?", "춘천", ["32010680"]),  # 도시 없이 동만 → 동의 도시
    ("강남구 역삼1동 어르신 어디부터 확인해야 해?", "강남", ["11230640"]),
    ("춘천 남면 어르신 어디부터 확인해야 해?", "춘천", ["32010350"]),
    ("남면 어르신 어디부터 확인해야 해?", "춘천", ["32010350"]),
    ("춘천 남산면 어르신", "춘천", ["32010360"]),  # 긴 이름 먼저 (남면 아님)
    ("춘천 신사우동 어르신", "춘천", ["32010710"]),  # 강남 신사동 아님
    ("강남 신사동 어르신", "강남", ["11230510"]),
    ("강남 어르신 결제는?", "강남", []),
    ("서면으로 보고할 춘천 어르신 결제는?", "춘천", []),  # "서면으로" 는 서면(면) 아님
])
def test_resolve_place(q, city, adms):
    p = resolve_place(q)
    assert p.ambiguous is None and p.city == city and p.adm_cds == adms


@pytest.mark.parametrize("q,options", [
    ("역삼동 어르신 결제는?", ["강남 역삼1동", "강남 역삼2동"]),  # 줄임이 두 동
    ("춘천 효자동 어르신", ["춘천 효자1동", "춘천 효자2동", "춘천 효자3동"]),
    ("강남 남면 어르신 어디부터 확인해야 해?", ["춘천 남면", "강남 전체"]),  # 동과 도시가 다름
    ("강남구 강남동 어르신", ["춘천 강남동", "강남 전체"]),
])
def test_ambiguous_place_gives_options(q, options):
    p = resolve_place(q)
    assert p.ambiguous and p.options == options and p.city is None and p.adm_cds == []


def test_cities_in_removes_dong_names_first():
    assert cities_in("춘천 강남동") == {"춘천"}
    assert cities_in("강남은 어때?") == {"강남"}
    assert cities_in("춘천 남면 (32010350) 춘천 강남동 (32010680)") == {"춘천"}
    # Guard 도시 검사도 같은 함수 (설명 문장 "춘천 강남동은 …" 을 도시가 다르다고 막지 않음)
    assert G._cities_in("춘천 강남동은 먼저 볼 동입니다") == {"춘천"}
    assert G._cities_in("서울 강남구 결제") == {"강남"}


# ---------------------------------------------------------------- 기간


@pytest.mark.parametrize("q,label,lo,hi", [
    ("지난주 춘천 고령층 결제는?", "지난주", "2025-10-13", "2025-10-19"),
    ("이번 주 춘천 고령층 결제는?", "이번 주", "2025-10-20", "2025-10-20"),
    ("이번 달 춘천 고령층 결제는?", "이번 달", "2025-10-01", "2025-10-20"),
    ("지난달 강남 고령층 결제는?", "지난달", "2025-09-01", "2025-09-30"),
    ("최근 일주일 강남 고령층 결제는?", "최근 7일", "2025-10-14", "2025-10-20"),
    ("추석 전 춘천 고령층 결제는?", "추석 전", "2025-09-26", "2025-10-02"),  # 연휴 10/3~10/9 앞 7일
    ("추석 연휴 춘천 고령층 결제는?", "추석 연휴", "2025-10-03", "2025-10-09"),
    ("추석 뒤 춘천 고령층 결제는?", "추석 후", "2025-10-10", "2025-10-16"),
    ("10월 7일 춘천 고령층 결제는?", "2025-10-07", "2025-10-07", "2025-10-07"),
    ("2025-10-01부터 10/7까지 춘천 고령층 결제는?", "2025-10-01~2025-10-07", "2025-10-01", "2025-10-07"),
    ("9월 춘천 고령층 결제는?", "2025-09", "2025-09-01", "2025-09-30"),
])
def test_period_rule(q, label, lo, hi):
    assert find_period(q) == label
    assert window(label, AS_OF) == (date.fromisoformat(lo), date.fromisoformat(hi))


def test_no_period_words():
    for q in ("춘천 고령층 결제는?", "강남 60대의 밤 카드 결제는?", "강남 1인세대 비율 상위 동은?"):
        assert find_period(q) is None


def test_scope_request_rules():
    req = nodes.normalize_question("지난주 춘천 고령층 결제는?", AS_OF).model_dump(mode="json")
    look, why = nodes.scope_request(req, "lookup", AS_OF)
    assert why is None and (look["period_from"], look["period_to"]) == ("2025-10-13", "2025-10-19")
    inv, why = nodes.scope_request(req, "investigate", AS_OF)  # 조사는 등급이 기준일 기준 → 창 안 씀
    assert why is None and (inv["period_from"], inv["period_to"]) == ("2025-07-01", "2025-10-20")
    assert inv["period_label"] == "지난주"
    # 기준일 뒤 기간 → 보류, 자료 기간 밖 → 보류
    late = nodes.normalize_question("추석 후 춘천 고령층 결제는?", date(2025, 9, 26)).model_dump(mode="json")
    assert nodes.scope_request(late, "lookup", date(2025, 9, 26))[1].startswith("TIME_PERIOD_AFTER_AS_OF")
    early = nodes.normalize_question("지난달 춘천 고령층 결제는?", date(2025, 7, 10)).model_dump(mode="json")
    assert nodes.scope_request(early, "lookup", date(2025, 7, 10))[1].startswith("TIME_PERIOD_OUT_OF_DATA")
    # 동: 조회는 adm_cd·차원, 조사는 adm_cds 만 (카드 결제 × 동 금지 결합에 걸리지 않게)
    dong = nodes.normalize_question("춘천 남면 어르신 어디부터 확인해야 해?", AS_OF).model_dump(mode="json")
    inv, _ = nodes.scope_request(dong, "investigate", AS_OF)
    assert inv["adm_cd"] is None and "adm_cd" not in inv["dimensions"] and inv["adm_cds"] == ["32010350"]
    one = nodes.normalize_question("강남구 역삼1동 1인세대 비율은?", AS_OF).model_dump(mode="json")
    look, _ = nodes.scope_request(one, "lookup", AS_OF)
    assert look["adm_cd"] == "11230640" and "adm_cd" in look["dimensions"]


# ---------------------------------------------------------------- 그래프 (가짜 도구)


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    a = Agent(tmp_path_factory.mktemp("qu") / "runs.db", tools_backend="stub")
    yield a
    a.close()


def test_chuncheon_gangnamdong_is_chuncheon(agent):
    st = agent.start("qu-gnd", "qa", "2025-10-20", question="춘천 강남동 어르신 어디부터 확인해야 해?")
    v = st["values"]
    assert st["waiting"] and v["route"] == "investigate"
    assert v["request"]["city"] == "춘천" and v["request"]["adm_cds"] == ["32010680"]
    assert all(c["city"] == "춘천" for c in v["candidates"])
    assert v["place_scope"]["adm_cds"] == ["32010680"]


def test_nammyeon_keeps_only_that_dong(agent):
    st = agent.start("qu-nm", "qa", "2025-10-20", question="춘천 남면 어르신 어디부터 확인해야 해?")
    v = st["values"]
    assert v["candidates"] and all(
        "32010350" in {c.get("adm_cd"), *c["check_first_adm"]} for c in v["candidates"])
    whole = agent.start("qu-all", "qa", "2025-10-20", question="춘천 어르신 어디부터 확인해야 해?")["values"]
    lv = {c["candidate_id"]: c["candidate_level"] for c in whole["candidates"]}
    assert all(lv[c["candidate_id"]] == c["candidate_level"] for c in v["candidates"])  # 등급은 도시 전체와 같음
    assert any(s.startswith("질문한 동: 춘천 남면") for s in v["report"]["fact_sentences"])


@pytest.mark.parametrize("q", ["역삼동 어르신 결제는?", "강남 남면 어르신 어디부터 확인해야 해?"])
def test_ambiguous_place_abstains_with_options_without_llm(agent, q):
    st = agent.start(f"qu-amb-{len(q)}", "qa", "2025-10-20", question=q, llm="fake")
    v = st["values"]
    assert v["path"][-1] == "abstain_answer" and v["abstain_reason"].startswith("AMBIGUOUS_PLACE")
    assert "선택지:" in v["abstain_reason"] and v["llm_log"] == []  # 짐작하지 않고 되묻기, LLM 안 부름
    assert v["answer"]["needed_data"]


def test_last_week_lookup_uses_window(agent):
    v = agent.start("qu-lw", "qa", "2025-10-20", question="지난주 춘천 고령층 결제는?")["values"]
    assert v["route"] == "lookup" and v["path"][-1] == "answer"
    assert (v["request"]["period_from"], v["request"]["period_to"]) == ("2025-10-13", "2025-10-19")
    assert any(s.startswith("조회 기간: '지난주'") for s in v["report"]["fact_sentences"])


def test_future_period_lookup_abstains(agent):
    v = agent.start("qu-fut", "qa", "2025-09-26", question="추석 뒤 춘천 고령층 결제는?")["values"]
    assert v["path"][-1] == "abstain_answer" and v["abstain_reason"].startswith("TIME_PERIOD_AFTER_AS_OF")


def test_period_word_does_not_change_investigate_levels(agent):
    base = agent.start("qu-p0", "qa", "2025-09-26", question="춘천 어르신 어디부터 확인해야 해?")["values"]
    asked = agent.start("qu-p1", "qa", "2025-09-26", question="지난주 춘천 어르신 어디부터 확인해야 해?")["values"]
    lv = lambda v: {c["candidate_id"]: c["candidate_level"] for c in v["candidates"]}  # noqa: E731
    assert lv(asked) == lv(base)
    assert any("등급 계산에 쓰지 않음" in s for s in asked["report"]["fact_sentences"])


def test_d4_supported_card1_path_stub(agent):
    v = agent.start("qu-c1", "qa", "2025-10-20", question="강남 60대의 밤 카드 결제는 어때?")["values"]
    assert v["contract_check"]["source"] == "card1" and v["tools_run"] == ["get_card_time_band"]
    assert v["path"][-1] == "answer"
    assert nodes.CARD1_NOTE in v["report"]["fact_sentences"]


def test_d4_activity_still_abstains_with_alternative(agent):
    v = agent.start("qu-d4", "qa", "2025-10-20", question="강남 60대의 밤 활동은 어때?")["values"]
    assert v["contract_check"]["issues"][0]["code"] == "GRAIN_FORBIDDEN_COMBO"
    alt = [n for n in v["answer"]["needed_data"] if n.startswith("대안:")]
    assert alt and "카드 데이터1" in alt[0] and "어디 사는지" in alt[0]
    # 진짜로 없는 조합(통신 연령 × 시간대)은 대안을 붙여도 보류
    v2 = agent.start("qu-d4b", "qa", "2025-10-20", question="강남 60대의 밤 체류 인구는 어때?")["values"]
    assert v2["answer"]["status"] == "abstained"


# ---------------------------------------------------------------- LLM 빈칸 채우기 · 프롬프트 판


class FakeClient:
    def __init__(self, texts):
        self.texts, self.requests = list(texts), []
        self.messages = self

    def create(self, **kw):
        self.requests.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.texts.pop(0))],
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def _classify_out(route="investigate", **req):
    base = {k: None for k in ("city", "metric", "group", "age_min", "age_max", "sex", "time_of_day", "adm_names",
                              "period")}
    return json.dumps({"route": route, "request": {**base, **req}, "reason": "r"})


def _classify_with(monkeypatch, question, out, as_of="2025-10-20"):
    llm = AnthropicLLM(client=FakeClient([out]))
    monkeypatch.setattr(nodes, "_llm", lambda state: (llm, None))
    st = initial_state("fill", "qa", as_of, question=question, tools_backend="stub", llm="claude")
    st.update(nodes.intake(st))
    return {**st, **nodes.classify(st)}


def test_llm_fills_only_empty_fields(monkeypatch):
    # 규칙이 동을 못 읽음("남 면") → LLM 동 이름으로 채움. 기간은 규칙이 읽었으니 LLM 값(이번 달)은 버림
    out = _classify_with(monkeypatch, "지난주 춘천 남 면 어르신 어디부터 확인해야 해?",
                         _classify_out(city="춘천", adm_names=["남면"], period="이번 달"))
    assert out["request"]["adm_cds"] == ["32010350"] and out["request"]["period_label"] == "지난주"
    rec = next(r for r in out["llm_log"] if r["slot"] == "classify")
    assert rec["llm_filled"] == ["adm_cds"] and rec["prompt_version"] == "classify_v3"


def test_llm_cannot_override_rule_dong_or_cross_city(monkeypatch):
    out = _classify_with(monkeypatch, "춘천 남면 어르신 어디부터 확인해야 해?",
                         _classify_out(city="춘천", adm_names=["동면"]))
    assert out["request"]["adm_cds"] == ["32010350"]  # 규칙 값 유지
    out = _classify_with(monkeypatch, "춘천 어르신 어디부터 확인해야 해?",
                         _classify_out(city="춘천", adm_names=["역삼1동"]))
    assert out["request"]["adm_cds"] == []  # 다른 도시 동은 버림
    rec = next(r for r in out["llm_log"] if r["slot"] == "classify")
    assert rec["llm_fill_rejected"]


def test_llm_fills_period_when_rule_empty(monkeypatch):
    out = _classify_with(monkeypatch, "춘천 고령층 결제 요즘 어때?", _classify_out(route="lookup", period="최근 7일"))
    assert out["route"] == "lookup" and out["request"]["period_label"] == "최근 7일"
    assert (out["request"]["period_from"], out["request"]["period_to"]) == ("2025-10-14", "2025-10-20")


def test_classify_schema_enums():
    req = classify_schema()["properties"]["request"]["properties"]
    names = req["adm_names"]["anyOf"][0]["items"]["enum"]
    assert "강남동" in names and "역삼1동" in names and len(names) == 47
    assert "지난주" in req["period"]["anyOf"][0]["enum"]


def test_llm_log_prompt_version_is_slot_prompt():
    """AG-08/AG-15: 기록의 프롬프트 판은 자리별 프롬프트 파일 이름 (설정 판 llm-v1 이 아님)."""
    llm = AnthropicLLM(client=FakeClient([_classify_out(city="춘천")]))
    _, rec = call_llm(llm, "classify", llm.classify, "춘천 어르신", None)
    assert rec["prompt_version"] == "classify_v3" and rec["prompt_version"] != llm.prompt_version
    llm = AnthropicLLM(client=FakeClient([]))
    _, rec = call_llm(llm, "choose_extra_tools", llm.choose_extra_tools, {}, [], [])  # API 호출 없이 끝남
    assert rec["prompt_version"] == "choose_tools_v1"


def test_followup_other_city_abstains_on_stub_card(agent):
    from bigcontest.agent.followup import answer_followup

    v = agent.start("qu-fu", "qa", "2025-10-20", question="춘천 어르신 어디부터 확인해야 해?")["values"]
    out = answer_followup(v, "강남은 어때?", "none")
    assert out["status"] == "abstained" and out["abstain_reason"].startswith("OUT_OF_SCOPE")
    out = answer_followup(v, "강남동은 어때?", "none")  # 춘천 동 → 도시는 맞음, 카드에 후보가 없으면 NO_TARGET
    assert not out["abstain_reason"] or not out["abstain_reason"].startswith("OUT_OF_SCOPE")


def test_graph_runs_end_to_end_without_llm():
    out = build_graph().invoke(initial_state("qu-e2e", "qa", "2025-10-20", question="강남구 역삼1동 1인세대 비율은?",
                                             tools_backend="stub"), {"recursion_limit": 100})
    assert out["path"][-1] == "answer" and out["request"]["adm_cd"] == "11230640"
