"""두 경로 경보: absolute(실제 감소) / local(도시 공통 변화를 뺀 이탈)."""

import numpy as np

from bigcontest.signals.alarm import Panel, direction, records
from fixtures import HOLIDAYS, card_daily_small

S, E = 90, 97  # 변화 구간 [S, E)
T = E - 1  # 창의 마지막 관측일


def _alarm(p, col, path, t=T):
    return p.res[path]["alarm"][t, p.cols.index(col)]


def test_city_wide_drop_only_absolute():
    Y = card_daily_small()
    city_cols = [c for c in Y.columns if c[0] == "A"]
    Y.iloc[S:E, [Y.columns.get_loc(c) for c in city_cols]] *= 0.6
    p = Panel(Y, HOLIDAYS)
    col = city_cols[0]
    assert _alarm(p, col, "absolute") == 1
    assert _alarm(p, col, "local") == 0
    # 도시 공통 변화는 버리지 않고 city_shock 신호로 남는다
    assert p.city_signal["A"]["city_shock_z"].iloc[S:E].lt(-2.5).sum() >= 3


def test_target_only_drop_both_paths():
    Y = card_daily_small()
    col = Y.columns[0]
    Y.iloc[S:E, 0] *= 0.6
    p = Panel(Y, HOLIDAYS)
    assert _alarm(p, col, "absolute") == 1
    assert _alarm(p, col, "local") == 1
    rec = records(p)
    row = rec[(rec["city"] == col[0]) & (rec["cohort"] == col[1])].iloc[0]
    assert row["path"] == "both" and row["direction"] == "감소"
    assert row["alarm_date"] == Y.index[S + 2]  # 3일째 낮은 날이 마지막 관측일
    assert row["window_end"] == row["alarm_date"]


def test_mad_zero_is_withheld():
    Y = card_daily_small()
    col = Y.columns[0]
    Y[col] = 500.0  # 흔들림 없는 시계열
    p = Panel(Y, HOLIDAYS)
    st = p.state(col, "absolute")
    assert (st["reason"].iloc[p.cfg.min_train:] == "MAD=0").all()
    assert st["z"].iloc[p.cfg.min_train:].isna().all()
    assert st["alarm"].iloc[p.cfg.min_train:].isna().all()  # 0(경보 없음)이 아니라 보류


def test_less_increase_than_city_is_not_decrease():
    Y = card_daily_small()
    city_cols = [c for c in Y.columns if c[0] == "A"]
    Y.iloc[S:E, [Y.columns.get_loc(c) for c in city_cols]] *= 1.5
    col = city_cols[0]
    Y.iloc[S:E, Y.columns.get_loc(col)] = Y.iloc[S:E, Y.columns.get_loc(col)] / 1.5 * 1.15
    p = Panel(Y, HOLIDAYS)
    assert _alarm(p, col, "local") == 1
    assert _alarm(p, col, "absolute") == 0
    rec = records(p)
    row = rec[(rec["city"] == "A") & (rec["cohort"] == col[1])].iloc[0]
    assert row["path"] == "local"
    assert row["direction"] == "증가"  # 도시보다 덜 늘었을 뿐 감소가 아님


def test_direction_labels():
    assert direction(-0.2) == "감소"
    assert direction(0.2) == "증가"
    assert direction(0.01) == "변화 없음"
    assert direction(np.nan) is None


def test_rain_explained_alarm_is_not_rejected():
    import pandas as pd

    Y = card_daily_small()
    col = Y.columns[0]
    rain = pd.DataFrame({"A": 0.0, "B": 0.0}, index=Y.index)
    rainy = list(range(30, S, 6)) + list(range(S, E))
    rain.iloc[rainy, 0] = 10.0
    Y.iloc[rainy, 0] *= 0.75  # 대상 코호트는 비 오는 날마다 25% 줄어듦
    p = Panel(Y, HOLIDAYS)
    rec = records(p, rain=rain)
    row = rec[(rec["city"] == "A") & (rec["cohort"] == col[1]) & (rec["alarm_date"] >= Y.index[S])].iloc[0]
    assert row["rain_explained"]
    assert row["weather_explained_local"] > 0.1
    assert row["review"] == "통과"  # 강수는 자동 기각하지 않음


def test_synthetic_noise_keeps_autocorrelation():
    from bigcontest.signals.alarm import _ar1

    x = _ar1(np.random.default_rng(0), 5000, 0.1, 0.35)
    assert abs(np.corrcoef(x[:-1], x[1:])[0, 1] - 0.35) < 0.05
    assert abs(x.std() - 0.1) < 0.01
