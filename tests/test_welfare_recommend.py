"""자원 추천 보완 테스트: 원장 시점, 기상 하위 유형, 제외 뒤 상위 K, 위급 시 안내,
전화형 자원 확인일(공식 페이지), '확인 필요' 이유 분리. 원본 데이터 없이 도는 것과 dev 스냅샷이 필요한 것을 나눔."""

from datetime import date

import pandas as pd
import pytest

from bigcontest.resources import ledger
from bigcontest.resources.collect import load_corrections
from bigcontest.resources.ledger import derive
from bigcontest.resources.match import (
    CHECK_ELIG,
    CHECK_HOLIDAY,
    _needs_for,
    find_resources,
    load_needs,
    select_resources,
)
from bigcontest.agent.tools import welfare as W

GN = "11230650"  # 강남 역삼2동
ORIGIN = (37.50, 127.04)
BASE = {
    "rid": "", "name": "", "type": "관계형성·여가", "subtype": "노인복지관", "hub": "False", "target_stage": "노년",
    "eligibility": "만 60세 이상", "city": "강남", "address": "서울특별시 강남구 어딘가", "adm_cd": GN, "adm_nm": "역삼2동",
    "dong_method": "좌표", "lat": "37.501", "lon": "127.041", "coord_source": "ArcGIS World Geocoder 번지점(키 없음·탐색용)",
    "phone": "", "hours": "", "source_url": "https://www.data.go.kr/data/15160109/fileData.do",
    "verified_date": "2026-03-18", "collected_date": "2026-10-13", "src": "mohw",
}
PHONE = {"type": "상담/정신건강", "subtype": "전화·광역(비공간)", "target_stage": "전 생애", "eligibility": "누구나, 24시간",
         "address": "", "adm_cd": "", "adm_nm": "", "lat": "", "lon": "", "coord_source": "none"}


def make(*rows) -> pd.DataFrame:
    return derive(pd.DataFrame([{**BASE, **r} for r in rows]))


# ------------------------------------------------------------ 기상 하위 유형
@pytest.mark.parametrize("trigger,cause", [
    ("R4 폭염 예보 (최고 33℃ 이상, 08/01)", "폭염형"),
    ("R6 한파 특보 근사 (12/20)", "한파·대설형"),
    ("R6 대설 특보 근사 (12/20)", "한파·대설형"),
    ("R6 호우 특보 근사 (07/15)", "호우형"),
    ("R6-COLD", "한파·대설형"),
    ("R6 기상특보", "기상 대응 확인 필요"),  # 하위 유형을 모르면 폭염으로 단정하지 않음
    ("W 동절기 (춘천 읍·면)", "계절 공동화형"),
    ("R1 추석 연휴 D-7 (10/03~10/09)", "달력 공백형"),
    (None, "1인가구 밀집형"),
])
def test_cause_keeps_weather_subtype(trigger, cause):
    assert W.cause_for(trigger, None, 5.0) == cause


def test_cold_and_snow_never_heat():
    for t in ("R6 한파 특보 근사 (01/10)", "R6 대설 특보 근사 (12/20)", "W 동절기 (춘천 읍·면); R6 대설 특보 근사 (12/20)"):
        c = W.cause_for(t, None, 5.0)
        assert "폭염" not in c
        assert "폭염" not in W._cause_label(c)
    cfg = load_needs()
    needs, _ = _needs_for(None, "한파·대설형", cfg)
    assert needs == ["난방", "안부·관계", "이동 지원", "생필품"]
    needs, _ = _needs_for(None, "호우형", cfg)
    assert needs == ["안부·관계", "이동 지원", "주거 안전"]


def test_far_rule_keeps_original_cause():
    c = W.cause_for("W 동절기 (춘천 읍·면); R6 대설 특보 근사 (12/20)", 18.9, 5.0)
    assert c == "거점 원거리형+계절 공동화형+한파·대설형"
    needs, cause = _needs_for(None, c, load_needs())
    assert cause["prefer_phone"] and "난방" in needs and "안부·관계" in needs
    assert "계절 공동화형 + 한파·대설형" in W._cause_label(c)
    assert W.cause_for(None, 18.9, 5.0) == "거점 원거리형"  # 원래 원인이 없으면 그대로


# ------------------------------------------------------------ 제외 뒤 상위 K
def test_hard_exclusion_before_top_k_and_backfill():
    d = make(
        {"rid": "A", "name": "가까운 복지관", "lat": "37.5001", "verified_date": "보도 기준(재확인 필요)"},  # 확인일 없음
        {"rid": "B", "name": "두번째 복지관", "lat": "37.5010"},
        {"rid": "C", "name": "세번째 복지관", "lat": "37.5020"},
    )
    found = find_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN)
    assert [m.resource_id for m in found][:1] == ["A"]  # 순위로는 맨 앞
    sel = select_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN, limit=2, per_subtype=None)
    assert [m.resource_id for m in sel.ranked] == ["B", "C"]  # A 를 빼고 다음 자원으로 채움
    assert [(m.resource_id, any("확인일" in w for w in why)) for m, why in sel.excluded] == [("A", True)]
    short = select_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN, limit=5)
    assert len(short.ranked) == 2 and short.n_valid == 2  # 모자라면 모자란 대로


def test_out_of_area_physical_is_hard_excluded():
    d = make({"rid": "A", "name": "관할 밖 거점"}, {"rid": "B", "name": "안쪽 복지관"})
    d.loc[0, "coordinate_status"] = "권역 외"
    sel = select_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN)
    assert [m.resource_id for m in sel.ranked] == ["B"]
    assert any("관할 밖" in w for m, why in sel.excluded for w in why)


# ------------------------------------------------------------ 위급 시 안내
def test_crisis_lines_go_to_separate_group():
    d = make(
        {"rid": "R0743", "name": "자살예방상담전화", **PHONE},
        {"rid": "P1", "name": "외로움 상담", **PHONE},
        {"rid": "H1", "name": "어느 행정복지센터", "type": "행정창구", "subtype": "행정복지센터", "eligibility": "관할 주민"},
        {"rid": "S1", "name": "어느 노인복지관"},
    )
    # 거점 원거리형: 위기 전화가 아닌 전화형이 위기 전화보다 앞
    far = select_resources(d, GN, None, cause_type="거점 원거리형", origin=ORIGIN)
    assert far.ranked[0].resource_id == "P1"
    assert [m.resource_id for m in far.crisis] == ["R0743"]
    assert "R0743" not in [m.resource_id for m in far.ranked]
    # 고령층 + 계절 공동화형(안부·관계): 행정복지센터 → (거점 원거리형이면 전화형) → 노인복지관, 위기 전화는 순위 밖
    old = select_resources(d, GN, "고령층", cause_type="거점 원거리형+계절 공동화형", origin=ORIGIN)
    assert [m.resource_id for m in old.ranked] == ["H1", "P1", "S1"]
    assert [m.resource_id for m in old.crisis] == ["R0743"]


def test_check_reasons_are_separate():
    d = make(
        {"rid": "E", "name": "자격 모름", "eligibility": "시설별"},
        {"rid": "H", "name": "휴일 모름", "eligibility": "누구나"},
    )
    m = {x.resource_id: x for x in find_resources(d, GN, "고령층", cause_type="달력 공백형", origin=ORIGIN)}
    assert CHECK_ELIG in m["E"].checks
    assert CHECK_ELIG not in m["H"].checks and CHECK_HOLIDAY in m["H"].checks
    assert m["H"].eligibility == "해당됨"  # 휴일 운영을 몰라도 자격은 그대로


# ------------------------------------------------------------ 원장 시점 · 전화형 확인일
def test_ledger_time_note():
    led = pd.DataFrame({"collected_date": ["2026-10-13", "2026-09-01"]})
    assert W.ledger_time_note(led, date(2025, 12, 20)) == "현재 원장(수집 2026-10-13) 기준 — 2025년 당시 운영·위치는 확인되지 않음"
    assert W.ledger_time_note(led, date(2026, 10, 20)) == "현재 원장(수집 2026-10-13) 기준"


def test_phone_resources_verified_from_official_pages():
    d = ledger.load().set_index("resource_id")
    for rid in ("R0739", "R0741", "R0742", "R0743", "R0744"):
        r = d.loc[rid]
        assert r["last_verified"] == "2026-10-13" and r["page_collected_at"] == "2026-10-13"
        assert r["verified_url"].startswith("https://")
        assert r["operating_status"] == "미상"  # 운영 상태는 바꾸지 않음
        assert "재확인" in r["verified_date"]  # 원래 칸(verified_date)은 그대로
    assert d.loc["R0739", "operation_confirmed_at"] == "2026-09-23"  # 공식 안내 발행일
    assert d.loc["R0743", "operation_confirmed_at"] == ""  # 페이지에 날짜 없음 → 비움
    # R0737 은 서울시 공식 기사(2026-07-27 발행)로 주소·운영 시간을 확인해 채움
    r = d.loc["R0737"]
    assert (r["last_verified"], r["page_collected_at"], r["operation_confirmed_at"]) == ("2026-10-14", "2026-10-14", "2026-07-27")
    assert r["verified_url"] == "https://mediahub.seoul.go.kr/archives/2018856" and "재확인" in r["verified_date"]
    assert r["holiday_open"] == "미상"  # 일요일 휴무만 있고 공휴일은 페이지에 없음
    corr = load_corrections()
    mine = corr[(corr["corrected_by"] == "공식 페이지 확인") & (corr["corrected_at"] == "2026-10-13")]
    assert set(mine["resource_id"]) == {"R0739", "R0741", "R0742", "R0743", "R0744"}
    assert (mine["corrected_at"] == "2026-10-13").all()


# ------------------------------------------------------------ 실제 도구 (dev 스냅샷)
def _dev():
    from bigcontest.agent.tools.datasource import dev_available

    return dev_available()


@pytest.mark.skipif(not _dev(), reason="dev 스냅샷 자료 없음")
def test_real_envelopes_state_ledger_time():
    from bigcontest.agent.schemas import NormalizedRequest
    from bigcontest.agent.tools.datasource import dev_snapshot_id
    from bigcontest.agent.tools.stubs import ToolContext

    as_of = date(2025, 12, 20)
    req = NormalizedRequest(city="춘천", metric="카드결제", period_to=as_of, group="고령층")
    ctx = ToolContext(scenario="normal", as_of=as_of, analysis_mode="strict_as_of", snapshot_id=dev_snapshot_id(),
                      request=req, backend="real")
    note = "2025년 당시 운영·위치는 확인되지 않음"
    env, items = W.find_resources(ctx, "32010390", "고령층", cause_type="거점 원거리형+계절 공동화형")
    acc = W.get_access_gap(ctx)
    (elg,) = W.check_eligibility(ctx, "R0677", "고령층")
    for e in [env, elg, *acc]:
        assert any(note in w for w in e.warnings), e.evidence_id
        assert e.availability_basis == "unknown" and e.actual_available_at is None
        assert e.observed_to is None or e.observed_to <= as_of  # 원장 수집일을 관측 기간으로 넣지 않음 (G-13)
    assert all(any("현재 대응안 예시" in w for w in it["warnings"]) for it in items)
    assert [it["resource_id"] for it in items if it["group"] == "위급 시 안내"] == ["R0742", "R0744"]
    # 고령층 안부·관계 첫 순위: 노인맞춤돌봄서비스(제도, 신청은 행정복지센터) → 면 행정복지센터
    assert [it["resource_type"] for it in items[:2]] == ["노인맞춤돌봄서비스", "행정복지센터"]


def test_response_plan_excludes_crisis_and_uses_r6_subtype():
    from bigcontest.agent.schemas import Candidate
    from bigcontest.agent.tools.report import generate_response_plan

    c = Candidate.model_construct(candidate_id="C1", candidate_level="Level 2",
                                  candidate_type="scheduled_access_review", trigger="R6 한파 특보 근사")
    res = [{"candidate_id": "C1", "name": "자살예방상담전화", "phone_based": True, "eligibility": "적합",
            "operating_status": "확인 필요", "warnings": ["묶음: 위급 시 안내 (첫 추천 아님)"]},
           {"candidate_id": "C1", "name": "남면 행정복지센터", "resource_type": "행정복지센터",
            "eligibility": "적합", "operating_status": "확인 필요", "warnings": []}]
    plan = generate_response_plan(c, res)
    assert "한파특보" in plan and "폭염" not in plan
    assert "자살예방상담전화" not in plan and "남면 행정복지센터" in plan
