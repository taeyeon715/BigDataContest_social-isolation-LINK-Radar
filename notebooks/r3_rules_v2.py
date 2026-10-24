"""R-3 rules-v2.0 결과: 470칸 × 기준일 3개 등급, v1.1과 비교 (reports/r3_규칙개정/변경계획.md).

칸 판정은 에이전트와 같은 함수를 쓴다 (holiday_watch.cell_facts·winter_facts, agent.tools.cell_axes.axes_at).
등급 근거는 신한·SK 신호(H·W·C), 공공 자료(S·V·A)는 Level 3 확인용 (README 9절). 방향 미확인 신호는 configs/signal_check.csv (10절).

순서
0. 칸 단위 H 방향 확인 (설계 7절): 강남 시민생활 '외출·소통 모두 적음' 비율과 H의 칸 단위 순위상관을 성·연령마다.
   ρ < 0 인 성·연령이 절반 이상이면 H를 근거 축에서 빼고 맥락으로만 둔다 (결과 전 고정 규칙). 성·연령별 확인은 r3c.
1. 기준일마다 그날 공개된 달로 S·H·V·A(axes_at)와 읍·면 겨울 변화 W, 지난 연휴 카드 값 C
2. 트리거·반증·이탈 크기: LINK-Radar 근거 수집(rules.compare.collect)을 v2.0 카드 경보 기준(z −2.0, 7일 중 4일)으로
3. 칸마다 decide_v2, 위험 유형
4. v1.1(R-2 동별_등급표)과 비교: 집단 차별화, 동 규모 판별, 독립 기준, 등급 분포, 바뀐 후보, 도시별 Level 3
5. 민감도: 상위 비율 0.25·0.30·0.40, 거점 3·7km
6. 태연 분석 보완 확인 (설계 7절): 지역 나눠 H, 월별 순위 안정성

실제 2025 자료 결과는 모두 탐색이다(이미 본 자료로 설계를 고쳤다). 확인은 다음 명절·새 기간 자료로 남긴다.
"""

from __future__ import annotations

import warnings
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bigcontest.signals import alarm as AL

V2 = {"z_thr": -2.0, "k": 4}
AL.Panel.__init__.__defaults__ = (AL.AlarmConfig(**V2),)  # 이 실행 안에서만 v2.0 경보 기준 (rules_v2.yaml deviation)

from bigcontest import holiday_watch as HW  # noqa: E402
from bigcontest.agent.tools import signal as SIG  # noqa: E402
from bigcontest.agent.tools.cell_axes import axes_at  # noqa: E402
from bigcontest.agent.tools.common import daily_cutoff, usable_months  # noqa: E402
from bigcontest.agent.tools.datasource import dev_snapshot_id, open_datasource  # noqa: E402
from bigcontest.cells import cell_index, s_axis  # noqa: E402
from bigcontest.config import ROOT  # noqa: E402
from bigcontest.contracts import cohorts_config  # noqa: E402
from bigcontest.external import resident_single_by_age, resident_population, seoul_living  # noqa: E402
from bigcontest.rules.compare import collect, ctx_for, run_variant  # noqa: E402
from bigcontest.rules.levels_v2 import CellFacts, axes_on, cell_type, decide_v2, load_rules_v2  # noqa: E402
from bigcontest.stay import h_axis  # noqa: E402
from bigcontest.vulnerability import v_axis  # noqa: E402
from bigcontest.winter import winter_by_area  # noqa: E402

warnings.filterwarnings("ignore", category=RuntimeWarning)
OUT = ROOT / "reports" / "r3_규칙개정"
R2 = ROOT / "reports" / "r2_위험도결과"
SID = dev_snapshot_id()
DS = open_datasource(SID)
RV2 = load_rules_v2()
MODE = "simulated_replay"
CITIES = ["강남", "춘천"]
DATES = {"추석 D-7": date(2025, 9, 26), "추석 뒤": date(2025, 10, 13), "동절기": date(2025, 12, 20)}
GROUPS = list(cohorts_config()["groups"])
GROUP_CELLS = {  # 정책 집단 → 칸 (설계 1절)
    "고령층": [("남", "60대+"), ("여", "60대+")],
    "중장년 남성 1인가구": [("남", "40대"), ("남", "50대")],
    "50~69세 남성": [("남", "50대"), ("남", "60대+")],
    "청년 남성": [("남", "20대"), ("남", "30대")],
}
CARD_AGE = {"20대": "20대", "30대": "30대", "40대": "40대", "50대": "50대", "60대": "60대+", "70대": "60대+", "80대+": "60대+"}
SEX = {"M": "남", "F": "여", "남성": "남", "여성": "여"}
LEVELS = ["Level 3", "Level 2", "Level 1", "Level 0", "판단 보류"]
RANK = {"후보 아님": 0, "Level 0": 0, "Level 1": 1, "Level 2": 2, "Level 3": 3, "판단 보류": -1}
names = DS.adm_names().set_index("adm_cd")

POP = resident_population()
ONE = resident_single_by_age()
TEL = {n: DS.tel(n) for n in ("tel_age", "tel_time", "tel_wkdy")}


def month_of(source: str, d: date) -> list[str]:
    return [m.base_month for m in usable_months(source, d, MODE)]


# ------------------------------------------------------------ 0. H 방향 확인 (설계 7절)
H_FULL = h_axis(TEL["tel_age"], TEL["tel_time"], POP, [7, 8, 9, 10, 11, 12])
sl = seoul_living()
sl = sl[sl["iso_all"].notna() & sl["pop"].gt(0)].copy()
sl["age5"] = sl["age"].map(lambda a: None if a < 20 else ("60대+" if a >= 60 else f"{a // 10 * 10}대"))
slc = sl.dropna(subset=["age5"]).groupby(["adm_cd", "sex", "age5"])[["iso_all", "pop"]].sum()
slc["iso_rate"] = slc["iso_all"] / slc["pop"]
hd = H_FULL[H_FULL["city"] == "강남"].set_index(["adm_cd", "sex", "age5"]).join(slc["iso_rate"], how="inner")
hdir = []
for (sx, a), g in hd.groupby(level=["sex", "age5"]):
    g = g.dropna(subset=["h", "iso_rate"])
    r, p = spearmanr(g["h"], g["iso_rate"])
    hdir.append({"성": sx, "연령": a, "동 수": len(g), "ρ(H, 외출·소통 모두 적음)": r, "p": p})
hdir = pd.DataFrame(hdir)
H_OPPOSITE = int((hdir["ρ(H, 외출·소통 모두 적음)"] < 0).sum())
H_ON = H_OPPOSITE < len(hdir) / 2
print(f"H 방향: 반대 {H_OPPOSITE}/{len(hdir)} → H {'근거 축으로 씀' if H_ON else '맥락으로 내림'}")

# ------------------------------------------------------------ 1. 기준일별 축
def axes_for(dname: str, d: date) -> pd.DataFrame:
    """에이전트와 같은 470칸 축(axes_at: 거점 거리는 칸 연령의 정책 집단 자격 거리) + 읍·면 겨울 변화."""
    at = axes_at(DS, d, MODE)
    res_m, tel_m = at.res_month, list(at.tel_months)
    df = at.df.copy()
    w = winter_by_area(*TEL.values(), month_of("tel_age", d))
    wk = w.set_index("adm_cd") if len(w) else pd.DataFrame(columns=["w60", "w_ok", "shared"])
    df["w60"] = df["adm_cd"].map(wk["w60"]) if len(w) else np.nan
    df["w_ok"] = df["adm_cd"].map(wk["w_ok"]) if len(w) else np.nan
    df["w_shared"] = df["adm_cd"].map(wk["shared"]) if len(w) else np.nan
    df["res_month"], df["tel_months"] = res_m, "·".join(map(str, tel_m))
    df["기준일"], df["as_of"] = dname, d
    return df


AX = {dn: axes_for(dn, d) for dn, d in DATES.items()}

# 카드 C: 기준일에 결제를 다 볼 수 있는 지난 연휴의 60대 이상 의료 결제, 같은 성 40·50대 대비 (get_holiday_watch 와 같음)
BLOCKS = SIG.holiday_blocks(DS)
CARD, CARD_ROWS = {}, []
for city in CITIES:
    for dname, d in DATES.items():
        cut = daily_cutoff("card2", d, MODE)
        periods = HW.card_periods(BLOCKS, DS.holiday_names(), cut) if cut else []
        gap = HW.card_gap(DS.card2(), city, periods[-1], DS.holidays()) if periods else None
        CARD[(city, dname)] = {} if gap is None else {r["sex"][0]: r["rel"] for _, r in gap.iterrows()}
        if gap is not None:
            CARD_ROWS += [{"도시": city, "기준일": dname, "참고 연휴": f"{r['name']} ({r['start']:%m/%d}~{r['end']:%m/%d})",
                           "성": r["sex"], "연령": r["age"], "평소 대비": r["ratio"], "40·50대 대비": r["rel"]}
                          for _, r in gap.iterrows()]
HOLIDAY = ("R1", "R2", "R3")
TARGET = HW.watch_config(RV2)["target_ages"]

# ------------------------------------------------------------ 2. 트리거·반증·이탈 (v2.0 경보 기준으로 근거 수집)
TRIG, DEVS = {}, []
for city in CITIES:
    for dname, d in DATES.items():
        for g in GROUPS:
            c = ctx_for(SID, city, d, g)
            ev = collect(c)
            trig = next((e.value for e in ev if SIG.input_of(e) == "calendar_trigger" and e.status == "OK"), None)
            TRIG[(city, dname, g)] = trig
            for cand in run_variant(ev, "D", c.request, d, 99).candidates:
                if cand.candidate_type != "unexpected_activity_deviation":
                    continue
                size = next((rc.value for rc in cand.rule_checks if rc.condition == "이탈 크기 최소값"), None)
                DEVS.append({"도시": city, "기준일": dname, "집단": g, "분석 코호트": cand.analysis_cohort,
                             "반증 결과": cand.counter_result, "반증 사유": cand.counter_reason or "-",
                             "연휴 이동": (cand.rule_id or "").startswith("HOLD-HOLIDAY-TRAVEL"),
                             "이탈 크기": size if isinstance(size, float) else None})
DEV = pd.DataFrame(DEVS).drop_duplicates(["도시", "기준일", "분석 코호트"])  # 같은 코호트가 여러 집단에서 겹침
print("근거 수집 끝, 이탈 후보", len(DEV))


def cohort_cell(label: str) -> tuple[str, str] | None:
    """'60대 여성' → ('여', '60대+')."""
    parts = label.split()
    if len(parts) != 2:
        return None
    a, s = parts
    return (SEX.get(s), CARD_AGE.get(a))


# ------------------------------------------------------------ 3. 칸마다 판정
def cell_trigger(city: str, dname: str, row) -> str | None:
    """정책 집단마다 받은 트리거를 칸으로 옮긴다: 모든 집단이 받은 트리거 = 도시 전체 칸, 일부 집단만 = 그 집단 칸.
    동절기(W)는 설계 3절대로 춘천 읍·면 60대 이상 칸만."""
    got = {g: TRIG[(city, dname, g)] for g in GROUPS}
    tr = [t for t in got.values() if t]
    if not tr:
        return None
    t = tr[0]
    if str(t).startswith("W"):
        return t if (row["age5"] == "60대+" and names.loc[row["adm_cd"], "adm_type"] in ("읍", "면")) else None
    if all(got[g] == t for g in GROUPS):
        return t
    mine = [g for g, tt in got.items() if tt and (row["sex"], row["age5"]) in GROUP_CELLS[g]]
    return got[mine[0]] if mine else None


def facts_for(row, trig, dname: str) -> CellFacts:
    """에이전트 v2_levels._cell_decisions 와 같음: 카드 C는 연휴 트리거의 연휴 연령 칸만, 겨울 W는 동절기 칸만."""
    code = str(trig or "")[:2]
    if code.startswith("W"):
        w = None if pd.isna(row["w_ok"]) else pd.Series(
            {"w_ok": bool(row["w_ok"]), "w60": row["w60"], "shared": bool(row["w_shared"])})
        f = HW.winter_facts(row, w, trig, RV2)
    else:
        gap = CARD[(row["city"], dname)].get(row["sex"]) if code in HOLIDAY and row["age5"] in TARGET else None
        f = HW.cell_facts(row, trig or "-", RV2, gap)
    if not H_ON:
        f.axes.pop("H", None)
    return f


rows = []
for dname, df in AX.items():
    for _, r in df.iterrows():
        trig = cell_trigger(r["city"], dname, r)
        f = facts_for(r, trig, dname)
        on = axes_on(f)
        base = {"도시": r["city"], "기준일": dname, "adm_cd": r["adm_cd"], "동": names.loc[r["adm_cd"], "adm_nm"],
                "동 유형": names.loc[r["adm_cd"], "adm_type"], "성": r["sex"], "연령": r["age5"],
                "칸 인구": r["pop"], "S 1인 비율(수축)": r["s_eb"], "S 섬": r["s_top"], "H": r["h"], "H 섬": r["h_top"],
                "H 근거 축": H_ON, "H 기준": r["h_basis"], "V(수축)": r["v_eb"], "V 섬(공개일 반영)": r["v_counts"],
                "V 출처": r["v_source"], "A 거점 거리(km)": r["a_km"], "A 섬": r["a_top"], "W 겨울 변화": r["w60"],
                "W 섬": f.axes.get("W"), "C 섬": f.axes.get("C"), "방향 미확인": ",".join(f.unverified) or "-",
                "선 축": "+".join(on) or "-", "위험 유형": cell_type(on), "트리거": trig or "-",
                "주민등록 기준월": r["res_month"], "통신 달": r["tel_months"]}
        if trig:
            d = decide_v2(f, RV2)
            rows.append({**base, "등급": d.level, "규칙 id": d.rule_id, "상한": ",".join(d.caps) or "-",
                         "입력 상태": f.input_status, "근거 신뢰도": f.confidence, "사유": d.reason})
        else:
            rows.append({**base, "등급": "후보 아님", "규칙 id": "-", "상한": "-", "입력 상태": "-", "근거 신뢰도": "-", "사유": "트리거 없음"})
CELLS = pd.DataFrame(rows)

# 이탈 후보 (도시 × 코호트, Level 2 상한): 먼저 확인할 동 = 같은 성·연령 칸의 행동 고립 상위 (에이전트 relevel_deviation)
drows = []
for _, r in DEV.iterrows():
    sx, a = cohort_cell(r["분석 코호트"]) or (None, None)
    ax = AX[r["기준일"]]
    g = ax[(ax["city"] == r["도시"]) & (ax["sex"] == sx) & (ax["age5"] == a)
           & ax["h_top"].fillna(False).astype(bool) & ~ax["s_small"]].copy()
    g["ext"] = g["s_top"].astype(int) + g["v_counts"].astype(int) + g["a_top"].astype(int)
    first = g.sort_values(["ext", "h"], ascending=[False, False])["adm_cd"].head(3).tolist()
    f = CellFacts("unexpected_activity_deviation", counter_result=r["반증 결과"], counter_reason=r["반증 사유"],
                  holiday_travel=bool(r["연휴 이동"]), deviation_drop=r["이탈 크기"], axes={"H": bool(first)})
    d = decide_v2(f, RV2)
    drows.append({**r.to_dict(), "칸": f"{sx} {a}", "등급": d.level, "규칙 id": d.rule_id, "사유": d.reason,
                  "먼저 확인할 동(행동 고립 상위, 최대 3)": ", ".join(names.loc[first, "adm_nm"]) or "-"})
DEVT = pd.DataFrame(drows)

# ------------------------------------------------------------ 4. v1.1 과 비교
v11 = pd.read_csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str})
v11["dname"] = v11["기준일"].str.split(" \\(").str[0]
cmp_rows = []
for _, r in v11[v11["adm_cd"].notna()].iterrows():
    cs = GROUP_CELLS[r["집단"]]
    sub = CELLS[(CELLS["기준일"] == r["dname"]) & (CELLS["adm_cd"] == r["adm_cd"])
                & CELLS[["성", "연령"]].apply(tuple, axis=1).isin(cs)]
    best = max(sub["등급"], key=lambda x: RANK.get(x, 0)) if len(sub) else "후보 아님"
    cmp_rows.append({"도시": r["도시"], "기준일": r["dname"], "집단": r["집단"], "동": r["동"], "adm_cd": r["adm_cd"],
                     "v1.1 등급": r["등급"], "v1.1 규칙": r["규칙 id"], "v2.0 등급(집단 칸 중 최고)": best,
                     "v2.0 선 축": "; ".join(f"{a} {b}: {x}" for a, b, x in zip(sub["성"], sub["연령"], sub["선 축"]))})
CMP = pd.DataFrame(cmp_rows)
CMP["바뀜"] = CMP["v1.1 등급"].map(RANK) != CMP["v2.0 등급(집단 칸 중 최고)"].map(RANK)


def l2plus(lv):
    return RANK.get(lv, 0) >= 2


# 4-1 집단 차별화: 정책 집단별 Level 2+ 동 중 고령층 Level 2+ 동과 같은 비율
diff = []
for (city, dn), g in CMP.groupby(["도시", "기준일"]):
    for ver, col in (("v1.1", "v1.1 등급"), ("v2.0", "v2.0 등급(집단 칸 중 최고)")):
        old = set(g[(g["집단"] == "고령층") & g[col].map(l2plus)]["adm_cd"])
        for grp in GROUPS:
            if grp == "고령층":
                continue
            s = set(g[(g["집단"] == grp) & g[col].map(l2plus)]["adm_cd"])
            old3 = set(g[(g["집단"] == "고령층") & g[col].eq("Level 3")]["adm_cd"])
            s3 = set(g[(g["집단"] == grp) & g[col].eq("Level 3")]["adm_cd"])
            diff.append({"도시": city, "기준일": dn, "판": ver, "집단": grp, "Level 2+ 동": len(s),
                         "Level 2+ 고령층과 같은 동 비율": (len(s & old) / len(s)) if s else None,
                         "Level 3 동": len(s3), "Level 3 고령층과 같은 동 비율": (len(s3 & old3) / len(s3)) if s3 else None})
DIFF = pd.DataFrame(diff)

# 4-2·4-3 동 규모 판별, 독립 기준 (동 단위: 칸 값의 인구 가중 평균)
t2 = pd.read_csv(ROOT / "reports" / "t2_교차검증" / "동단위_지표.csv", dtype={"SGIS코드": str}).rename(columns={
    "SGIS코드": "adm_cd", "인구 이동률(%)": "춘천 인구 이동률(%)", "장애인 비율(%)": "춘천 장애인 비율(%)", "LINK-Radar S1": "S1 1인세대 비율"})
crit = []
for dn in DATES:
    ax = AX[dn].copy()
    ax["w"] = ax["pop"].fillna(0)
    dong = ax.groupby("adm_cd").apply(lambda g: pd.Series({
        "S(칸 평균)": np.average(g["s_eb"].fillna(g["s_eb"].mean()), weights=g["w"] + 1e-9),
        "H(칸 평균)": np.average(g["h"].fillna(g["h"].mean()), weights=g["w"] + 1e-9),
        "log 동 인구": np.log(g["pop"].sum())}))
    dong = dong.join(t2.set_index("adm_cd")[[c for c in t2.columns if c in ("춘천 인구 이동률(%)", "춘천 장애인 비율(%)", "S1 1인세대 비율")]], how="left")
    dong["city"] = dong.index.map(names["city"])
    for city in CITIES:
        d = dong[dong["city"] == city]
        for x in ["S(칸 평균)", "H(칸 평균)", "S1 1인세대 비율"]:
            if x not in d:
                continue
            row = {"기준일": dn, "도시": city, "지표": x, "ρ(log 동 인구)": spearmanr(d[x], d["log 동 인구"], nan_policy="omit")[0]}
            if city == "춘천":
                for y in ("춘천 인구 이동률(%)", "춘천 장애인 비율(%)"):
                    if y in d:
                        row[f"ρ({y})"] = spearmanr(d[x], d[y], nan_policy="omit")[0]
            crit.append(row)
CRIT = pd.DataFrame(crit)

# 4-4 등급 분포, 4-6 도시별 Level 3
dist = (CELLS.groupby(["도시", "기준일", "등급"]).size().unstack(fill_value=0)
        .reindex(columns=["Level 3", "Level 2", "Level 1", "판단 보류", "후보 아님"], fill_value=0).reset_index())
v11d = (v11[v11["adm_cd"].notna()].groupby(["도시", "dname", "등급"]).size()  # v1.1 단위 = 동 × 정책 집단 행
        .unstack(fill_value=0).reset_index().rename(columns={"dname": "기준일"}))

# ------------------------------------------------------------ 5. 민감도
sens = []
for share in (0.25, 0.30, 0.3333, 0.40):
    for far in (3.0, 5.0, 7.0):
        n3 = {c: 0 for c in CITIES}
        n2 = {c: 0 for c in CITIES}
        for dn, d in DATES.items():
            s = s_axis(POP, ONE, int(month_of("resident_hh", d)[-1][4:]), top_share=share)
            h = h_axis(TEL["tel_age"], TEL["tel_time"], POP, sorted(int(m[4:]) for m in month_of("tel_age", d)), top_share=share)
            v = v_axis(POP, int(month_of("resident_hh", d)[-1][4:]), top_share=share)
            base = AX[dn][["adm_cd", "sex", "age5", "a_km", "w_ok", "w_shared", "w60", "pop", "s_small", "city", "s_eb",
                           "s_rank", "s_n", "h_state", "h", "h_basis", "v_state", "v_eb", "v_source"]]
            ax = base.merge(s[["adm_cd", "sex", "age5", "s_top"]], on=["adm_cd", "sex", "age5"]) \
                     .merge(h[["adm_cd", "sex", "age5", "h_top"]], on=["adm_cd", "sex", "age5"]) \
                     .merge(v[["adm_cd", "sex", "age5", "v_top"]], on=["adm_cd", "sex", "age5"])
            gn_pub = date.fromisoformat(RV2["axes"]["v_published"]["강남"])
            ax["v_counts"] = ax["v_top"] & ((ax["city"] != "강남") | (d >= gn_pub))
            ax["a_top"] = ax["a_km"] >= far
            for _, r in ax.iterrows():
                trig = cell_trigger(r["city"], dn, r)
                if not trig:
                    continue
                lv = decide_v2(facts_for(r, trig, dn), RV2).level
                n3[r["city"]] += lv == "Level 3"
                n2[r["city"]] += lv == "Level 2"
        sens.append({"상위 비율": share, "거점 km": far, **{f"{c} Level 3 칸": n3[c] for c in CITIES},
                     **{f"{c} Level 2 칸": n2[c] for c in CITIES}})
SENS = pd.DataFrame(sens)

# ------------------------------------------------------------ 6. 태연 분석 보완 확인
# 6-1 월별 순위 안정성: 기준월별 S·H 칸 순위가 7~12월 전체 값과 얼마나 같은가
stab = []
for m in (9, 10, 11, 12):
    hm = h_axis(TEL["tel_age"], TEL["tel_time"], POP, list(range(7, m + 1)))
    sm_ = s_axis(POP, ONE, m)
    j = hm[["adm_cd", "sex", "age5", "h"]].merge(H_FULL[["adm_cd", "sex", "age5", "h"]], on=["adm_cd", "sex", "age5"], suffixes=("", "_full"))
    sj = sm_[["adm_cd", "sex", "age5", "s_eb"]].merge(s_axis(POP, ONE, 12)[["adm_cd", "sex", "age5", "s_eb"]],
                                                      on=["adm_cd", "sex", "age5"], suffixes=("", "_12"))
    stab.append({"기준월": m, "ρ(H 기준월, H 7~12월)": spearmanr(j["h"], j["h_full"], nan_policy="omit")[0],
                 "ρ(S 기준월, S 12월)": spearmanr(sj["s_eb"], sj["s_eb_12"], nan_policy="omit")[0]})
STAB = pd.DataFrame(stab)

# 6-2 지역 나눠 확인: 강남에서 맞춘 보정 계수로 춘천 H를 만들어도 춘천 이동률 관계가 유지되나 (반대도)
from bigcontest import stay as STAY  # noqa: E402


def h_with_coef(src_city: str, dst_city: str) -> pd.Series:
    df = H_FULL.copy()
    X = lambda g: pd.get_dummies(g["sex"] + "_" + g["age5"], dtype=float).assign(  # noqa: E731
        log_daynight=g["log_daynight"], log_dongpop=g["log_dongpop"])
    out = {}
    for y in ("stay_raw", "winter_raw"):
        src = df[df["city"] == src_city]
        beta, *_ = np.linalg.lstsq(X(src).to_numpy(), src[y].to_numpy(float), rcond=None)
        dst = df[df["city"] == dst_city]
        out[y] = pd.Series(dst[y].to_numpy(float) - X(dst).to_numpy() @ beta, index=dst.index)
    dst = df[df["city"] == dst_city].copy()
    dst["sa"], dst["wa"] = out["stay_raw"], out["winter_raw"]
    g = dst.groupby(["sex", "age5"])
    dst["hx"] = (g["sa"].rank(pct=True) + g["wa"].rank(pct=True)) / 2
    return dst[["adm_cd", "hx", "pop"]]


split = []
y_mob = t2.set_index("adm_cd")["춘천 인구 이동률(%)"]
for src in ("춘천", "강남"):  # 춘천 H를 춘천 계수(그대로) / 강남 계수로
    hx = h_with_coef(src, "춘천")
    dong = hx.assign(wx=hx["hx"] * hx["pop"]).groupby("adm_cd")[["wx", "pop"]].sum()
    dong = dong["wx"] / dong["pop"]
    split.append({"보정 계수를 맞춘 곳": src, "H를 만든 곳": "춘천",
                  "ρ(동 H, 춘천 이동률)": spearmanr(dong, y_mob.reindex(dong.index), nan_policy="omit")[0]})
SPLIT = pd.DataFrame(split)

# ------------------------------------------------------------ 저장
OUT.mkdir(parents=True, exist_ok=True)


def save(df, name):
    df.round(4).to_csv(OUT / name, index=False, encoding="utf-8-sig")


save(hdir, "H_방향확인_칸단위.csv")
save(CELLS, "칸별_등급표.csv")
save(pd.DataFrame(CARD_ROWS), "카드_지난연휴.csv")
save(DEVT, "이탈_후보_v2.csv")
save(CMP, "판비교_v11_v20.csv")
save(DIFF, "집단차별화_v11_v20.csv")
save(CRIT, "동규모_독립기준.csv")
save(dist, "등급분포_v20.csv")
save(v11d, "등급분포_v11.csv")
save(SENS, "민감도_v20.csv")
save(STAB, "월별_순위안정성.csv")
save(SPLIT, "지역나눠_H확인.csv")
print("저장 끝")
print(dist.to_string())
print(v11d.to_string())
