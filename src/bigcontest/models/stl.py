"""모델 1: STLForecast (주기 7 계절 제거 + 남은 부분은 ARIMA(0,1,1)) + 공휴일 처리.

- 코호트마다 따로 학습한다 (다른 코호트 값을 쓰지 않음).
- 학습 때 공휴일 값은 같은 요일 앞뒤 주 평균으로 바꾼다 (기준일 뒤 값은 쓰지 않으므로, 다음 주가 기준일 뒤면 앞 주 값만).
- 예측일이 공휴일이면 과거 공휴일의 평균 이탈(실제 − 대체값)을 더한다. 과거 공휴일이 없으면 0.
- 누락(NaN)은 학습 때 같은 방식으로 메운다.
"""

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.forecasting.stl import STLForecast

from bigcontest.models.baseline import HORIZONS, Forecaster


def _fill_same_weekday(x: np.ndarray, bad: np.ndarray) -> np.ndarray:
    """bad인 자리를 같은 요일 앞뒤 주(범위 안·bad 아님) 평균으로. 둘 다 없으면 앞뒤로 더 찾는다."""
    out = x.copy()
    n = len(x)
    for i in np.where(bad)[0]:
        vals = []
        for k in range(1, 5):
            for j in (i - 7 * k, i + 7 * k):
                if 0 <= j < n and not bad[j] and not np.isnan(x[j]):
                    vals.append(x[j])
            if vals:
                break
        out[i] = np.mean(vals) if vals else np.nan
    return out


class STLModel(Forecaster):
    name, version = "1", "1"

    def __init__(self, holidays=(), order=(0, 1, 1), robust: bool = True):
        super().__init__(holidays)
        self.order, self.robust = order, robust

    def _one(self, ly: np.ndarray, dates: pd.DatetimeIndex, horizons) -> np.ndarray:
        hol = self.is_hol(dates)
        bad = hol | np.isnan(ly)
        filled = _fill_same_weekday(ly, bad)
        if np.isnan(filled).any():
            filled = pd.Series(filled).interpolate(limit_direction="both").to_numpy()
        hol_eff = ly[hol & ~np.isnan(ly)] - filled[hol & ~np.isnan(ly)]
        hol_eff = float(hol_eff.mean()) if len(hol_eff) else 0.0
        s = pd.Series(filled, index=pd.DatetimeIndex(dates, freq="D"))
        H = max(horizons)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = STLForecast(s, ARIMA, model_kwargs={"order": self.order, "trend": "n"}, period=7, robust=self.robust).fit()
            fc = np.asarray(res.forecast(H))
        tgt = dates[-1] + pd.to_timedelta(np.arange(1, H + 1), "D")
        fc = fc + np.where(self.is_hol(tgt), hol_eff, 0.0)
        return fc[[h - 1 for h in horizons]]

    def predict(self, state, hist, horizons=HORIZONS):
        L = np.log(hist.to_numpy(float) + 1)
        dates = pd.DatetimeIndex(hist.index)
        out = {col: self._one(L[:, j], dates, horizons) for j, col in enumerate(hist.columns)}
        return pd.DataFrame(out, index=list(horizons))
