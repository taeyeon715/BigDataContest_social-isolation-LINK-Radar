"""모델이 원본 자료 없이 작은 fixture에서 돌고, 요청 메타데이터를 남기고, EDA-6 규칙과 맞물리는지."""

import numpy as np
import pandas as pd
import pytest

from bigcontest.models import SameWeekdayMean, SeasonalNaive, STLModel, validate_panel
from bigcontest.models.changepoint import pelt, prefix_discovery
from bigcontest.models.evaluate import (ModelPanel, ModelResid, add_intervals, attach_actual, forecast_metrics, origins,
                                        requests, rolling_forecast, run_pairs, step, transitions)
from bigcontest.signals.alarm import AlarmConfig, Panel, one_step_resid
from fixtures import HOLIDAYS, card_daily_small

META = ["prediction_origin", "forecast_horizon", "target_date", "feature_available_at", "trained_until", "model", "model_version"]


def test_request_metadata():
    Y = card_daily_small()
    fc = rolling_forecast(Y, STLModel(HOLIDAYS), origins(len(Y))[:2])
    assert set(META) <= set(fc.columns)
    assert (fc["target_date"] - fc["prediction_origin"] == pd.to_timedelta(fc["forecast_horizon"], "D")).all()
    assert (fc["feature_available_at"] <= fc["prediction_origin"]).all()
    assert (fc["trained_until"] <= fc["prediction_origin"]).all()
    rq = requests(fc)
    assert len(rq) == len(fc) and rq[0].forecast_horizon == 1


def test_seasonal_naive_is_last_week():
    Y = card_daily_small()
    fc = attach_actual(rolling_forecast(Y, SeasonalNaive(HOLIDAYS), [60]), Y)
    for r in fc.itertuples():
        j = list(Y.columns).index(r.cohort)
        assert np.isclose(r.yhat_log, np.log1p(Y.iloc[Y.index.get_loc(r.target_date) - 7, j]))


def test_0b8_equals_eda6_expected_value():
    Y = card_daily_small()
    r = ModelResid(Y, SameWeekdayMean(HOLIDAYS, n_weeks=8), origins(len(Y), full_horizon=None))
    dow, ih = Y.index.dayofweek.to_numpy(), Y.index.isin(HOLIDAYS)
    E0 = np.column_stack([one_step_resid(Y[c].to_numpy(), dow, ih, AlarmConfig()) for c in Y.columns])
    np.testing.assert_allclose(np.nan_to_num(r.E[56:], nan=9), np.nan_to_num(E0[56:], nan=9))


def test_model_panel_reproduces_eda6_panel():
    Y = card_daily_small()
    p0, p1 = Panel(Y, HOLIDAYS), ModelPanel(Y, HOLIDAYS)
    for k in ("absolute", "local"):
        np.testing.assert_array_equal(np.nan_to_num(p0.res[k]["alarm"], nan=-1), np.nan_to_num(p1.res[k]["alarm"], nan=-1))
    col = Y.columns[3]
    y = Y[col].to_numpy().copy()
    y[70:84] *= 0.8
    a, b = p0.score_one(col, y), p1.score_one(col, y)
    for k in a:
        np.testing.assert_array_equal(np.nan_to_num(a[k], nan=-1), np.nan_to_num(b[k], nan=-1))


def test_model_panel_matches_panel_with_missing_days():
    """미공급(NaN) 칸이 있어도 EDA-6 Panel과 같은 보류·경보 (0으로 채우지 않음)."""
    Y = card_daily_small()
    Y.iloc[60:63, 0] = np.nan
    Y.iloc[90, 4] = np.nan
    p0, p1 = Panel(Y, HOLIDAYS), ModelPanel(Y, HOLIDAYS)
    for k in ("absolute", "local"):
        np.testing.assert_array_equal(np.nan_to_num(p0.res[k]["alarm"], nan=-1), np.nan_to_num(p1.res[k]["alarm"], nan=-1))
        assert (p0.res[k]["reason"] == p1.res[k]["reason"]).all()
    for j in range(len(Y.columns)):
        np.testing.assert_array_equal(np.nan_to_num(p0.alarm_any(j, "either"), nan=-1), np.nan_to_num(p1.alarm_any(j, "either"), nan=-1))
    col = Y.columns[0]
    y = Y[col].to_numpy().copy()
    y[70:84] *= 0.8
    a, b = p0.score_one(col, y), p1.score_one(col, y)
    for k in a:
        np.testing.assert_array_equal(np.nan_to_num(a[k], nan=-1), np.nan_to_num(b[k], nan=-1))


def test_model_resid_injection_matches_full_rerun():
    # STL 은 코호트마다 따로 계산하므로(cohort_independent) 두 코호트만 써도 같은 것을 확인한다
    # (12개 코호트 전체로 돌리면 35초. 바꾼 코호트가 첫 열이 아니어야 열 찾기까지 확인됨)
    Y = card_daily_small().iloc[:, :2]
    o = origins(len(Y), full_horizon=None)
    m = STLModel(HOLIDAYS)
    r = ModelResid(Y, m, o)
    col = Y.columns[1]
    y = Y[col].to_numpy().copy()
    y[80:94] *= 0.85
    fast = r(col, y)
    Y2 = Y.copy()
    Y2[col] = y
    full = ModelResid(Y2, m, o).E[:, list(Y.columns).index(col)]
    np.testing.assert_allclose(np.nan_to_num(fast, nan=9), np.nan_to_num(full, nan=9))


def test_paired_injection_runs():
    Y = card_daily_small()
    p = ModelPanel(Y, HOLIDAYS, resid=ModelResid(Y, SameWeekdayMean(HOLIDAYS), origins(len(Y), full_horizon=None)))
    pairs = [(c, 85) for c in Y.columns]
    ex = run_pairs(p, pairs, {"step30": step(0.30)})
    tr = transitions(ex)
    assert tr["평가 가능"] + tr["시작 전 경보 중(제외)"] + tr["시작 전 보류(제외)"] == len(pairs)
    assert tr["새 탐지"] >= 1


def test_intervals_use_only_past_errors():
    Y = card_daily_small()
    fc = add_intervals(attach_actual(rolling_forecast(Y, SameWeekdayMean(HOLIDAYS), origins(len(Y))), Y), min_past=7)
    first = fc[fc["prediction_origin"] == fc["prediction_origin"].min()]
    assert (first["n_past_err"] == 0).all() and first["lo"].isna().all()
    later = fc[fc["prediction_origin"] == fc["prediction_origin"].max()]
    assert later["lo"].notna().all()
    m = forecast_metrics(fc, ["forecast_horizon"])
    assert {"mae", "rel_mae", "coverage"} <= set(m.columns)


def test_validate_panel_rejects_bad_dates():
    Y = card_daily_small()
    with pytest.raises(ValueError):
        validate_panel(Y.iloc[::-1])
    with pytest.raises(ValueError):
        validate_panel(pd.concat([Y.iloc[:5], Y.iloc[4:10]]))
    with pytest.raises(ValueError):
        validate_panel(Y.drop(Y.index[10]))


def test_pelt_finds_step_and_prefix_records_discovery():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.05, 150)
    r[90:] -= 0.3
    assert any(abs(c - 90) <= 3 for c in pelt(r, beta=4))
    found, est = prefix_discovery(r, 90, 110, beta=4)
    assert found is not None and found >= 90 and abs(est - 90) <= 3
