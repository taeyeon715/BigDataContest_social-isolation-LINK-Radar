"""모델 2: LightGBM (모든 코호트를 한 모델로, 기준일마다 다시 학습).

예측 대상: log1p(y[목표일]) − level, level = 기준일까지 28일 log1p 평균.

특징 (모두 기준일까지 관측된 값 또는 미리 알려진 달력)
- h, 목표일 요일·공휴일 여부·전날/다음날 공휴일·가장 가까운 공휴일까지 일수(±7로 자름)
- 같은 요일 지난 값 4개 (목표일 − 7·14·21·28일; h ≤ 7이라 모두 기준일 이전) − level
- 기준일 값 − level, 최근 7일 평균 − level
- 도시 공통요인: 기준일까지 같은 도시 코호트들의 (최근 7일 평균 − level) 중앙값
- 코호트 (도시, 연령, 성) 번호

넣지 않는 것: 날씨(예보 이력이 없어 미래 실측으로 대신하지 않음), 같은 미래 날짜의 다른 코호트 결제.
"""

import warnings

import numpy as np
import pandas as pd

from bigcontest.models.baseline import HORIZONS, Forecaster, city_of

FEATURES = ["h", "dow", "hol", "hol_prev", "hol_next", "hol_dist", "lag7", "lag14", "lag21", "lag28",
            "last", "mean7", "city_factor7", "city_id", "age_id", "sex_id"]
LEVEL_DAYS = 28

DEFAULT_PARAMS = dict(objective="l1", n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50,
                      subsample=0.8, subsample_freq=1, colsample_bytree=0.8, random_state=0, n_jobs=1, verbose=-1)


def _code(values) -> dict:
    return {v: i for i, v in enumerate(sorted(set(values), key=str))}


class LGBMModel(Forecaster):
    name, version = "2", "1"
    cohort_independent = False

    def __init__(self, holidays=(), params: dict | None = None, horizons=HORIZONS):
        super().__init__(holidays)
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.horizons = tuple(horizons)

    # ------------------------------------------------------------ 특징
    def _meta(self, cols):
        cities = [city_of(c) for c in cols]
        ages = [c[1] if isinstance(c, tuple) and len(c) > 1 else "" for c in cols]
        sexes = [c[2] if isinstance(c, tuple) and len(c) > 2 else "" for c in cols]
        cc, ac, sc = _code(cities), _code(ages), _code(sexes)
        return np.array(cities), np.array([cc[c] for c in cities]), np.array([ac[a] for a in ages]), np.array([sc[s] for s in sexes])

    def _calendar(self, dates: pd.DatetimeIndex) -> dict:
        hol = self.is_hol(dates)
        if len(self.holidays):
            d = (dates.values[:, None] - self.holidays.values[None, :]).astype("timedelta64[D]").astype(int)
            near = d[np.arange(len(dates)), np.abs(d).argmin(1)]
            dist = np.clip(-near, -7, 7).astype(float)  # 양수 = 공휴일이 앞으로 남음
        else:
            dist = np.full(len(dates), 7.0)
        return {"dow": dates.dayofweek.to_numpy(), "hol": hol.astype(int),
                "hol_prev": self.is_hol(dates - pd.Timedelta(days=1)).astype(int),
                "hol_next": self.is_hol(dates + pd.Timedelta(days=1)).astype(int), "hol_dist": dist}

    def _rows(self, L: np.ndarray, dates: pd.DatetimeIndex, cols, anchors, horizons, with_target: bool):
        """anchors(기준일 위치) × horizons × 코호트 행. with_target이면 목표일이 hist 안에 있는 행만."""
        n, k = L.shape
        cities, city_id, age_id, sex_id = self._meta(cols)
        cs = np.vstack([np.full((1, k), 0.0), np.nancumsum(L, 0)])
        cn = np.vstack([np.zeros((1, k)), np.cumsum(~np.isnan(L), 0)])

        def mean_back(a, m):
            lo = max(0, a - m + 1)
            c = cn[a + 1] - cn[lo]
            with np.errstate(all="ignore"):
                return np.where(c > 0, (cs[a + 1] - cs[lo]) / c, np.nan)

        all_dates = dates[0] + pd.to_timedelta(np.arange(n + max(horizons)), "D")
        cal = self._calendar(all_dates)
        cols_out = {f: [] for f in FEATURES + ["_col", "_level", "_h", "_y"]}
        for a in anchors:
            level = mean_back(a, LEVEL_DAYS)
            m7 = mean_back(a, 7) - level
            cf = np.empty(k)
            for c in np.unique(cities):
                sel = cities == c
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    cf[sel] = np.nanmedian(m7[sel])
            for h in horizons:
                t = a + h
                if with_target and t >= n:
                    continue
                for key in ("dow", "hol", "hol_prev", "hol_next", "hol_dist"):
                    cols_out[key].append(np.full(k, cal[key][t]))
                cols_out["h"].append(np.full(k, h))
                for lag in (7, 14, 21, 28):
                    i = t - lag
                    cols_out[f"lag{lag}"].append((L[i] - level) if i >= 0 else np.full(k, np.nan))
                for key, v in (("last", L[a] - level), ("mean7", m7), ("city_factor7", cf), ("city_id", city_id),
                               ("age_id", age_id), ("sex_id", sex_id), ("_col", np.arange(k)), ("_level", level),
                               ("_h", np.full(k, h)), ("_y", (L[t] - level) if t < n else np.full(k, np.nan))):
                    cols_out[key].append(v)
        if not cols_out["h"]:
            return pd.DataFrame(columns=FEATURES + ["_col", "_level", "_h", "_y"])
        return pd.DataFrame({key: np.concatenate(v) for key, v in cols_out.items()})

    # ------------------------------------------------------------ 학습·예측
    def fit(self, hist: pd.DataFrame):
        import lightgbm as lgb

        L = np.log(hist.to_numpy(float) + 1)
        dates = pd.DatetimeIndex(hist.index)
        anchors = range(LEVEL_DAYS - 1, len(L) - 1)
        X = self._rows(L, dates, list(hist.columns), anchors, self.horizons, with_target=True)
        X = X[~X["_y"].isna() & ~X["_level"].isna()]
        model = lgb.LGBMRegressor(**self.params)
        model.fit(X[FEATURES], X["_y"], categorical_feature=["city_id", "age_id", "sex_id"])
        return {"model": model, "trained_until": dates[-1], "n_rows": len(X)}

    def predict(self, state, hist, horizons=HORIZONS):
        L = np.log(hist.to_numpy(float) + 1)
        dates = pd.DatetimeIndex(hist.index)
        X = self._rows(L, dates, list(hist.columns), [len(L) - 1], horizons, with_target=False)
        pred = state["model"].predict(X[FEATURES]) + X["_level"].to_numpy()
        out = pd.DataFrame({"h": X["_h"].to_numpy(), "j": X["_col"].to_numpy(), "v": pred})
        M = out.pivot(index="h", columns="j", values="v").reindex(index=list(horizons), columns=range(len(hist.columns)))
        M.columns = hist.columns
        return M
