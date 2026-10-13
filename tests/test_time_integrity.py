"""시간 무결성: 미래 실측 날씨, 현재 원장 접근성, 기대 활동 연결.

현재 원장 접근성·분류 규칙은 합성 근거로, 나머지는 dev 스냅샷으로 본다 (자료가 없으면 그 시험만 건너뜀).
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from bigcontest.agent.schemas import Candidate, NormalizedRequest, ToolResult
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import dev_available, dev_snapshot_id, open_datasource
from bigcontest.agent.tools.stubs import ToolContext

real = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")


def ctx(city="강남", as_of="2025-07-06", mode="simulated_replay", allow=False, **req) -> ToolContext:
    as_of = date.fromisoformat(as_of)
    req.setdefault("group", None if ("age_min" in req or "sex" in req) else "고령층")
    r = NormalizedRequest(city=city, metric="카드결제", period_to=as_of, **req)
    return ToolContext(scenario="normal", as_of=as_of, analysis_mode=mode, snapshot_id=dev_snapshot_id(),
                       request=r, backend="real", allow_scenario=allow)


def weather_trg(results):
    return [r for r in results if r.status == "OK" and str(r.value).startswith(("R4", "R6"))]


# ---------------------------------------------------------------- 미래 실측 날씨


@real
def test_future_asos_not_used_without_scenario_flag():
    """기준일 07-06: 07-07 ASOS 실측(폭염)을 예보처럼 쓰지 않음. '예보 자료 없음 → 확인 필요'."""
    ds = open_datasource(dev_snapshot_id())
    w = ds.weather().set_index(["city", "date"])
    assert w.loc[("강남", pd.Timestamp("2025-07-07")), "tmax"] >= S.HEAT_TMAX  # 시험 전제: 다음 날 실제 폭염
    out = S.get_calendar_triggers(ctx())
    assert not weather_trg(out)
    assert not any(r.observed_to and r.observed_to > date(2025, 7, 6) for r in out)
    note = [r for r in out if r.evidence_id.endswith("-TRG-WX")]
    assert note and note[0].status == "EMPTY" and "예보 자료 없음" in note[0].warnings[0]
    assert "확인 필요" in note[0].warnings[0]


@real
def test_scenario_is_explicit_and_always_labelled():
    out = weather_trg(S.get_calendar_triggers(ctx(allow=True)))
    assert out, "allow_scenario=True 면 상황 시연 근거를 만듦"
    for r in out:
        assert S.is_scenario(r) and S.SCENARIO_FLAG in str(r.value) and S.SCENARIO_FLAG in (r.measured_grain or "")
        assert r.interpretation_status == "가설" and r.analysis_mode == "simulated_replay"
        assert r.simulated_available_at is None and r.availability_basis == "unknown"  # 기준일에 쓸 수 있던 자료 아님
        assert any("성능·기준일(as-of) 평가에서 제외" in w for w in r.warnings)
    assert any(r.observed_to == date(2025, 7, 7) for r in out)  # 미래 실측을 썼다는 사실이 그대로 보임


@real
def test_scenario_never_in_strict():
    out = S.get_calendar_triggers(ctx(mode="strict_as_of", allow=True))
    assert not weather_trg(out) and not any(S.is_scenario(r) for r in out)


def test_graph_context_reads_scenario_flag():
    from bigcontest.agent.nodes import _ctx

    base = {"as_of": "2025-07-06", "analysis_mode": "simulated_replay", "snapshot_id": "snap-stub-0001"}
    assert _ctx(base).allow_scenario is False  # 기본 시연 경로는 끔
    assert _ctx({**base, "allow_scenario": True}).allow_scenario is True


# ---------------------------------------------------------------- 현재 원장 접근성


AS_OF = date(2025, 12, 20)


def _ev(code, metric, value, scope, mode, **kw) -> ToolResult:
    return ToolResult(evidence_id=f"EV-20251220-CC-{code}", metric=metric, value=value, applied_scope=scope,
                      analysis_mode=mode, as_of=AS_OF, status="OK", value_state="observed", **kw)


def _level_inputs(mode: str, access_kw: dict | None = None):
    adms = [f"320103{i:02d}" for i in range(50, 60)]
    ev = [_ev(f"STR-{a}", "1인세대 비율", 0.6 - 0.01 * i, f"춘천 X면 ({a})", mode,
              simulated_available_at=AS_OF - timedelta(days=30)) for i, a in enumerate(adms)]
    ev.append(_ev(f"ACC-{adms[0]}", "관측 거점까지 직선거리", 18.99, f"춘천 남면 ({adms[0]})", mode, **(access_kw or {})))
    c = Candidate(candidate_id="C-20251220-001", candidate_type="scheduled_access_review", city="춘천", adm_cd=adms[0],
                  analysis_cohort="65+", trigger="W 동절기 (춘천 읍·면)", input_status="충족", counter_result="해당 없음",
                  evidence_ids=[e.evidence_id for e in ev])
    return [c], ev


def test_unknown_availability_access_is_labelled_in_replay():
    (c,), ev = _level_inputs("simulated_replay")
    assert not S.access_known_at_as_of(ev[-1])  # 2026년 원장: 공개 시점 미상
    (out,) = S.assign_level([c], ev)
    assert out.candidate_level == "Level 3" and S.ACCESS_CURRENT in out.level_reason  # 현재 동작 유지 + 표시


def test_unknown_availability_access_does_not_upgrade_in_strict():
    (c,), ev = _level_inputs("strict_as_of")
    (out,) = S.assign_level([c], ev)
    assert out.candidate_level == "Level 2"  # 역사적 Level 3 확정 안 함
    assert "참고" in out.level_reason and S.ACCESS_CURRENT in out.level_reason


@pytest.mark.parametrize("kw", [
    {"warnings": ["현재 원장(2026-03) 기준"], "simulated_available_at": AS_OF - timedelta(days=1),
     "availability_basis": "assumed"},
    {"observed_from": date(2026, 3, 1), "observed_to": date(2026, 3, 31), "simulated_available_at": date(2026, 3, 31),
     "availability_basis": "assumed"},
])
def test_current_ledger_markers_are_read_defensively(kw):
    (c,), ev = _level_inputs("strict_as_of", kw)
    assert not S.access_known_at_as_of(ev[-1])
    assert S.assign_level([c], ev)[0].candidate_level == "Level 2"


def test_access_known_at_as_of_counts_in_strict():
    (c,), ev = _level_inputs("strict_as_of", {"actual_available_at": AS_OF - timedelta(days=10),
                                              "availability_basis": "reported", "observed_to": AS_OF - timedelta(days=10)})
    assert S.access_known_at_as_of(ev[-1])
    (out,) = S.assign_level([c], ev)
    assert out.candidate_level == "Level 3" and S.ACCESS_CURRENT not in out.level_reason


# ---------------------------------------------------------------- 기대 활동


@real
def test_expected_matches_alarm_baseline_and_adopted_model():
    """EXP = 경보가 쓰는 같은 요일 8주 기준선 (= 0b8 h=1), FC = 채택 모델 0b 의 1~7일 예측."""
    from bigcontest.models.baseline import SameWeekdayMean

    c = ctx("춘천", "2025-10-15", age_min=20, age_max=29, sex="여")
    out = {r.metric: r for r in S.calc_expected_activity(c)}
    exp, fc = out[S.EXPECTED_METRIC], out[S.FORECAST_METRIC]
    p = open_datasource(c.snapshot_id).panel()
    t = pd.Timestamp("2025-10-12")  # 기준일 − 공급 지연 3일
    col = ("춘천", "20대", "여성")
    ref8 = SameWeekdayMean(p.holidays, n_weeks=8).forecast(p.Y.loc[p.dates < t, [col]], horizons=[1])[col].iloc[0]
    assert exp.value == pytest.approx(float(np.expm1(ref8)), abs=0.1)
    ref4 = SameWeekdayMean(p.holidays, n_weeks=4).forecast(p.Y.loc[p.dates <= t, [col]])[col]
    assert fc.value == pytest.approx([round(float(np.expm1(v)), 1) for v in ref4], abs=0.1)
    assert exp.trained_until == date(2025, 10, 11) and fc.trained_until == date(2025, 10, 12)
    assert exp.calibrated_until == fc.calibrated_until == date(2025, 12, 31)
    assert "2025-07~12 전체로 정한 경보 규칙" in exp.rule_derived_from and "0b" in fc.rule_derived_from
    assert exp.analysis_cohort == fc.analysis_cohort == "20대 여성" and exp.snapshot_id == c.snapshot_id
    for r in (exp, fc):
        avail = r.simulated_available_at
        assert r.observed_to <= c.as_of and avail <= c.as_of


@real
def test_expected_warmup_is_empty():
    out = S.calc_expected_activity(ctx("춘천", "2025-07-20"))
    exp = [r for r in out if r.metric == S.EXPECTED_METRIC]
    assert exp and all(r.status == "EMPTY" and "학습 기간 부족" in r.warnings[0] for r in exp)


def _dev_inputs(exp_cohort: str | None, exp_status: str = "OK"):
    mode = "simulated_replay"
    key = "CC-20F-20251007"
    ev = [_ev(f"DEV-{key}", S.DEVIATION_METRIC, 0.8, "춘천 (동 목록은 공유 맥락)", mode, analysis_cohort="20대 여성"),
          _ev("SER-20F", "카드 결제건수 일별 시계열", [1.0, 2.0], "춘천 (동 목록은 공유 맥락)", mode,
              analysis_cohort="20대 여성")]
    for i, m in enumerate(("반증: 공휴일 기대값", "반증: 도시 공통요인", "반증: 강수")):
        ev.append(_ev(f"CF{i + 1}-{key}", m, "통과", "춘천 (동 목록은 공유 맥락)", mode))
    if exp_status == "OK":
        ev.append(_ev("EXP-20F", S.EXPECTED_METRIC, 100.0, "춘천 (동 목록은 공유 맥락)", mode,
                      analysis_cohort=exp_cohort))
    else:
        ev.append(ToolResult(evidence_id="EV-20251220-CC-EXP-20F", metric=S.EXPECTED_METRIC, analysis_mode=mode,
                             as_of=AS_OF, status="EMPTY", warnings=[S.EXPECTED_PENDING], analysis_cohort=exp_cohort))
    req = NormalizedRequest(city="춘천", metric="카드결제", period_to=AS_OF, age_min=20, age_max=29, sex="여")
    return S.classify_candidate_type(ev, req, AS_OF)


def test_deviation_candidate_complete_with_expected_activity():
    (c,) = _dev_inputs("20대 여성")
    assert c.input_status == "충족" and not c.missing_inputs


@pytest.mark.parametrize("cohort,status", [("20대 여성", "EMPTY"), ("70대 여성", "OK")])
def test_deviation_partial_only_when_expected_missing(cohort, status):
    (c,) = _dev_inputs(cohort, status)  # warm-up(EMPTY) 이거나 다른 코호트 기대값뿐이면 부분 판정
    assert c.input_status == "부분 판정" and c.missing_inputs == ["expected_activity"]
