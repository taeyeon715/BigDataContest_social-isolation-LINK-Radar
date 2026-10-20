"""R-3 rules-v2.0: 카드 이탈 기준(z·창 일수·최소 하락폭)을 LINK-Sim 정답 자료로 고른다.

절차는 reports/r3_규칙개정/변경계획.md 5절에 결과 전에 고정했다.
- 자료: labeled_dataset(build_world(), n_scenarios=120, seed=0). 짝수 시나리오 = 고르기, 홀수 = 평가.
- 후보: z ∈ {−2.0, −2.5, −3.0} × 7일 중 {2, 3, 4}일 × 최소 하락폭 ∈ {5, 7.5, 10, 12.5, 15}%.
- 탐지(시나리오 단위): 정답 이탈일(label_deviation)이 있는 시나리오에서, 정답 이탈일 중 하루라도
  경보(absolute·local 중 하나) + 그날 창(7일) 평균 하락(absolute) ≥ 최소 하락폭.
- 배경 경보율: 대상이 아닌 코호트의 판정 가능한 코호트·일 중 같은 조건을 만족한 비율.
- 선택: 고르기 시나리오 재현율 최대, 단 배경 경보율 ≤ v1.1(−2.5, 3일, 10%) 값. 동률이면 최소 하락폭 큰 쪽 → |z| 큰 쪽.
합성 결과이며 실제 고립 탐지율이 아니다.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from bigcontest.agent.tools.datasource import DataSource, dev_snapshot_id
from bigcontest.config import ROOT
from bigcontest.signals.alarm import AlarmConfig, Panel
from bigcontest.sim import inject as I
from bigcontest.sim.world import build_world

OUT = ROOT / "reports" / "r3_규칙개정"
ZS = [-2.0, -2.5, -3.0]
KS = [2, 3, 4]
DROPS = [0.05, 0.075, 0.10, 0.125, 0.15]
V11 = (-2.5, 3, 0.10)


def window_drop(E: np.ndarray, win: int) -> np.ndarray:
    """날짜 × 코호트: 그날까지 창 평균 absolute 잔차의 하락 비율 (양수 = 하락)."""
    out = np.full(E.shape, np.nan)
    for t in range(E.shape[0]):
        w = E[max(0, t - win + 1): t + 1]
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # 창 전체가 결측인 앞날
            out[t] = -np.expm1(np.nanmean(w, axis=0))
    return out


def evaluate(panel: pd.DataFrame, scen: pd.DataFrame, hol, ids: list[int]) -> pd.DataFrame:
    rows = []
    for s in ids:
        sc = scen.loc[s]
        Y = I.wide(panel, s)
        lab_t = panel[(panel["scenario_id"] == s) & panel["is_target"]].set_index("date")["label_deviation"]
        target = (sc["city"], sc["age_g"], sc["SEX_CCD"])
        base = Panel(Y, hol)
        for z in ZS:
            for k in KS:
                p = base.with_config(z_thr=z, k=k)
                drop = window_drop(p.E, p.cfg.win)
                A = np.column_stack([p.alarm_any(j, "either") for j in range(len(p.cols))])
                jt = p.cols.index(target)
                lab = lab_t.reindex(p.dates).fillna(False).to_numpy(bool)
                for md in DROPS:
                    hit = (A == 1) & (drop >= md)
                    judged = ~np.isnan(A)
                    others = [j for j in range(len(p.cols)) if j != jt]
                    rows.append({"scenario_id": s, "persona": sc["persona_name"], "frac": sc["inj_fraction"],
                                 "z": z, "k": k, "min_drop": md, "has_label": bool(lab.any()),
                                 "detected": bool((hit[:, jt] & lab).any()),
                                 "bg_hits": int(hit[:, others][judged[:, others]].sum()),
                                 "bg_cells": int(judged[:, others].sum())})
    return pd.DataFrame(rows)


def summarize(r: pd.DataFrame) -> pd.DataFrame:
    g = r.groupby(["z", "k", "min_drop"])
    out = pd.DataFrame({
        "재현율": g.apply(lambda d: d.loc[d["has_label"], "detected"].mean()),
        "정답 시나리오": g.apply(lambda d: int(d["has_label"].sum())),
        "배경 경보율": g["bg_hits"].sum() / g["bg_cells"].sum(),
    })
    return out.reset_index()


def choose(sel: pd.DataFrame) -> pd.Series:
    v11 = sel[(sel["z"] == V11[0]) & (sel["k"] == V11[1]) & np.isclose(sel["min_drop"], V11[2])].iloc[0]
    ok = sel[sel["배경 경보율"] <= v11["배경 경보율"] + 1e-12].copy()
    ok["absz"] = ok["z"].abs()
    return ok.sort_values(["재현율", "min_drop", "absz"], ascending=[False, False, False]).iloc[0]


def main():
    hol = DataSource(dev_snapshot_id()).holidays()
    panel, scen = I.labeled_dataset(build_world(), n_scenarios=120, seed=0)
    scen = scen.set_index("scenario_id", drop=False)
    ids = scen["scenario_id"].tolist()
    r = evaluate(panel, scen, hol, ids)
    r["split"] = np.where(r["scenario_id"] % 2 == 0, "고르기", "평가")
    sel = summarize(r[r["split"] == "고르기"])
    ev = summarize(r[r["split"] == "평가"])
    best = choose(sel)
    pick = ev[(ev["z"] == best["z"]) & (ev["k"] == best["k"]) & np.isclose(ev["min_drop"], best["min_drop"])].assign(판="v2.0 (고른 값)")
    old = ev[(ev["z"] == V11[0]) & (ev["k"] == V11[1]) & np.isclose(ev["min_drop"], V11[2])].assign(판="v1.1")
    OUT.mkdir(parents=True, exist_ok=True)
    sel.round(4).to_csv(OUT / "카드기준_LINK-Sim_고르기.csv", index=False, encoding="utf-8-sig")
    ev.round(4).to_csv(OUT / "카드기준_LINK-Sim_평가.csv", index=False, encoding="utf-8-sig")
    pd.concat([old, pick]).round(4).to_csv(OUT / "카드기준_선택.csv", index=False, encoding="utf-8-sig")
    per = r[(r["split"] == "평가") & r["has_label"]]
    pp = []
    for nm, (z, k, md) in {"v1.1": V11, "v2.0": (best["z"], best["k"], best["min_drop"])}.items():
        d = per[(per["z"] == z) & (per["k"] == k) & np.isclose(per["min_drop"], md)]
        pp.append(d.groupby("persona")["detected"].mean().rename(nm))
    pd.concat(pp, axis=1).round(4).reset_index().to_csv(OUT / "카드기준_페르소나별.csv", index=False, encoding="utf-8-sig")
    print("고른 값:", best.to_dict())
    print(pd.concat([old, pick]).round(4).to_string())


if __name__ == "__main__":
    main()
