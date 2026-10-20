"""rules-v2.0 W: 춘천 읍·면별 겨울 체류 변화 (reports/r3_규칙개정/변경계획.md 3절).

v1.1(`agent/tools/signal.calc_winter_contraction`)과 같은 '주민형 격자'(밤/낮 비가 읍·면 중앙값 이상이고 주말/평일 0.8~1.25인
250m 격자, 60+ 월평균 체류 2 이상) 정의를 쓰되, 값을 읍·면 전체 하나가 아니라 **읍·면마다** 낸다.
- 그 읍·면 주민형 격자가 MIN_CORE 개 미만이면 읍·면 전체 값을 쓰고 shared=True ("도시 공유 맥락").
- W 근거 인정 = 값이 DECLINE(−5%) 미만 (기존 도구의 '감소' 판정 값).
"""

from __future__ import annotations

import pandas as pd

from bigcontest.telecom import AGE_COLS, WKDY_COLS, age_cols, grid_id

SUMMER = ("202507", "202509")
WINTER = ("202511", "202512")
MIN_CORE = 10
DECLINE = -0.05


def _mode(df: pd.DataFrame, key: str, col: str) -> pd.Series:
    return df.groupby(key)[col].agg(lambda s: s.mode().iloc[0])


def winter_by_area(tel_age: pd.DataFrame, tel_time: pd.DataFrame, tel_wkdy: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    """months: 쓸 수 있는 'YYYYMM' (공개 지연 반영은 호출하는 쪽). 열: adm_cd, adm_type, n_core, w60, w_all, scope, shared, w_ok"""
    summer = [m for m in SUMMER if m in months]
    winter = [m for m in WINTER if m in months]
    if not summer or not winter:
        return pd.DataFrame(columns=["adm_cd", "adm_type", "n_core", "w60", "w_all", "scope", "shared", "w_ok"])
    sel = []
    for d in (tel_age, tel_time, tel_wkdy):
        d = d[(d["city"] == "춘천") & d["STD_YM"].isin(months)].copy()
        d["g"] = grid_id(d["X_COORD"], d["Y_COORD"])
        sel.append(d)
    cc, tt, ww = sel
    cc["s60"] = cc[age_cols("60GU")].sum(axis=1)
    cc["all"] = cc[AGE_COLS].sum(axis=1)
    tt["night"] = tt[[f"TMST_{h:02d}" for h in range(0, 6)]].sum(axis=1)
    tt["day"] = tt[[f"TMST_{h:02d}" for h in range(11, 16)]].sum(axis=1)
    ww["wd"] = ww[WKDY_COLS[:5]].mean(axis=1)
    ww["we"] = ww[WKDY_COLS[5:]].mean(axis=1)
    gt = tt.groupby("g")[["night", "day"]].sum()
    gw = ww.groupby("g")[["wd", "we"]].sum()
    gm = cc.groupby(["g", "STD_YM"])["s60"].sum().unstack().fillna(0)
    gall = cc.groupby(["g", "STD_YM"])["all"].sum().unstack().fillna(0)
    info = pd.DataFrame({"nd": gt["night"] / gt["day"], "ww": gw["we"] / gw["wd"], "type": _mode(cc, "g", "adm_type"),
                         "adm_cd": _mode(cc, "g", "adm_cd"), "s60": gm.mean(axis=1)}).dropna()
    info = info[info["s60"] >= 2]
    cut = info.loc[info["type"] != "동", "nd"].quantile(0.5)
    info["core"] = (info["nd"] >= cut) & info["ww"].between(0.8, 1.25)

    def change(idx):
        w60 = gm.loc[idx, winter].mean(axis=1).sum() / gm.loc[idx, summer].mean(axis=1).sum() - 1
        wall = gall.loc[idx, winter].mean(axis=1).sum() / gall.loc[idx, summer].mean(axis=1).sum() - 1
        return float(w60), float(wall)

    rural = info[info["type"].isin(["읍", "면"]) & info["core"]]
    agg60, aggall = change(rural.index)
    rows = []
    adm_type = _mode(cc, "adm_cd", "adm_type")
    for adm, t in adm_type.items():
        if t not in ("읍", "면"):
            continue
        idx = rural.index[rural["adm_cd"] == adm]
        if len(idx) >= MIN_CORE:
            w60, wall = change(idx)
            rows.append((adm, t, len(idx), w60, wall, "그 읍·면 주민형 격자", False))
        else:
            rows.append((adm, t, len(idx), agg60, aggall, f"춘천 읍·면 전체 주민형 격자 {len(rural)}개 (그 읍·면 {len(idx)}개 < {MIN_CORE})", True))
    out = pd.DataFrame(rows, columns=["adm_cd", "adm_type", "n_core", "w60", "w_all", "scope", "shared"])
    out["w_ok"] = out["w60"] < DECLINE
    return out
