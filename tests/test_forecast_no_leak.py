"""예측 기준일 이후 자료(미래 결제, 모든 코호트)를 바꿔도 이미 만든 예측은 그대로.

날씨는 예측 모델의 입력이 아니다 (예보 이력이 없어 미래 실측으로 대신하지 않음).
"""

import numpy as np
import pytest

from bigcontest.models import LGBMModel, SameWeekdayMean, SeasonalNaive, STLModel
from bigcontest.models.evaluate import ModelResid, origins, rolling_forecast
from bigcontest.models.lgbm import FEATURES
from fixtures import HOLIDAYS, card_daily_small

SMALL = {"n_estimators": 30}
MODELS = [SeasonalNaive(HOLIDAYS), SameWeekdayMean(HOLIDAYS), STLModel(HOLIDAYS), LGBMModel(HOLIDAYS, params=SMALL)]


def _future_changed(Y, o):
    Y2 = Y.copy()
    rng = np.random.default_rng(3)
    Y2.iloc[o + 1:] = Y2.iloc[o + 1:] * rng.uniform(0.2, 3.0, Y2.iloc[o + 1:].shape)
    return Y2


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.name)
def test_future_values_do_not_change_forecast(model):
    Y = card_daily_small()
    a = rolling_forecast(Y, model, [69])
    b = rolling_forecast(_future_changed(Y, 69), model, [69])
    np.testing.assert_allclose(a["yhat_log"], b["yhat_log"])


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.name)
def test_future_values_do_not_change_past_residual(model):
    """탐지용 잔차: t까지의 잔차는 t 뒤 자료가 바뀌어도 그대로."""
    Y = card_daily_small()
    o = origins(len(Y), full_horizon=None)
    T = 90
    e0 = ModelResid(Y, model, o).E
    e1 = ModelResid(_future_changed(Y, T), model, o).E
    np.testing.assert_allclose(np.nan_to_num(e0[: T + 1], nan=9), np.nan_to_num(e1[: T + 1], nan=9))


def test_no_weather_or_future_cohort_feature():
    banned = ("rain", "tavg", "tmax", "tmin", "weather", "snow", "humid", "wind", "future")
    assert not [f for f in FEATURES if any(b in f for b in banned)]


def test_lgbm_trains_only_on_targets_up_to_origin():
    Y = card_daily_small()
    m = LGBMModel(HOLIDAYS, params=SMALL)
    st = m.fit(Y.iloc[:70])
    assert st["trained_until"] == Y.index[69]
    st2 = m.fit(_future_changed(Y, 69).iloc[:70])
    np.testing.assert_allclose(m.predict(st, Y.iloc[:70]).to_numpy(), m.predict(st2, Y.iloc[:70]).to_numpy())
