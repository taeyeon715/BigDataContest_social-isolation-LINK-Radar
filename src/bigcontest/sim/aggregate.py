"""⑤ 집계: 가상 사람 활동 → 제공 데이터와 같은 단위·열 이름의 표.

| 표 | 단위 (제공 데이터와 같음) | 만드는 법 |
|---|---|---|
| card2 | 일 × 가맹점 도시 × 성 × 연령 × 업종(MCT_RY_CD), 같은 시도 거주 | 코호트 × 업종 묶음 × 일 포아송 건수 → 묶음 안 세부 업종 비중(targets)으로 다항 분할 |
| card1 | 일 × 도시 × 시간대 4구간 × 성 × 연령 | 코호트 × 일 건수 → 시간대 비중(도시 × 연령 × 월, targets)으로 다항 분할 |
| card2_visitors | 일 × 도시 × 거주 구분 | 같은 시도 거주 = 합성 합계, 외지 = 합계 × 외지 비율(날 종류별, targets) × 잡음 |
| tel_age / tel_time / tel_wkdy | 50m 칸 × 월 (× 성연령 / 시간 / 요일) | 사람 외출 수준 × 계절 체류 지수 → 집·활동 칸, 동 × 성연령 구성비로 사후 층화 가중 |

통신 칸은 실제 격자가 아니라 동 중심점(ref/adm_cd_names.csv, 공개 좌표) 둘레에 만든 **가상 칸**이다
(BLOCK_CD 9번째 자리 '9' = 가상 칸 표시).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.sim import params as P
from bigcontest.sim.world import COHORTS, World
from bigcontest.telecom import AGE_COLS, TIME_COLS, WKDY_COLS

TEL_AGE_OF = {"10대": "10G", "20대": "20G", "30대": "30G", "40대": "40G", "50대": "50G",
              "60대": "60GU", "70대": "60GU", "80대+": "60GU"}
TEL_SEX_OF = {"남성": "MAN", "여성": "WMAN"}
BETA_ACT = 0.6  # 외출 흐름 중 활동 칸 몫 (가정값)
WEATHER_OUT = 0.85  # 60세 이상: 폭염·한파 날 외출 배율 (가정값)


def card2_rows(w: World, cnt: np.ndarray | None = None) -> pd.DataFrame:
    """card2 (datasource._card2_from 결과와 같은 열 일부). 같은 시도 거주(local=True)·본사등록 아님만 만든다."""
    cnt = w.counts() if cnt is None else cnt
    rng = np.random.default_rng(w.seed + 11)
    dates = w.cal.dates
    T = len(dates)
    names = w.cal.holiday_names
    frames = []
    for city in P.CITIES:
        ci = [i for i, c in enumerate(COHORTS) if c[0] == city]
        for k, cat in enumerate(P.CATS):
            sh = w.targets["mct_share"][city].get(cat)
            if not sh:
                continue
            mcts = list(sh)
            pv = np.array([sh[m] for m in mcts], float)
            pv = pv / pv.sum()
            n = cnt[ci, k, :].astype(np.int64)  # 코호트 × 일
            split = rng.multinomial(n.reshape(-1), pv).reshape(len(ci), T, len(mcts))
            c_idx, t_idx, m_idx = np.nonzero(split)
            frames.append(pd.DataFrame({
                "date": dates[t_idx], "city": city,
                "age_g": [COHORTS[ci[j]][1] for j in c_idx], "SEX_CCD": [COHORTS[ci[j]][2] for j in c_idx],
                "MCT_RY_CD": np.array(mcts, dtype=object)[m_idx], "cat": cat,
                "USE_CNT": split[c_idx, t_idx, m_idx].astype(np.int64)}))
    d = pd.concat(frames, ignore_index=True)
    d["age"] = d["age_g"]
    d["local"] = True
    d["hq"] = False
    d["corp"] = False
    d["dow"] = d["date"].dt.dayofweek.astype("int32")
    d["holiday"] = d["date"].map(names)
    d["data_source"] = "synthetic"
    return d.sort_values(["date", "city", "age_g", "SEX_CCD", "MCT_RY_CD"]).reset_index(drop=True)


def card1_rows(w: World, cnt: np.ndarray | None = None) -> pd.DataFrame:
    cnt = w.counts() if cnt is None else cnt
    rng = np.random.default_rng(w.seed + 12)
    tot = cnt.sum(1).astype(np.int64)  # 코호트 × 일
    dates = w.cal.dates
    rows = []
    for i, (city, age, sex) in enumerate(COHORTS):
        for m in sorted(set(w.cal.month)):
            t_idx = np.where(w.cal.month == m)[0]
            sh = w.targets["card1_band"].get(f"{city}|{age}|{m}")
            pv = np.array([sh[b] for b in P.TIME_BANDS], float)
            pv = pv / pv.sum()
            split = rng.multinomial(tot[i, t_idx], pv)
            for j, b in enumerate(P.TIME_BANDS):
                rows.append(pd.DataFrame({"date": dates[t_idx], "city": city, "TIME_GB": b, "SEX_CCD": sex,
                                          "age_g": age, "USE_CNT": split[:, j]}))
    d = pd.concat(rows, ignore_index=True)
    return d[d["USE_CNT"] > 0].sort_values(["date", "city", "age_g", "SEX_CCD", "TIME_GB"]).reset_index(drop=True)


def visitors(w: World, cnt: np.ndarray | None = None) -> pd.DataFrame:
    """DataSource.card2_visitors() 모양: (date, city) × local(False/True)."""
    cnt = w.counts() if cnt is None else cnt
    rng = np.random.default_rng(w.seed + 13)
    kind = np.where(w.cal.off_block, "연휴", np.where(w.cal.dow >= 5, "주말", "평일"))
    out = []
    for city in P.CITIES:
        ci = [i for i, c in enumerate(COHORTS) if c[0] == city]
        local = cnt[ci].sum((0, 1))
        ratio = np.array([w.targets["visitor_ratio"][city][k] for k in kind])
        other = np.round(local * ratio * np.exp(rng.normal(0, w.targets["visitor_log_sd"][city], len(local))))
        out.append(pd.DataFrame({"date": w.cal.dates, "city": city, False: other, True: local}))
    d = pd.concat(out).set_index(["date", "city"]).sort_index()
    d.columns = pd.Index([False, True], name="local")
    return d.astype(float)


# ---------------------------------------------------------------- 통신


def _cells(w: World, rng) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """동마다 가상 칸 (집 칸·활동 칸). 반환: 칸 표, 사람 → 집 칸, 사람 → 활동 칸."""
    from bigcontest.sim.population import PUBLIC_FILES
    from bigcontest.config import ROOT

    names = pd.read_csv(ROOT / PUBLIC_FILES["adm_names"], dtype=str).set_index("adm_cd")
    people = w.people
    home = np.zeros(len(people), int)
    act = np.zeros(len(people), int)
    rows = []
    for adm, idx in people.groupby("adm_cd").indices.items():
        info = names.loc[adm]
        x0 = (float(info["lon"]) - 127.0) * 88_800 + 1_000_000
        y0 = (float(info["lat"]) - 37.5) * 111_000 + 1_950_000
        x0, y0 = round(x0 / 50) * 50, round(y0 / 50) * 50
        n_home = int(np.clip(round(len(idx) / 5), 4, 400))
        n_act = max(2, n_home // 4)
        side = int(np.ceil(np.sqrt(n_act)))
        act_xy = [(x0 + 50 * (i % side), y0 + 50 * (i // side)) for i in range(n_act)]
        hs = int(np.ceil(np.sqrt(n_home))) + 2
        ring = [(x0 + 100 * (i - hs // 2), y0 + 100 * (j - hs // 2)) for i in range(hs) for j in range(hs)]
        ring = [p for p in ring if not (x0 - 100 <= p[0] <= x0 + 50 * side and y0 - 100 <= p[1] <= y0 + 50 * side)]
        home_xy = ring[:n_home]
        base = len(rows)
        for j, (x, y) in enumerate(act_xy + home_xy):
            rows.append({"adm_cd": adm, "city": info["city"], "adm_type": info["adm_type"], "X_COORD": float(x),
                         "Y_COORD": float(y), "kind": "act" if j < n_act else "home",
                         "BLOCK_CD": f"{adm}9{base + j:011d}", "oa13": f"{adm}99999"})
        act[idx] = base + rng.integers(0, n_act, len(idx))
        home[idx] = base + n_act + rng.integers(0, len(home_xy), len(idx))
    return pd.DataFrame(rows), home, act


def person_flow(w: World) -> np.ndarray:
    """사람 × 일 외출 수준 (거주 여부·상태·계절 체류 지수·날씨)."""
    pk = w.people["persona"].map({k: i for i, k in enumerate(P.PERSONA_IDS)}).to_numpy()
    f = P.out_table()[pk[:, None], w.states] * w.present
    old = w.people["band"].isin(["60대", "70대", "80대+"]).to_numpy()
    city = w.people["city"].to_numpy()
    dtype = w.people["adm_type"].to_numpy()
    months = sorted(set(w.cal.month))
    for c in P.CITIES:
        for t_ in ("동", "읍", "면"):
            key = f"{c}|{t_}"
            mi = w.targets["tel_month_index"].get(key)
            if mi is None:
                continue
            for grp, m_old in (("s60", True), ("u60", False)):
                sel = (city == c) & (dtype == t_) & (old == m_old)
                idx = np.array([mi[grp][f"2025{m:02d}"] for m in months])
                f[sel] *= idx[np.searchsorted(months, w.cal.month)][None, :]
        bad = (w.cal.heat[c] | w.cal.cold[c])
        f[np.ix_((city == c) & old, bad)] *= WEATHER_OUT
    # 날씨·페르소나가 바꾼 월 평균을 목표 월 지수(7월 = 1)에 다시 맞춘다 (달 안의 날씨·상태 차이는 그대로 둠)
    mi_idx = np.searchsorted(months, w.cal.month)
    for c in P.CITIES:
        for t_ in ("동", "읍", "면"):
            mi = w.targets["tel_month_index"].get(f"{c}|{t_}")
            if mi is None:
                continue
            for grp, m_old in (("s60", True), ("u60", False)):
                sel = (city == c) & (dtype == t_) & (old == m_old)
                if not sel.any():
                    continue
                mean = np.array([f[sel][:, mi_idx == j].sum() / max(sel.sum() * (mi_idx == j).sum(), 1)
                                 for j in range(len(months))])
                if mean[0] <= 0:
                    continue
                tgt = np.array([mi[grp][f"2025{m:02d}"] for m in months])
                corr = np.where(mean > 0, tgt / (mean / mean[0]), 1.0)
                f[sel] *= corr[mi_idx][None, :]
    return f


def tel_frames(w: World) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(w.seed + 14)
    cells, home, act = _cells(w, rng)
    f = person_flow(w)  # 사람 × 일
    months = sorted(set(w.cal.month))
    mi = np.searchsorted(months, w.cal.month)
    M = len(months)
    n = len(w.people)
    Fm = np.stack([f[:, mi == j].mean(1) for j in range(M)], 1)  # 사람 × 월
    Fd = np.zeros((n, M, 7))
    for j in range(M):
        for d in range(7):
            sel = (mi == j) & (w.cal.dow == d)
            Fd[:, j, d] = f[:, sel].mean(1) if sel.any() else np.nan
    col = (w.people["sex"].map(TEL_SEX_OF) + "_FLOW_POP_CNT_" + w.people["band"].map(TEL_AGE_OF)).to_numpy()
    col_i = pd.Index(AGE_COLS).get_indexer(col)
    # 동 × 성연령 사후 층화 가중 (통신 체류에는 주민 밖 통근·방문이 섞여 있어 구성비를 실제 집계에 맞춘다)
    adm = w.people["adm_cd"].to_numpy()
    syn = pd.DataFrame({"adm": adm, "col": col_i, "v": Fm.mean(1)}).groupby(["adm", "col"])["v"].sum().unstack(fill_value=0)
    syn = syn.reindex(columns=range(len(AGE_COLS)), fill_value=0)
    real = pd.DataFrame(w.targets["tel_comp"]).T.reindex(index=syn.index, columns=AGE_COLS).fillna(0).to_numpy()
    s = syn.to_numpy()
    share = s / s.sum(1, keepdims=True)
    wt = np.where(share > 0, real / np.where(share > 0, share, 1), 0.0)
    wt = wt / np.maximum((wt * share).sum(1, keepdims=True), 1e-12)  # 동 합계는 그대로
    wt_p = wt[syn.index.get_indexer(adm), col_i] * P.SCALE
    C = len(cells)
    kind_act = (cells["kind"] == "act").to_numpy()
    out = {}
    # 성연령: 칸 × 월 × 12
    A = np.zeros((C, M, len(AGE_COLS)))
    for cell, part in ((home, 1 - BETA_ACT), (act, BETA_ACT)):
        for j in range(M):
            np.add.at(A[:, j, :], (cell, col_i), wt_p * Fm[:, j] * part)
    # 시간대·요일 모양 (도시 × 동 유형, targets) × 칸 종류 보정 (가정값: 집 칸은 밤·주말, 활동 칸은 낮·평일 쪽)
    night = np.isin(np.arange(24), list(range(0, 7)) + list(range(19, 24)))
    day = np.isin(np.arange(24), range(9, 18))
    hour = np.zeros((C, 24))
    wk = np.zeros((C, 7))
    for i, r in enumerate(cells.itertuples()):
        hp = np.asarray(w.targets["tel_hour"][f"{r.city}|{r.adm_type}"])
        hp = hp * ((1 + 0.4 * day) if r.kind == "act" else (1 + 0.8 * night))
        hour[i] = hp / hp.sum() * 24
        wp = np.asarray(w.targets["tel_wkdy"][f"{r.city}|{r.adm_type}"])
        wp = wp * np.where(np.arange(7) >= 5, 0.9 if r.kind == "act" else 1.1, 1.0)
        wk[i] = wp / wp.mean()
    # 가상 주민이 없는 동 × 성연령 칸(작은 면의 청년 등): 실제 구성비만큼 '방문 체류'를 활동 칸에 고르게 더함 (상태 변화 없음)
    tot0 = A.sum(2)
    cell_adm = cells["adm_cd"].to_numpy()
    for a_i, a in enumerate(syn.index):
        miss = (s[a_i] <= 0) & (real[a_i] > 0)
        if not miss.any():
            continue
        cm = cell_adm == a
        acts = np.where(cm & kind_act)[0]
        rest = 1 - real[a_i][miss].sum()
        for j in range(M):
            add = real[a_i][miss] / max(rest, 1e-6) * A[cm, j, :].sum()
            A[np.ix_(acts, [j], np.where(miss)[0])] += (add / len(acts))[None, None, :]
    tot = A.sum(2)  # 칸 × 월 (하루 평균)
    grow = np.where(tot0 > 0, tot / np.where(tot0 > 0, tot0, 1), 1.0)
    Dw = np.zeros((C, M, 7))
    for cell, part in ((home, 1 - BETA_ACT), (act, BETA_ACT)):
        for j in range(M):
            for d in range(7):
                np.add.at(Dw[:, j, d], cell, wt_p * np.nan_to_num(Fd[:, j, d]) * part)
    key = cells[["BLOCK_CD", "X_COORD", "Y_COORD", "city", "adm_cd", "oa13", "adm_type"]]
    ym = [f"2025{m:02d}" for m in months]
    for name, vals, cols in (("tel_age", A, AGE_COLS),
                             ("tel_time", tot[:, :, None] * hour[:, None, :], TIME_COLS),
                             ("tel_wkdy", Dw * grow[:, :, None] * wk[:, None, :], WKDY_COLS)):
        frames = []
        for j in range(M):
            d = pd.DataFrame(np.round(vals[:, j, :], 2), columns=cols)
            d.insert(0, "STD_YM", ym[j])
            frames.append(pd.concat([d, key.reset_index(drop=True)], axis=1))
        t = pd.concat(frames, ignore_index=True)
        t = t[t[cols].sum(axis=1) > 0]
        out[name] = t[["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD", *cols, "city", "adm_cd", "oa13", "adm_type"]].reset_index(drop=True)
    out["_cells"] = cells
    return out
