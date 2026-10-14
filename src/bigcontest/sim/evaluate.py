"""LINK-Sim 검증·활용 계산 (충실도·유용성·안전성, 최소 감지 고립 비율, 정답 순위, 스트레스 시험).

모든 결과는 **합성**이다. 실제 고립 탐지 성능이 아니다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.sim import params as P
from bigcontest.sim.calibrate import fit_effects, rule_stats
from bigcontest.sim.world import COHORTS, World

TOL = {  # 충실도 통과 기준 (미리 정함)
    "dow_corr": 0.9, "hol_abs": 0.05, "month_abs": 0.03, "mad_ratio": (0.8, 1.25), "lag1_abs": 0.1,
    "cat_tvd": 0.02, "city_mad_ratio": (0.8, 1.25), "city_lag1_abs": 0.15,
    "ipf_rel": 0.01, "tel_share_abs": 0.01, "tel_month_abs": 0.05,
}


# ---------------------------------------------------------------- 충실도: 카드


def card_fidelity(Yr: pd.DataFrame, Ys: pd.DataFrame, cal, holidays, cat_r: pd.DataFrame | None = None,
                  cat_s: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """코호트별 비교표와 도시 공통 변화 비교표. 회귀 효과는 두 자료 모두 같은 식으로 다시 잰다."""
    er, es = fit_effects(Yr, cal), fit_effects(Ys, cal)
    rr, rs = rule_stats(Yr, holidays), rule_stats(Ys, holidays)
    rows = []
    for c in Yr.columns:
        k = "|".join(c)
        a, b = er[c], es[c]
        ra, rb = pd.Series(a["resid"]), pd.Series(b["resid"])
        row = {"도시": c[0], "연령": c[1], "성": c[2],
               "요일 모양 상관": float(np.corrcoef(a["dow"], b["dow"])[0, 1]),
               "요일 효과 최대 차": float(np.max(np.abs(np.subtract(a["dow"], b["dow"])))),
               "월 효과 최대 차": float(np.max(np.abs(np.subtract(a["month"], b["month"])))),
               "공휴일 효과 실제": a["effects"]["hol"], "공휴일 효과 합성": b["effects"]["hol"],
               "local MAD 실제": rr["cohort"][k]["local_mad"], "local MAD 합성": rs["cohort"][k]["local_mad"],
               "lag1 실제": rr["cohort"][k]["local_lag1"], "lag1 합성": rs["cohort"][k]["local_lag1"],
               "잔차 lag7 실제": float(ra.autocorr(7)), "잔차 lag7 합성": float(rb.autocorr(7)),
               "잔차 첨도 실제": float(ra.kurt()), "잔차 첨도 합성": float(rb.kurt()),
               "실제·합성 일별 잔차 상관": float(ra.corr(rb))}
        if cat_r is not None and cat_s is not None:
            row["업종 비중 TVD"] = float(0.5 * np.abs(cat_r.loc[c].reindex(P.CATS).fillna(0) -
                                                      cat_s.loc[c].reindex(P.CATS).fillna(0)).sum())
        rows.append(row)
    t = pd.DataFrame(rows)
    t["MAD 비"] = t["local MAD 합성"] / t["local MAD 실제"]
    t["공휴일 차"] = (t["공휴일 효과 합성"] - t["공휴일 효과 실제"]).abs()
    t["lag1 차"] = (t["lag1 합성"] - t["lag1 실제"]).abs()
    lo, hi = TOL["mad_ratio"]
    t["통과"] = ((t["요일 모양 상관"] >= TOL["dow_corr"]) & (t["공휴일 차"] <= TOL["hol_abs"])
                & (t["월 효과 최대 차"] <= TOL["month_abs"]) & t["MAD 비"].between(lo, hi) & (t["lag1 차"] <= TOL["lag1_abs"])
                & ((t["업종 비중 TVD"] <= TOL["cat_tvd"]) if "업종 비중 TVD" in t else True))
    city = pd.DataFrame([{"도시": c, "공통 변화 MAD 실제": rr["city"][c]["shock_mad"], "공통 변화 MAD 합성": rs["city"][c]["shock_mad"],
                          "공통 lag1 실제": rr["city"][c]["shock_lag1"], "공통 lag1 합성": rs["city"][c]["shock_lag1"]}
                         for c in P.CITIES])
    city["MAD 비"] = city["공통 변화 MAD 합성"] / city["공통 변화 MAD 실제"]
    lo, hi = TOL["city_mad_ratio"]
    city["통과"] = city["MAD 비"].between(lo, hi) & ((city["공통 lag1 합성"] - city["공통 lag1 실제"]).abs() <= TOL["city_lag1_abs"])
    return t, city


def cat_shares(card2: pd.DataFrame) -> pd.DataFrame:
    g = card2.groupby(["city", "age_g", "SEX_CCD", "cat"])["USE_CNT"].sum().unstack("cat").fillna(0)
    return g.div(g.sum(axis=1), axis=0)


def weekday_profile(Y: pd.DataFrame, holidays) -> pd.DataFrame:
    """요일별 평균 log 결제 − 전체 평균 (공휴일 제외), 도시 × 요일 (코호트 평균)."""
    L = np.log(Y + 1)
    L = L[~pd.DatetimeIndex(L.index).isin(pd.DatetimeIndex(holidays))]
    d = L.groupby(L.index.dayofweek).mean() - L.mean()
    return d.T.groupby(level=0).mean()


# ---------------------------------------------------------------- 충실도: 인구·통신


def population_fidelity(info: dict) -> dict:
    cells = info["cells"]
    m1 = info["m1"]
    sim = cells.groupby(["adm_cd", "sex", "band"])["sim_n"].sum() * info["ipf"]["scale"]
    real = m1.stack([0, 1]).rename_axis(["adm_cd", "sex", "band"])
    real = real.reorder_levels(["adm_cd", "sex", "band"]).reindex(sim.index)
    big = real >= 400  # 표본 오차가 작을 칸 (가상 10명 이상)
    rel = ((sim - real).abs() / real.where(real > 0))
    one = cells[cells["alone"]].groupby("adm_cd")["sim_n"].sum() * info["ipf"]["scale"]
    hh = info["hh1"].reindex(one.index)
    return {"IPF 반복": info["ipf"]["iterations"], "IPF 칸 최대 상대 오차": info["ipf"]["max_rel_error_cell"],
            "가상 사람 수": info["ipf"]["n_people"], "축소 배율": info["ipf"]["scale"],
            "표본 동×성×연령 칸 상대 오차 중앙값(실제 400명 이상 칸)": float(rel[big].median()),
            "표본 동×성×연령 칸 상대 오차 90%(실제 400명 이상 칸)": float(rel[big].quantile(0.9)),
            "표본 동 1인 인구 상대 오차 중앙값": float(((one - hh).abs() / hh).median()),
            "도시 10세 이상 인구 상대 오차": float(abs(sim.sum() - real.sum()) / real.sum())}


def tel_fidelity(real: dict, syn: dict) -> dict[str, pd.DataFrame]:
    """real/syn: {"tel_age":…, "tel_time":…, "tel_wkdy":…}. 동 60+ 비중·월 지수(보정함), 시간대·밤낮 비(보정 안 함)."""
    from bigcontest.telecom import AGE_COLS, TIME_COLS, WKDY_COLS, grid_id

    def share60(t):
        g = t.groupby("adm_cd")[AGE_COLS].sum()
        return g[[c for c in AGE_COLS if c.endswith("60GU")]].sum(axis=1) / g.sum(axis=1)

    s = pd.DataFrame({"실제": share60(real["tel_age"]), "합성": share60(syn["tel_age"])}).dropna()
    s["차"] = (s["합성"] - s["실제"]).abs()

    def month_idx(t):
        t = t.assign(s60=t[[c for c in AGE_COLS if c.endswith("60GU")]].sum(axis=1))
        g = t.groupby(["city", "adm_type", "STD_YM"])["s60"].sum()
        return g / g.groupby(level=[0, 1]).transform("first")

    m = pd.DataFrame({"실제": month_idx(real["tel_age"]), "합성": month_idx(syn["tel_age"])}).dropna()
    m["차"] = (m["합성"] - m["실제"]).abs()

    def hour(t):
        g = t.groupby(["city", "adm_type"])[TIME_COLS].sum()
        return g.div(g.sum(axis=1), axis=0)

    hr, hs = hour(real["tel_time"]), hour(syn["tel_time"])
    h = pd.DataFrame({"시간대 모양 상관": [float(np.corrcoef(hr.loc[i], hs.loc[i])[0, 1]) for i in hr.index],
                      "최대 차(%p)": [float((hr.loc[i] - hs.loc[i]).abs().max() * 100) for i in hr.index]}, index=hr.index)

    def nd(t):
        t = t.assign(g=grid_id(t["X_COORD"], t["Y_COORD"]))
        g = t.groupby(["city", "g"])[TIME_COLS].sum()
        r = (g[TIME_COLS[0:6]].sum(axis=1) / g[TIME_COLS[11:16]].sum(axis=1)).replace([np.inf, -np.inf], np.nan)
        return r.groupby(level=0).quantile([0.1, 0.5, 0.9]).unstack()

    q = pd.concat({"실제": nd(real["tel_time"]), "합성": nd(syn["tel_time"])}, axis=1)

    def wk(t):
        g = t.groupby(["city", "adm_type"])[WKDY_COLS].sum()
        return g.div(g.mean(axis=1), axis=0)

    wr, ws = wk(real["tel_wkdy"]), wk(syn["tel_wkdy"])
    wkt = pd.DataFrame({"요일 모양 최대 차": [float((wr.loc[i] - ws.loc[i]).abs().max()) for i in wr.index]}, index=wr.index)
    return {"동_60대이상_비중": s, "월_체류지수": m, "시간대_모양": h, "격자_밤낮비": q, "요일_모양": wkt}


# ---------------------------------------------------------------- 유용성: 경보


def alarm_rate(Y: pd.DataFrame, holidays) -> pd.DataFrame:
    """배경 경보율 (주입 없음): 판정 가능한 코호트·일 중 경보(either) 비율, 도시별."""
    from bigcontest.signals.alarm import Panel

    p = Panel(Y, holidays)
    A = np.column_stack([p.alarm_any(j, "either") for j in range(len(p.cols))])
    out = []
    for c in P.CITIES:
        m = p.cities == c
        a = A[:, m]
        out.append({"도시": c, "경보 비율(코호트·일)": float(np.nanmean(a)), "판정 가능 칸": int((~np.isnan(a)).sum())})
    a = A
    out.append({"도시": "전체", "경보 비율(코호트·일)": float(np.nanmean(a)), "판정 가능 칸": int((~np.isnan(a)).sum())})
    return pd.DataFrame(out)


def step_starts(cal, n_per: int = 10, seed: int = 0, dur: int = 14, min_train: int = 28) -> dict:
    """EDA-6 방식 시작일 (코호트마다 n_per개, 주입 기간·앞뒤 하루에 공휴일 없음)."""
    rng = np.random.default_rng(seed)
    T = len(cal.dates)
    ok = [s for s in range(min_train + 7, T - dur) if not cal.is_hol[max(0, s - 1): s + dur + 1].any()]
    return {c: sorted(rng.choice(ok, n_per, replace=False).tolist()) for c in COHORTS}


def step_detection(Y: pd.DataFrame, holidays, starts: dict, drop: float = 0.15, dur: int = 14) -> pd.DataFrame:
    """계단형 주입 짝 실험: 시작일부터 dur일 × (1−drop). 탐지 = 주입 기간 안 경보(either), 대조 경보 쌍은 뺌."""
    from bigcontest.signals.alarm import Panel

    p = Panel(Y, holidays)
    rows = []
    for c, ss in starts.items():
        j = p.cols.index(c)
        ctrl = p.alarm_any(j, "either")
        for s in ss:
            y = Y[c].to_numpy().copy()
            y[s: s + dur] *= 1 - drop
            a = p.score_one(c, y)["either"]
            win = slice(s, s + dur)
            hit = np.where(a[win] == 1)[0]
            rows.append({"도시": c[0], "연령": c[1], "성": c[2], "시작": s, "대조 경보": bool((ctrl[win] == 1).any()),
                         "탐지": bool(len(hit)), "지연": int(hit[0]) if len(hit) else None})
    return pd.DataFrame(rows)


def boot_rate(df: pd.DataFrame, col: str = "탐지", n: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """코호트 단위 부트스트랩 (같은 코호트의 시작일은 독립이 아님)."""
    d = df[~df["대조 경보"]]
    g = d.groupby(["도시", "연령", "성"])[col].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), (n, len(g)))
    rates = g["sum"].to_numpy()[idx].sum(1) / g["count"].to_numpy()[idx].sum(1)
    return float(d[col].mean()), float(np.quantile(rates, 0.025)), float(np.quantile(rates, 0.975))


# ---------------------------------------------------------------- (a) 최소 감지 고립 비율

FRACS_DETECT = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
ONSET_DATES = ("2025-08-04", "2025-08-25", "2025-09-08", "2025-10-20", "2025-11-03", "2025-11-17", "2025-12-01")


def detect_curve(w: World, backgrounds: dict[str, pd.DataFrame], holidays, personas=("S1", "S4"), fracs=FRACS_DETECT,
                 onsets=ONSET_DATES, window: int = 28) -> pd.DataFrame:
    """코호트 × 시작일 × 페르소나 × 비율 → 탐지 여부. backgrounds: {"합성": 합성 패널, "실제": 실제 패널}.
    '합성' = 합성 세계 결제(같은 난수로 짝) / 그 밖 = 그 결제 × 합성 기대 비율 λ_f/λ_0 (하이브리드: 실제 배경 + 가상 변화량)."""
    from scipy.stats import poisson

    from bigcontest.signals.alarm import Panel
    from bigcontest.sim import inject as I

    panels = {k: Panel(Y, holidays) for k, Y in backgrounds.items()}
    T = w.T
    rows = []
    for c in COHORTS:
        ci = COHORTS.index(c)
        mult = np.exp(w.log_cal[ci] + w.noise[ci])[None, :]
        for od in onsets:
            t0 = int(w.cal.dates.get_loc(pd.Timestamp(od)))
            win = slice(t0, min(t0 + window, T))
            for per in personas:
                inj = I.prepare(w, c, per, t0, max_frac=max(fracs))
                lam = np.stack([inj.lam(w, f) for f in fracs])  # 비율 × 업종 × 일
                syn = poisson.ppf(np.broadcast_to(w.cnt_u[ci], lam.shape), np.maximum(lam * mult, 1e-9)).sum(1)
                ratio = lam.sum(1) / np.maximum(w.lam0[ci].sum(0), 1e-9)
                for bg, p in panels.items():
                    j = p.cols.index(c)
                    ctrl_hit = bool((p.alarm_any(j, "either")[win] == 1).any())
                    for fi, f in enumerate(fracs):
                        y = syn[fi] if bg == "합성" else backgrounds[bg][c].to_numpy() * ratio[fi]
                        a = p.score_one(c, y)["either"]
                        hit = np.where(a[win] == 1)[0]
                        rows.append({"배경": bg, "도시": c[0], "연령": c[1], "성": c[2], "시작일": od,
                                     "페르소나": P.PERSONAS[per].name, "배정 비율": f, "대조 경보": ctrl_hit,
                                     "탐지": bool(len(hit)), "지연": int(hit[0]) if len(hit) else None,
                                     "창 안 최대 단절 비율": float(inj.w_frac(f)[win].max()),
                                     "창 안 최대 기대 감소": float(1 - ratio[fi][win].min())})
    return pd.DataFrame(rows)


def min_detectable(curve: pd.DataFrame, by=("배경", "페르소나", "도시"), levels=(0.5, 0.8)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(최소 비율 표, 비율별 탐지율 표). 대조 경보 쌍은 뺀다. 대조 경보율은 (코호트, 시작일) 쌍 기준."""
    d = curve[~curve["대조 경보"]]
    rate = d.groupby([*by, "배정 비율"])["탐지"].mean().rename("탐지율").reset_index()
    ctrl = curve.drop_duplicates([*by, "도시", "연령", "성", "시작일"]).groupby(list(by))["대조 경보"].mean()         if "도시" not in by else curve.drop_duplicates([*by, "연령", "성", "시작일"]).groupby(list(by))["대조 경보"].mean()
    out = []
    for key, g in rate.groupby(list(by)):
        g = g.sort_values("배정 비율")
        row = dict(zip(by, key))
        for lv in levels:
            ok = g[g["탐지율"] >= lv]
            row[f"탐지율 {int(lv * 100)}% 최소 비율"] = float(ok["배정 비율"].iloc[0]) if len(ok) else None
        row["가장 큰 비율에서 탐지율"] = float(g["탐지율"].iloc[-1])
        row["대조 경보율"] = float(ctrl.loc[key])
        out.append(row)
    return pd.DataFrame(out), rate


# ---------------------------------------------------------------- (a) 정답 순위


def dong_risk_scenarios(people: pd.DataFrame, seed: int = 0) -> dict[str, pd.Series]:
    """동마다 고립 부담 배수. A = 구조 가정과 일치(1인 비율 높은 동이 더 높음), B = 구조와 무관(무작위, 1인 구성 효과도 상쇄)."""
    rng = np.random.default_rng(seed)
    alone = people.groupby("adm_cd")["alone"].mean()
    a = (alone / alone.mean()) ** 2
    rnd = pd.Series(np.exp(rng.normal(0, 0.7, len(alone))), index=alone.index)
    b = rnd * (alone.mean() / alone)
    return {"A 구조 일치": a, "B 구조 무관": b}


def true_burden(w: World, group_bands: list[str], sex: str | None, as_of: int, days: int = 28) -> pd.DataFrame:
    """동별 합성 정답: 기준일 앞 days일 동안 대상 집단 사람 중 W(단절) 비율 평균, W 인 사람 수(가상 × 축소 배율)."""
    sel = w.people["band"].isin(group_bands).to_numpy().copy()
    if sex:
        sel &= (w.people["sex"] == sex).to_numpy()
    inw = ((w.states == P.W) & w.present)[:, max(0, as_of - days): as_of + 1].mean(1)
    d = pd.DataFrame({"adm_cd": w.people["adm_cd"].to_numpy()[sel], "w": inw[sel]})
    g = d.groupby("adm_cd")["w"].agg(["mean", "sum"])
    g.columns = ["단절 비율", "단절 인원"]
    g["단절 인원"] *= P.SCALE
    return g


def rank_quality(cands, burden: pd.DataFrame, city_adms: list[str]) -> dict:
    """규칙 순위(등급 → 검토 순서) vs 합성 정답 순위 Spearman, Level 3 이 정답 상위 25% 동을 얼마나 담나."""
    from scipy.stats import spearmanr

    lv = {"Level 3": 3, "Level 2": 2, "Level 1": 1, "Level 0": 0}
    score = {}
    for c in cands:
        if c.candidate_type != "scheduled_access_review" or not c.adm_cd:
            continue
        s = lv.get(c.candidate_level or "Level 0", 0) * 1000 - (c.queue_rank or 999)
        score[c.adm_cd] = max(score.get(c.adm_cd, -1e9), s)
    adms = [a for a in city_adms if a in burden.index]
    x = np.array([score.get(a, -1e6) for a in adms], float)
    out = {}
    for col in ("단절 비율", "단절 인원"):
        y = burden.loc[adms, col].to_numpy()
        ok = len(adms) > 2 and np.ptp(x) > 0 and np.ptp(y) > 0
        out[f"Spearman({col})"] = float(spearmanr(x, y).statistic) if ok else None
    top = set(burden.loc[adms, "단절 비율"].sort_values(ascending=False).index[: max(1, len(adms) // 4)])
    l3 = {a for a, s in score.items() if s >= 3000 - 999}
    out["Level 3 동 수"] = len(l3)
    out["Level 3 중 정답 상위 25%"] = (len(l3 & top) / len(l3)) if l3 else None
    out["정답 상위 25% 중 Level 3"] = len(l3 & top) / len(top)
    return out
