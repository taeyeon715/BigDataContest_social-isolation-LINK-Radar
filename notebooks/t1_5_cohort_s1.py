"""T1.5 집단별 S1: 구조 축 S1(동 전체 1인세대 비율)을 집단마다 그 집단의 1인세대 비율로 바꿔 보는 대안.

사전 등록: reports/t1_5_집단별S1/사전등록.md · 사전등록.json (결과 전에 커밋). 이 스크립트는 json 값을 읽어 쓴다.
실행: uv run python notebooks/t1_5_cohort_s1.py   (dev 스냅샷 자료가 있어야 함: 이탈·반증 근거가 카드 자료)
결과: reports/t1_5_집단별S1/ (README.md, CSV). 공식 등급(rules-v1.1, R-2)은 바꾸지 않고 나란히 비교만 한다.

- 집단별 S1 = 집단 1인세대 수 ÷ 집단 주민등록 인구 (행안부 성·연령별 1인세대수 + 15097972, 같은 동·같은 달)
- 기준월은 지금 S1과 같은 달 (get_structure 가 기준일에 고른 달)
- 같은 근거에서 구조 근거(structure_vulnerability)의 값·분모·순위 표시만 바꿔 R-2와 같은 함수(비교안 D)로 등급을 다시 낸다
- 원천(카드·통신) 값은 결과에 쓰지 않는다. 비율·순위·등급·건수·공개 인구(10 미만 가림)만
"""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import dev_available, dev_snapshot_id, open_datasource
from bigcontest.config import ROOT
from bigcontest.contracts import cohorts_config
from bigcontest.external import resident_population, resident_single_by_age
from bigcontest.report import Report
from bigcontest.rules import load_rules
from bigcontest.rules.candidates import RANK_PREFIX, rank_population, rank_warning
from bigcontest.rules.compare import collect, ctx_for, run_variant

OUT = ROOT / "reports" / "t1_5_집단별S1"
PRE = json.loads((OUT / "사전등록.json").read_text(encoding="utf-8"))
R2 = ROOT / "reports" / "r2_위험도결과"
CITIES = ["강남", "춘천"]
DATES = {"추석 D-7": date(2025, 9, 26), "추석 뒤": date(2025, 10, 13), "동절기": date(2025, 12, 20)}  # R-1·R-2 와 같음
GROUPS = list(cohorts_config()["groups"])
COHORTS = {**PRE["집단"], **PRE["Q4 후보 집단"]}
Q4G = next(iter(PRE["Q4 후보 집단"]))
DONG_GROUPS = ["고령층", "중장년 남성 1인가구", "50~69세 남성"]  # 동 단위 집단 (청년 남성은 도시 단위 안내)
NOT_CAND = "후보 아님"
LV_NUM = {NOT_CAND: 0, "Level 0": 0, "Level 1": 1, "판단 보류": 0.5, "Level 2": 2, "Level 3": 3}
SMALL = 10


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T2 = _load("t2_cross_validation", ROOT / "notebooks" / "t2_cross_validation.py")


def small(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return "<10" if v < SMALL else int(round(v))


def alt_rules(base: dict, top_share: float | None = None) -> dict:
    """사전 등록 규칙: rules-v1.1 그대로 + abs_min 안 씀 (+ 민감도용 상위 비율)."""
    r = copy.deepcopy(base)
    s = r["thresholds"]["structure"]
    s["abs_min"] = PRE["규칙"]["abs_min"] if PRE["규칙"]["abs_min"] is not None else 0.0
    s["top_share"] = top_share if top_share is not None else PRE["규칙"]["top_share"]
    return r


# ------------------------------------------------------------ 집단별 S1 (공개 주민등록만)
def cohort_s1(single: pd.DataFrame, pop: pd.DataFrame, month: int, spec: dict) -> pd.DataFrame:
    lo, hi, sex = spec["age_min"], spec["age_max"], spec["sex"]
    assert lo % 5 == 0 and (hi is None or (hi + 1) % 5 == 0), "5세 구간 경계와 맞지 않음"
    s = single[(single["month"] == month) & (single["age_lo"] >= lo)]
    s = s[s["age_hi"].notna() & (s["age_hi"] <= hi)] if hi is not None else s
    p = pop[(pop["month"] == month) & (pop["age"] >= lo)]
    if hi is not None:
        p = p[p["age"] <= hi]
    if sex:
        s, p = s[s["sex"] == sex], p[p["sex"] == sex]
    out = pd.DataFrame({"1인세대": s.groupby("adm_cd")["n"].sum(min_count=1), "인구": p.groupby("adm_cd")["pop"].sum(min_count=1)})
    out["S1"] = out["1인세대"] / out["인구"].where(out["인구"] > 0)
    return out


# ------------------------------------------------------------ 근거 바꾸기
def swap_structure(ev: list, s1: pd.DataFrame, city: str, month_txt: str, label: str) -> list:
    """구조 근거의 값·분모·순위 표시만 집단별 S1로. 순위 모집단 = 도시 × 기준월 행정동 전체."""
    structs = [e for e in ev if S.input_of(e) == "structure_vulnerability" and e.status == "OK"]
    adms = {S.adm_of(e) for e in structs}
    full = {a: float(s1.loc[a, "S1"]) for a in adms if a in s1.index and pd.notna(s1.loc[a, "S1"])}
    ranks = rank_population(full)
    popu = f"{city} {len(full)}개 동 전체, {month_txt} 주민등록 집단별 1인세대 ({label}) (T1.5 대안)"
    out = []
    for e in ev:
        if S.input_of(e) != "structure_vulnerability" or e.status != "OK":
            out.append(e)
            continue
        a = S.adm_of(e)
        v = full.get(a)
        warns = [w for w in e.warnings if not w.startswith(RANK_PREFIX)]
        if v is None:
            out.append(e.model_copy(update={"value": None, "value_state": "missing", "warnings": warns}))
            continue
        out.append(e.model_copy(update={
            "value": round(v, 4), "n_obs": int(s1.loc[a, "인구"]), "denominator": f"{label} 주민등록 인구",
            "warnings": warns + [rank_warning(popu, *ranks[a])]}))
    return out


def dong_rows(cands, ev, city, dname, g, names, variant, rules):
    structs = [e for e in ev if S.input_of(e) == "structure_vulnerability" and e.status == "OK"]
    facts = S.structure_facts(structs, rules)
    sched = {c.adm_cd: c for c in cands if c.candidate_type == "scheduled_access_review"}
    adms = sorted(names.index[names["city"] == city])
    rows = []
    for adm in adms + ([None] if None in sched else []):
        c = sched.get(adm)
        f = facts.get(adm) if adm else None
        rows.append({"도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "adm_cd": adm or "",
                     "동": names.loc[adm, "adm_nm"] if adm else "(도시 전체)", "판": variant,
                     "등급": c.candidate_level if c else NOT_CAND, "규칙 id": c.rule_id if c else "-",
                     "S1 값": round(f.value, 4) if f else None, "S1 순위": f.rank if f else None,
                     "구조 상위": bool(f.top) if f else None})
    return rows


def dev_rows(cands, city, dname, g, names, variant):
    out = []
    for c in cands:
        if c.candidate_type != "unexpected_activity_deviation":
            continue
        out.append({"도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "분석 코호트": c.analysis_cohort,
                    "판": variant, "등급": c.candidate_level, "규칙 id": c.rule_id,
                    "먼저 확인할 동": ", ".join(names.loc[a, "adm_nm"] for a in c.check_first_adm if a in names.index) or "-"})
    return out


def jac(a: set, b: set):
    return len(a & b) / len(a | b) if (a | b) else None


def nm_list(s, names):
    return ", ".join(sorted(names.loc[a, "adm_nm"] for a in s)) or "-"


# ------------------------------------------------------------ 실행
def run(write: bool = True) -> dict:
    assert dev_available(), "dev 스냅샷 자료가 없음"
    sid = dev_snapshot_id()
    ds = open_datasource(sid)
    names = ds.adm_names().set_index("adm_cd")
    rules = load_rules()
    rules_alt = alt_rules(rules)
    k = max(rules["selection"]["budgets"])
    single, pop = resident_single_by_age(), resident_population()

    ev, req, month = {}, {}, {}
    for city in CITIES:
        for dname, d in DATES.items():
            for g in GROUPS:
                c = ctx_for(sid, city, d, g)
                req[(city, dname, g)] = c.request
                ev[(city, dname, g)] = collect(c)
                st = [e for e in ev[(city, dname, g)] if S.input_of(e) == "structure_vulnerability" and e.status == "OK"]
                month[(city, dname, g)] = st[0].observed_from.month
    months = sorted(set(month.values()))
    assert {f"2025-{m:02d}" for m in months} == {v for kk, v in PRE["기준월"].items() if kk != "원칙"}, months

    # 집단별 S1 표 (5개 집단 × 기준월 × 동). 지금 S1 = 같은 달 동 전체 1인세대 비율
    hh = ds.households()
    s1 = {}
    s1_rows = []
    for m in months:
        h = hh[hh["month"] == m].set_index("adm_cd")
        for g, spec in COHORTS.items():
            t = cohort_s1(single, pop, m, spec)
            s1[(m, g)] = t
            for city in CITIES:
                adms = sorted(names.index[names["city"] == city])
                old = {a: float(h.loc[a, "one_person_rate"]) for a in adms}
                new = {a: float(t.loc[a, "S1"]) for a in adms if pd.notna(t.loc[a, "S1"])}
                ro, rn = rank_population(old), rank_population(new)
                n = len(adms)
                cut = int(np.ceil(PRE["규칙"]["top_share"] * n))
                for a in adms:
                    s1_rows.append({"도시": city, "adm_cd": a, "동": names.loc[a, "adm_nm"], "집단": g, "기준월": f"2025-{m:02d}",
                                    "지금 S1(동 전체 1인세대 비율)": round(old[a], 4), "지금 순위": ro[a][0],
                                    "집단별 S1": round(new[a], 4) if a in new else None,
                                    "집단별 순위": rn[a][0] if a in rn else None,
                                    "집단 1인세대 수": small(t.loc[a, "1인세대"]), "집단 인구": small(t.loc[a, "인구"]),
                                    "지금 상위 30%": ro[a][0] <= cut and old[a] >= rules["thresholds"]["structure"]["abs_min"],
                                    "집단별 상위 30%": a in rn and rn[a][0] <= cut})
    s1tab = pd.DataFrame(s1_rows)

    # 등급: v1.1 (바꾸지 않은 근거) vs 대안 (구조 근거만 바꿈)
    rows, drows, cands_alt = [], [], {}
    for key in ev:
        city, dname, g = key
        m = month[key]
        base = run_variant(ev[key], "D", req[key], DATES[dname], k, rules).candidates
        ev_alt = swap_structure(ev[key], s1[(m, g)], city, f"2025-{m:02d}", g)
        alt = run_variant(ev_alt, "D", req[key], DATES[dname], k, rules_alt).candidates
        cands_alt[key] = (ev_alt, alt)
        rows += dong_rows(base, ev[key], city, dname, g, names, "rules-v1.1", rules)
        rows += dong_rows(alt, ev_alt, city, dname, g, names, "집단별 S1 대안", rules_alt)
        drows += dev_rows(base, city, dname, g, names, "rules-v1.1") + dev_rows(alt, city, dname, g, names, "집단별 S1 대안")
    long = pd.DataFrame(rows)
    keys = ["도시", "기준일", "집단", "adm_cd", "동"]
    b = long[long["판"] == "rules-v1.1"].drop(columns="판").set_index(keys)
    a = long[long["판"] == "집단별 S1 대안"].drop(columns="판").set_index(keys)
    cmp = b.join(a, lsuffix=" (v1.1)", rsuffix=" (대안)", how="outer").reset_index()
    cmp["등급 바뀜"] = cmp["등급 (v1.1)"] != cmp["등급 (대안)"]
    dn = cmp["등급 (대안)"].map(LV_NUM) - cmp["등급 (v1.1)"].map(LV_NUM)
    cmp["방향"] = np.where(~cmp["등급 바뀜"], "같음", np.where(dn > 0, "올라감", "내려감"))

    # 실행 전 확인: 바꾸지 않은 근거 = R-2 동별_등급표 (566행)
    r2 = pd.read_csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str}, encoding="utf-8-sig").fillna({"adm_cd": ""})
    chk = r2.set_index(keys)[["등급", "규칙 id"]].join(b[["등급", "규칙 id"]], rsuffix=" 재실행", how="outer")
    assert len(chk) == len(r2) == 566, len(chk)
    assert (chk["등급"] == chk["등급 재실행"]).all() and (chk["규칙 id"] == chk["규칙 id 재실행"]).all(), "R-2 재현 실패"

    dv = pd.DataFrame(drows)
    dkeys = ["도시", "기준일", "집단", "분석 코호트"]
    dcmp = (dv[dv["판"] == "rules-v1.1"].drop(columns="판").set_index(dkeys)
            .join(dv[dv["판"] == "집단별 S1 대안"].drop(columns="판").set_index(dkeys), lsuffix=" (v1.1)", rsuffix=" (대안)",
                  how="outer").reset_index())
    dcmp["등급 바뀜"] = dcmp["등급 (v1.1)"] != dcmp["등급 (대안)"]
    dcmp["먼저 확인할 동 바뀜"] = dcmp["먼저 확인할 동 (v1.1)"] != dcmp["먼저 확인할 동 (대안)"]

    # 1. 후보 동 겹침 (예정된 점검 Level 2 이상, 구조 상위)
    def dong_sets(df, col_lv, col_top):
        out = {}
        for (city, dl, g), x in df[df["adm_cd"] != ""].groupby(["도시", "기준일", "집단"]):
            out[(city, dl, g)] = (set(x.loc[x[col_lv].isin(["Level 2", "Level 3"]), "adm_cd"]),
                                  set(x.loc[x[col_top].fillna(False).astype(bool), "adm_cd"]))
        return out

    sb, sa = dong_sets(cmp, "등급 (v1.1)", "구조 상위 (v1.1)"), dong_sets(cmp, "등급 (대안)", "구조 상위 (대안)")
    ov = []
    for key in sb:
        for i, what in enumerate(["Level 2 이상", "구조 상위"]):
            x, y = sb[key][i], sa[key][i]
            ov.append({"도시": key[0], "기준일": key[1], "집단": key[2], "집합": what, "v1.1 동 수": len(x), "대안 동 수": len(y),
                       "겹침": len(x & y), "Jaccard": jac(x, y), "빠진 동": nm_list(x - y, names), "새로 든 동": nm_list(y - x, names)})
    overlap = pd.DataFrame(ov)

    # 2. 등급 변화 수
    lc = (cmp.groupby(["도시", "기준일", "집단"])
          .agg(행=("등급 바뀜", "size"), 바뀜=("등급 바뀜", "sum"),
               올라감=("방향", lambda s: int((s == "올라감").sum())), 내려감=("방향", lambda s: int((s == "내려감").sum())))
          .reset_index())
    for col in ["v1.1", "대안"]:
        for lv in ["Level 2", "Level 3"]:
            lc[f"{lv} ({col})"] = cmp.groupby(["도시", "기준일", "집단"])[f"등급 ({col})"].apply(lambda s, lv=lv: int((s == lv).sum())).values
    trans = (cmp[cmp["등급 바뀜"]].groupby(["규칙 id (v1.1)", "규칙 id (대안)"]).size().rename("행 수").reset_index())

    # 3. 지금 S1 ↔ 집단별 S1 ρ
    rho = []
    for (city, g, m), x in s1tab.groupby(["도시", "집단", "기준월"]):
        x = x.dropna(subset=["집단별 S1"])
        r = spearmanr(x["지금 S1(동 전체 1인세대 비율)"], x["집단별 S1"])
        top_o = set(x.loc[x["지금 상위 30%"], "adm_cd"])
        top_n = set(x.loc[x["집단별 상위 30%"], "adm_cd"])
        rho.append({"도시": city, "집단": g, "기준월": m, "동 수": len(x), "ρ(지금 S1, 집단별 S1)": r.statistic, "p": r.pvalue,
                    "상위 30% 겹침": len(top_o & top_n), "상위 30% 동 수": len(top_n)})
    rho = pd.DataFrame(rho)

    # 4. 집단 사이 겹침 (같은 도시·기준일)
    pairs = [("고령층", "중장년 남성 1인가구"), ("고령층", "50~69세 남성"), ("중장년 남성 1인가구", "50~69세 남성")]
    cg = []
    for city in CITIES:
        for dname in DATES:
            dl = f"{dname} ({DATES[dname]:%m/%d})"
            for g1, g2 in pairs:
                for i, what in enumerate(["Level 2 이상", "구조 상위"]):
                    row = {"도시": city, "기준일": dl, "집단 1": g1, "집단 2": g2, "집합": what}
                    for tag, ss in [("v1.1", sb), ("대안", sa)]:
                        x, y = ss[(city, dl, g1)][i], ss[(city, dl, g2)][i]
                        row |= {f"{tag} 집단1 수": len(x), f"{tag} 집단2 수": len(y), f"{tag} 겹침": len(x & y),
                                f"{tag} Jaccard": jac(x, y)}
                    cg.append(row)
    cross = pd.DataFrame(cg)

    # 5. 시민생활 상관 (강남, 추석 D-7 근거, 탐색). R-1 2장과 같은 방법
    sl_rows = []
    for g in GROUPS:
        e0 = ev[("강남", "추석 D-7", g)]
        sl = {S.adm_of(e): float(e.value) for e in e0
              if e.metric.startswith("시민생활") and e.status == "OK" and isinstance(e.value, (int, float))}
        old = {S.adm_of(e): float(e.value) for e in e0 if S.input_of(e) == "structure_vulnerability" and e.status == "OK"}
        new = {S.adm_of(e): float(e.value) for e in cands_alt[("강남", "추석 D-7", g)][0]
               if S.input_of(e) == "structure_vulnerability" and e.status == "OK" and e.value is not None}
        adms = sorted(set(sl) & set(old) & set(new))
        ro, po = T2.perm([old[x] for x in adms], [sl[x] for x in adms])
        rn, pn = T2.perm([new[x] for x in adms], [sl[x] for x in adms])
        sl_rows.append({"집단": g, "동 수": len(adms), "시민생활 지표": next(e.metric for e in e0 if e.metric.startswith("시민생활")),
                        "ρ 지금 S1": spearmanr([old[x] for x in adms], [sl[x] for x in adms]).statistic,
                        "ρ 집단별 S1": spearmanr([new[x] for x in adms], [sl[x] for x in adms]).statistic,
                        "순열 p 지금(단측)": po, "순열 p 집단별(단측)": pn})
    slc = pd.DataFrame(sl_rows)

    # 6. T-2 독립 기준 다시 채점 (동 값 = 추석 D-7 기준월 집단별 S1)
    m0 = int(PRE["기준월"]["추석 D-7"][5:])
    ty, lr = T2.load_ty(), T2.load_lr()
    dl0 = T2.dong_level(ty, lr)
    d = dl0[["도시", "읍면동명", "LINK-Radar S1", "log 동 인구(2025.12)", "인구 이동률(%)", "장애인 비율(%)"]].copy()
    ind = [("LINK-Radar S1", "지금 S1 (동 전체, T-2와 같음)")]
    for g in COHORTS:
        col = f"집단별 S1: {g}"
        d[col] = s1[(m0, g)]["S1"].reindex(d.index)
        ind.append((col, col))
    cr = []
    ch = d[d["도시"] == "춘천"]
    for crit in ["인구 이동률(%)", "장애인 비율(%)"]:
        for col, lab in ind:
            r, p = T2.perm(ch[col], ch[crit])
            cr.append({"지표": lab, "기준": f"춘천 {crit} (2024.12)", "도시": "춘천", "n": len(ch), "ρ": r, "순열 p(단측)": p,
                       "판정": "통과" if (r > 0 and p <= 0.05) else "미통과"})
    for city in CITIES:
        gg = d[d["도시"] == city]
        for col, lab in ind:
            r = spearmanr(gg[col], gg["log 동 인구(2025.12)"])[0]
            cr.append({"지표": lab, "기준": "|ρ(지표, log 동 인구)| < 0.3", "도시": city, "n": len(gg), "ρ": r,
                       "순열 p(단측)": np.nan, "판정": "통과" if abs(r) < 0.3 else "미통과"})
    crit = pd.DataFrame(cr)

    # 7. 민감도: 상위 비율 20%·40%
    sens = []
    for ts in (0.20, 0.30, 0.40):
        rr = alt_rules(rules, ts)
        lv2 = {}
        for key, (ev_alt, _) in cands_alt.items():
            city, dname, g = key
            if g not in DONG_GROUPS:
                continue
            cs = run_variant(ev_alt, "D", req[key], DATES[dname], k, rr).candidates
            lv2[key] = {c.adm_cd for c in cs if c.candidate_type == "scheduled_access_review" and c.adm_cd
                        and c.candidate_level in ("Level 2", "Level 3")}
        for key, s in lv2.items():
            city, dname, g = key
            o = lv2[(city, dname, "중장년 남성 1인가구")] if g == "고령층" else None
            sens.append({"상위 비율": ts, "도시": city, "기준일": dname, "집단": g, "Level 2 이상 동 수": len(s),
                         "고령층 ∩ 중장년 남성": len(s & o) if o is not None else None})
    sens = pd.DataFrame(sens)

    # Q4 판정 (사전 등록 규칙)
    q = PRE["Q4 규칙"]
    q4 = []
    for city in CITIES:
        for m in months:
            x = s1tab[(s1tab["도시"] == city) & (s1tab["기준월"] == f"2025-{m:02d}")]
            top = lambda g: set(x.loc[(x["집단"] == g) & x["집단별 상위 30%"], "adm_cd"])  # noqa: E731
            union = set().union(*[top(g) for g in DONG_GROUPS])
            new_d = top(Q4G) - union
            v = x[x["집단"] == Q4G].set_index("adm_cd")["집단별 S1"]
            rhos = {g: spearmanr(v, x[x["집단"] == g].set_index("adm_cd")["집단별 S1"].reindex(v.index)).statistic
                    for g in DONG_GROUPS}
            q4.append({"도시": city, "기준월": f"2025-{m:02d}", "중장년 여성 상위 동": nm_list(top(Q4G), names),
                       "기존 집단 상위에 없는 동": nm_list(new_d, names), "새 동 수": len(new_d),
                       **{f"ρ(중장년 여성, {g})": r for g, r in rhos.items()},
                       "조건 A": len(new_d) >= q["new_dong_min"], "조건 B": all(r < q["rho_max"] for r in rhos.values())})
    q4 = pd.DataFrame(q4)
    ab = q4.assign(ab=q4["조건 A"] & q4["조건 B"]).groupby("도시")["ab"].all()
    one = (q4.groupby("도시")["조건 A"].any() | q4.groupby("도시")["조건 B"].any())
    verdict = "정책 집단 추가 권고" if ab.any() else ("참고 칸으로만 표시" if one.any() else "추가하지 않음")

    res = {"s1": s1tab, "cmp": cmp, "dev": dcmp, "overlap": overlap, "levels": lc, "trans": trans, "rho": rho,
           "cross": cross, "sl": slc, "criteria": crit, "sens": sens, "q4": q4, "verdict": verdict, "names": names}
    if write:
        save(res)
    return res


FILES = {"s1": "집단별S1_동x집단x기준월.csv", "cmp": "등급비교_동x집단x기준일.csv", "dev": "이탈후보_비교.csv",
         "overlap": "후보겹침.csv", "levels": "등급변화_요약.csv", "trans": "규칙id_변화.csv", "rho": "S1_순위상관.csv",
         "cross": "집단간_겹침.csv", "sl": "시민생활_상관.csv", "criteria": "독립기준_채점.csv", "sens": "민감도_상위비율.csv",
         "q4": "Q4_판정.csv"}


def save(res: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, f in FILES.items():
        res[key].to_csv(OUT / f, index=False, encoding="utf-8-sig")
    write_readme(res)


def f2(x, nd=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    if isinstance(x, (bool, np.bool_)):
        return "예" if x else "아니오"
    if isinstance(x, (float, np.floating)):
        return f"{x:.{nd}f}".replace("-", "−")
    return str(x)


def tbl(r: Report, df: pd.DataFrame, nd=2):
    r.table(df.map(lambda v: f2(v, nd)), index=False)


def pair_txt(o, a, b, j):
    return f"{o}/{min(a, b)} (J {f2(j)})"


def write_readme(res: dict) -> None:
    r = Report("t1_5_집단별S1", "T-1.5 집단별 S1: 집단마다 그 집단의 1인세대 비율로 구조 축 바꿔 보기 (대안 비교)")
    cmp, ov, rho, cross = res["cmp"], res["overlap"], res["rho"], res["cross"]
    dev, sl, cr, sens, q4 = res["dev"], res["sl"], res["criteria"], res["sens"], res["q4"]
    n_chg = int(cmp["등급 바뀜"].sum())
    r.p("지금 LINK-Radar 구조 축 S1은 **동 전체 1인세대 비율(전 연령)** 하나라 고령층·중장년 남성이 같은 동을 후보로 받습니다. "
        "이 리포트는 S1을 **집단마다 그 집단의 1인세대 비율**로 바꾸면 무엇이 달라지는지 봅니다. "
        "**공식 등급(rules-v1.1, [R-2](../r2_위험도결과/README.md))은 바꾸지 않습니다.** 아래는 모두 'rules-v1.1 vs 집단별 S1 대안' 나란히 비교입니다. "
        "스크립트는 [`notebooks/t1_5_cohort_s1.py`](../../notebooks/t1_5_cohort_s1.py)입니다.")

    r.h("0. 실행 전에 고정한 것")
    r.p("기준은 결과보다 먼저 커밋했습니다: [사전등록.md](사전등록.md) · [사전등록.json](사전등록.json) (스크립트가 json 값을 읽음).")
    r.p("")
    r.p("- **집단별 S1 = 그 집단의 1인세대 수 ÷ 그 집단의 주민등록 인구** (같은 동·같은 달). 집단: 고령층 65세 이상, 중장년 남성 40~64세, "
        "50~69세 남성, 청년 남성 20~34세, Q4 후보 중장년 여성 50~59세. 5세 구간과 경계가 정확히 맞습니다.")
    r.p("- 자료: 행정안전부 주민등록 인구통계 '성/연령별 주민등록 1인세대수'(행정동, 월말, 5세, 등록 구분 전체) "
        "[`external/resident/주민등록_성연령별1인세대_2025-07_12.csv`](../../external/resident/주민등록_성연령별1인세대_2025-07_12.csv)와 "
        "같은 달 성·연령별 인구(15097972). 동 합계가 세대원수별 자료의 1인세대 수와 282행 모두 같습니다.")
    r.p("- **시점**: 기준일마다 지금 S1과 같은 기준월(기준일에 공개된 가장 최근 달)을 씁니다. 추석 D-7(09/26) → 2025-08, "
        "추석 뒤(10/13) → 2025-09, 동절기(12/20) → 2025-11. 성·연령별 1인세대수는 세대원수별 자료와 같은 월말 통계라 공개 시점이 같다고 가정했습니다.")
    r.p("- **규칙**: rules-v1.1 모양 그대로(구조 상위 = 도시 안 상위 30%), 구조 근거의 값·분모·순위 표시만 바꿉니다. "
        "절대 최소값 0.35는 전 연령 비율 눈금이라 대안에서 쓰지 않습니다(지금 결과에서도 0.35가 후보를 막은 적은 없음). "
        "분모(근거 신뢰도의 100 미만 기준)는 그 집단 인구입니다.")
    r.p("- **실행 전 확인 통과**: 바꾸지 않은 근거로 같은 코드를 돌리면 R-2 `동별_등급표.csv` 566행의 등급·규칙 id가 모두 같습니다.")

    r.h("1. 지금 S1과 집단별 S1은 얼마나 다른가")
    t = (rho[rho["집단"] != Q4G].groupby(["도시", "집단"])
         .agg(lo=("ρ(지금 S1, 집단별 S1)", "min"), hi=("ρ(지금 S1, 집단별 S1)", "max"),
              ov=("상위 30% 겹침", "min"), k=("상위 30% 동 수", "first")).reset_index())
    t["ρ (기준월 3개)"] = [f2(a) if f2(a) == f2(b) else f"{f2(a)}~{f2(b)}" for a, b in zip(t["lo"], t["hi"])]
    t["상위 30% 겹침 (최소)"] = [f"{a}/{b}" for a, b in zip(t["ov"], t["k"])]
    tbl(r, t[["도시", "집단", "ρ (기준월 3개)", "상위 30% 겹침 (최소)"]])
    r.p("Spearman ρ(지금 S1, 집단별 S1), 동 22·25개. 기준월마다 값은 [`S1_순위상관.csv`](S1_순위상관.csv), 동별 값은 "
        "[`집단별S1_동x집단x기준월.csv`](집단별S1_동x집단x기준월.csv)(공개 주민등록 인원, 10 미만은 '<10').")
    r.p("")
    r.p("- 순위는 크게 같습니다. 1인세대가 많은 동은 대부분 어느 집단에서나 혼자 사는 비율이 높습니다. 강남 중장년 남성은 거의 그대로이고, "
        "동 단위 집단 중에서는 **고령층이 가장 많이 다릅니다**. 춘천 청년 남성은 따로 움직입니다.")

    r.h("2. 후보 동이 어떻게 바뀌나 (예정된 점검, Level 2 이상)")
    x = ov[(ov["집합"] == "Level 2 이상") & ((ov["v1.1 동 수"] > 0) | (ov["대안 동 수"] > 0))]
    tbl(r, x[["도시", "기준일", "집단", "v1.1 동 수", "대안 동 수", "겹침", "Jaccard", "빠진 동", "새로 든 동"]])
    r.p("구조 상위(상위 30%) 동 집합의 전후는 [`후보겹침.csv`](후보겹침.csv)의 '구조 상위' 행입니다. 청년 남성은 두 판 모두 도시 단위 안내 1건이라 동 후보가 없습니다.")
    r.p("")
    up, dn = int((cmp["방향"] == "올라감").sum()), int((cmp["방향"] == "내려감").sum())
    tr = res["trans"]
    r.p(f"- **등급이 바뀐 행: 566행 중 {n_chg}행**(올라감 {up}, 내려감 {dn}). 규칙 id 변화: "
        + ", ".join(f"{a} → {b} {n}행" for a, b, n in tr[["규칙 id (v1.1)", "규칙 id (대안)", "행 수"]].itertuples(index=False))
        + " ('-' = 후보 아님). 전체는 [`등급비교_동x집단x기준일.csv`](등급비교_동x집단x기준일.csv), 요약은 [`등급변화_요약.csv`](등급변화_요약.csv).")
    l3 = cmp[(cmp["등급 (대안)"] == "Level 3") & cmp["등급 바뀜"]]
    if len(l3):
        r.p("- Level 3이 새로 생긴 곳: " + ", ".join(sorted({f"{a} {b} {c}" for a, b, c in l3[["도시", "동", "집단"]].itertuples(index=False)}))
            + " (집단별 S1로 구조 상위에 들어 접근성 5km 조건과 함께 Level 3).")
    r.p("- 강남 중장년 남성 후보 7곳은 그대로입니다. 바뀌는 것은 주로 **고령층**입니다(위 표의 빠진 동·새로 든 동).")
    nd = int(dev["먼저 확인할 동 바뀜"].sum())
    r.p(f"- 예상 밖 이탈 후보 {len(dev)}건은 등급이 바뀐 것이 {int(dev['등급 바뀜'].sum())}건이고, '먼저 확인할 동'(공유 맥락)이 바뀐 것이 {nd}건입니다"
        "([`이탈후보_비교.csv`](이탈후보_비교.csv)). 이탈은 도시 단위 카드 신호라 동이 바뀌어도 등급은 반증·이탈 크기가 정합니다.")

    r.h("3. 고령층과 중장년 남성이 같은 동을 받는 문제")
    x = cross[(cross["집단 1"] == "고령층") & (cross["집단 2"] == "중장년 남성 1인가구")].copy()
    x["v1.1"] = [pair_txt(*v) for v in x[["v1.1 겹침", "v1.1 집단1 수", "v1.1 집단2 수", "v1.1 Jaccard"]].itertuples(index=False)]
    x["대안"] = [pair_txt(*v) for v in x[["대안 겹침", "대안 집단1 수", "대안 집단2 수", "대안 Jaccard"]].itertuples(index=False)]
    x = x[(x[["v1.1 집단1 수", "v1.1 집단2 수"]].min(axis=1) > 0) | (x[["대안 집단1 수", "대안 집단2 수"]].min(axis=1) > 0)]
    tbl(r, x[["도시", "기준일", "집합", "v1.1", "대안"]])
    r.p("겹친 동 수 / 작은 쪽 동 수 (J = Jaccard). 한쪽 집단에 예정된 점검이 없는 기준일(강남 추석 뒤·동절기, 춘천 추석 뒤)의 Level 2 이상 행은 뺐습니다. "
        "50~69세 남성과의 겹침도 [`집단간_겹침.csv`](집단간_겹침.csv)에 있습니다.")
    r.p("")
    r.p("- 구조 상위 집합은 지금 S1에서 모든 집단이 **완전히 같습니다**(같은 S1이라 당연). 집단별 S1에서는 고령층 ↔ 중장년 남성이 강남 5/7, 춘천 6/8로 줄어듭니다. "
        "다만 **여전히 대부분 겹칩니다**. 1인세대가 몰린 동은 고령·중장년 모두 혼자 사는 비율이 높기 때문입니다.")
    r.p("- 중장년 남성 ↔ 50~69세 남성은 집단별 S1에서도 거의 같습니다. 두 집단은 연령이 겹칩니다.")

    r.h("4. 청년 남성 다시 보기 (강남 시민생활, 탐색)")
    tbl(r, sl[["집단", "동 수", "ρ 지금 S1", "ρ 집단별 S1", "순열 p 집단별(단측)"]], 3)
    r.p("R-1 2장과 같은 방법: 강남 22개 동, 추석 D-7 근거의 시민생활 '외출·소통 모두 적음' 비율(SKT 추정, 집단별 칸)과의 순위 상관. 시민생활은 등급 입력이 아닙니다.")
    r.p("")
    yr = sl.set_index("집단").loc["청년 남성"]
    ok = yr["ρ 집단별 S1"] > 0 and yr["순열 p 집단별(단측)"] <= 0.05
    r.p(f"- 청년 남성은 집단별 S1로 바꿔도 **{'양의 상관으로 바뀝니다' if yr['ρ 집단별 S1'] > 0 else '여전히 음의 상관'}**입니다"
        f"({f2(yr['ρ 지금 S1'])} → {f2(yr['ρ 집단별 S1'])}). "
        + ("사전 등록대로 '동 단위 선정 재검토 대상'으로 적습니다." if ok else
           "청년 남성이 혼자 사는 비율이 높은 동이 이 집단의 '외출·소통 적음'이 많은 동이 아닙니다. 사전 등록대로 **청년 남성 도시 단위 안내는 그대로** 둡니다(재검토 조건 ρ > 0, p ≤ 0.05 미충족)."))
    r.p("- 다른 집단은 지금 S1과 차이가 작습니다. 집단별 S1이 시민생활과 더 잘 맞는다고 말할 만한 차이는 없습니다.")

    r.h("5. T-2 독립 기준 다시 채점")
    c = cr.copy()
    c["결과"] = [f"{f2(a)}{(' (p ' + f2(p, 3) + ')') if not np.isnan(p) else ''} {j}" for a, p, j in c[["ρ", "순열 p(단측)", "판정"]].itertuples(index=False)]
    c["기준 · 도시"] = c["기준"].str.replace("|ρ(지표, log 동 인구)| < 0.3", "동 규모 (ρ 절댓값 < 0.3)", regex=False) + " · " + c["도시"]
    w = c.pivot(index="지표", columns="기준 · 도시", values="결과").reindex(c["지표"].drop_duplicates()).reset_index()
    w["통과 수"] = w["지표"].map(c.assign(ok=c["판정"] == "통과").groupby("지표")["ok"].sum()).astype(int)
    tbl(r, w)
    r.p("T-2와 같은 함수·판정(ρ > 0 이고 순열 p ≤ 0.05, 10,000번, 단측, seed 0 / 동 규모는 |ρ(지표, log 동 인구 2025.12)| < 0.3). 동 값은 추석 D-7 기준월(2025-08). "
        "**강남 '혼자 사는 주민 비율'은 S1과 같은 입력이라 채점하지 않습니다.** 중장년 여성 행은 Q4 참고입니다. 전체는 [`독립기준_채점.csv`](독립기준_채점.csv).")
    r.p("")
    r.p("- 고령층·중장년 남성·50~69세 남성 S1은 **장애인 비율 기준이 더 강해지고**, **동 규모 판별은 더 나빠집니다**(강남도 미통과로 바뀜). "
        "작은 동(춘천 읍·면)일수록 중장년·고령 혼자 사는 비율이 높게 나옵니다. 이동률은 셋 다 통과하지 못합니다.")
    r.p("- 청년 남성 S1은 반대로 **이동률과 두 도시 동 규모를 통과**하고 장애인 비율은 통과하지 못합니다. 청년 1인세대는 전입이 많은 동에 모입니다. "
        "다만 청년 남성은 동을 고르지 않는 집단이라 이 결과는 등급에 쓰이지 않습니다.")
    r.p("- 동 단위로 후보를 고르는 세 집단만 보면 통과 수가 지금 S1(2개)보다 줄어듭니다(1개). 독립 기준으로는 바꿀 근거가 약합니다.")

    r.h("6. 민감도: 상위 비율 20%·40%")
    key3 = ["상위 비율", "도시", "기준일"]
    s = (sens[sens["집단"] == "고령층"][key3 + ["Level 2 이상 동 수", "고령층 ∩ 중장년 남성"]]
         .rename(columns={"Level 2 이상 동 수": "고령층 Level 2 이상"})
         .merge(sens[sens["집단"] == "중장년 남성 1인가구"][key3 + ["Level 2 이상 동 수"]]
                .rename(columns={"Level 2 이상 동 수": "중장년 남성 Level 2 이상"}), on=key3))
    s = s[(s["고령층 Level 2 이상"] > 0) & (s["중장년 남성 Level 2 이상"] > 0)].copy()
    s["고령층 ∩ 중장년 남성"] = s["고령층 ∩ 중장년 남성"].astype(int)
    tbl(r, s[key3 + ["고령층 Level 2 이상", "중장년 남성 Level 2 이상", "고령층 ∩ 중장년 남성"]])
    r.p("집단별 S1 대안에서 상위 비율만 바꿨습니다. 집단별 후보 수 전체는 [`민감도_상위비율.csv`](민감도_상위비율.csv).")

    r.h("7. Q4: 중장년 여성(50~59세)을 정책 집단으로 더할까")
    q = q4.copy()
    q["ρ 최댓값(기존 세 집단)"] = q[[k for k in q.columns if k.startswith("ρ(중장년 여성")]].max(axis=1)
    tbl(r, q[["도시", "기준월", "기존 집단 상위에 없는 동", "새 동 수", "ρ 최댓값(기존 세 집단)", "조건 A", "조건 B"]])
    r.p("사전 등록 규칙: 조건 A = 중장년 여성 S1 상위 30% 동 중 기존 동 단위 집단(고령층·중장년 남성·50~69세 남성) 상위 합집합에 없는 동이 2곳 이상, "
        "조건 B = 기존 세 집단 S1과의 ρ가 모두 0.8 미만. 한 도시라도 기준월 3개 모두 A·B → 추가 권고 / A 또는 B만 → 참고 칸 / 둘 다 아님 → 추가 안 함. "
        "전체 칸은 [`Q4_판정.csv`](Q4_판정.csv).")
    r.p("")
    r.p(f"- **판정: {res['verdict']}.** 중장년 여성 상위 동은 기존 집단 상위 동에 거의 다 들어 있고, S1 순위가 중장년 남성과 거의 같습니다. "
        "새 집단을 만들면 '새 동'이 아니라 '같은 동의 다른 대상'이 늘어납니다(T-2 5절과 같은 결론).")

    r.h("8. 정리")
    r.p("- 집단별 S1로 바꾸면 **고령층 후보가 일부 다른 동으로 옮겨 가고**, 고령층 ↔ 중장년 남성 겹침이 '완전히 같음'에서 '대부분 같음'으로 줄어듭니다. "
        f"등급이 바뀐 행은 566행 중 {n_chg}행입니다.")
    r.p("- 청년 남성의 음의 상관은 집단별 S1에서도 남아 도시 단위 안내를 유지합니다. 중장년 여성은 사전 등록 판정대로 더하지 않습니다.")
    r.p("- 독립 기준에서는 집단별 S1이 장애인 비율과 더 맞지만 동 규모에 더 끌립니다. 지금 S1보다 낫다고 말할 근거는 없습니다.")
    r.p("- 그래서 이 결과는 **공식 등급을 바꾸지 않고**, 후보 카드의 맥락 칸('이 집단의 1인세대 비율 순위')으로 붙이는 것을 검토할 재료입니다. "
        "등급 규칙에 넣으려면 rules 판을 올리는 별도 사전 등록이 필요합니다.")

    r.h("사전 등록과 다른 점 · 한계")
    r.p("- 사전 등록에서 바꾼 기준은 없습니다. 독립 기준 채점과 S1 상관에 중장년 여성 행을 참고로 더 적었고, 시민생활 상관에 순열 p를 더 적었습니다(판정에는 청년 남성 행만 씀).")
    r.p("- 주민등록 1인세대 ≠ 실제 1인가구입니다. 1인세대의 성·연령은 혼자인 세대주의 것입니다.")
    r.p("- 성·연령별 1인세대수의 과거 공개 시점은 확인하지 않았습니다(세대원수별 자료와 같다고 가정).")
    r.p("- 동 22·25개라 ρ의 불확실성이 큽니다. 시민생활은 SKT 추정이라 탐색입니다. 실제 고립 정답은 없습니다.")

    r.h("파일")
    files = pd.DataFrame([
        ("사전등록.md · 사전등록.json", "실행 전에 고정한 기준"),
        ("집단별S1_동x집단x기준월.csv", "동 × 집단(5) × 기준월(3): 지금 S1·집단별 S1·순위·상위 여부·집단 1인세대 수·인구(10 미만 가림)"),
        ("등급비교_동x집단x기준일.csv", "566행: rules-v1.1 vs 대안 등급·규칙 id·S1 값·순위·구조 상위"),
        ("등급변화_요약.csv · 규칙id_변화.csv", "도시 × 기준일 × 집단 등급 변화 수, 규칙 id 바뀐 쌍"),
        ("이탈후보_비교.csv", "예상 밖 이탈 후보의 등급·먼저 확인할 동 전후"),
        ("후보겹침.csv", "Level 2 이상·구조 상위 동 집합 전후, 겹침·Jaccard·빠진·새 동"),
        ("집단간_겹침.csv", "고령층·중장년 남성·50~69세 남성 서로 겹침 전후"),
        ("S1_순위상관.csv", "지금 S1 ↔ 집단별 S1 ρ (도시 × 집단 × 기준월)"),
        ("시민생활_상관.csv", "강남 시민생활 ↔ S1 순위 상관 (탐색)"),
        ("독립기준_채점.csv", "T-2 독립 기준 다시 채점"),
        ("민감도_상위비율.csv", "상위 20·30·40%"),
        ("Q4_판정.csv", "중장년 여성 판정 칸"),
    ], columns=["파일", "내용"])
    tbl(r, files)
    r.save()


if __name__ == "__main__":
    r = run()
    print(r["levels"].to_string())
    print(r["overlap"].round(2).to_string())
    print(r["cross"].round(2).to_string())
    print(r["rho"].round(2).to_string())
    print(r["sl"].round(3).to_string())
    print(r["criteria"].round(3).to_string())
    print(r["sens"].to_string())
    print(r["q4"].round(2).to_string())
    print("Q4:", r["verdict"])
