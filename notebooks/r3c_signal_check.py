"""R-3c 신호 방향 확인 (reports/r3_규칙개정/변경계획.md 10절, configs/rules_v2.yaml signal_check).

강남 22개 동 × 성 × 연령마다, 칸 신호(H 행동 고립, S 1인 비율)와 시민생활 '외출·커뮤니케이션 모두 적음' 비율의
동 단위 순위상관을 본다. ρ > 0 이고 단측 순열 p ≤ 0.05 면 '확인', 아니면 '방향 미확인'.
결과: configs/signal_check.csv (에이전트가 읽음), reports/r3_규칙개정/신호_방향확인.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from bigcontest.agent.tools.datasource import dev_snapshot_id, open_datasource
from bigcontest.cells import s_axis
from bigcontest.config import ROOT
from bigcontest.external import resident_population, resident_single_by_age, seoul_living
from bigcontest.rules.levels_v2 import load_rules_v2
from bigcontest.stay import h_axis

CFG = load_rules_v2()["signal_check"]
OUT = ROOT / "reports" / "r3_규칙개정"


def reference() -> pd.Series:
    sl = seoul_living()
    sl = sl[sl["iso_all"].notna() & sl["pop"].gt(0)].copy()
    sl["age5"] = sl["age"].map(lambda a: None if a < 20 else ("60대+" if a >= 60 else f"{a // 10 * 10}대"))
    g = sl.dropna(subset=["age5"]).groupby(["adm_cd", "sex", "age5"])[["iso_all", "pop"]].sum()
    return (g["iso_all"] / g["pop"]).rename("ref")


def spearman_perm(x: np.ndarray, y: np.ndarray, n: int, rng) -> tuple[float, float]:
    rx, ry = rankdata(x), rankdata(y)
    rx, ry = rx - rx.mean(), ry - ry.mean()
    rho = float(rx @ ry / np.sqrt((rx @ rx) * (ry @ ry)))
    hits = sum(float(rx @ rng.permutation(ry)) >= rho * np.sqrt((rx @ rx) * (ry @ ry)) for _ in range(n))
    return rho, (hits + 1) / (n + 1)


def main():
    ds = open_datasource(dev_snapshot_id())
    pop = resident_population()
    h = h_axis(ds.tel("tel_age"), ds.tel("tel_time"), pop, [7, 8, 9, 10, 11, 12]).set_index(["adm_cd", "sex", "age5"])["h"]
    s = s_axis(pop, resident_single_by_age(), 12).set_index(["adm_cd", "sex", "age5"])["s_eb"]
    ref = reference()
    rng = np.random.default_rng(CFG["seed"])
    rows = []
    for axis, val in (("H", h), ("S", s)):
        j = pd.concat([val.rename("v"), ref], axis=1, join="inner").dropna()
        for (sex, age), g in j.groupby(level=["sex", "age5"]):
            rho, p = spearman_perm(g["v"].to_numpy(), g["ref"].to_numpy(), CFG["permutations"], rng)
            ok = rho > CFG["rho_min"] and p <= CFG["p_max"]
            rows.append({"axis": axis, "sex": sex, "age5": age, "n_dong": len(g), "rho": round(rho, 3),
                         "p": round(p, 4), "status": "확인" if ok else "방향 미확인"})
    t = pd.DataFrame(rows).sort_values(["axis", "sex", "age5"])
    t.to_csv(ROOT / "configs" / "signal_check.csv", index=False, encoding="utf-8")
    t.to_csv(OUT / "신호_방향확인.csv", index=False, encoding="utf-8-sig")
    print(t.to_string(index=False))


if __name__ == "__main__":
    main()
