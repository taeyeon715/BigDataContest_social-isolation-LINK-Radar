"""계약 테스트. 원본 데이터 없이 돈다."""

from datetime import date

import pytest

from bigcontest.agent.schemas import (
    ASSUMED_AVAILABILITY_LABEL,
    Candidate,
    NormalizedRequest,
    ToolResult,
    ValueState,
)
from bigcontest.contracts.claims import card_claim, check_claim, claim_lists
from bigcontest.contracts.cohort import label_allowed, resolve_cohort
from bigcontest.contracts.grain import check_request_contract, get_data_capabilities
from bigcontest.contracts.inputs import evaluate_inputs, required_tools
from bigcontest.contracts.time import (
    Provenance,
    availability,
    can_join_same_month,
    check_data_time,
    check_model_time,
    check_time_contract,
    observed_window,
)
from bigcontest.contracts.values import parse_value, usable_for_stats

# ------------------------------------------------------------ 단위 계약


def test_forbidden_combo_age_by_time_of_day_is_error():
    # "강남 60대의 밤 활동" → 통신 성연령 표 × 시간대 표 결합이 필요 → ERROR
    req = NormalizedRequest(city="강남", metric="체류인구", age_min=60, age_max=69, time_of_day="18_23")
    res = check_request_contract(req)
    assert res.status == "ERROR"
    assert res.reason_codes == ["GRAIN_FORBIDDEN_COMBO"]
    assert res.issues[0].rule == "G-02"


@pytest.mark.parametrize(
    "kwargs, code",
    [
        (dict(metric="체류인구", dimensions=["age", "weekday"]), "GRAIN_FORBIDDEN_COMBO"),
        (dict(metric="카드결제", dimensions=["customer_residence", "time_of_day"]), "GRAIN_FORBIDDEN_COMBO"),
        (dict(metric="카드결제", adm_cd="11230510"), "DIMENSION_NOT_AVAILABLE"),
        (dict(metric="1인세대비율", sex="남", dimensions=["household_size"]), "GRAIN_FORBIDDEN_COMBO"),
        (dict(metric="날씨", dimensions=["adm_cd"]), "DIMENSION_NOT_AVAILABLE"),
    ],
)
def test_unsupported_requests(kwargs, code):
    res = check_request_contract(NormalizedRequest(city="강남", **kwargs))
    assert res.status == "ERROR"
    assert code in res.reason_codes


def test_city_not_covered():
    res = check_request_contract(NormalizedRequest(city="춘천", metric="시민생활지표"))
    assert res.status == "ERROR" and res.reason_codes == ["CITY_NOT_COVERED"]


def test_supported_requests_pick_source():
    card = check_request_contract(NormalizedRequest(city="춘천", metric="카드결제", dimensions=["customer_residence", "date"]))
    assert card.status == "OK" and card.source == "card2"
    slot = check_request_contract(NormalizedRequest(city="강남", metric="카드결제", age_min=60, time_of_day="18_23"))
    assert slot.status == "OK" and slot.source == "card1"
    assert any("데이터1" in w for w in slot.warnings)
    tel = check_request_contract(NormalizedRequest(city="강남", metric="체류인구", dimensions=["adm_cd", "time_of_day"]))
    assert tel.status == "OK" and tel.source == "tel_time"


def test_capabilities_cover_all_sources():
    cap = get_data_capabilities()
    names = {s.source for s in cap.sources}
    assert {"tel_age", "tel_time", "tel_wkdy", "card1", "card2", "resident_pop", "resident_hh", "seoul_living", "asos", "resources"} == names
    sl = next(s for s in cap.sources if s.source == "seoul_living")
    assert sl.cities == ["강남"] and sl.observed_window_status == "미확인"


# ------------------------------------------------------------ 연령 계약


def test_65plus_with_card_attaches_warning():
    res = check_request_contract(NormalizedRequest(city="강남", metric="카드결제", age_min=65))
    assert res.status == "OK"
    assert res.analysis_cohort == "60대·70대·80대+"
    assert res.service_eligibility_group == "65세 이상"
    assert any("60~64세가 섞임" in w for w in res.warnings)


def test_group_uses_design_table():
    old = resolve_cohort("card2", group="고령층")
    assert old.bands == ["60대", "70대", "80대+"] and not old.exact
    mid = resolve_cohort("card2", group="중장년 남성 1인가구")
    assert mid.bands == ["40대", "50대"]
    assert any("60~64세가 빠짐" in w for w in mid.warnings)
    tel = resolve_cohort("tel_age", group="고령층")
    assert tel.analysis_cohort == "60+" and tel.warnings


def test_telecom_60plus_never_called_70plus():
    res = check_request_contract(NormalizedRequest(city="춘천", metric="체류인구", age_min=70))
    assert res.status == "ERROR" and res.reason_codes == ["COHORT_NOT_SUPPORTED"]
    assert not label_allowed("tel_age", 70)
    assert label_allowed("tel_age", 60)
    assert label_allowed("card2", 70)


def test_exact_cohort_has_no_warning():
    res = resolve_cohort("card2", 60, 79)
    assert res.exact and res.warnings == []
    assert resolve_cohort("resident_pop", 65).exact


# ------------------------------------------------------------ 시간 계약


def test_post_as_of_data_rejected():
    item = availability("card2", day=date(2025, 10, 10))  # 가정 공개일 10-13
    for mode in ("simulated_replay", "strict_as_of"):
        d = check_data_time(item, date(2025, 10, 5), mode)
        assert not d.usable and d.code == "TIME_DATA_AFTER_AS_OF"
    # 관측은 끝났지만 가정 공개일이 기준일 뒤
    d = check_data_time(item, date(2025, 10, 11), "simulated_replay")
    assert not d.usable and d.code == "TIME_DATA_AFTER_AS_OF"
    assert check_data_time(item, date(2025, 10, 13), "simulated_replay").usable
    # 사후 분석은 쓰되 표시
    d = check_data_time(item, date(2025, 10, 5), "retrospective")
    assert d.usable and d.labels


def test_strict_as_of_rejects_unknown_availability():
    item = availability("card2", day=date(2025, 9, 1))
    assert item.actual_available_at is None and item.availability_basis == "assumed"
    d = check_data_time(item, date(2025, 10, 1), "strict_as_of")
    assert not d.usable and d.code == "TIME_AVAILABILITY_UNKNOWN" and d.role == "background"
    known = availability("card2", day=date(2025, 9, 1), actual_available_at=date(2025, 9, 5))
    assert known.availability_basis == "reported"
    assert check_data_time(known, date(2025, 10, 1), "strict_as_of").usable


def test_simulated_replay_attaches_assumption_label():
    item = availability("tel_age", base_month="202508")
    d = check_data_time(item, date(2025, 10, 1), "simulated_replay")
    assert d.usable and ASSUMED_AVAILABILITY_LABEL in d.labels
    # 반환 봉투도 스스로 표시를 붙임
    r = ToolResult(
        evidence_id="EV-1",
        metric="m",
        value=1.0,
        analysis_mode="simulated_replay",
        as_of=date(2025, 10, 1),
        simulated_available_at=date(2025, 9, 15),
        availability_basis="assumed",
        status="OK",
    )
    assert ASSUMED_AVAILABILITY_LABEL in r.warnings
    # 가정 공개일도 없으면 배경 자료로만
    none = availability("tel_age", base_month="202508", assume=False)
    assert none.availability_basis == "unknown"
    assert check_data_time(none, date(2025, 10, 1), "simulated_replay").role == "background"


def test_model_after_as_of():
    # 2025 추석 이후 회복 지연으로 만든 규칙을 추석 D-7(9/26)에 씀
    rule = Provenance("R3 회복 지연 규칙", rule_derived_from=(date(2025, 10, 10), date(2025, 10, 19)))
    d = check_model_time(rule, date(2025, 9, 26), "strict_as_of")
    assert not d.usable and d.code == "TIME_MODEL_AFTER_AS_OF"
    d = check_model_time(rule, date(2025, 9, 26), "simulated_replay")
    assert d.usable and any("사후 참고" in x for x in d.labels)
    model = Provenance("기대 활동", trained_until=date(2025, 9, 20), calibrated_until=date(2025, 9, 25))
    assert check_model_time(model, date(2025, 9, 26), "strict_as_of").usable
    assert not check_model_time(Provenance("x"), date(2025, 9, 26), "strict_as_of").usable


def test_time_contract_aggregate():
    items = [availability("card2", day=date(2025, 9, d)) for d in (1, 2, 30)]
    tc = check_time_contract(items, date(2025, 9, 10), "simulated_replay", required_sources={"card2"})
    assert tc.result.status == "OK" and len(tc.usable) == 2
    tc = check_time_contract(items, date(2025, 9, 10), "strict_as_of", required_sources={"card2"})
    assert tc.result.status == "ERROR" and "TIME_NO_USABLE_DATA" in tc.result.reason_codes
    rule = Provenance("규칙", trained_until=date(2025, 12, 31))
    tc = check_time_contract([], date(2025, 9, 10), "strict_as_of", models=[rule])
    assert tc.result.issues[0].rule == "G-13"


def test_seoul_living_window_differs_from_base_month():
    start, end, status = observed_window("seoul_living", "202507")
    assert (start, end, status) == (date(2025, 4, 1), date(2025, 6, 30), "미확인")
    item = availability("seoul_living", base_month="202507")
    assert item.actual_available_at is None  # FAQ "20일경"을 실제 공개일로 보지 않음
    assert item.simulated_available_at == date(2025, 8, 20)
    d = check_data_time(item, date(2025, 8, 1), "simulated_replay")
    assert not d.usable  # 아직 공개 전
    d = check_data_time(item, date(2025, 9, 1), "simulated_replay")
    assert d.usable and any("미확인" in x for x in d.labels)
    # 관측 기간이 미확인이면 통신과 "같은 달"로 결합하지 않음
    assert not can_join_same_month("seoul_living", "tel_age", "202507")
    assert can_join_same_month("tel_age", "resident_pop", "202507")


# ------------------------------------------------------------ 필수 근거 / 선택 맥락


def test_winter_review_without_card_and_seoul_living_is_full():
    chk = evaluate_inputs(
        "춘천 동절기 점검",
        {"calendar_trigger", "target_group", "structure_vulnerability", "access_gap", "winter_contraction"},
    )
    assert chk.status == "충족" and chk.missing_required == []
    assert "card_cohort_series" in chk.missing_optional and "validation_reference" in chk.missing_optional
    assert chk.warnings


def test_missing_required_gives_partial_judgement():
    have = {"cohort_deviation", "card_cohort_series", "expected_activity", "holiday_expectation", "city_common_factor"}
    chk = evaluate_inputs("unexpected_activity_deviation", have)
    assert chk.status == "부분 판정" and chk.missing_inputs == ["weather_asos"]
    cand = Candidate(
        candidate_id="C-1",
        candidate_type="unexpected_activity_deviation",
        city="춘천",
        analysis_cohort="80대+",
        input_status=chk.status,
        missing_inputs=chk.missing_inputs,
        counter_result="보류",
    )
    assert cand.missing_inputs == ["weather_asos"]
    with pytest.raises(ValueError):
        Candidate(**{**cand.model_dump(), "missing_inputs": []})


def test_missing_core_holds_and_any_of():
    chk = evaluate_inputs("unexpected_activity_deviation", {"card_cohort_series"})
    assert chk.status == "보류"
    chk = evaluate_inputs("scheduled_access_review", {"calendar_trigger", "target_group"})
    assert chk.status == "부분 판정"
    assert chk.missing_inputs == ["structure_vulnerability 또는 access_gap"]
    assert evaluate_inputs("scheduled_access_review", {"calendar_trigger", "target_group", "access_gap"}).status == "충족"
    with pytest.raises(KeyError):
        evaluate_inputs("scheduled_access_review", {"없는근거"})


def test_required_tools_fixed():
    tools = required_tools("unexpected_activity_deviation")
    assert {"calc_cohort_deviation", "get_card_cohort_series", "check_confounders"} <= set(tools)


# ------------------------------------------------------------ 주장 계약


def _card_ev(**kw):
    base = dict(
        evidence_id="EV-20251001-0007",
        metric="생필품 결제 평소 대비",
        value=0.84,
        analysis_mode="simulated_replay",
        as_of=date(2025, 10, 20),
        merchant_region="춘천시",
        customer_residence="강원",
        status="OK",
    )
    base.update(kw)
    return ToolResult(**base)


def test_valid_card_claim_passes():
    text = card_claim("강원", "80대 이상", "춘천", "생필품", "낮음")
    assert text == "강원 거주 80대 이상 고객의 춘천 가맹점 생필품 결제가 비교 기준보다 낮음"
    assert check_claim(text, "card2", _card_ev()).ok


@pytest.mark.parametrize(
    "original, swapped",
    [("강원 거주", "춘천 거주"), ("강원 거주", "춘천시 거주"), ("강원 거주", "강남구 거주"), ("강원 거주", "남면 거주")],
)
def test_residence_finer_than_sido_rejected(original, swapped):
    text = card_claim("강원", "80대 이상", "춘천", "생필품", "낮음").replace(original, swapped)
    res = check_claim(text, "card2", _card_ev())
    assert not res.ok and "RESIDENCE_FINER_THAN_SIDO" in res.codes


def test_seoul_to_gangnam_residence_rejected():
    ev = _card_ev(merchant_region="강남구", customer_residence="서울")
    ok = card_claim("서울", "70대", "강남", "병원·의원", "낮음")
    assert check_claim(ok, "card2", ev).ok
    assert "RESIDENCE_FINER_THAN_SIDO" in check_claim(ok.replace("서울 거주", "강남구 거주"), "card2", ev).codes
    assert "RESIDENCE_MISMATCH" in check_claim(ok.replace("서울 거주", "경기 거주"), "card2", ev).codes
    assert "RESIDENCE_NOT_IN_SOURCE" in check_claim(ok, "card1").codes


def test_shared_context_forbids_adm_change():
    ev = _card_ev(is_shared_context=True, applied_scope="춘천 남면")
    for text in ["이 동의 결제가 줄었다", "논현1동의 70대 결제가 줄었다", "남면 결제가 감소했다"]:
        assert "ADM_LEVEL_FROM_SHARED_CONTEXT" in check_claim(text, "card2", ev).codes, text
    assert check_claim("먼저 확인할 동: 남면 (도시 단위 신호 공유)", "card2", ev).ok
    _, forbidden = claim_lists("card2", is_shared_context=True)
    assert any("이 동의 결제가 줄었다" in f for f in forbidden)


def test_other_forbidden_claims():
    assert "CARD_ISOLATION_DETECTION" in check_claim("카드로 독거 고령자를 찾았다", "card2").codes
    assert "TEL_AGE_FINER_THAN_60PLUS" in check_claim("이 칸의 70대 이상 체류가 많다", "tel_age").codes
    assert "STAY_AS_RESIDENT" in check_claim("이 칸의 주민 수가 많다", "tel_age").codes
    assert "HOUSEHOLD_TERM" in check_claim("1인가구 비율이 높다", "resident_hh").codes
    assert "TEL_AGE_IN_RHYTHM" in check_claim("60대의 밤 체류가 많다", "tel_time").codes
    assert "DIAGNOSIS" in check_claim("이 사람은 고립 위험 87점", "resident_hh").codes
    assert check_claim("1인세대 비율이 구 내 상위", "resident_hh").ok


# ------------------------------------------------------------ 값 상태


def test_value_states_distinct():
    assert parse_value("0") == (0.0, ValueState.ZERO)
    assert parse_value("*") == (None, ValueState.SUPPRESSED)
    assert parse_value("") == (None, ValueState.MISSING)
    assert parse_value(float("nan")) == (None, ValueState.MISSING)
    assert parse_value("abc") == (None, ValueState.MISSING)  # 0 으로 바꾸지 않음
    assert parse_value("1,234") == (1234.0, ValueState.OBSERVED)
    assert parse_value("0", quality_suspect=True) == (0.0, ValueState.QUALITY_SUSPECT)
    assert usable_for_stats(ValueState.ZERO) and not usable_for_stats(ValueState.QUALITY_SUSPECT)


def test_card2_claim_filled_and_passes_guard():
    """할 수 있는 말의 빈칸은 근거 값으로 채움. 모르는 값은 빼고, 채운 문장도 거주지 검사(G-03)를 통과."""
    from bigcontest.agent.schemas import ToolResult
    from bigcontest.contracts.claims import check_claim, claim_lists, fill_claim, fill_claims

    tmpl = claim_lists("card2")[0][0]
    ev = ToolResult.model_validate({
        "evidence_id": "EV-1", "metric": "m", "value": 0.9, "measured_grain": "도시×연령×일 (생필품 업종)", "applied_scope": "춘천",
        "merchant_region": "춘천시", "customer_residence": "강원", "analysis_cohort": "70대 여성",
        "analysis_mode": "simulated_replay", "as_of": "2025-09-26", "status": "OK", "valid_claims": [tmpl]})
    got = fill_claims(ev).valid_claims[0]
    assert got == "강원 거주 70대 여성 고객의 춘천시 가맹점 생필품 결제가 비교 기준보다 낮음/높음"
    assert check_claim(got, "card2", ev).ok
    bare = fill_claim(tmpl, {})
    assert bare == "고객의 가맹점 결제가 비교 기준보다 낮음/높음" and check_claim(bare, "card2").ok
    assert fill_claim("{관측소} 관측소 기준 폭염일", {}) == "관측소 기준 폭염일"
