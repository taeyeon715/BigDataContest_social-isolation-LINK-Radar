"""보정 목표 (targets.json): 제공 데이터에서 **집계 통계만** 뽑아 저장한다.

- 원천 값(결제건수·금액·체류량)은 저장하지 않는다. 저장하는 것은 비율·비중·로그 효과·MAD·자기상관뿐이다.
- 실제 자료(data/interim)가 있어야 다시 만든다: `uv run python -m bigcontest.sim.calibrate`
- 레포에 올린 targets.json 만 있으면 원본 없이도 LINK-Sim 을 돌릴 수 있다 (원본 없는 시연).

카드 (카드 데이터2, 같은 시도 거주, 본사등록 제외: 경보 패널과 같은 거르기)
- 코호트(도시 × 연령 × 성)마다 log(결제+1) 회귀: 요일 7 + 월 6 + 공휴일 + 연휴(공휴일 아닌 날) + 연휴 직전 + 폭염 + 한파 + 강수
- 경보 규칙이 보는 잔차의 흔들림: local 잔차 MAD·lag-1 자기상관 (코호트), 도시 공통 변화 MAD·lag-1 (도시)
- 업종 묶음 비중 (코호트 × 7묶음), 묶음 안 세부 업종 비중 (도시 × 묶음 × 업종), 외지 손님 비율 (도시 × 날 종류)
카드 데이터1: 시간대 4구간 비중 (도시 × 연령 × 월)
통신: 동 × 성 × 연령 구성비, 시간대·요일 모양 (도시 × 동 유형), 월별 체류 지수 (도시 × 동 유형 × 60+ 여부, 7월 = 1)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bigcontest.sim import params as P

TARGETS_FILE = Path(__file__).with_name("targets.json")
MAD_K = 1.4826
EFFECTS = ("hol", "block", "pre", "heat", "cold", "rain")


def cohort_key(c) -> str:
    return "|".join(c)


def design(cal, city: str) -> np.ndarray:
    """회귀 설계 행렬: 요일 7 (절편 대신) + 월 2~6 (7월 기준) + 효과 6."""
    T = len(cal.dates)
    X = [np.eye(7)[cal.dow]]
    months = sorted(set(cal.month))
    X.append(np.column_stack([(cal.month == m).astype(float) for m in months[1:]]))
    X.append(np.column_stack([cal.is_hol, cal.off_block & ~cal.is_hol, cal.pre_hol,
                              cal.heat[city], cal.cold[city], cal.rain[city]]).astype(float))
    return np.hstack(X).reshape(T, -1)


def fit_effects(Y: pd.DataFrame, cal) -> dict:
    """코호트마다 OLS. 반환: {코호트: {dow, month, effects, resid(배열)}}."""
    out = {}
    L = np.log(Y.astype(float) + 1)
    for c in Y.columns:
        X = design(cal, c[0])
        y = L[c].to_numpy()
        ok = np.isfinite(y)
        beta, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
        dow = beta[:7]
        lvl = dow.mean()
        month = np.r_[0.0, beta[7:12]]
        month = month - month.mean()
        eff = dict(zip(EFFECTS, beta[12:].tolist()))
        out[c] = {"dow": (dow - lvl).tolist(), "month": month.tolist(), "effects": eff,
                  "resid": y - X @ beta}
    return out


def rule_stats(Y: pd.DataFrame, holidays) -> dict:
    """경보 규칙이 보는 잔차의 흔들림. 코호트: local 잔차 MAD·lag1, absolute 잔차 MAD. 도시: 공통 변화 MAD·lag1.
    공휴일과 첫 28일(학습 기간)은 뺀다."""
    from bigcontest.signals.alarm import AlarmConfig, one_step_resid

    idx = pd.DatetimeIndex(Y.index)
    is_hol = idx.isin(pd.DatetimeIndex(sorted(holidays)))
    E = pd.DataFrame(np.column_stack([one_step_resid(Y[c].to_numpy(), idx.dayofweek.to_numpy(), is_hol, AlarmConfig())
                                      for c in Y.columns]), index=idx, columns=Y.columns)
    E[is_hol] = np.nan
    E.iloc[:28] = np.nan
    cities = np.array([c[0] for c in Y.columns])

    def mad(s):
        s = s.dropna()
        return float((s - s.median()).abs().median() * MAD_K) if len(s) else float("nan")

    def lag1(s):
        v = s.autocorr(1)
        return 0.0 if np.isnan(v) else float(v)

    shock = {c: E.loc[:, cities == c].median(axis=1) for c in np.unique(cities)}
    coh = {}
    for j, col in enumerate(Y.columns):
        loc = E[col] - shock[cities[j]]
        coh[cohort_key(col)] = {"local_mad": mad(loc), "local_lag1": lag1(loc), "abs_mad": mad(E[col])}
    city = {c: {"shock_mad": mad(s), "shock_lag1": lag1(s)} for c, s in shock.items()}
    return {"cohort": coh, "city": city}


def resid_noise(eff: dict, cols, cal) -> dict:
    """회귀 잔차를 도시 공통(같은 날 코호트 잔차 중앙값)과 코호트 몫으로 나눈 흔들림 (공휴일 제외).
    합성 잡음의 출발값: 크기 = MAD × 1.4826, 자기상관 = lag-1."""
    R = pd.DataFrame({c: eff[c]["resid"] for c in cols}, index=cal.dates)
    R[cal.is_hol] = np.nan
    cities = np.array([c[0] for c in cols])
    out = {"cohort": {}, "city": {}}
    for city in np.unique(cities):
        sub = R.loc[:, cities == city]
        m = sub.median(axis=1)
        out["city"][city] = {"resid_sd": float((m - m.median()).abs().median() * MAD_K), "resid_phi": float(m.autocorr(1))}
        for c in sub.columns:
            x = sub[c] - m
            out["cohort"][cohort_key(c)] = {"resid_sd": float((x - x.median()).abs().median() * MAD_K),
                                            "resid_phi": float(x.autocorr(1))}
    return out


def compute_targets(ds) -> dict:
    """실제 DataSource(dev 스냅샷) → 집계 통계 dict."""
    from bigcontest.sim.calendar import load_calendar
    from bigcontest.telecom import AGE_COLS, TIME_COLS, WKDY_COLS

    cal = load_calendar()
    p = ds.panel()
    Y = p.Y
    eff = fit_effects(Y, cal)
    rs = rule_stats(Y, ds.holidays())
    rn = resid_noise(eff, Y.columns, cal)
    c2 = ds.card2()
    cat = c2.groupby(["city", "age_g", "SEX_CCD", "cat"])["USE_CNT"].sum()
    cat = cat / cat.groupby(level=[0, 1, 2]).transform("sum")
    mct = c2.groupby(["city", "cat", "MCT_RY_CD"])["USE_CNT"].sum()
    mct = mct / mct.groupby(level=[0, 1]).transform("sum")
    # 외지 손님 비율 (같은 시도 밖 거주 ÷ 같은 시도 거주), 날 종류별 평균
    v = ds.card2_visitors()
    ratio = (v[False] / v[True]).unstack("city").reindex(cal.dates)
    kind = np.where(cal.off_block, "연휴", np.where(cal.dow >= 5, "주말", "평일"))
    vis = {c: {k: float(np.exp(np.log(ratio[c][kind == k]).mean())) for k in ("평일", "주말", "연휴")} for c in P.CITIES}
    vis_sd = {c: float(np.log(ratio[c]).groupby(kind).transform(lambda s: s - s.mean()).std()) for c in P.CITIES}
    # 카드 데이터1 시간대 비중
    c1 = ds.card1()
    b = c1.assign(month=c1["date"].dt.month).groupby(["city", "age_g", "month", "TIME_GB"])["USE_CNT"].sum()
    b = b / b.groupby(level=[0, 1, 2]).transform("sum")
    # 통신
    ta, tt, tw = ds.tel("tel_age"), ds.tel("tel_time"), ds.tel("tel_wkdy")
    comp = ta.groupby("adm_cd")[AGE_COLS].sum()
    comp = comp.div(comp.sum(axis=1), axis=0)
    ta = ta.assign(s60=ta[[c for c in AGE_COLS if c.endswith("60GU")]].sum(axis=1), all=ta[AGE_COLS].sum(axis=1))
    ta["u60"] = ta["all"] - ta["s60"]
    mi = ta.groupby(["city", "adm_type", "STD_YM"])[["s60", "u60"]].sum()
    mi = mi / mi.groupby(level=[0, 1]).transform("first")
    hp = tt.groupby(["city", "adm_type"])[TIME_COLS].sum()
    hp = hp.div(hp.sum(axis=1), axis=0)
    wp = tw.groupby(["city", "adm_type"])[WKDY_COLS].sum()
    wp = wp.div(wp.mean(axis=1), axis=0)
    from bigcontest.telecom import grid_id

    tt = tt.assign(g=grid_id(tt["X_COORD"], tt["Y_COORD"]))
    gt = tt.groupby(["city", "g"])[TIME_COLS].sum()
    nd = (gt[TIME_COLS[0:6]].sum(axis=1) / gt[TIME_COLS[11:16]].sum(axis=1)).replace([np.inf, -np.inf], np.nan)
    nd_q = nd.groupby(level=0).quantile([0.1, 0.25, 0.5, 0.75, 0.9]).unstack()

    def r(x, k=5):
        return round(float(x), k)

    return {
        "version": P.SIM_VERSION,
        "note": "제공 데이터 집계 통계(비율·로그 효과·MAD·자기상관)만. 결제건수·금액·체류량 원천 값 없음",
        "dates": [P.START, P.END],
        "card": {
            cohort_key(c): {
                "dow": [r(x) for x in eff[c]["dow"]], "month": [r(x) for x in eff[c]["month"]],
                "effects": {k: r(x) for k, x in eff[c]["effects"].items()},
                "cat_share": {k: r(cat.get((c[0], c[1], c[2], k), 0.0), 6) for k in P.CATS},
                **{k: r(x) for k, x in rs["cohort"][cohort_key(c)].items()},
                **{k: r(x) for k, x in rn["cohort"][cohort_key(c)].items()},
            } for c in Y.columns},
        "city_noise": {c: {k: r(x) for k, x in {**d, **rn["city"][c]}.items()} for c, d in rs["city"].items()},
        "mct_share": {c: {k: {m: r(x, 6) for m, x in mct.loc[(c, k)].items()} for k in P.CATS if (c, k) in mct.index}
                      for c in P.CITIES},
        "visitor_ratio": {c: {k: r(x) for k, x in d.items()} for c, d in vis.items()},
        "visitor_log_sd": {c: r(x) for c, x in vis_sd.items()},
        "card1_band": {f"{c}|{a}|{m}": {t: r(b.get((c, a, m, t), 0.0)) for t in P.TIME_BANDS}
                       for c, a, m in sorted({k[:3] for k in b.index})},
        "tel_comp": {a: {k: r(x, 6) for k, x in row.items()} for a, row in comp.iterrows()},
        "tel_month_index": {f"{c}|{t}": {g: {m: r(mi.loc[(c, t, m), g]) for m in sorted(mi.loc[(c, t)].index)}
                                         for g in ("s60", "u60")} for c, t in sorted({k[:2] for k in mi.index})},
        "tel_hour": {f"{c}|{t}": [r(x, 6) for x in row] for (c, t), row in hp.iterrows()},
        "tel_wkdy": {f"{c}|{t}": [r(x) for x in row] for (c, t), row in wp.iterrows()},
        "tel_grid_nightday_q": {c: {str(q): r(x) for q, x in row.items()} for c, row in nd_q.iterrows()},
    }


def load_targets(path: Path = TARGETS_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    from bigcontest.agent.tools.datasource import DataSource, dev_snapshot_id

    t = compute_targets(DataSource(dev_snapshot_id()))
    with open(TARGETS_FILE, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=1)
    print(f"저장: {TARGETS_FILE} (코호트 {len(t['card'])}개, 동 {len(t['tel_comp'])}개)")


if __name__ == "__main__":
    main()
