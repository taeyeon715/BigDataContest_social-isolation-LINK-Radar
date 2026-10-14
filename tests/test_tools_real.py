"""실제 도구 (dev 스냅샷: data/interim + external + ref). 자료가 없으면 건너뜀.

- 모든 도구가 §5.1 반환 봉투를 돌려주고, data_version 에 원천·파일 해시가 있음
- 기준일 뒤 자료를 쓰지 않음 (예보 가정 트리거 제외), 이탈 경보·반증은 기준일 카드 (미래 회복을 보지 않음)
- 도시 공통 하락은 '확인 필요'(공통 변화), 실제 기각 경보(춘천 20대 여성 10/07)는 기각
"""

from datetime import date

import pytest

from bigcontest.agent.schemas import ResourceRecommendation, ToolResult
from bigcontest.agent.tools import call_tool, find_resources
from bigcontest.agent.tools import data as D
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools import welfare as W
from bigcontest.agent.tools.datasource import (
    DataUnavailable,
    SnapshotMismatch,
    DataSource,
    dev_available,
    dev_snapshot_id,
    open_datasource,
)
from bigcontest.agent.schemas import NormalizedRequest
from bigcontest.agent.tools.stubs import ToolContext

pytestmark = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")


def ctx(city="춘천", as_of="2025-10-15", mode="simulated_replay", allow_scenario=False, **req) -> ToolContext:
    as_of = date.fromisoformat(as_of)
    req.setdefault("group", None if ("age_min" in req or "sex" in req) else "고령층")
    r = NormalizedRequest(city=city, metric="카드결제", period_to=as_of, **req)
    return ToolContext(scenario="normal", as_of=as_of, analysis_mode=mode, snapshot_id=dev_snapshot_id(),
                       request=r, backend="real", allow_scenario=allow_scenario)


def ok(results: list[ToolResult]) -> list[ToolResult]:
    return [r for r in results if r.status == "OK"]


def no_late(results: list[ToolResult], as_of: date) -> None:
    for r in results:
        assert not S.is_scenario(r), r.evidence_id  # 기본 경로에는 상황 시연(미래 실측) 근거가 없음
        assert r.observed_to is None or r.observed_to <= as_of, r.evidence_id
        avail = r.actual_available_at or r.simulated_available_at
        assert avail is None or avail <= as_of, (r.evidence_id, avail)


def test_snapshot_is_pinned():
    sid = dev_snapshot_id()
    assert sid.startswith("dev-") and open_datasource(sid) is open_datasource(sid)
    with pytest.raises(SnapshotMismatch):
        DataSource("dev-000000000000")
    with pytest.raises(DataUnavailable):
        open_datasource("snap-20251020-01")  # 발행 스냅샷 읽기는 아직 없음


@pytest.mark.parametrize("tool", ["get_data_capabilities", "check_request_contract", "get_structure", "get_mobility_layer",
                                  "get_card_cohort_series", "get_validation_reference", "get_calendar_triggers",
                                  "calc_expected_activity", "calc_cohort_deviation", "check_confounders",
                                  "calc_holiday_gap", "calc_winter_contraction", "calc_heat_response", "get_access_gap"])
@pytest.mark.parametrize("city", ["강남", "춘천"])
def test_every_tool_returns_envelope(tool, city):
    c = ctx(city)
    out = call_tool(tool, c)
    assert all(isinstance(r, ToolResult) for r in out)
    assert all(r.status != "ERROR" for r in out), [(r.evidence_id, r.warnings) for r in out if r.status == "ERROR"]
    assert all(r.snapshot_id == c.snapshot_id and r.analysis_mode == "simulated_replay" for r in out)
    assert len({r.evidence_id for r in out}) == len(out)  # 근거 ID 중복 없음
    for r in ok(out):
        if r.data_version and "@sha256:" in r.data_version:
            assert r.data_version.split("@")[0] in ("card2", "card1", "tel_age", "resident_hh", "resident_pop", "seoul_living",
                                                    "asos", "resources", "welfare_recipients")
        if r.merchant_region:
            assert r.customer_residence in ("서울", "강원") and r.is_shared_context
    no_late(out, c.as_of)


def test_structure_and_access():
    s = [r for r in ok(D.get_structure(ctx("강남"))) if r.metric == "1인세대 비율"]
    assert len(s) == 22 and all(0 < r.value < 1 for r in s)
    assert all(r.observed_to == date(2025, 9, 30) for r in s)  # 10/15 에는 9월분까지 (공개 가정 10일)
    acc_gn = ok(W.get_access_gap(ctx("강남", group="고령층")))
    assert all(r.value < S.STUB_LEVEL_RULES["access_far_km"] for r in acc_gn)  # 강남: 거리 공백 근거 약함
    acc_cc = {r.evidence_id[-8:]: r.value for r in ok(W.get_access_gap(ctx("춘천")))}
    assert acc_cc["32010350"] > 10  # 남면


def test_validation_reference_only_gangnam():
    assert D.get_validation_reference(ctx("춘천"))[0].status == "EMPTY"
    v = D.get_validation_reference(ctx("강남", group="중장년 남성 1인가구"))
    assert len(v) == 22 and all("탐색적" in r.metric for r in v)


def test_triggers():
    vals = lambda c: [r.value for r in ok(S.get_calendar_triggers(c))]  # noqa: E731
    assert any(str(v).startswith("R1 추석") for v in vals(ctx("강남", "2025-09-26")))
    assert any(str(v).startswith("W 동절기") for v in vals(ctx("춘천", "2025-12-20")))
    assert not any(str(v).startswith("W") for v in vals(ctx("강남", "2025-12-20")))
    assert any(str(v).startswith("R2") for v in vals(ctx("춘천", "2025-10-06")))
    assert any(str(v).startswith("R3") for v in vals(ctx("춘천", "2025-10-15")))
    assert not any(str(v).startswith("R3") for v in vals(ctx("춘천", "2025-10-15", age_min=20, age_max=29)))
    # 예보 기록이 없으므로 기본은 R4 를 만들지 않고 "예보 자료 없음 → 확인 필요"
    assert not any(str(v).startswith("R4") for v in vals(ctx("춘천", "2025-07-28")))
    hot = [r for r in ok(S.get_calendar_triggers(ctx("춘천", "2025-07-28", allow_scenario=True)))
           if str(r.value).startswith("R4")]
    assert hot and S.is_scenario(hot[0]) and hot[0].interpretation_status == "가설"
    strict = [r.value for r in ok(S.get_calendar_triggers(ctx("춘천", "2025-07-28", "strict_as_of", allow_scenario=True)))]
    assert not any(str(v).startswith(("R4", "R6")) for v in strict)  # 예보 기록 없음 → strict 에서 안 씀


def test_expected_activity_connected():
    """이탈 기대값 = EDA-6 기준선, 1~7일 예측 = 3A 채택 0b."""
    out = S.calc_expected_activity(ctx())
    exp = [r for r in out if r.metric == S.EXPECTED_METRIC]
    fc = [r for r in out if r.metric == S.FORECAST_METRIC]
    assert exp and fc and all(r.status == "OK" for r in out)
    assert all(r.rule_derived_from == S.ALARM_RULE_FROM for r in exp)
    assert all("0b" in r.rule_derived_from and len(r.value) == 7 for r in fc)


def test_real_rejected_alarm_and_no_future_leak():
    c = ctx("춘천", "2025-10-15", age_min=20, age_max=29, sex="여")
    (dev,) = ok(S.calc_cohort_deviation(c))
    assert dev.analysis_cohort == "20대 여성" and dev.direction == "감소" and dev.is_shared_context
    assert dev.merchant_region == "춘천시" and dev.customer_residence == "강원"
    conf = {r.metric: r for r in S.check_confounders(c)}
    assert conf["반증: 공휴일 기대값"].value == "기각"
    assert conf["반증: 강수"].value in ("설명 일부", "통과", "판정 불가")  # 강수는 기각에 쓰지 않음
    # 경보 직후 기준일(자료는 10/07 까지): 연휴 뒤 회복을 아직 못 봄 → 검토 중
    early = {r.metric: r.value for r in S.check_confounders(ctx("춘천", "2025-10-10", age_min=20, age_max=29, sex="여"))}
    assert early["반증: 공휴일 기대값"] == "검토 중"


def test_city_common_drop_is_confirm_needed():
    conf = [r for r in S.check_confounders(ctx("강남", "2025-10-15")) if r.metric == "반증: 도시 공통요인"]
    assert any(r.value == "공통 변화" for r in conf)
    assert not any(r.value == "기각" for r in S.check_confounders(ctx("강남", "2025-10-15")))


def test_strict_mode_card_is_not_used():
    (r,) = D.get_card_cohort_series(ctx(mode="strict_as_of"))
    assert r.status == "EMPTY" and "TIME_AVAILABILITY_UNKNOWN" in r.warnings[0]


def test_winter_contraction_respects_publication():
    assert S.calc_winter_contraction(ctx("춘천", "2025-12-10"))[0].status == "EMPTY"  # 11월분 공개 전
    w, tod = S.calc_winter_contraction(ctx("춘천", "2025-12-20"))  # rules-v1.1: 시간대 상대 리듬(맥락)이 뒤에 붙음
    assert w.status == "OK" and w.value < 1 and w.observed_to == date(2025, 11, 30)
    assert tod.metric == S.RHYTHM_METRIC and tod.status == "OK" and tod.observed_to <= date(2025, 12, 20)


def test_find_resources_maps_ledger_values():
    c = ctx("춘천", "2025-12-20")
    env, items = find_resources(c, "32010350", "고령층", cause_type="거점 원거리형")
    assert env.metric == "복지 자원 후보" and env.value == len(items) and items
    for it in items:
        assert it["eligibility"] in ("적합", "확인 필요", "부적합")
        assert it["operating_status"] in ("운영", "확인 필요", "종료")
        if it["verified_date"]:
            ResourceRecommendation(**{k: v for k, v in it.items() if k in ResourceRecommendation.model_fields})
    # 위기 전화는 순위 밖 "위급 시 안내"로 맨 아래. 순위 안은 고령층 안부·관계 첫 순위
    # (노인맞춤돌봄서비스 제도 → 면 행정복지센터) 부터
    groups = [it["group"] for it in items]
    assert groups == sorted(groups, key=lambda g: g == "위급 시 안내")
    ranked = [it for it in items if it["group"] != "위급 시 안내"]
    assert [it["resource_type"] for it in ranked[:2]] == ["노인맞춤돌봄서비스", "행정복지센터"]
    assert not any(it["phone_based"] for it in ranked[:2])
    assert all(it["verified_date"] for it in items)  # 제외 규칙을 먼저 적용한 뒤 상위 K
    (e,) = W.check_eligibility(c, items[0]["resource_id"], "어린이")
    assert e.value == "확인 필요"
