"""기대 활동 예측의 공통 틀과 기준선 (0a, 0b).

입력 Y: 날짜 × 코호트 결제건수 표 (열 = (도시, 연령, 성) 같은 튜플, 첫 칸이 도시). 누락은 NaN.
모델은 log(y+1)을 예측한다.

시간 규칙
- 모델은 prediction_origin(예측 기준일)까지 자른 표(hist)만 받는다. 기준일 뒤 결제·날씨는 볼 수 없다.
- 달력(공휴일 목록)은 미리 알려진 정보라 미래 날짜도 쓸 수 있다.
- 결과 행마다 prediction_origin, forecast_horizon, target_date, feature_available_at, trained_until을 남긴다.
"""

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

HORIZONS = tuple(range(1, 8))


@dataclass(frozen=True)
class Request:
    """예측 요청 한 건 (코호트 하나 × 목표일 하나)."""

    cohort: tuple
    prediction_origin: pd.Timestamp
    forecast_horizon: int
    target_date: pd.Timestamp
    feature_available_at: pd.Timestamp  # 쓴 특징 중 가장 늦은 관측일 (= 기준일)


def validate_panel(Y: pd.DataFrame) -> pd.DataFrame:
    """모델 입력 검사: 날짜 정렬·중복 없음·하루 간격 연속. 누락은 NaN으로 두고 0으로 채우지 않는다."""
    idx = pd.DatetimeIndex(Y.index)
    if not idx.is_monotonic_increasing:
        raise ValueError("날짜가 정렬되어 있지 않음")
    if idx.has_duplicates:
        raise ValueError("같은 날짜가 두 번 이상 있음")
    if len(idx) > 1 and (np.diff(idx.values).astype("timedelta64[D]").astype(int) != 1).any():
        raise ValueError("날짜가 하루 간격으로 이어지지 않음 (빠진 날은 NaN 행으로 넣어야 함)")
    if len(set(Y.columns)) != len(Y.columns):
        raise ValueError("코호트 열이 중복됨")
    return Y


def city_of(col) -> str:
    return col[0] if isinstance(col, tuple) else col


class Forecaster:
    """모델 공통 틀. fit(hist) → state, predict(state, hist) → 행 = h(1..H), 열 = 코호트, 값 = log1p 예측."""

    name = "base"
    version = "1"
    cohort_independent = True  # True면 코호트마다 따로 계산 (주입 실험에서 한 코호트만 다시 계산)

    def __init__(self, holidays=()):
        self.holidays = pd.DatetimeIndex(sorted(pd.Timestamp(h) for h in holidays))

    def fit(self, hist: pd.DataFrame):
        return None

    def predict(self, state, hist: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
        raise NotImplementedError

    def forecast(self, hist: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
        return self.predict(self.fit(hist), hist, horizons)

    # 달력 도우미 (미래 날짜도 쓸 수 있음)
    def is_hol(self, dates) -> np.ndarray:
        return pd.DatetimeIndex(dates).isin(self.holidays)


def _log(hist: pd.DataFrame) -> np.ndarray:
    return np.log(hist.to_numpy(float) + 1)


class SeasonalNaive(Forecaster):
    """0a: 전 주 같은 요일 값. h ≤ 7이면 목표일 − 7일은 항상 기준일 이전."""

    name, version = "0a", "1"

    def predict(self, state, hist, horizons=HORIZONS):
        L = _log(hist)
        n = len(L)
        rows = {}
        for h in horizons:
            k = int(np.ceil(h / 7)) * 7  # 기준일 이전의 가장 최근 같은 요일
            i = n - 1 + h - k
            rows[h] = L[i] if i >= 0 else np.full(L.shape[1], np.nan)
        return pd.DataFrame(rows, index=hist.columns).T


class SameWeekdayMean(Forecaster):
    """0b: 기준일 이전 같은 요일 최근 n주 평균 (공휴일은 빈칸 처리, 빈칸 아닌 값이 min_obs개 이상일 때만).

    n=8이면 EDA-6 경보 규칙의 기대값(one_step_resid)과 같다.
    """

    version = "1"

    def __init__(self, holidays=(), n_weeks: int = 4, min_obs: int = 2):
        super().__init__(holidays)
        self.n_weeks, self.min_obs = n_weeks, min_obs
        self.name = "0b" if n_weeks == 4 else f"0b{n_weeks}"

    def predict(self, state, hist, horizons=HORIZONS):
        L = _log(hist)
        L[self.is_hol(hist.index)] = np.nan
        n = len(L)
        rows = {}
        for h in horizons:
            k0 = int(np.ceil(h / 7)) * 7
            idx = [n - 1 + h - k0 - 7 * m for m in range(self.n_weeks)]
            idx = [i for i in idx if i >= 0]
            V = L[idx] if idx else np.full((1, L.shape[1]), np.nan)
            cnt = (~np.isnan(V)).sum(0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                m = np.nanmean(V, 0) if len(idx) else V[0]
            rows[h] = np.where(cnt >= self.min_obs, m, np.nan)
        return pd.DataFrame(rows, index=hist.columns).T
