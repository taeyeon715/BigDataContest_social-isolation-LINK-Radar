"""실제 도구 공통 규칙 (자료 없이 도는 테스트): 근거 ID·신호 키, 시간 계약, 구조 상위 판정, 반증 판정, 원인 유형·대응안.

- 도시 공통 하락은 기각하지 않고 보류(확인 필요), 강수는 자동 기각 안 함 (설계 §5.4)
- 상대 순위만으로 '상위'를 만들지 않음 (절대 최소값)
"""

from datetime import date

import pytest

from bigcontest.agent.schemas import Candidate, ToolResult
from bigcontest.agent.tools import TOOL_NAMES, call_tool
from bigcontest.agent.tools import report as R
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.common import (
    city_of_evidence,
    cohort_key,
    daily_cutoff,
    signal_key,
    usable_months,
)
from bigcontest.agent.tools.stubs import ToolContext
from bigcontest.agent.tools.welfare import cause_for

AS_OF = date(2025, 10, 15)


def ev(eid: str, metric: str, value, **kw) -> ToolResult:
    return ToolResult(evidence_id=eid, metric=metric, value=value, analysis_mode="retrospective", as_of=AS_OF,
                      status=kw.pop("status", "OK"), **kw)


def test_signal_key_pairs_deviation_and_confounders():
    assert signal_key("EV-20251015-CC-DEV-CC-20F-20251007") == "CC-20F-20251007"
    assert signal_key("EV-20251015-CC-CF3-CC-20F-20251007") == "CC-20F-20251007"
    assert signal_key("EV-20250926-DEV") == "" and signal_key("EV-20250926-CF1") == ""  # 가짜 도구
    assert signal_key("EV-20251015-CC-STR-32010350") is None
    assert cohort_key("춘천", "80대+", "여성") == "CC-80PF"


def test_city_of_evidence():
    assert city_of_evidence(ev("EV-20251015-GN-TRG-R1", "달력 트리거", "R1")) == "강남"
    assert city_of_evidence(ev("EV-20251015-STR-32010350", "1인세대 비율", 0.5, applied_scope="춘천 남면 (32010350)")) == "춘천"


def test_time_contract_cutoffs():
    assert daily_cutoff("card2", AS_OF, "simulated_replay") == date(2025, 10, 12)  # 공급 지연 3일
    assert daily_cutoff("card2", AS_OF, "retrospective") == AS_OF
    assert daily_cutoff("card2", AS_OF, "strict_as_of") is None  # 실제 공개일 모름
    # 주민등록 9월분은 10/10 공개(가정) → 10/15 에 9월까지
    assert [m.base_month for m in usable_months("resident_hh", AS_OF, "simulated_replay")][-1] == "202509"
    # 통신 11월분은 12/15 공개(가정)
    assert "202511" not in [m.base_month for m in usable_months("tel_age", date(2025, 12, 10), "simulated_replay")]
    assert "202511" in [m.base_month for m in usable_months("tel_age", date(2025, 12, 20), "simulated_replay")]


def _structs(vals: dict[str, float]) -> list[ToolResult]:
    return [ev(f"EV-20251015-STR-{a}", "1인세대 비율", v, applied_scope=f"춘천 X ({a})") for a, v in vals.items()]


def test_structure_top_needs_rank_and_absolute_minimum():
    vals = {f"3201{i:04d}": 0.30 - i * 0.001 for i in range(10)}  # 모두 절대 최소값(0.35) 미만
    assert S.structure_tops(_structs(vals)) == {}
    vals = {f"3201{i:04d}": 0.70 - i * 0.03 for i in range(10)}
    tops = S.structure_tops(_structs(vals))
    assert len(tops) == 3  # 10곳 중 상위 30%
    assert all(v >= 0.35 for v, _, _ in tops.values())


def _dev_candidate(status="충족") -> Candidate:
    return Candidate(candidate_id="C-1", candidate_type="unexpected_activity_deviation", city="강남",
                     analysis_cohort="70대 남성", input_status=status, counter_result="보류",
                     missing_inputs=[] if status == "충족" else ["expected_activity"])


def _conf(cf1, cf2, cf3, notes=("n1", "n2", "n3")):
    return {
        "holiday_expectation": ev("EV-1-CF1-K", "반증: 공휴일 기대값", cf1, warnings=[notes[0]]),
        "city_common_factor": ev("EV-1-CF2-K", "반증: 도시 공통요인", cf2, warnings=[notes[1]]),
        "weather_asos": ev("EV-1-CF3-K", "반증: 강수", cf3, warnings=[notes[2]]),
    }


def test_city_common_drop_is_never_rejection():
    res, why = S.judge_counter(_dev_candidate(), _conf("설명 일부", "공통 변화", "통과"))
    assert res == "보류" and "확인 필요" in why


def test_rain_does_not_reject():
    res, why = S.judge_counter(_dev_candidate("부분 판정"), _conf("통과", "통과", "설명 일부", ("a", "b", "비 설명")))
    assert res == "통과" and "비 설명" in why


def test_holiday_rejection_and_pending():
    assert S.judge_counter(_dev_candidate(), _conf("기각", "통과", "통과"))[0] == "기각"
    res, why = S.judge_counter(_dev_candidate(), _conf("검토 중", "검토 중", "통과"))
    assert res == "보류" and "검토 중" in why
    # 반증 자료 결측 → 그 후보만 보류
    conf = _conf("통과", "통과", "통과")
    del conf["weather_asos"]
    assert S.judge_counter(_dev_candidate(), conf)[0] == "보류"


def test_levels_rules_v1():
    c = Candidate(candidate_id="C-2", candidate_type="scheduled_access_review", city="춘천", adm_cd="32010350",
                  analysis_cohort="60대·70대·80대+", trigger="W 동절기 (춘천 읍·면)", input_status="충족",
                  counter_result="해당 없음")
    evidence = _structs({"32010350": 0.61, "32010110": 0.30, "32010310": 0.33}) + [
        ev("EV-20251015-ACC-32010350", "관측 거점까지 직선거리", 18.6, unit="km", applied_scope="춘천 남면 (32010350)")]
    (out,) = S.assign_level([c], evidence)
    assert out.candidate_level == "Level 3" and "rules-v1.1" in out.level_reason
    assert out.rule_id == "L3-STRUCT-ACCESS-TRIGGER" and out.rule_version == "rules-v1.1"
    partial = Candidate(**{**c.model_dump(), "input_status": "부분 판정", "missing_inputs": ["winter_contraction"]})
    assert S.assign_level([partial], evidence)[0].candidate_level == "Level 2"  # 부분 판정은 Level 3 불가


def test_cause_and_plan():
    assert cause_for("W 동절기 (춘천 읍·면)", 18.6, 5.0) == "거점 원거리형+계절 공동화형"  # 원래 원인 보존
    assert cause_for("R1 추석 연휴 D-7 (10/03~10/09)", 0.4, 5.0) == "달력 공백형"
    assert cause_for(None, None, 5.0) == "1인가구 밀집형"
    c = Candidate(candidate_id="C-3", candidate_type="scheduled_access_review", city="춘천", adm_cd="32010350",
                  analysis_cohort="60+", trigger="W 동절기 (춘천 읍·면)", input_status="충족",
                  counter_result="해당 없음", candidate_level="Level 3", level_reason="x")
    plan = R.generate_response_plan(c, [], 5.0, 18.6)
    assert "순회 안부" in plan and "전화형·찾아가는 서비스부터" in plan and "자원 후보 없음" in plan
    assert R.generate_response_plan(Candidate(**{**c.model_dump(), "candidate_level": "Level 1"}), [], 5.0, 1.0) is None


def test_call_tool_whitelist_and_real_error_envelope():
    ctx = ToolContext(scenario="normal", as_of=AS_OF, analysis_mode="simulated_replay",
                      snapshot_id="not-a-snapshot", backend="real")
    with pytest.raises(KeyError):
        call_tool("run_sql", ctx)
    assert "run_sql" not in TOOL_NAMES
    from bigcontest.agent.schemas import NormalizedRequest

    ctx.request = NormalizedRequest(city="춘천", metric="카드결제", group="고령층", period_to=AS_OF)
    (out,) = call_tool("get_structure", ctx)  # 열 수 없는 스냅샷 → 예외가 아니라 ERROR 봉투
    assert out.status == "ERROR" and out.error_code == "DATA_UNAVAILABLE"
