"""rules-v2.0 H축: 행동 고립 (태연 04 H_v2를 기준월별로 옮김, reports/r3_규칙개정/변경계획.md 2·7절).

칸(동 × 성 × 연령5)마다
1. 체류도(보정 전) = log((기준월까지 월평균 통신 유동 + 1) / (칸 주민등록 인구 + 1))
2. 보정 체류도 = 도시마다 [성·연령 칸 더미, log 동 주간/야간 유동 비(상업성), log 동 인구]로 회귀한 잔차
3. 겨울 체류 유지 = 겨울(11·12월 중 쓸 수 있는 달) 평균 유동 ÷ 여름(7·8월) 평균 유동 − 1, 같은 회귀로 보정
4. H = (같은 도시·성·연령 안 백분위(보정 체류도) + 백분위(보정 겨울 체류)) ÷ 2
   겨울 달이 아직 없으면 H = 백분위(보정 체류도)이고 h_basis 에 그렇게 적는다.
- 칸의 월평균 유동이 1명 미만이면 H 결측 (춘천 통신 잡음, 설계 7절).
- H가 '섬' = 같은 도시·성·연령 안 상위 1/3.
H_v2(겨울 지표도 보정)는 태연 04에서 결과를 본 뒤 고친 판이라 '자료 노출 후 설정'이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.cells import AGE5, TOP_SHARE, cell_index, cell_population
from bigcontest.telecom import AGE_COLS, AGES, SEXES

SUMMER = (7, 8)
WINTER = (11, 12)
MIN_FLOW = 1.0
DAY_HOURS = [f"TMST_{h:02d}" for h in range(9, 18)]
NIGHT_HOURS = [f"TMST_{h:02d}" for h in range(0, 6)]


def cell_flow(tel_age: pd.DataFrame) -> pd.DataFrame:
    """통신 성연령(50m 칸 × 월) → 동 × 성 × 연령5 × 월 유동 합 (10대 제외)."""
    t = tel_age.copy()
    t["month"] = t["STD_YM"].str[4:].astype(int)
    g = t.groupby(["adm_cd", "month"])[AGE_COLS].sum().stack().rename("flow").reset_index()
    col = g["level_2"]
    g["sex"] = col.str.split("_").str[0].map(SEXES)
    g["age5"] = col.str.extract(r"CNT_(\w+)$")[0].map(AGES)
    g = g[g["age5"].isin(AGE5)]
    return g[["adm_cd", "sex", "age5", "month", "flow"]]


def day_night(tel_time: pd.DataFrame, months: list[int]) -> pd.Series:
    """동 주간(9~17시) 평균 ÷ 야간(0~5시) 평균 유동 (쓸 수 있는 달만)."""
    t = tel_time[tel_time["STD_YM"].str[4:].astype(int).isin(months)]
    s = t.groupby("adm_cd")[DAY_HOURS + NIGHT_HOURS].sum()
    return (s[DAY_HOURS].sum(axis=1) / len(DAY_HOURS)) / (s[NIGHT_HOURS].sum(axis=1) / len(NIGHT_HOURS))


def _residual(df: pd.DataFrame, y: str) -> pd.Series:
    """도시마다 y ~ 칸 더미 + log 상업성 + log 동 인구 의 잔차 (최소제곱)."""
    out = pd.Series(np.nan, index=df.index)
    for _, g in df.groupby("city"):
        ok = g[y].notna() & np.isfinite(g["log_daynight"]) & np.isfinite(g["log_dongpop"])
        g = g[ok]
        X = pd.get_dummies(g["sex"] + "_" + g["age5"], dtype=float)
        X["log_daynight"] = g["log_daynight"]
        X["log_dongpop"] = g["log_dongpop"]
        beta, *_ = np.linalg.lstsq(X.to_numpy(), g[y].to_numpy(dtype=float), rcond=None)
        out.loc[g.index] = g[y].to_numpy(dtype=float) - X.to_numpy() @ beta
    return out


def h_axis(tel_age: pd.DataFrame, tel_time: pd.DataFrame, pop: pd.DataFrame, months: list[int],
           top_share: float = TOP_SHARE, winter: tuple[int, ...] = WINTER) -> pd.DataFrame:
    """기준월까지 쓸 수 있는 달(months, 공개 지연 반영은 호출하는 쪽)로 칸마다 H.

    pop: external.resident_population() (1세). 칸·동 인구는 months 의 마지막 달 값.
    winter: 겨울로 볼 달 (기본 11·12월. 태연 04 재현 확인에는 (12,))."""
    months = sorted(months)
    m_last = months[-1]
    cells = cell_index()
    fl = cell_flow(tel_age)
    fl = fl[fl["month"].isin(months)]
    mon = fl.pivot_table(index=["adm_cd", "sex", "age5"], columns="month", values="flow", aggfunc="sum")
    df = cells.merge(cell_population(pop, m_last), on=["adm_cd", "sex", "age5"], how="left")
    key = pd.MultiIndex.from_frame(df[["adm_cd", "sex", "age5"]])
    df["flow"] = mon.mean(axis=1).reindex(key).to_numpy()
    dongpop = pop[pop["month"] == m_last].groupby("adm_cd")["pop"].sum()
    df["log_dongpop"] = np.log(df["adm_cd"].map(dongpop))
    df["log_daynight"] = np.log(df["adm_cd"].map(day_night(tel_time, months)))
    df["stay_raw"] = np.log((df["flow"] + 1) / (df["pop"] + 1))
    df["stay_adj"] = _residual(df, "stay_raw")
    win = [m for m in winter if m in months]
    if win and all(m in months for m in SUMMER):
        w = mon[win].mean(axis=1) / mon[list(SUMMER)].mean(axis=1) - 1
        df["winter_raw"] = w.reindex(key).to_numpy()
        df["winter_adj"] = _residual(df, "winter_raw")
        basis = f"체류도 + 겨울({'·'.join(map(str, win))}월)"
    else:
        df["winter_raw"] = np.nan
        df["winter_adj"] = np.nan
        basis = "체류도만 (겨울 달 아직 없음)"
    grp = ["city", "sex", "age5"]
    df["p_stay"] = df.groupby(grp)["stay_adj"].rank(pct=True)
    df["p_winter"] = df.groupby(grp)["winter_adj"].rank(pct=True)
    df["h"] = (df["p_stay"] + df["p_winter"]) / 2 if win else df["p_stay"]
    df.loc[df["flow"].fillna(0) < MIN_FLOW, "h"] = np.nan
    df["h_state"] = np.where(df["h"].notna(), "observed", "missing")
    df["h_basis"] = basis
    df["h_months"] = "·".join(map(str, months))
    rk = df.groupby(grp)["h"]
    df["h_rank"] = rk.rank(ascending=False, method="first")
    df["h_n"] = rk.transform("count")
    df["h_top"] = (df["h_rank"] <= np.ceil(df["h_n"] * top_share)) & df["h"].notna()
    return df
