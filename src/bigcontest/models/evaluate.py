"""rolling-origin 예측 평가와, 모델 잔차로 EDA-6 경보 규칙을 다시 돌리는 도구.

예측
- rolling_forecast: 기준일(origin)마다 그날까지 자른 표로 학습·예측. 행마다 요청 메타데이터를 남긴다.
- add_intervals: 같은 모델·코호트의 과거 오차(목표일이 기준일 이전인 것만)로 만든 경험적 예측구간
- forecast_metrics: MAE, 상대 MAE, 구간 포함률

탐지 (signals/alarm.py는 고치지 않고 감싼다)
- residual_matrix: 날짜 t의 잔차 = log(y_t+1) − (t보다 앞선 가장 최근 기준일의 예측)
- ModelPanel: EDA-6 Panel과 같은 점수·경보 계산에 기대값만 모델 예측으로 바꾼 것
- 짝지은 주입 실험·배경 경보율 도구는 EDA-6 노트북과 같은 정의
"""

import warnings

import numpy as np
import pandas as pd

from bigcontest.models.baseline import HORIZONS, Forecaster, validate_panel
from bigcontest.signals.alarm import (AlarmConfig, Panel, _check_column, _nanmedian, check_panel_input, combine_any, one_step_resid,
                                      robust_z, window_alarm)

# ------------------------------------------------------------ 자료


def card_cohort_panel(keys=("city", "age_g", "SEX_CCD"), start="2025-07-01", end="2025-12-31") -> tuple[pd.DataFrame, int]:
    """카드 데이터2 코호트 일별 결제건수 (날짜 × 코호트). 관측 행이 없는 칸은 0이 아니라 NaN. 반환: (표, NaN 칸 수)."""
    from bigcontest.card import load_card2

    c2 = load_card2()
    s = c2.groupby(["date", *keys])["USE_CNT"].sum()
    W = s.unstack(list(keys)).astype(float)
    W = W.reindex(pd.date_range(start, end, name="date"))
    W = W[sorted(W.columns)]
    validate_panel(W)
    return W, int(W.isna().sum().sum())


# ------------------------------------------------------------ 분할


def origins(n: int, min_train: int = 56, step: int = 7, full_horizon: int | None = 7) -> list[int]:
    """rolling-origin 기준일 위치. 첫 기준일 = min_train − 1 (학습 min_train일). full_horizon이면 o + full_horizon < n 인 것만."""
    last = n - 1 - (full_horizon or 1)
    return list(range(min_train - 1, last + 1, step))


def split_windows(dates: pd.DatetimeIndex, origin_pos: list[int], horizons=HORIZONS) -> pd.DataFrame:
    """기준일마다 학습 구간과 평가 구간 (테스트용)."""
    rows = []
    for o in origin_pos:
        tg = [o + h for h in horizons if o + h < len(dates)]
        rows.append({"origin": dates[o], "train_from": dates[0], "train_to": dates[o],
                     "eval_from": dates[tg[0]] if tg else pd.NaT, "eval_to": dates[tg[-1]] if tg else pd.NaT})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ 예측


def rolling_forecast(Y: pd.DataFrame, model: Forecaster, origin_pos: list[int], horizons=HORIZONS,
                     keep_states: bool = False):
    """기준일마다 Y.iloc[:o+1]만 모델에 넘긴다. 반환: 긴 표 (keep_states면 (표, {o: state}))."""
    validate_panel(Y)
    dates = pd.DatetimeIndex(Y.index)
    rows, states = [], {}
    for o in origin_pos:
        hist = Y.iloc[: o + 1]
        st = model.fit(hist)
        P = model.predict(st, hist, horizons)
        if keep_states and st is not None:
            states[o] = st
        org = dates[o]
        for h in horizons:
            for col in Y.columns:
                rows.append((col, org, h, org + pd.Timedelta(days=h), org, org, model.name, model.version, float(P.loc[h, col])))
    out = pd.DataFrame(rows, columns=["cohort", "prediction_origin", "forecast_horizon", "target_date", "feature_available_at",
                                      "trained_until", "model", "model_version", "yhat_log"])
    return (out, states) if keep_states else out


def requests(fc: pd.DataFrame) -> list:
    """긴 표 → Request 목록 (요청 메타데이터 확인용)."""
    from bigcontest.models.baseline import Request

    return [Request(r.cohort, r.prediction_origin, int(r.forecast_horizon), r.target_date, r.feature_available_at)
            for r in fc.itertuples(index=False)]


def attach_actual(fc: pd.DataFrame, Y: pd.DataFrame) -> pd.DataFrame:
    pos = {d: i for i, d in enumerate(pd.DatetimeIndex(Y.index))}
    colpos = {c: j for j, c in enumerate(Y.columns)}
    A = Y.to_numpy(float)
    ti = fc["target_date"].map(pos)
    ok = ti.notna()
    y = np.full(len(fc), np.nan)
    y[ok.to_numpy()] = A[ti[ok].astype(int).to_numpy(), fc.loc[ok, "cohort"].map(colpos).to_numpy()]
    out = fc.copy()
    out["y"] = y
    out["yhat"] = np.expm1(out["yhat_log"])
    out["err_log"] = np.log1p(out["y"]) - out["yhat_log"]
    return out


def add_intervals(fc: pd.DataFrame, nominal: float = 0.8, min_past: int = 14) -> pd.DataFrame:
    """기준일 o의 구간 폭 = 같은 모델·코호트에서 목표일 ≤ o인 과거 예측의 |log 오차| nominal 분위수 (h 묶음)."""
    out = fc.copy()
    out["lo"], out["hi"], out["n_past_err"] = np.nan, np.nan, 0
    for (_, _), g in out.groupby(["model", "cohort"], sort=False):
        e = g[["target_date", "err_log"]].dropna().sort_values("target_date")
        td, ae = e["target_date"].to_numpy(), np.abs(e["err_log"].to_numpy())
        for o, gi in g.groupby("prediction_origin"):
            past = ae[td <= np.datetime64(o)]
            out.loc[gi.index, "n_past_err"] = len(past)
            if len(past) >= min_past:
                q = np.quantile(past, nominal)
                out.loc[gi.index, "lo"] = np.expm1(gi["yhat_log"] - q)
                out.loc[gi.index, "hi"] = np.expm1(gi["yhat_log"] + q)
    out["covered"] = np.where(out["lo"].notna() & out["y"].notna(), (out["y"] >= out["lo"]) & (out["y"] <= out["hi"]), np.nan)
    return out


def forecast_metrics(fc: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """코호트마다 MAE·상대 MAE(MAE ÷ 평균 실제값)를 낸 뒤 by로 평균. 포함률은 구간이 있는 행만."""
    d = fc.dropna(subset=["y", "yhat"]).copy()
    d["ae"] = (d["y"] - d["yhat"]).abs()
    if "covered" not in d:
        d["covered"] = np.nan
    keys = list(dict.fromkeys(by + ["cohort"]))
    per = d.groupby(keys).agg(mae=("ae", "mean"), mean_y=("y", "mean"), n=("ae", "size"),
                              coverage=("covered", "mean"), n_interval=("covered", "count"))
    per["rel_mae"] = per["mae"] / per["mean_y"]
    return per.groupby(by).agg(mae=("mae", "mean"), rel_mae=("rel_mae", "mean"), coverage=("coverage", "mean"),
                               n_target=("n", "sum"), n_cohort=("n", "size"), n_interval=("n_interval", "sum"))


# ------------------------------------------------------------ 잔차·탐지


def prediction_matrix(fc: pd.DataFrame, Y: pd.DataFrame) -> np.ndarray:
    """날짜 t × 코호트: t보다 앞선 가장 최근 기준일의 예측 (log1p). 없으면 NaN."""
    dates = pd.DatetimeIndex(Y.index)
    pos = {d: i for i, d in enumerate(dates)}
    colpos = {c: j for j, c in enumerate(Y.columns)}
    P = np.full(Y.shape, np.nan)
    d = fc.sort_values("prediction_origin")  # 늦은 기준일이 덮어씀 = 가장 최근 기준일
    ti = d["target_date"].map(pos)
    ok = ti.notna().to_numpy()
    P[ti[ok].astype(int).to_numpy(), d.loc[ok, "cohort"].map(colpos).to_numpy()] = d.loc[ok, "yhat_log"].to_numpy()
    return P


def residual_matrix(fc: pd.DataFrame, Y: pd.DataFrame) -> np.ndarray:
    return np.log(Y.to_numpy(float) + 1) - prediction_matrix(fc, Y)


class ModelResid:
    """한 모델의 잔차 행렬과, 한 코호트 값을 바꿨을 때 그 코호트 잔차를 다시 계산하는 함수.

    retrain=False이고 모델이 여러 코호트를 함께 학습하면(LightGBM), 주입 실험에서 모델을 다시 학습하지 않고
    원래 모델에 바뀐 자료로 만든 특징만 넣는다 (근사. 28개 코호트 중 하나만 바뀜).
    """

    def __init__(self, Y: pd.DataFrame, model: Forecaster, origin_pos: list[int], retrain: bool = False):
        self.Y, self.model, self.origin_pos, self.retrain = Y, model, sorted(origin_pos), retrain
        self.fc, self.states = rolling_forecast(Y, model, self.origin_pos, keep_states=True)
        self.P = prediction_matrix(self.fc, Y)
        self.E = np.log(Y.to_numpy(float) + 1) - self.P
        self.n_refit = 0

    def __call__(self, col, y_new: np.ndarray, upto: int | None = None) -> np.ndarray:
        j = list(self.Y.columns).index(col)
        y_old = self.Y[col].to_numpy(float)
        diff = np.where(~(np.isclose(y_old, y_new) | (np.isnan(y_old) & np.isnan(y_new))))[0]
        p = self.P[:, j].copy()
        if len(diff):
            s = int(diff[0])
            upto = len(y_old) - 1 if upto is None else upto
            Ynew = self.Y.copy()
            Ynew[col] = y_new
            for o in self.origin_pos:
                if o < s or o >= upto:
                    continue
                hist = Ynew.iloc[: o + 1]
                if self.model.cohort_independent:
                    P = self.model.forecast(hist[[col]])
                    vals = P[col]
                else:
                    st = self.model.fit(hist) if self.retrain else self.states[o]
                    vals = self.model.predict(st, hist)[col]
                self.n_refit += 1
                for h, v in vals.items():
                    if o + h < len(p):
                        p[o + h] = v
        return np.log(np.asarray(y_new, float) + 1) - p


class ModelPanel(Panel):
    """EDA-6 Panel과 같은 점수·경보 (absolute / local 두 경로, robust z, 7일 중 3일).
    다른 점은 잔차 e(t)를 넘겨받는다는 것뿐이다 (resid.E). score_one은 resid(col, y)로 잔차를 다시 계산한다."""

    def __init__(self, Y: pd.DataFrame, holidays, cfg: AlarmConfig = AlarmConfig(), resid=None, E: np.ndarray | None = None,
                 name: str = ""):
        validate_panel(Y)
        check_panel_input(Y)
        self.Y, self.cfg, self.resid, self.name = Y.astype(float), cfg, resid, name
        self.dates = pd.DatetimeIndex(Y.index)
        hol = pd.DatetimeIndex(sorted(holidays))
        self.holidays = hol
        self.missing = Y.isna().to_numpy()
        self.is_hol = self.dates.isin(hol)
        self.near_hol = np.array([np.abs((hol - d).days).min() <= 1 if len(hol) else False for d in self.dates])
        self.dow = self.dates.dayofweek.to_numpy()
        self.cols = list(Y.columns)
        self.cities = np.array([c[0] if isinstance(c, tuple) else c for c in self.cols])
        if E is None:
            E = resid.E if resid is not None else np.column_stack(
                [one_step_resid(Y[c].to_numpy(), self.dow, self.is_hol, cfg) for c in self.cols])
        self.E = np.asarray(E, float)
        self._score()

    def _resid_one(self, col, y_new, upto=None):
        if self.resid is None:
            return one_step_resid(np.asarray(y_new, float), self.dow, self.is_hol, self.cfg)
        return self.resid(col, y_new, upto=upto)

    def score_one(self, col, y_new: np.ndarray, upto: int | None = None) -> dict:
        """Panel.score_one과 같은 계산 (기대값만 모델 잔차)."""
        cfg = self.cfg
        j = self.cols.index(col)
        y_new = _check_column(y_new, len(self.dates))
        e = self._resid_one(col, y_new, upto)
        same = np.where(self.cities == self.cities[j])[0]
        others = [k for k in same if k != j]
        if cfg.leave_target_out:
            s = _nanmedian(self.E[:, others], 1)
        else:
            M = self.E[:, same].copy()
            M[:, list(same).index(j)] = e
            s = _nanmedian(M, 1)
        out = {}
        for path, r in [("absolute", e), ("local", e - s)]:
            z, _, _, _ = robust_z(r, self.is_hol, cfg, np.isnan(y_new))
            out[path] = window_alarm(z, cfg)[0]
        out["either"] = combine_any(out["absolute"], out["local"])
        return out

    def until(self, as_of) -> "ModelPanel":
        """as_of까지 자른 패널. 잔차는 과거만 쓰므로 그대로 잘라 쓴다 (모델을 다시 학습하지 않음)."""
        k = int((self.dates <= pd.Timestamp(as_of)).sum())
        if k == 0:
            raise ValueError(f"as_of {pd.Timestamp(as_of).date()}가 자료 시작일보다 앞섬")
        return ModelPanel(self.Y.iloc[:k], self.holidays, self.cfg, E=self.E[:k], name=self.name)

    def with_config(self, **kw):
        raise NotImplementedError("ModelPanel은 설정을 바꾸려면 새로 만들 것")

    def replace_column(self, col, y_new):
        raise NotImplementedError("ModelPanel은 score_one을 쓸 것")


# ------------------------------------------------------------ EDA-6과 같은 정의의 평가 도구


def window_outcome(alarm: np.ndarray, s: int, success_days: int = 14) -> tuple[int, int, float]:
    """(시작 전날 경보 중, 창 안 경보, 첫 경보까지 일수). 시작 전날이 보류(NaN)면 pre = -1."""
    a_pre = alarm[s - 1]
    pre = -1 if np.isnan(a_pre) else int(a_pre == 1)
    w = alarm[s: s + success_days]
    hit = np.where(w == 1)[0]
    return pre, int(len(hit) > 0), (float(hit[0]) if len(hit) else np.nan)


def transitions(pr: pd.DataFrame) -> dict:
    """pr: pre, ctrl, inj, delay. 시작 전날 경보 중(1)·보류(-1)인 쌍은 제외."""
    new = pr[pr["pre"] == 0]
    c00 = int(((new["ctrl"] == 0) & (new["inj"] == 0)).sum())
    c01 = int(((new["ctrl"] == 0) & (new["inj"] == 1)).sum())
    c10 = int(((new["ctrl"] == 1) & (new["inj"] == 0)).sum())
    c11 = int(((new["ctrl"] == 1) & (new["inj"] == 1)).sum())
    return {
        "탐지": c01 / (c00 + c01) if (c00 + c01) else np.nan,
        "대조 경보": new["ctrl"].mean() if len(new) else np.nan,
        "순증 경보율 차이(%p)": (new["inj"].mean() - new["ctrl"].mean()) * 100 if len(new) else np.nan,
        "0→0": c00, "0→1": c01, "1→0": c10, "1→1": c11,
        "평가 가능": len(new), "대조 음성": c00 + c01, "새 탐지": c01,
        "시작 전 경보 중(제외)": int((pr["pre"] == 1).sum()), "시작 전 보류(제외)": int((pr["pre"] == -1).sum()),
        "지연 중앙값(일)": new.loc[(new["ctrl"] == 0) & (new["inj"] == 1), "delay"].median(),
    }


def run_pairs(panel: ModelPanel, pairs, variants: dict, paths=("local",), success_days: int = 14) -> pd.DataFrame:
    """짝지은 주입. variants = {이름: f(y, s) → 바뀐 y}. 행 = (코호트, 시작일, 변형, 경로).
    pre_low = 시작 직전 win−1일(첫 판정 창에 들어가는 날) 중 z < 기준인 날 수, 대조 자료 기준 (EDA-6과 같은 정의.
    local 경로는 local z, 그 밖은 absolute z)."""
    rows = []
    for col, s in pairs:
        j = panel.cols.index(col)
        y0 = panel.Y[col].to_numpy(float)
        ctrl = {path: window_outcome(panel.alarm_any(j, path), s, success_days) for path in paths}
        for name, f in variants.items():
            al = panel.score_one(col, f(y0.copy(), s), upto=s + success_days)
            for path in paths:
                pre, c_hit, _ = ctrl[path]
                _, i_hit, delay = window_outcome(al[path], s, success_days)
                z = panel.res["local" if path == "local" else "absolute"]["z"][s - (panel.cfg.win - 1): s, j]
                pre_low = int(np.nansum(z < panel.cfg.z_thr))
                rows.append((col, s, name, path, pre, c_hit, i_hit, delay, pre_low))
    return pd.DataFrame(rows, columns=["cohort", "start", "variant", "path", "pre", "ctrl", "inj", "delay", "pre_low"])


def step(drop: float, length: int = 14):
    def f(y, s):
        y[s: s + length] *= 1 - drop
        return y
    return f


def ramp(drop: float, length: int = 14):
    def f(y, s):
        y[s: s + length] *= 1 - drop * np.arange(1, length + 1)[: len(y[s: s + length])] / length
        return y
    return f


def background(panel: Panel, cand: list[int], path: str = "local", success_days: int = 14, from_pos: int = 0) -> pd.DataFrame:
    """코호트별 배경 경보율. 평가창(14일) 기준과 코호트·일 기준(분모 = from_pos 이후 경보 판정 가능 코호트·일)."""
    from bigcontest.signals.alarm import episode_count

    rows = []
    for j, col in enumerate(panel.cols):
        a = panel.alarm_any(j, path)
        res = [window_outcome(a, s, success_days) for s in cand]
        pre = np.array([r[0] for r in res])
        hit = np.array([r[1] for r in res])
        ev = ~np.isnan(a)
        sel = ev.copy()
        sel[:from_pos] = False
        rows.append({"cohort": col, "평가창 수": int((pre >= 0).sum()),
                     "평가창 중 경보 있음": hit[pre >= 0].mean() if (pre >= 0).any() else np.nan,
                     "새로 경보 난 창": hit[pre == 0].mean() if (pre == 0).any() else np.nan,
                     "alarm_days": int((a[sel] == 1).sum()), "eval_days": int(sel.sum()),
                     "episodes": episode_count(np.where(sel, a, np.nan))})
    return pd.DataFrame(rows)


def bg_summary(bg: pd.DataFrame, by=None) -> pd.Series | pd.DataFrame:
    def agg(x):
        return pd.Series({"평가창 중 경보 있음": x["평가창 중 경보 있음"].mean(), "새로 경보 난 창": x["새로 경보 난 창"].mean(),
                          "경보일 / 판정 가능 코호트·일": x["alarm_days"].sum() / x["eval_days"].sum(),
                          "경보 건수 / 판정 가능 코호트·100일": x["episodes"].sum() / x["eval_days"].sum() * 100,
                          "판정 가능 코호트·일": x["eval_days"].sum()})
    if by is None:
        return agg(bg)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return bg.groupby(by).apply(agg, include_groups=False)
