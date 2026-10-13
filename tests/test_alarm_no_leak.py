"""미래 데이터 차단: t 이후 자료를 바꿔도 t까지의 점수·경보는 그대로여야 한다."""

import numpy as np

from bigcontest.signals.alarm import Panel
from fixtures import HOLIDAYS, card_daily_small

T = 80


def _same_until(a, b, t):
    np.testing.assert_array_equal(np.isnan(a[: t + 1]), np.isnan(b[: t + 1]))
    np.testing.assert_allclose(np.nan_to_num(a[: t + 1]), np.nan_to_num(b[: t + 1]))


def test_future_change_does_not_change_past_alarm():
    Y = card_daily_small()
    p0 = Panel(Y, HOLIDAYS)
    Y2 = Y.copy()
    rng = np.random.default_rng(1)
    Y2.iloc[T + 1:] = Y2.iloc[T + 1:] * rng.uniform(0.1, 3.0, Y2.iloc[T + 1:].shape)
    p1 = Panel(Y2, HOLIDAYS)
    for path in ("absolute", "local"):
        for key in ("z", "alarm", "low"):
            _same_until(p0.res[path][key], p1.res[path][key], T)
        assert (p0.res[path]["reason"][: T + 1] == p1.res[path]["reason"][: T + 1]).all()
    _same_until(p0.S, p1.S, T)
    for c in p0.city_signal:
        _same_until(p0.city_signal[c]["city_shock_z"].to_numpy(), p1.city_signal[c]["city_shock_z"].to_numpy(), T)


def test_future_injection_does_not_change_past():
    Y = card_daily_small()
    p0 = Panel(Y, HOLIDAYS)
    col = Y.columns[0]
    y = Y[col].to_numpy().copy()
    y[T + 1:] *= 0.5
    p1 = p0.replace_column(col, y)
    for path in ("absolute", "local"):
        _same_until(p0.res[path]["alarm"], p1.res[path]["alarm"], T)


def test_no_score_before_min_train():
    p = Panel(card_daily_small(), HOLIDAYS)
    assert np.isnan(p.res["local"]["z"][: p.cfg.min_train]).all()
    assert (p.res["local"]["reason"][: p.cfg.min_train] == "학습 기간 부족").all()


def test_score_one_matches_full_recompute():
    Y = card_daily_small()
    p0 = Panel(Y, HOLIDAYS)
    col = Y.columns[2]
    y = Y[col].to_numpy().copy()
    y[60:74] *= 0.8
    fast = p0.score_one(col, y)
    full = p0.replace_column(col, y)
    j = p0.cols.index(col)
    for path in ("absolute", "local", "either"):
        np.testing.assert_array_equal(np.nan_to_num(fast[path], nan=-1), np.nan_to_num(full.alarm_any(j, path), nan=-1))
