"""담당자용 출력(확인할 영역·대응 템플릿·체크리스트·대외용 표시) + 등급 설명 틀. 자료 없이 도는 테스트.

- 확인할 영역: 근거·원인 → 영역 (판단도구 문항 채점 아님)
- 대응 템플릿 5칸: 주체 / 방법 / 기한 / 확인할 영역 / 연계 자원. 위급 시 안내는 연계 자원 밖 따로
- 내일 아침 체크리스트: 기존 명단을 동·집단으로 걸러 보라는 안내 (시스템은 명단을 만들지 않음)
- 청년 남성은 방문이 아니라 안내 강화
- explain_level: 조건별 충족·기준까지 거리·빠진 근거가 들어오면 바뀔 등급
"""

import math
from datetime import date

import pytest

from bigcontest.agent.schemas import (
    CHECK_AREA_NOTE,
    DISCLAIMER,
    EXTERNAL_NOTICE,
    Candidate,
    EvidenceCard,
    Report,
    RuleCheck,
)
from bigcontest.agent.tools import report as R
from bigcontest.agent.tools import signal as S

AS_OF = date(2025, 9, 26)


def cand(**kw) -> Candidate:
    base = dict(candidate_id="C-1", candidate_type="scheduled_access_review", city="강남", adm_cd="11230520",
                analysis_cohort="40대·50대", service_eligibility_group="40~64세 남성",
                trigger="R1 추석 연휴 D-7 (10/03~10/09)", input_status="충족", counter_result="해당 없음",
                candidate_level="Level 2", level_reason="구조 취약성 상위(1인세대 비율 0.61) + 예정된 트리거 [임시 기준]")
    base.update(kw)
    return Candidate(**base)


def areas(c, far=False) -> list[str]:
    return [a.area for a in R.check_areas(c, far)]


# ---------------------------------------------------------------- 확인할 영역


def test_check_areas_mapping():
    assert areas(cand())[:2] == ["건강·복약", "사회적 관계·연락"]  # R1 진료 단절 + 1인세대·소통
    assert areas(cand(trigger="R4 폭염 예보", service_eligibility_group="65세 이상", level_reason="x"))[0] == "식사·생활"
    w = areas(cand(city="춘천", adm_cd="32010350", trigger="W 동절기 (춘천 읍·면)", service_eligibility_group="65세 이상",
                   level_reason="x"))
    assert w == ["이동·외출", "주거·안전"]
    hanpa = areas(cand(trigger="R6 한파 특보 근사", service_eligibility_group="65세 이상", level_reason="x"))
    assert hanpa[0] == "주거·안전" and "경제·지원" in hanpa
    rain = areas(cand(trigger="R6 호우 특보 근사", service_eligibility_group="65세 이상", level_reason="x"))
    assert rain[:2] == ["주거·안전", "이동·외출"]
    dev = cand(candidate_type="unexpected_activity_deviation", adm_cd=None, check_first_adm=["11230520"],
               is_shared_context=True, counter_result="통과", trigger=None, service_eligibility_group="65세 이상",
               level_reason="x")
    assert areas(dev) == ["식사·생활", "건강·복약"]
    # 거점이 멀면 이동·외출
    assert "이동·외출" in areas(cand(level_reason="x"), far=True)
    a = R.check_areas(cand())[0]
    assert a.reasons == ["연휴 진료 단절(R1)"] and a.ask


def test_check_area_note_fixed():
    act = R.build_candidate_action(cand(), [], 5.0, 0.4, AS_OF)
    assert act.check_area_note == CHECK_AREA_NOTE == "판단도구 문항 채점 아님, 현장에서 먼저 물어볼 영역"


# ---------------------------------------------------------------- 대응 템플릿 5칸


def test_action_plan_five_slots_and_due():
    res = [{"candidate_id": "C-1", "name": "외로움안녕120", "phone_based": True, "eligibility": "적합",
            "operating_status": "확인 필요", "warnings": []},
           {"candidate_id": "C-1", "name": "자살예방상담전화", "phone_based": True, "eligibility": "적합",
            "operating_status": "확인 필요", "group": "위급 시 안내", "warnings": []},
           {"candidate_id": "C-9", "name": "다른 후보 자원", "eligibility": "적합", "operating_status": "운영"}]
    p = R.build_action_plan(cand(), res, 5.0, 0.4, AS_OF)
    assert p.owner == ["동 복지팀(맞춤형복지팀)"]
    assert p.method == "전화 안부 → 방문 대상 추리기"
    assert p.due_date == date(2025, 10, 2) and p.due.startswith("연휴 전날까지")  # D-7 → 연휴 전날
    assert p.check_areas[0] == "건강·복약"
    assert p.linked_resources == ["외로움안녕120(전화형, 자격 적합, 운영 확인 필요)"]
    assert p.emergency_contacts == ["자살예방상담전화"]  # 위급 시 안내는 따로
    assert "자살예방상담전화" not in p.text and "다른 후보 자원" not in p.text
    for slot in ("주체", "방법", "기한", "확인할 영역", "연계 자원"):
        assert slot in p.text
    assert R.generate_response_plan(cand(), res, 5.0, 0.4, AS_OF) == p.text


def test_due_by_trigger():
    w = cand(city="춘천", adm_cd="32010350", trigger="W 동절기 (춘천 읍·면)", service_eligibility_group="65세 이상")
    assert R.due_of(w, date(2025, 12, 20)) == ("2주 안 (2026-01-03까지)", date(2026, 1, 3))
    dev = cand(candidate_type="unexpected_activity_deviation", adm_cd=None, check_first_adm=["11230520"],
               is_shared_context=True, counter_result="통과", trigger=None)
    assert R.due_of(dev, date(2025, 10, 15))[1] == date(2025, 10, 18)  # 3일 안
    # 트리거가 여럿이면 가장 이른 기한
    both = cand(trigger="R1 추석 연휴 D-3 (10/03~10/09); W 동절기 (춘천 읍·면)")
    assert R.due_of(both, AS_OF)[1] == date(2025, 9, 28)
    # 기준일이 없으면 날짜 없이 문구만
    assert R.due_of(cand(), None) == ("연휴 전날까지", None)


def test_far_method_and_level_filter():
    p = R.build_action_plan(cand(service_eligibility_group="65세 이상", trigger="W 동절기 (춘천 읍·면)", city="춘천",
                                 adm_cd="32010350"), [], 5.0, 18.6, date(2025, 12, 20))
    assert p.method.startswith("전화형·찾아가는 서비스 먼저")
    assert "노인맞춤돌봄 수행기관" in p.owner
    assert "자원 후보 없음" in p.text and p.resource_note
    assert R.build_candidate_action(cand(candidate_level="Level 1"), [], 5.0, 0.4, AS_OF) is None
    assert R.build_candidate_action(cand(candidate_level="판단 보류", input_status="보류", missing_inputs=["x"]),
                                    [], 5.0, 0.4, AS_OF) is None


def test_youth_gets_guidance_not_visits():
    """청년 남성은 방문 점검이 아니라 안내 강화 (거점이 멀어도)."""
    y = cand(adm_cd="11230640", analysis_cohort="20대·30대", service_eligibility_group="20~34세 남성")
    act = R.build_candidate_action(y, [], 5.0, 9.0, AS_OF)
    assert act.group == "청년 남성"
    assert act.action_plan.method == "안내 강화(문자·SNS·청년 지원 안내)"
    assert "방문" not in act.action_plan.method and "방문" not in act.checklist.method
    assert any("청년" in o for o in act.action_plan.owner)


def test_group_key_fallback_by_cohort():
    assert R.group_key(cand(service_eligibility_group=None, analysis_cohort="60대·70대·80대+")) == "고령층"
    assert R.group_key(cand(service_eligibility_group=None, analysis_cohort="20대·30대 남성")) == "청년 남성"
    assert R.group_key(cand(service_eligibility_group=None, analysis_cohort="20대 여성")) == "기본"
    assert R.group_key(cand(service_eligibility_group="고령층")) == "고령층"


# ---------------------------------------------------------------- 내일 아침 체크리스트


def test_morning_checklist():
    old = cand(city="춘천", adm_cd="32010350", trigger="W 동절기 (춘천 읍·면)", analysis_cohort="60대·70대·80대+",
               service_eligibility_group="65세 이상", candidate_level="Level 3")
    res = [{"candidate_id": "C-1", "name": "남면 행정복지센터", "resource_type": "행정복지센터", "eligibility": "적합",
            "operating_status": "확인 필요"}]
    ck = R.build_candidate_action(old, res, 5.0, 18.6, date(2025, 12, 20)).checklist
    names = " ".join(ck.lists)
    for want in ("행복이음 위기정보", "고독사위기대응시스템 배분 명단", "노인맞춤돌봄 대상자", "기존 사례관리 가구"):
        assert want in names
    assert all("춘천 남면" in x and "65세 이상" in x for x in ck.lists)  # 이 동·집단으로 걸러 봄
    assert "명단을 만들지 않음" in ck.note
    assert any("노인맞춤돌봄 수행기관" in x for x in ck.contacts)
    assert any("통리장" in x for x in ck.contacts)
    assert any("남면 행정복지센터" in x for x in ck.contacts)  # 매칭된 자원
    assert ck.due.startswith("2주 안") and ck.check_areas[:2] == ["이동·외출", "주거·안전"]
    # 이탈 후보는 먼저 확인할 동으로
    dev = cand(candidate_type="unexpected_activity_deviation", adm_cd=None, check_first_adm=["11230520", "11230640"],
               is_shared_context=True, counter_result="통과", trigger=None, service_eligibility_group="65세 이상")
    act = R.build_candidate_action(dev, [], 5.0, None, date(2025, 10, 15))
    assert act.where == "강남 먼저 확인할 동 논현1동, 역삼1동"
    assert act.checklist.due.startswith("3일 안")


# ---------------------------------------------------------------- 대외용 표시


def test_report_distribution_and_external_notice():
    card = EvidenceCard(card_id="CARD-x-1", title="t", snapshot_id="s", analysis_mode="retrospective",
                        as_of=AS_OF, evidence_revision=1)
    r = Report(report_revision=1, evidence_card=card)
    assert r.distribution == "내부용(대외비)"
    assert r.external_notice == EXTERNAL_NOTICE and "특정 동네가 위험하다는 뜻이 아니라" in r.external_notice
    assert r.actions == [] and r.decision_trace == [] and r.level_explanations == []
    with pytest.raises(ValueError):
        Report(report_revision=1, evidence_card=card, distribution="대외용")
    assert DISCLAIMER  # 고지 문구는 근거 카드에 그대로


def test_action_templates_yaml_consistent():
    t = R.action_templates()
    six = {"건강·복약", "식사·생활", "사회적 관계·연락", "이동·외출", "주거·안전", "경제·지원"}
    assert set(t["areas"]) == six
    tables = [r["areas"] for r in t["rules"].values()] + [t["deviation"]["areas"], t["far"]["areas"],
                                                          t["structure"]["areas"]]
    tables += [g.get("extra_areas") or {} for g in t["groups"].values()]
    assert all(set(x) <= six for x in tables)
    assert set(t["rules"]) >= {"R1", "R2", "R3", "R4", "R6", "R6 호우", "R6 대설", "R6 한파", "W"}
    assert t["check_area_note"] == CHECK_AREA_NOTE and t["external_notice"] == EXTERNAL_NOTICE


# ---------------------------------------------------------------- explain_level


def stub_rule_checks(c: Candidate, structure: float | None, rank: tuple[int, int] | None,
                     access_km: float | None) -> list[dict]:
    """지금 임시 기준(STUB_LEVEL_RULES)으로 만든 조건 목록 (테스트용. 실제로는 등급 엔진이 채움)."""
    r = S.STUB_LEVEL_RULES
    top = rank is not None and rank[0] <= max(1, math.ceil(r["structure_top_share"] * rank[1]))
    return [
        {"condition": "구조 상위 30%", "met": None if rank is None else top,
         "value": structure, "threshold": "상위 30%", "input": "structure_vulnerability", "level_if_met": "Level 2"},
        {"condition": "1인세대 비율 최소값", "met": None if structure is None else structure >= r["structure_min"],
         "value": structure, "threshold": r["structure_min"], "comparator": ">="},
        {"condition": "관측 거점까지 직선거리", "met": None if access_km is None else access_km >= r["access_far_km"],
         "value": access_km, "threshold": r["access_far_km"], "comparator": ">=", "unit": "km",
         "input": "access_gap", "level_if_met": "Level 3"},
        {"condition": "예정된 트리거", "met": bool(c.trigger), "value": c.trigger},
    ]


def test_explain_level_met_and_distance():
    c = cand(rule_id="R-SCHED-2", rule_version="stub-v0")
    c = c.model_copy(update={"rule_checks": [RuleCheck(**x) for x in stub_rule_checks(c, 0.61, (2, 22), 0.4)]})
    ex = R.explain_level(c)
    assert ex.lines[0] == "적용 규칙 R-SCHED-2 (stub-v0) → Level 2"
    assert "구조 상위 30% 조건: 충족(0.61, 기준 상위 30%)" in ex.lines
    dist = next(v for v in ex.checks if v.condition == "관측 거점까지 직선거리")
    assert dist.met is False and dist.distance == pytest.approx(4.6)
    assert dist.text == "관측 거점까지 직선거리 조건: 미충족(0.4km, 기준 5km 이상, 부족 4.6km)"
    minimum = next(v for v in ex.checks if v.condition == "1인세대 비율 최소값")
    assert minimum.met and minimum.distance == pytest.approx(0.26) and "여유 0.26" in minimum.text
    assert ex.what_if == ["관측 거점까지 직선거리 조건을 충족하면 Level 3 가능 (기준까지 4.6km 부족)"]


def test_explain_level_missing_input_and_no_rule():
    c = cand(input_status="부분 판정", missing_inputs=["access_gap", "winter_contraction"])
    ex = R.explain_level(c, stub_rule_checks(c, 0.61, (2, 22), None))
    assert "관측 거점까지 직선거리 조건: 자료 없음(기준 5km 이상)" in ex.lines
    assert "관측 거점까지 직선거리 자료(access_gap)가 들어와 충족되면 Level 3 가능" in ex.what_if
    assert any(w.startswith("빠진 필수 근거 winter_contraction") for w in ex.what_if)
    assert not any(w.startswith("빠진 필수 근거 access_gap") for w in ex.what_if)  # 위 조건과 겹치지 않음
    assert ex.lines[0].startswith("적용 규칙 기록 없음 → Level 2 (등급 이유:")
    # 조건이 없으면 등급 이유 한 줄만
    assert R.explain_level(cand()).checks == []


# ---------------------------------------------------------------- 가짜 API (화면 작업용)


@pytest.fixture
def api_client(tmp_path):
    import sys

    from bigcontest.config import ROOT

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from api.app import create_app

    app = create_app(db_path=tmp_path / "state.sqlite", delay_s=0)
    with TestClient(app) as c:
        yield c
    app.state.store.close()


def _report(client, body):
    from api.models import ReportResponse

    rid = client.post("/runs", json=body).json()["run_id"]
    return ReportResponse.model_validate(client.get(f"/runs/{rid}/report").json()).report


def test_fake_api_exposes_new_fields(api_client):
    # D2 남면 동절기 Level 3
    rep = _report(api_client, {"mode": "qa", "candidate_id": "C-20251101-002", "as_of": "2025-12-20"})
    assert rep.distribution == "내부용(대외비)" and rep.external_notice == EXTERNAL_NOTICE
    (a,) = rep.actions
    assert a.where == "춘천 남면" and a.group == "고령층"
    assert [x.area for x in a.check_areas][:2] == ["이동·외출", "주거·안전"]
    assert a.action_plan.text in rep.fact_sentences and a.checklist.lists
    (ex,) = rep.level_explanations
    assert ex.rule_id == "SCHED-L3" and any("구조 상위 30% 조건: 충족" in x for x in ex.lines)
    assert [t.node for t in rep.decision_trace][0] == "intake"
    assert any(t.node == "assign_level" and t.rule_ids == ["SCHED-L3"] for t in rep.decision_trace)
    # 청년 남성 이탈 후보: 안내 강화
    y = _report(api_client, {"mode": "qa", "candidate_id": "C-20250818-511", "as_of": "2025-08-20"})
    assert y.actions[0].action_plan.method == "안내 강화(문자·SNS·청년 지원 안내)"
    # Level 1 후보는 대응안 없음, 등급 설명은 있음
    low = _report(api_client, {"mode": "qa", "candidate_id": "C-20250926-504", "as_of": "2025-09-26"})
    assert low.actions == [] and low.level_explanations[0].what_if
    # 후보 목록에도 rule_checks
    items = api_client.get("/candidates", params={"city": "춘천", "as_of": "2025-12-20"}).json()["items"]
    assert items and all(c["rule_id"] and c["rule_checks"] for c in items)
