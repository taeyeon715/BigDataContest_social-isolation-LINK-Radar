"""시연 사례 D1~D4: 실제 도구(dev 스냅샷) + 가짜 LLM / LLM 없음. 자료가 없으면 건너뜀.

끝점·등급·Guard 판정을 고정한다. LLM 설명 문장은 달라도 된다.
- D1 강남 구조 취약성 검토 (추석 D-7, 중장년 남성): 1인세대 상위 동만 후보·Level 2, 강남은 Level 3 없음,
  시민생활은 탐색적 참고, "거리 공백 근거 약함", 자원 운영 상태 "확인 필요"
- D2 춘천 읍·면 동절기 점검 (12/20, 고령층): 동절기 트리거는 읍·면만, 면 지역 Level 3, 찾아가는 서비스 자원,
  첫 자원은 노인맞춤돌봄서비스(제도), 그다음 면 행정복지센터, 위기 전화는 맨 아래 "위급 시 안내". Level 3 의 접근성 축은 2026년 원장 거리라
  "현재 원장 기준 접근성"으로 표시 (simulated_replay. strict 에서는 안 셈)
- D3 달력 효과로 경보 처리 (10/15): 춘천 20대 여성 10/07 경보는 기각 → Level 1. 기대 활동(EDA-6 기준선)이 연결돼
  필수 근거는 충족.
  같은 날 강남 고령층 경보는 대부분 "확인 필요(도시 공통 변화)" → 판단 보류 (기각 아님)
- D4 근거 부족 보류: "강남 60대의 밤 체류 인구" → 보류 답변 + 필요한 자료 + 결제 기준 대안 안내.
  통신은 성연령 표와 시간대 표가 따로라 "60대 밤 체류"는 정말로 알 수 없음
- D4B 결제 기준이면 답함: "강남 60대의 밤 카드 결제" → 카드 데이터1(보조, 연령 × 시간대 4구간)로 조회.
  가맹점 기준이고 거주지가 없다는 한계를 같이 적음 (심사 점검: 예전 D4 예시는 카드 데이터1로 답할 수 있었음)
등급은 configs/rules.yaml (rules-v1.1). D1 강남 7곳 Level 2 (구조 상위·접근성 기준으로 22곳에서 줄어듦),
D2 읍·면 8곳 중 Level 3 3곳.
"""

import pytest

from bigcontest.agent.graph import Agent, build_graph
from bigcontest.agent.state import initial_state
from bigcontest.agent.tools.datasource import dev_available

pytestmark = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")

D1 = ("2025-09-26", "추석 전에 강남 1인세대 많은 동의 중장년 남성은 어디부터 점검해야 해?")
D2 = ("2025-12-20", "춘천 읍·면 어르신 겨울철 점검은 어디부터 해야 해?")
D3 = ("2025-10-15", "추석 연휴 뒤 춘천 20대 여성 카드 결제 이탈 경보를 조사해줘")
D3B = ("2025-10-15", "추석 연휴 뒤 강남 어르신 카드 결제 이탈 경보를 조사해줘")
D4 = ("2025-10-20", "강남 60대의 밤 체류 인구는 어때?")
D4B = ("2025-10-20", "강남 60대의 밤 카드 결제는 어때?")
NAMMYEON = ("2025-12-20", "춘천 남면 어르신 어디부터 확인해야 해?")
GANGNAMDONG = ("2025-12-20", "춘천 강남동 어르신 어디부터 확인해야 해?")
LLMS = ["none", "fake", "fake_all_tools", "fake_no_tools", "fake_error", "fake_wrong_fact_once"]


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    a = Agent(tmp_path_factory.mktemp("demo") / "runs.db", tools_backend="real")
    yield a
    a.close()


_RUNS: dict = {}


def run(agent, name, case, llm="none") -> dict:
    key = (name, llm)
    if key not in _RUNS:
        as_of, q = case
        _RUNS[key] = agent.start(f"{name}-{llm}", "qa", as_of, question=q, llm=llm)
    return _RUNS[key]


def levels(v: dict) -> dict:
    return {c["candidate_id"]: (c["candidate_type"], c.get("adm_cd"), c["analysis_cohort"], c["input_status"],
                                c["counter_result"], c["candidate_level"]) for c in v["candidates"]}


def final_guard(v: dict) -> list[tuple[str, str, str]]:
    """마지막 Guard 판정 (신호 1개 + 사실·표현 2개)."""
    g = v["guard"]
    sig = [x for x in g if x["guard"] == "guard_signal"][-1]
    rep = [x for x in g if x["guard"] == "guard_report"][-2:]
    return [(x["guard"], x["check"], x["verdict"]) for x in [sig, *rep]]


def text(v: dict) -> str:
    r = v["report"]
    return "\n".join(r["fact_sentences"] + ([r["narrative"]] if r.get("narrative") else []))


PASS3 = [("guard_signal", "신호", "PASS"), ("guard_report", "사실", "PASS"), ("guard_report", "표현", "PASS")]


# ---------------------------------------------------------------- D1


def test_d1_gangnam_structure_review(agent):
    st = run(agent, "D1", D1)
    v = st["values"]
    assert st["waiting"] and v["route"] == "investigate"  # 끝점: 승인 대기
    assert final_guard(v) == PASS3
    by_adm = {c["adm_cd"]: c for c in v["candidates"] if c["candidate_type"] == "scheduled_access_review"}
    # rules-v2.0: 후보 동도 칸 판정(남성 40·50대)으로 정함. 통신 행동 고립 신호가 있으면 Level 2,
    # 동 전체 1인세대 상위로만 들어온 동(삼성2동·일원1동)은 통신 신호가 없어 Level 1
    assert len(by_adm) == 11 and all("R1 추석" in c["trigger"] for c in by_adm.values())
    lv = {a: c["candidate_level"] for a, c in by_adm.items()}
    assert sorted(a for a, x in lv.items() if x == "Level 1") == ["11230590", "11230730"]  # 삼성2동·일원1동
    assert all(c["rule_id"].startswith("V2-L2-DATA-TRIGGER") for c in by_adm.values() if c["candidate_level"] == "Level 2")
    assert lv["11230640"] == lv["11230520"] == "Level 2"  # 역삼1동·논현1동
    assert all("칸 판정" in c["level_reason"] for c in by_adm.values())
    assert {c["event_id"] for c in by_adm.values()} == {"강남:R1:20251003"}
    assert all(c["event_opened_at"] == "2025-09-26" for c in by_adm.values())  # 트리거 당일(D-7) 생성
    assert not any(c["candidate_level"] == "Level 3" for c in v["candidates"])  # 중장년은 연휴 카드 근거가 없음 → 통신 신호 하나
    assert all(c["analysis_cohort"] == "40대·50대" for c in by_adm.values())
    t = text(v)
    assert "거리 공백 근거 약함" in t
    assert "시민생활" in t and "탐색적 참고" in t
    assert v["resources"] and all(r["operating_status"] == "확인 필요" for r in v["resources"])  # 가용성 확인 필요
    assert all(r["city"] == "강남" for r in v["resources"])
    # 공식 페이지로 확인일을 채운 외로움안녕120(전화형)이 추천에 들어감
    assert any(r["resource_id"] == "R0739" for r in v["resources"])
    # 연휴(R1): 휴일 운영 약국·병의원(E-Gen 공휴일 진료시간)이 의료 접근으로 들어감. 명절 당일은 다시 확인
    holiday = [r for r in v["resources"] if r["resource_type"] in ("휴일 운영 약국", "휴일 운영 병·의원")]
    assert holiday and all(any("명절 당일 운영" in w for w in r["warnings"]) for r in holiday)
    # 공식 자료로 지금 활동을 확인하지 못한 사람 자원(우리동네돌봄단)은 추천하지 않고 '확인할 자원'으로
    assert not any(r["resource_id"] == "PP-GN02" for r in v["resources"])
    env = [e for e in v["evidence"] if e["metric"] == "복지 자원 후보"]
    assert any(any(w.startswith("확인할 자원(추천 제외): PP-GN02") for w in e["warnings"]) for e in env)


def test_d1_approve_finalizes_once(agent):
    st = run(agent, "D1", D1)
    rid = "D1-none"
    rev = st["values"]["report_revision"]
    first = agent.approve(rid, "approved", rev)
    again = agent.approve(rid, "approved", rev)
    assert first["status"] == "accepted" and first["record"]["kind"] == "확정"
    assert again["status"] == "duplicate"


# ---------------------------------------------------------------- D2


def test_d2_chuncheon_winter_review(agent):
    st = run(agent, "D2", D2)
    v = st["values"]
    assert st["waiting"] and final_guard(v) == PASS3
    assert "calc_winter_contraction" in v["tools_run"]
    cands = v["candidates"]
    # 동절기는 읍·면 60대 이상 칸 판정: 남산면·동산면은 통신 신호 2개(행동 고립 + 겨울 체류 감소) + 외부 확인이라 Level 3,
    # 남면·북산면은 통신 신호 하나라 Level 2. 동산면은 칸 판정으로 새로 든 면
    assert len(cands) == 8 and all(c["trigger"].startswith("W 동절기") for c in cands)
    assert all(c["input_status"] == "충족" for c in cands)
    l3 = {c["adm_cd"] for c in cands if c["candidate_level"] == "Level 3"}
    assert l3 == {"32010360", "32010320"} and all(c["rule_id"] == "V2-L3-DATA2-EXT1" for c in cands if c["adm_cd"] in l3)
    # 나머지는 겨울 체류 감소(통신) 또는 행동 고립 하나 → Level 2 (외부 자료만으로는 오르지 않음)
    assert all(c["candidate_level"] == "Level 2" and c["rule_id"].startswith("V2-L2-DATA-TRIGGER")
               for c in cands if c["adm_cd"] not in l3)
    assert {c["event_id"] for c in cands} == {"춘천:W:2025"}
    t = text(v)
    assert "직선" in t and "춘천 읍·면 겨울 변화" in t
    selected = {c["candidate_id"] for c in cands if c["selected_for_review"]}
    res = [r for r in v["resources"] if r["candidate_id"] in selected]
    assert res  # 거리가 가깝더라도 검토·자원 연결 흐름은 유지된다.
    # 제외 규칙(확인일 없음 등)을 먼저 적용한 뒤 상위 K → G-07 에서 다시 빠지는 자원이 없음
    assert "자원 제외(G-07)" not in t and all(r["verified_date"] for r in v["resources"])
    # 위기 전화는 첫 추천이 아니라 맨 아래 "위급 시 안내". 공식 페이지 확인 뒤 확인일이 있음
    for cid in selected:
        mine = [r for r in v["resources"] if r["candidate_id"] == cid]
        crisis = [i for i, r in enumerate(mine) if r.get("group") == "위급 시 안내"]
        assert not crisis  # 현재 세 면은 원거리형이 아니므로 위기 전화 묶음을 강제로 붙이지 않는다.
        assert crisis == list(range(len(mine) - len(crisis), len(mine)))  # 모두 맨 아래
        # 고령층 안부·관계 첫 순위: 노인맞춤돌봄서비스(제도, 신청은 행정복지센터) → 면 행정복지센터
        assert [r["resource_type"] for r in mine[:2]] == ["노인맞춤돌봄서비스", "행정복지센터"]
    env = [e for e in v["evidence"] if e["metric"] == "복지 자원 후보"]
    assert env and all(any("2025년 당시 운영·위치는 확인되지 않음" in w for w in e["warnings"]) for e in env)
    assert all(e["availability_basis"] == "unknown" and e["observed_to"] is None for e in env)


# ---------------------------------------------------------------- D3


def test_d3_calendar_effect_rejects_real_alarm(agent):
    st = run(agent, "D3", D3)
    v = st["values"]
    assert st["waiting"] and final_guard(v) == PASS3
    (c,) = v["candidates"]
    assert c["candidate_type"] == "unexpected_activity_deviation" and c["analysis_cohort"] == "20대 여성"
    assert c["counter_result"] == "기각" and c["candidate_level"] == "Level 1"
    assert "연휴 뒤 평소 수준으로 회복" in c["counter_reason"]
    assert c["is_shared_context"] and c["check_first_adm"]
    assert c["input_status"] == "충족" and not c["missing_inputs"]  # 기대 활동 = EDA-6 기준선
    assert any("-EXP-20F" in i for i in c["evidence_ids"]) and any("-FC-20F" in i for i in c["evidence_ids"])
    assert not any(e["metric"] == "달력 트리거" and e["status"] == "OK" and "상황 시연" in str(e["value"])
                   for e in v["evidence"])  # 기본 경로에는 상황 시연 없음


def test_d3_city_common_drop_is_not_rejected(agent):
    v = run(agent, "D3B", D3B)["values"]
    cs = v["candidates"]
    assert cs and all(c["candidate_type"] == "unexpected_activity_deviation" for c in cs)
    assert not any(c["counter_result"] == "기각" for c in cs)
    held = [c for c in cs if c["candidate_level"] == "판단 보류"]
    assert len(held) == 5 and all("확인 필요" in c["counter_reason"] for c in held)
    # 9/19 60대 남성 경보는 창 평균 하락이 절대 최소값(10%) 미만 → Level 1 (rules-v1.1)
    assert [c["rule_id"] for c in cs if c["candidate_level"] == "Level 1"] == ["L1-DEV-SMALL"]
    assert final_guard(v) == PASS3


# ---------------------------------------------------------------- D4


def test_d4_insufficient_evidence_abstains(agent):
    st = run(agent, "D4", D4)
    v = st["values"]
    assert v["path"][-1] == "abstain_answer" and v["answer"]["status"] == "abstained"
    assert v["contract_check"]["issues"][0]["code"] == "GRAIN_FORBIDDEN_COMBO"
    assert v["answer"]["needed_data"] and v["report"]["is_abstain"]
    assert v["tools_run"] == []  # 계약 검사에서 멈춤
    # 결제 기준이면 볼 수 있다는 대안 안내 (거주지·체류는 모른다는 한계 포함)
    alt = [n for n in v["answer"]["needed_data"] if n.startswith("대안:")]
    assert alt and "카드 데이터1(보조)" in alt[0] and "어디 사는지" in alt[0]


def test_d4b_payment_night_answered_by_card1(agent):
    v = run(agent, "D4B", D4B)["values"]
    assert v["route"] == "lookup" and v["path"][-1] == "answer" and v["answer"]["status"] == "answered"
    assert v["contract_check"]["source"] == "card1"
    assert "카드 데이터1(보조) 사용: 고객 거주지 없음, 데이터2와 합산 금지" in v["contract_check"]["warnings"]
    assert v["tools_run"] == ["get_card_time_band"]
    (e,) = [e for e in v["evidence"] if e["status"] == "OK"]
    assert e["data_version"].startswith("card1@") and e["analysis_cohort"] == "60대"
    assert e["merchant_region"] == "강남구" and e["customer_residence"] is None  # 데이터1 에는 거주지가 없음
    assert 0 < e["value"] < 1 and "저녁·밤 18~23시" in e["metric"]
    assert any("주민인지 알 수 없음" in w for w in e["warnings"])
    assert any("체류·외출·고립을 뜻하지 않음" in w for w in e["warnings"])
    t = text(v)
    assert "거주지 모름" in t and "실제 체류·외출은 알 수 없음" in t
    assert final_guard_lookup(v) == [("guard_report", "사실", "PASS"), ("guard_report", "표현", "PASS")]


def final_guard_lookup(v: dict) -> list[tuple[str, str, str]]:
    return [(x["guard"], x["check"], x["verdict"]) for x in v["guard"] if x["guard"] == "guard_report"][-2:]


# ---------------------------------------------------------------- 질문 속 동 (AG-01·AG-02)


def test_nammyeon_question_levels_same_as_monitoring(agent):
    """질문한 동만 남기지만 등급은 정기 감시(도시 전체)와 같다."""
    v = run(agent, "NM", NAMMYEON)["values"]
    assert v["request"]["adm_cds"] == ["32010350"]
    cands = v["candidates"]
    assert cands and all("32010350" in {c.get("adm_cd"), *c["check_first_adm"]} for c in cands)
    mon = build_graph().invoke(initial_state("mon-nm", "monitoring", NAMMYEON[0], tools_backend="real", city="춘천"),
                               {"recursion_limit": 100})
    key = lambda c: (c["candidate_type"], c.get("adm_cd"), c["analysis_cohort"])  # noqa: E731
    mon_lv = {key(c): (c["candidate_level"], c["rule_id"]) for c in mon["candidates"]}
    for c in cands:
        assert mon_lv[key(c)] == (c["candidate_level"], c["rule_id"])
    ps = v["place_scope"]
    assert ps["city_total"] == len(mon["candidates"]) and ps["kept"] == len(cands)
    assert "남면" in v["evidence_card"]["title"] and final_guard(v) == PASS3


def test_chuncheon_gangnamdong_not_gangnam_gu(agent):
    v = run(agent, "GND", GANGNAMDONG)["values"]
    assert v["request"]["city"] == "춘천" and v["request"]["adm_cds"] == ["32010680"]
    assert all(c["city"] == "춘천" for c in v["candidates"])
    assert not any(r["city"] == "강남" for r in v["resources"])
    assert any(s.startswith("질문한 동: 춘천 강남동") for s in v["report"]["fact_sentences"])


def test_followup_other_city_on_chuncheon_card(agent):
    """춘천 실행에서 "강남은 어때?" → 보류 (카드 근거 범위의 '강남동'을 강남구로 읽지 않음)."""
    from bigcontest.agent.followup import answer_followup

    v = run(agent, "D2", D2)["values"]
    out = answer_followup(v, "강남은 어때?", "none")
    assert out["status"] == "abstained" and out["abstain_reason"].startswith("OUT_OF_SCOPE")
    out = answer_followup(v, "강남구 어르신은 어때?", "none")
    assert out["status"] == "abstained" and out["abstain_reason"].startswith("OUT_OF_SCOPE")
    out = answer_followup(v, "남면은 왜 Level 2야?", "none")  # 현재 등급으로 같은 도시 동을 묻는다
    assert out["status"] == "answered"


def test_after_as_of_request_abstains():
    out = build_graph().invoke(initial_state("late", "qa", "2025-10-20", question="강남 1인세대 비율 상위 동은?",
                                             analysis_mode="strict_as_of", tools_backend="real"),
                               {"recursion_limit": 100})
    assert out["path"][-1] == "abstain_answer" and "TIME_" in out["abstain_reason"]


# ---------------------------------------------------------------- 결정론 · LLM 없는 경로


@pytest.mark.parametrize("name,case", [("D1", D1), ("D2", D2), ("D3", D3), ("D3B", D3B)])
def test_levels_same_for_any_llm(agent, name, case):
    base = run(agent, name, case, "none")
    assert base["values"]["report"]["narrative"] is None  # 템플릿만으로 끝까지
    for llm in LLMS[1:]:
        v = run(agent, name, case, llm)["values"]
        assert levels(v) == levels(base["values"]), llm
        assert final_guard(v) == PASS3, llm  # 틀린 사실을 넣은 설명은 REVISE 뒤 통과


def test_wrong_fact_revised(agent):
    v = run(agent, "D3", D3, "fake_wrong_fact_once")["values"]
    verdicts = [g["verdict"] for g in v["guard"] if g["guard"] == "guard_report" and g["check"] == "사실"]
    assert verdicts == ["REVISE", "PASS"]


def test_d4_same_for_any_llm(agent):
    for llm in ("fake", "fake_error"):
        v = run(agent, "D4", D4, llm)["values"]
        assert v["answer"]["status"] == "abstained"


# ---------------------------------------------------------------- 정기 감시 도시


def test_monitoring_city_selection():
    both = build_graph().invoke(initial_state("mon-both", "monitoring", "2025-09-26", tools_backend="real"),
                                {"recursion_limit": 100})
    assert both["path"][-1] == "publish_candidates"
    assert {c["city"] for c in both["candidates"]} == {"강남", "춘천"}
    one = build_graph().invoke(initial_state("mon-cc", "monitoring", "2025-09-26", tools_backend="real", city="춘천"),
                               {"recursion_limit": 100})
    assert {c["city"] for c in one["candidates"]} == {"춘천"}
    lv = lambda s: {(c["city"], c.get("adm_cd"), c["candidate_level"]) for c in s["candidates"] if c["city"] == "춘천"}  # noqa: E731
    assert lv(one) == lv(both)  # 같은 도시면 같은 등급


# ---------------------------------------------------------------- 담당자용 출력 · 판단 기록


def actions_of(v: dict) -> dict:
    return {a["candidate_id"]: a for a in v["report"]["actions"]}


@pytest.mark.parametrize("name,case", [("D1", D1), ("D2", D2), ("D3B", D3B)])
def test_level23_have_action_plan_checklist_areas(agent, name, case):
    v = run(agent, name, case)["values"]
    acts = actions_of(v)
    lv23 = {c["candidate_id"] for c in v["candidates"] if c["candidate_level"] in ("Level 2", "Level 3")}
    assert set(acts) == lv23  # Level 2·3 후보마다, 그 밖에는 없음
    for cid in lv23:
        a = acts[cid]
        assert a["check_areas"] and a["check_area_note"] == "판단도구 문항 채점 아님, 현장에서 먼저 물어볼 영역"
        p, ck = a["action_plan"], a["checklist"]
        assert p["owner"] and p["method"] and p["due"] and p["check_areas"]
        assert p["text"] in v["report"]["fact_sentences"]  # 보고서에는 한 줄로
        assert ck["lists"] and ck["contacts"] and "명단을 만들지 않음" in ck["note"]
        assert not any("위급 시 안내" in r for r in p["linked_resources"])
    assert v["report"]["distribution"] == "내부용(대외비)"


def test_d1_nonhyeon1_action(agent):
    v = run(agent, "D1", D1)["values"]
    cid = next(c["candidate_id"] for c in v["candidates"] if c.get("adm_cd") == "11230520")  # 논현1동
    a = actions_of(v)[cid]
    assert a["group"] == "중장년 남성 1인가구" and a["where"] == "강남 논현1동"
    assert [x["area"] for x in a["check_areas"]][:2] == ["건강·복약", "사회적 관계·연락"]  # R1 + 1인세대·소통
    assert a["action_plan"]["due_date"] == "2025-10-02"  # 추석 연휴 전날
    assert all("강남 논현1동 · 40~64세 남성" in x for x in a["checklist"]["lists"])


def test_d2_nammyeon_action(agent):
    v = run(agent, "D2", D2)["values"]
    cid = next(c["candidate_id"] for c in v["candidates"] if c.get("adm_cd") == "32010350")  # 남면
    a = actions_of(v)[cid]
    assert a["candidate_level"] == "Level 2" and a["group"] == "고령층"
    assert [x["area"] for x in a["check_areas"]][:2] == ["이동·외출", "주거·안전"]  # 동절기 접근 거리 + 난방
    p = a["action_plan"]
    assert p["due_date"] == "2026-01-03" and p["method"] == "전화 안부 → 방문 대상 추리기"
    assert "노인맞춤돌봄 수행기관" in p["owner"]
    assert not any(n in p["text"] for n in p["emergency_contacts"])
    assert any("노인맞춤돌봄 대상자" in x for x in a["checklist"]["lists"])


def test_demo_decision_trace(agent):
    st = run(agent, "D1", D1, "fake")
    tr = st["values"]["decision_trace"]
    assert [t["node"] for t in tr] == st["values"]["path"]
    by = {t["node"]: t for t in tr}
    assert by["classify"]["source"] == "llm" and by["classify"]["reason"]
    assert by["collect_signals"]["reason"]
    assert by["guard_report"]["next"] == "await_approval"
    assert all(D1[1] not in str(t) for t in tr)
