"""H3 도메인 반영 (대응 템플릿 action-v2 · 보호 자원 칸 · 중장년 남성 연결 묶음 · 이의·정정 기록). 자료 없이 도는 테스트.

- 폭염 특보 단계별 안부 횟수: 후보 heat_stage 칸 → 트리거 글 → 없으면 '확인 필요' (단계를 지어내지 않음). 중대경보 하루 2회
- 명절 전·중·후 한 묶음 (R1·R2·R3), 지금 단계 표시, 집단별 안부 횟수
- 연락할 곳의 사전 안부 협력자 (임대인·관리사무소·고시원 운영자): 주거 맥락일 때만
- 중장년 남성 연결 묶음 + 사회적 처방형 3단계 (안내 → 연결 → 확인). 묶음 자원은 원장에 있어야 함
- 보호 자원 칸: 강남 경로당 동별, 춘천 경로당 시군 집계만, 춘천 쉼터 미수집 (위험 칸 아님)
- 이의·정정 기록: 남기기(검토 이상)·조회·상태 바꾸기(승인), 자유 글 가림, 감사 로그, 멱등, 닫힌 기록은 409
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from bigcontest.agent.schemas import ActionPlan, Candidate
from bigcontest.agent.tools import report as R
from bigcontest.resources import protective as P
from bigcontest.resources.ledger import V2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.auth import Auth, Identity  # noqa: E402

AS_OF = date(2025, 9, 26)


def cand(**kw) -> Candidate:
    base = dict(candidate_id="C-1", candidate_type="scheduled_access_review", city="강남", adm_cd="11230520",
                analysis_cohort="60대·70대·80대 이상", service_eligibility_group="65세 이상",
                trigger="R1 추석 연휴 D-7 (10/03~10/09)", input_status="충족", counter_result="해당 없음",
                candidate_level="Level 2", level_reason="예정된 트리거 [임시 기준]")
    base.update(kw)
    return Candidate(**base)


def plan(c: Candidate, as_of: date = AS_OF) -> ActionPlan:
    act = R.build_candidate_action(c, [], 5.0, 0.4, as_of)
    assert act is not None
    return act.action_plan


# ---------------------------------------------------------------- 폭염 특보 단계


@pytest.mark.parametrize("trigger, stage, freq", [
    ("R4 폭염중대경보 예보", "폭염중대경보", "하루 2회"),
    ("R4 폭염경보 예보", "폭염경보", "매일 1회"),
    ("R4 폭염주의보 예보", "폭염주의보", "격일 1회"),
    ("R4 폭염 [시나리오] (최고 33℃ 이상, 08/01 실측)", "특보 단계 확인 필요", "특보 단계 확인 전까지"),
])
def test_heat_stage_frequency_from_trigger(trigger, stage, freq):
    p = plan(cand(trigger=trigger), date(2025, 8, 1))
    assert p.heat_stage == stage and freq in p.contact_frequency
    assert "받지 않으면 당일 방문" in p.contact_frequency  # 전화 → 무응답 시 방문 순서
    assert f"안부 횟수({stage})" in p.text


def test_heat_stage_field_wins_and_non_heat_has_none():
    # 규칙 엔진이 heat_stage 칸을 채우면 그 값을 먼저 (Candidate 에 칸이 없을 때도 같은 함수가 돌아야 함)
    assert R.heat_stage_key(SimpleNamespace(trigger="R4 폭염 예보", heat_stage="중대경보")) == "중대경보"
    assert R.heat_stage_key(SimpleNamespace(trigger="R4 폭염경보 예보", heat_stage=None)) == "경보"
    p = plan(cand())  # R1 은 폭염 단계 없음
    assert p.heat_stage is None and p.contact_frequency is None and "안부 횟수" not in p.text


def test_heat_template_matches_mohw_two_per_day():
    h = R.action_templates()["heat_stages"]
    assert set(h["stages"]) == {"주의보", "경보", "중대경보"}
    assert "하루 2회" in h["stages"]["중대경보"]["frequency"]


# ---------------------------------------------------------------- 명절 전·중·후


def test_holiday_timeline_three_phases_with_current():
    tl = plan(cand()).holiday_timeline
    assert [x.phase for x in tl] == ["전", "중", "후"] and [x.rule for x in tl] == ["R1", "R2", "R3"]
    assert [x.current for x in tl] == [True, False, False]
    assert tl[1].contact == "전화·방문 2회"  # 고령층: 연휴 중 2회 (강남구 2026 추석 대책과 같은 리듬)
    r3 = plan(cand(trigger="R3 추석 연휴 뒤 회복 확인 (10/09 뒤 10일)", city="춘천", adm_cd="32010540"),
              date(2025, 10, 10)).holiday_timeline
    assert [x.current for x in r3] == [False, False, True]
    mid = plan(cand(service_eligibility_group="40~64세 남성", analysis_cohort="40대·50대 남")).holiday_timeline
    assert "동의한 가구만" in mid[1].contact  # 중장년 남성은 방문 강요가 아니라 동의 기반
    assert plan(cand(trigger="W 동절기 (춘천 읍·면)", city="춘천", adm_cd="32010350")).holiday_timeline == []


# ---------------------------------------------------------------- 사전 안부 협력자


def test_housing_partners_only_in_housing_context():
    old = plan(cand())  # 고령층 R1, 1인세대 상위 아님 → 없음
    assert old.partners == [] and old.partner_note is None
    top = plan(cand(level_reason="구조 취약성 상위(1인세대 비율 0.61) + 예정된 트리거"))
    assert any("임대인" in x for x in top.partners) and any("고시원" in x for x in top.partners)
    assert "개인정보는 넘겨받지 않음" in top.partner_note
    heat = plan(cand(trigger="R4 폭염경보 예보"), date(2025, 8, 1))
    assert heat.partners  # 폭염: 집 안에 오래 머무는 때
    act = R.build_candidate_action(cand(level_reason="구조 취약성 상위(1인세대 비율 0.61)"), [], 5.0, 0.4, AS_OF)
    assert any("사전 안부 협력" in x for x in act.checklist.contacts)


# ---------------------------------------------------------------- 중장년 남성 연결 묶음


def test_midlife_bundle_and_link_steps():
    led = pd.read_csv(V2, dtype=str, keep_default_na=False).set_index("resource_id")
    groups = R.action_templates()["groups"]
    for city, items in groups["중장년 남성 1인가구"]["bundle"].items():
        for x in items:
            assert x["resource_id"] in led.index, x
            assert led.loc[x["resource_id"], "city"] == city
            assert led.loc[x["resource_id"], "source_url"] and led.loc[x["resource_id"], "last_verified"]
    subtypes = {led.loc[x["resource_id"], "subtype"] for items in groups["중장년 남성 1인가구"]["bundle"].values()
                for x in items}
    assert {"고용복지플러스센터", "중독관리통합지원센터"} <= subtypes
    for city, adm in (("강남", "11230520"), ("춘천", "32010540")):
        p = plan(cand(city=city, adm_cd=adm, service_eligibility_group="40~64세 남성", analysis_cohort="40대·50대 남"))
        assert [s.step for s in p.link_steps] == ["안내", "연결", "확인"]
        assert any("고용" in b.name for b in p.bundle) and any("중독" in b.name for b in p.bundle)
    assert plan(cand()).bundle == [] and plan(cand()).link_steps == []  # 고령층에는 붙이지 않음


# ---------------------------------------------------------------- 보호 자원 칸


def test_protective_gangnam_dong_counts():
    led = P.ledger_v2()
    out = P.protective_for(led, "강남", "11230510")
    g = {x["group"]: x for x in out["groups"]}
    n = int(((led["city"] == "강남") & (led["subtype"] == "경로당") & (led["adm_cd"] == "11230510")).sum())
    assert g["경로당"]["in_dong"] == n and g["경로당"]["status"] == "수집" and g["경로당"]["city_total"] >= n
    assert g["더위·한파 쉼터"]["in_dong"] is None  # 도시 단위 안내뿐이면 동 안 수는 모름 (0 이 아님)
    assert all(i["scope"] == "도시" for i in g["더위·한파 쉼터"]["items"])
    assert "위험 표시가 아님" in out["note"]


def test_protective_chuncheon_city_count_and_pending():
    out = P.protective_for(P.ledger_v2(), "춘천", "32010310")  # 동면 (면)
    g = {x["group"]: x for x in out["groups"]}
    assert g["경로당"]["status"] == "도시 집계만" and g["경로당"]["city_total"] == 371
    assert "면 지역 157곳" in g["경로당"]["note"] and g["경로당"]["in_dong"] is None
    assert g["더위·한파 쉼터"]["status"] == "미수집"
    assert g["돌봄 이웃"]["items"]


def test_city_counts_file_matches_raw():
    if not P.RAW.exists():
        pytest.skip("원본 xlsx 없음 (external/resources/protective/raw/, git 추적 안 함)")
    new = P.build_counts(P.RAW).astype(str).drop(columns=["collected_date"])
    old = pd.read_csv(P.COUNTS, dtype=str, keep_default_na=False).drop(columns=["collected_date"])
    assert new.equals(old)


# ---------------------------------------------------------------- API


TOKENS = {
    "tok-view": Identity("viewer-01", "조회", True),
    "tok-review": Identity("worker-01", "검토", True),
    "tok-approve": Identity("lead-01", "승인", True),
}
H = {k.split("-")[1]: {"Authorization": f"Bearer {k}"} for k in TOKENS}
OBJ = {"target_type": "candidate", "candidate_id": "C-20250926-001", "city": "강남", "adm_cd": "11230520",
       "reason_category": "후보가 아님(현장 사정)", "text": "김철수 씨 010-1234-5678 은 이미 사례관리 중", "idempotency_key": "obj-1"}


@pytest.fixture(params=["fake", "real"])
def secured(tmp_path, request):
    kw = {"backend": "real", "tools_backend": "stub", "llm": "none"} if request.param == "real" else {}
    app = create_app(db_path=tmp_path / "s.sqlite", delay_s=0, auth=Auth(TOKENS), **kw)
    with TestClient(app) as c:
        yield c
    if request.param == "real":
        app.state.runs.close()
    app.state.store.close()


def test_protective_endpoint(secured):
    r = secured.get("/resources/protective", params={"city": "춘천", "adm_cd": "32010540"}, headers=H["view"])
    assert r.status_code == 200
    body = r.json()
    assert body["adm_nm"] == "약사명동" and [g["group"] for g in body["groups"]] == ["경로당", "더위·한파 쉼터", "돌봄 이웃"]
    assert secured.get("/resources/protective", params={"city": "강남", "adm_cd": "32010540"},
                       headers=H["view"]).status_code == 400  # 다른 도시 동
    assert secured.get("/resources/protective", params={"city": "부산"}, headers=H["view"]).status_code == 400


def test_objection_flow(secured):
    assert secured.post("/objections", json=OBJ, headers=H["view"]).status_code == 403  # 조회 역할은 못 남김
    r = secured.post("/objections", json=OBJ, headers=H["review"])
    assert r.status_code == 201
    o = r.json()["objection"]
    assert o["status"] == "접수" and o["submitted_by"] == "worker-01"
    assert "010-1234-5678" not in o["text"] and "김철수" not in o["text"]  # 저장 전 가림
    again = secured.post("/objections", json=OBJ, headers=H["review"]).json()
    assert again["duplicate"] and again["objection"]["objection_id"] == o["objection_id"]
    assert secured.post("/objections", json={**OBJ, "reason_category": "기타"},
                        headers=H["review"]).status_code == 409  # 같은 키, 다른 내용
    lst = secured.get("/objections", params={"city": "강남"}, headers=H["view"]).json()
    assert lst["total"] == 1 and lst["items"][0]["objection_id"] == o["objection_id"]
    assert secured.get("/objections", params={"candidate_id": "없는-후보"}, headers=H["view"]).json()["total"] == 0
    oid = o["objection_id"]
    assert secured.post(f"/objections/{oid}/status", json={"status": "반영"}, headers=H["review"]).status_code == 403
    done = secured.post(f"/objections/{oid}/status", json={"status": "반영", "resolution": "다음 규칙 평가에 넣음"},
                        headers=H["approve"])
    assert done.status_code == 200 and done.json()["objection"]["status"] == "반영"
    assert done.json()["objection"]["resolved_by"] == "lead-01"
    assert secured.post(f"/objections/{oid}/status", json={"status": "기각"}, headers=H["approve"]).status_code == 409
    assert secured.post("/objections/obj-x/status", json={"status": "기각"}, headers=H["approve"]).status_code == 404
    store = secured.app.state.store
    acts = [r["action"] for r in store._all("SELECT action FROM audit_log ORDER BY id")]
    assert acts.count("objection_recorded") == 1 and "objection_status" in acts and "objection_rejected" in acts
    detail = [json.loads(r["detail"]) for r in store._all("SELECT detail FROM audit_log WHERE action='objection_recorded'")]
    assert "text" not in detail[0]  # 감사 로그에는 자유 글을 넣지 않음


def test_objection_validation(secured):
    bad = {**OBJ, "target_type": "run", "idempotency_key": "obj-2"}
    assert secured.post("/objections", json=bad, headers=H["review"]).status_code == 400  # run_id 없음
    assert secured.post("/objections", json={**bad, "run_id": "run-none"}, headers=H["review"]).status_code == 404
    assert secured.post("/objections", json={**OBJ, "idempotency_key": "obj-3", "reason_category": "아무거나"},
                        headers=H["review"]).status_code in (400, 422)
    assert secured.get("/objections", params={"status": "끝"}, headers=H["view"]).status_code == 400
