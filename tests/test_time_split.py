"""rolling-origin 분할: 학습 구간은 항상 평가 구간보다 앞, 모든 코호트가 같은 분할."""

import pandas as pd

from bigcontest.models import SameWeekdayMean
from bigcontest.models.evaluate import origins, rolling_forecast, split_windows
from fixtures import HOLIDAYS, card_daily_small


def test_train_window_before_eval_window():
    Y = card_daily_small()
    o = origins(len(Y))
    assert o[0] == 55  # 최소 8주(56일) 학습
    assert all(b - a == 7 for a, b in zip(o, o[1:]))  # 1주씩 이동
    w = split_windows(pd.DatetimeIndex(Y.index), o)
    assert (w["train_to"] < w["eval_from"]).all()
    assert (w["train_to"] == w["origin"]).all()
    assert (w["eval_to"] <= Y.index[-1]).all()


def test_full_horizon_origins_fit_in_data():
    Y = card_daily_small()
    o = origins(len(Y), full_horizon=7)
    assert o[-1] + 7 <= len(Y) - 1
    assert origins(len(Y), full_horizon=None)[-1] + 1 <= len(Y) - 1


def test_all_cohorts_share_split():
    Y = card_daily_small()
    fc = rolling_forecast(Y, SameWeekdayMean(HOLIDAYS), origins(len(Y)))
    keys = fc.groupby("cohort").apply(lambda g: tuple(sorted(zip(g["prediction_origin"], g["target_date"]))), include_groups=False)
    assert keys.nunique() == 1
    assert (fc["target_date"] > fc["prediction_origin"]).all()
