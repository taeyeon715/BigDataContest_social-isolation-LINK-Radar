"""R-1 후보·등급 규칙 평가 (configs/rules.yaml rules-v1.1)

실행: uv run python notebooks/r1_level_rules.py   (dev 스냅샷 자료가 있어야 함)
결과: reports/r1_등급규칙/ (README.md, figs/, *.csv). CSV 에는 비율·순위·등급·건수(후보 수)만 (원천 값 없음).

1. 결과표: 두 도시 × 기준일 3개 × 감시 집단 × 후보 종류 → 등급별 후보 수, K=5/10/20 목록 (rule_id 포함)
2. 비교안 A~D: Top-K 구성 변화(Jaccard), 근거 종류별 후보 수, 잘못된 단위 주장, 근거 누락, 강남 시민생활과의 순위 관계(탐색적)
3. 민감도: 임계값 ±, 지표 하나씩 빼기 → Top-10 겹침률, 후보 수 범위
4. 연휴 뒤 기대 경로, 이탈 지속 조건의 지연, 전체/필터 조회 같은 등급, 60~64세 남성, D1 후보 수
5. 페르소나 주입 (합성 검증, 실제 고립 탐지율 아님)
7. rules-v1.1 (H3): v1.0 동작 재현과 비교, 집단 차별화, 카드가 바꾼 판단(외지 손님·시간대 리듬·업종 다양성),
   폭염 특보 단계, 명절 전·중·후 묶음, 수급자 비율, 1인세대 순증, "행 없음 = 0" 민감도
"""

from __future__ import annotations

import os

os.environ.setdefault("LINKRADAR_RULES", "v1.1")  # rules-v1.1 결과 재현 (bigcontest.rules.version)

from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bigcontest.agent.schemas import NormalizedRequest
from bigcontest.agent.tools import call_tool
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import DataSource, dev_available, dev_snapshot_id, open_datasource, register_source
from bigcontest.config import ROOT
from bigcontest.contracts import cohorts_config
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.rules import load_rules, rules_version
from bigcontest.rules import candidates as RC
from bigcontest.rules.compare import (
    VARIANT_INPUTS,
    VARIANT_NAME,
    collect,
    ctx_for,
    filter_evidence,
    jaccard,
    key_of,
    level_counts,
    run_rules,
    run_variant,
    top_k,
    wrong_unit_claims,
)
from bigcontest.rules.sensitivity import leave_one_out_runs, threshold_runs
from bigcontest.signals.alarm import combine_any

assert dev_available(), "dev 스냅샷 자료가 없음"
SID = dev_snapshot_id()
DS = open_datasource(SID)
RULES = load_rules()
VER = rules_version(RULES)
CITIES = ["강남", "춘천"]
DATES = {"추석 D-7": date(2025, 9, 26), "추석 뒤": date(2025, 10, 13), "동절기": date(2025, 12, 20)}
GROUPS = list(cohorts_config()["groups"])  # 고령층, 중장년 남성 1인가구, 청년 남성, 50~69세 남성 (보조 판)
KS = RULES["selection"]["budgets"]
VARIANTS = ["A", "B", "C", "D"]
TYPE_KO = {"scheduled_access_review": "예정된 점검", "unexpected_activity_deviation": "예상 밖 이탈"}

# ------------------------------------------------------------ 근거 모으기 (도시 × 날짜 × 집단, 도구 한 번)
EV: dict[tuple, list] = {}
REQ: dict[tuple, NormalizedRequest] = {}
for city in CITIES:
    for dname, d in DATES.items():
        for g in GROUPS:
            c = ctx_for(SID, city, d, g)
            REQ[(city, dname, g)] = c.request
            EV[(city, dname, g)] = collect(c)
print("근거 수집 끝", len(EV))


def run(key, variant="D", k=10, rules=None, drop=None):
    city, dname, g = key
    return run_variant(EV[key], variant, REQ[key], DATES[dname], k, rules, drop).candidates


# ------------------------------------------------------------ 1. 결과표 (비교안 D = 전체)
rows, topk_rows = [], []
for key in EV:
    city, dname, g = key
    cands = run(key, "D", max(KS))
    for t in TYPE_KO:
        cs = [c for c in cands if c.candidate_type == t]
        lc = level_counts(cs)
        rows.append({"도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "후보 종류": TYPE_KO[t],
                     "후보 수": len(cs), **{k: v for k, v in lc.items()},
                     "규칙 id": ", ".join(sorted({c.rule_id for c in cs})) or "-",
                     "대응 종류": ", ".join(sorted({c.response_type for c in cs if c.response_type})) or "-"})
    for k in KS:
        for rank, c in enumerate(top_k(sorted(cands, key=lambda c: c.queue_rank), k), start=1):
            topk_rows.append({"도시": city, "기준일": dname, "집단": g, "K": k, "순서": rank,
                              "후보": key_of(c), "등급": c.candidate_level, "rule_id": c.rule_id,
                              "근거 신뢰도": c.evidence_confidence, "대응 종류": c.response_type})
res = pd.DataFrame(rows)
topk = pd.DataFrame(topk_rows)

# ------------------------------------------------------------ 2. 비교안 A~D
SL = {}
for g in GROUPS:  # 강남 시민생활 (탐색적 참고, 등급 입력 아님)
    vals = {S.adm_of(e): float(e.value) for e in EV[("강남", "추석 D-7", g)]
            if e.metric.startswith("시민생활") and e.status == "OK" and isinstance(e.value, (int, float))}
    SL[g] = vals


def dong_priority(cands, adms):
    """동마다 점검 후보 등급(없으면 0)과 검토 순서 → 높은 것이 먼저 볼 동."""
    lv = {"Level 3": 3, "Level 2": 2, "Level 1": 1, "판단 보류": 0.5}
    s = {a: 0.0 for a in adms}
    for c in cands:
        if c.candidate_type == "scheduled_access_review" and c.adm_cd in s:
            s[c.adm_cd] = max(s[c.adm_cd], lv.get(c.candidate_level, 0))
    return s


cmp_rows = []
for key in EV:
    city, dname, g = key
    base_top = {}
    prev = None
    for v in VARIANTS:
        cands = run(key, v, 10)
        ev = filter_evidence(EV[key], VARIANT_INPUTS[v])
        t10 = {key_of(c) for c in top_k(sorted(cands, key=lambda c: c.queue_rank), 10)}
        base_top[v] = t10
        row = {"도시": city, "기준일": dname, "집단": g, "비교안": v, "쓰는 근거": VARIANT_NAME[v],
               "후보 수": len(cands),
               "예정된 점검": sum(c.candidate_type == "scheduled_access_review" for c in cands),
               "예상 밖 이탈": sum(c.candidate_type == "unexpected_activity_deviation" for c in cands),
               "Level 2 이상": sum(c.candidate_level in ("Level 2", "Level 3") for c in cands),
               "Level 3": sum(c.candidate_level == "Level 3" for c in cands),
               "판단 보류": sum(c.candidate_level == "판단 보류" for c in cands),
               "구조 근거 후보": sum(any("STR-" in i for i in c.evidence_ids) for c in cands),
               "접근성 근거 후보": sum(any("ACC-" in i for i in c.evidence_ids) for c in cands),
               "통신 근거 후보": sum(any(("WIN" in i or "MOB" in i) for i in c.evidence_ids) for c in cands),
               "카드 근거 후보": sum(any(("DEV-" in i or "SER-" in i) for i in c.evidence_ids) for c in cands),
               "잘못된 단위 주장": wrong_unit_claims(cands, ev),
               "필수 근거 누락 후보": sum(c.input_status != "충족" for c in cands),
               "Top-10 겹침률(A 대비)": jaccard(t10, base_top["A"]),
               "Top-10 겹침률(앞 비교안 대비)": jaccard(t10, base_top[prev]) if prev else None}
        if city == "강남" and dname == "추석 D-7" and SL.get(g):
            adms = sorted(SL[g])
            pr = dong_priority(cands, adms)
            x = [pr[a] for a in adms]
            y = [SL[g][a] for a in adms]
            row["시민생활 순위상관(탐색적)"] = spearmanr(x, y).statistic if len(set(x)) > 1 else None
            row["시민생활 동 수"] = len(adms)
        cmp_rows.append(row)
        prev = v
cmp = pd.DataFrame(cmp_rows)

# 구조 값 그 자체와 시민생활의 순위상관 (참고: 등급이 아니라 연속값)
struct_corr = []
for g in GROUPS:
    st = {S.adm_of(e): float(e.value) for e in EV[("강남", "추석 D-7", g)]
          if S.input_of(e) == "structure_vulnerability" and e.status == "OK"}
    adms = sorted(set(st) & set(SL[g]))
    if len(adms) > 5:
        struct_corr.append({"집단": g, "동 수": len(adms),
                            "1인세대 비율 ↔ 시민생활 순위상관(탐색적)": spearmanr([st[a] for a in adms],
                                                                    [SL[g][a] for a in adms]).statistic})
struct_corr = pd.DataFrame(struct_corr).astype({"동 수": int})

# ------------------------------------------------------------ 3. 민감도
sens_rows, loo_rows = [], []
for key in EV:
    city, dname, g = key
    if g not in ("고령층", "중장년 남성 1인가구"):
        continue
    for r in threshold_runs(EV[key], REQ[key], DATES[dname], 10):
        sens_rows.append({"도시": city, "기준일": dname, "집단": g, **r})
    for r in leave_one_out_runs(EV[key], REQ[key], DATES[dname], 10):
        loo_rows.append({"도시": city, "기준일": dname, "집단": g, **r})
sens = pd.DataFrame(sens_rows)
loo = pd.DataFrame(loo_rows)
rng = (sens.groupby(["도시", "기준일", "집단"])
       .agg(후보수_최소=("후보 수", "min"), 후보수_최대=("후보 수", "max"),
            L2이상_최소=("Level 2 이상", "min"), L2이상_최대=("Level 2 이상", "max"),
            L3_최소=("Level 3", "min"), L3_최대=("Level 3", "max"),
            Top10겹침률_최소=("Top-10 겹침률", "min")).reset_index())
base_counts = cmp[cmp["비교안"] == "D"].set_index(["도시", "기준일", "집단"])[["후보 수", "Level 2 이상", "Level 3"]]
rng = rng.join(base_counts, on=["도시", "기준일", "집단"]).rename(
    columns={"후보 수": "후보수_기본", "Level 2 이상": "L2이상_기본", "Level 3": "L3_기본"})

# ------------------------------------------------------------ 4-1. 연휴 뒤 기대 경로 (도시 공통요인)
P = DS.panel()
hol = {d.date() for d in P.holidays}
path_rows = []
for start, end, name in S.holiday_blocks(DS):
    for city in CITIES:
        s = np.nanmedian(P.E[:, P.cities == city], axis=1)
        idx = [i for i in np.where(P.dates > pd.Timestamp(end))[0] if not P.is_hol[i]][:10]
        for n, i in enumerate(idx, start=1):
            path_rows.append({"연휴": f"{name} ({start:%m/%d}~{end:%m/%d}, {(end - start).days + 1}일)", "도시": city,
                              "연휴 뒤 n일째": n, "날짜": f"{P.dates[i]:%m/%d}",
                              "도시 공통 평소 대비": round(float(np.expm1(s[i])), 3),
                              "−5% 아래": bool(s[i] < RULES["thresholds"]["holiday_path"]["recover_tol"])})
paths = pd.DataFrame(path_rows)

# 기대 경로가 바꾸는 판정: 2025 실제 경보(사후 목록)의 '확인 필요'를 경로 뒤 회복으로 다시 봄
rec = DS.alarm_card(P.dates[-1].date())
blocks = S.holiday_blocks(DS)
recheck = []
for _, r in rec[rec["review"] == "확인 필요"].iterrows():
    alarm, w0 = pd.Timestamp(r["alarm_date"]).date(), pd.Timestamp(r["window_start"]).date()
    bl = [b for b in blocks if b[0] - timedelta(days=1) <= alarm and b[1] + timedelta(days=1) >= w0]
    if not bl:
        continue
    age, sex = r["cohort"].split(" ")
    j = P.cols.index((r["city"], age, sex))
    verdict, why = RC.post_path_review(P.E, P.L, P.dates, j, bl[-1], hol, P.dates[-1].date(), RULES)
    recheck.append({"도시": r["city"], "코호트": r["cohort"], "경보일": f"{alarm:%m/%d}", "기대 경로 판정": verdict})
recheck = pd.DataFrame(recheck)

# ------------------------------------------------------------ 4-2. 이탈 지속 조건 (2일 연속)의 지연
rng_ = np.random.default_rng(0)
persist_rows = []
starts = list(range(P.cfg.min_train + 14, len(P.dates) - 21, 7))
for j, col in enumerate(P.cols):
    y = P.Y[col].to_numpy()
    if np.nanmean(y) < 5:
        continue
    for s0 in starts:
        y2 = y.copy()
        y2[s0:s0 + 14] = y2[s0:s0 + 14] * 0.85
        out = P.score_one(col, y2)
        a = out["either"]
        hit = [t for t in range(s0, s0 + 14) if a[t] == 1]
        hit2 = [t for t in range(s0 + 1, s0 + 14) if a[t] == 1 and a[t - 1] == 1]
        persist_rows.append({"city": col[0], "d1": hit[0] - s0 if hit else None, "d2": hit2[0] - s0 if hit2 else None})
pr = pd.DataFrame(persist_rows)
persist = pd.DataFrame([
    {"규칙": "지금 (7일 중 3일)", "탐지율": pr["d1"].notna().mean(), "지연 중앙값(일)": pr["d1"].median()},
    {"규칙": "+ 2일 연속", "탐지율": pr["d2"].notna().mean(), "지연 중앙값(일)": pr["d2"].median()},
])
both = pr.dropna()
extra_delay = float((both["d2"] - both["d1"]).mean())
n_inject = len(pr)

# ------------------------------------------------------------ 4-3. 전체 vs 필터 조회
filter_rank = []
for city in CITIES:
    c = ctx_for(SID, city, DATES["추석 D-7"], "고령층")
    full = call_tool("get_structure", c)
    tops_full = S.structure_tops([e for e in full if e.status == "OK"])
    for e in full:
        if e.status != "OK":
            continue
        adm = S.adm_of(e)
        one = call_tool("get_structure", c, adm_cd=adm)
        tops_one = S.structure_tops([x for x in one if x.status == "OK"])
        filter_rank.append({"도시": city, "adm_cd": adm, "전체 조회 상위": adm in tops_full, "동 하나 조회 상위": adm in tops_one})
filter_rank = pd.DataFrame(filter_rank)
filter_mismatch = int((filter_rank["전체 조회 상위"] != filter_rank["동 하나 조회 상위"]).sum())

# ------------------------------------------------------------ 4-4. 60~64세 남성 (주민등록 성연령)
from bigcontest.external import resident_population  # noqa: E402

rp = resident_population()
rp = rp[rp["month"] == 8]
names = DS.adm_names().set_index("adm_cd")
tot = rp.groupby("adm_cd")["pop"].sum()
m6064 = rp[(rp["sex"] == "남") & rp["age"].between(60, 64)].groupby("adm_cd")["pop"].sum()
male6064 = pd.DataFrame({"60~64세 남성 비중": (m6064 / tot).round(4)}).dropna()
male6064 = male6064[male6064.index.isin(names.index)]
male6064["도시"] = names.loc[male6064.index, "city"]
male6064["동"] = names.loc[male6064.index, "adm_nm"]
male6064["도시 안 순위"] = male6064.groupby("도시")["60~64세 남성 비중"].rank(ascending=False, method="min").astype(int)
male6064 = male6064.reset_index().sort_values(["도시", "도시 안 순위"])[["도시", "adm_cd", "동", "60~64세 남성 비중", "도시 안 순위"]]
board = res[res["집단"] == "50~69세 남성"]

# ------------------------------------------------------------ 4-5. D1 후보 수
d1 = [c for c in run(("강남", "추석 D-7", "중장년 남성 1인가구"), "D", 10) if c.candidate_type == "scheduled_access_review"]
d1_n, d1_lv = len(d1), level_counts(d1)


# ------------------------------------------------------------ 5. 페르소나 주입 (합성)
def persona(name, col, lo, hi, factor, as_of, group="고령층", city="춘천", expect=""):
    ds = DataSource(SID)
    base = ds.panel()
    y = base.Y[col].to_numpy().copy()
    m = (base.dates >= pd.Timestamp(lo)) & (base.dates <= pd.Timestamp(hi))
    y[m] = y[m] * factor
    ds._cache["panel"] = base.replace_column(col, y)
    sid = f"persona-{name}"
    register_source(sid, ds)
    c = ctx_for(sid, city, as_of, group)
    ev = collect(c)
    cands = run_rules(filter_evidence(ev, VARIANT_INPUTS["D"]), c.request, as_of, 10).candidates
    mine = [x for x in cands if x.candidate_type == "unexpected_activity_deviation" and x.analysis_cohort == f"{col[1]} {col[2]}"]
    got = "; ".join(f"{x.counter_result}→{x.candidate_level} ({x.rule_id})" for x in mine) or "후보 없음"
    sched = [x for x in cands if x.candidate_type == "scheduled_access_review"]
    return {"페르소나": name, "주입": f"{col[0]} {col[1]} {col[2]} 결제 {lo:%m/%d}~{hi:%m/%d} {1 - factor:.0%} 감소",
            "기준일": f"{as_of:%m/%d}", "기대": expect, "결과(이탈 후보)": got,
            "먼저 확인할 동": ", ".join(mine[0].check_first_adm) if mine else "-",
            "같은 날 예정된 점검 Level 3": sum(x.candidate_level == "Level 3" for x in sched),
            "기대와 맞음": None}


personas = [
    persona("P1 평소 기간 지속 하락", ("춘천", "70대", "여성"), date(2025, 11, 3), date(2025, 11, 30), 0.8,
            date(2025, 11, 12), expect="반증 통과 → Level 2 (구조 상위 동 공유 맥락)"),
    persona("P2 연휴 중 평소보다 더 깊은 하락", ("춘천", "70대", "남성"), date(2025, 10, 3), date(2025, 10, 13), 0.8,
            date(2025, 10, 24), expect="연휴 평소 감소로 설명 안 됨 → 반증 통과 (기각 아님)"),
    persona("P3 연휴 뒤에도 지속", ("춘천", "70대", "여성"), date(2025, 10, 3), date(2025, 11, 9), 0.8,
            date(2025, 10, 24), expect="기각되지 않음"),
    persona("P4 겨울 고령 지속 하락", ("춘천", "80대+", "남성"), date(2025, 12, 1), date(2025, 12, 31), 0.8,
            date(2025, 12, 12), expect="이탈 Level 2. 같은 날 동절기 점검은 11월 통신 공개(12/15 가정) 전이라 부분 판정 → Level 3 없음"),
    persona("P5 작은 코호트 하락", ("춘천", "80대+", "여성"), date(2025, 11, 3), date(2025, 11, 30), 0.8,
            date(2025, 11, 12), expect="못 잡을 수 있음 (EDA-6: 춘천 80대+ 15% 하락 탐지 17%)"),
]
pers = pd.DataFrame(personas)
ok = []
for _, r in pers.iterrows():
    g = r["결과(이탈 후보)"]
    if r["페르소나"].startswith(("P1", "P4")):
        ok.append("Level 2" in g and "통과" in g and (r["페르소나"].startswith("P1") or r["같은 날 예정된 점검 Level 3"] == 0))
    elif r["페르소나"].startswith("P2"):
        ok.append("통과" in g)
    elif r["페르소나"].startswith("P5"):
        ok.append(True)  # 한계 확인용 (어느 결과든 그대로 보고)
    else:
        ok.append("기각" not in g and g != "후보 없음")
pers["기대와 맞음"] = ["예" if x else "아니오" for x in ok]

# ------------------------------------------------------------ 7. rules-v1.1 (H3): v1.0 동작 재현과 비교
# v1.0 동작 = 같은 근거에서 v1.1 이 더한 입력(대상 인구 비중·연휴 외지 손님)을 빼고, 도시 단위 안내 집단을 비움.
# 폭염 특보 단계·명절 묶음·맥락 칸은 표시만이라 등급 수를 바꾸지 않는다.
from bigcontest.rules import context as X  # noqa: E402

V10_DROP = {"structure_target_population", "holiday_visitor_context"}
V10_RULES = load_rules({"thresholds.city_guidance.groups": []})


def run_v10(ev, req, as_of, k=10):
    return run_variant(ev, "D", req, as_of, k, V10_RULES, V10_DROP).candidates


def lv_summary(cs):
    return {"후보": len(cs), "L2+": sum(c.candidate_level in ("Level 2", "Level 3") for c in cs),
            "L3": sum(c.candidate_level == "Level 3" for c in cs), "보류": sum(c.candidate_level == "판단 보류" for c in cs)}


ver_rows = []
for key in EV:
    city, dname, g = key
    old, new = run_v10(EV[key], REQ[key], DATES[dname]), run(key, "D", 10)
    o, n = lv_summary(old), lv_summary(new)
    ro = {key_of(c): c.rule_id for c in old}
    rn = {key_of(c): c.rule_id for c in new}
    from collections import Counter

    changed = Counter(f"{ro.get(k, '없음')} → {rn.get(k, '없음')}" for k in set(ro) | set(rn) if ro.get(k) != rn.get(k))
    if o == n and not changed:
        continue
    ver_rows.append({"도시": city, "기준일": dname, "집단": g,
                     "후보 v1.0": o["후보"], "후보 v1.1": n["후보"], "L2+ v1.0": o["L2+"], "L2+ v1.1": n["L2+"],
                     "L3 v1.0": o["L3"], "L3 v1.1": n["L3"],
                     "바뀐 규칙 (후보 수)": "; ".join(f"{x} ({cnt})" for x, cnt in sorted(changed.items()))})
ver = pd.DataFrame(ver_rows)


# 7-1. 집단 차별화: 춘천 추석 D-7 집단별 점검 동
diff_rows = []
for city in CITIES:
    sets = {}
    for g in GROUPS:
        cs = [c for c in run((city, "추석 D-7", g), "D", 10) if c.candidate_type == "scheduled_access_review"]
        sets[g] = ({c.adm_cd or "도시 단위" for c in cs}, cs)
    for g, (adms, cs) in sets.items():
        diff_rows.append({"도시": city, "집단": g, "점검 후보": len(cs),
                          "Level 3": sum(c.candidate_level == "Level 3" for c in cs),
                          "동 단위": sum(c.adm_cd is not None for c in cs),
                          "고령층과 같은 동 비율": jaccard(adms, sets["고령층"][0]),
                          "규칙 id": ", ".join(sorted({c.rule_id for c in cs})) or "-",
                          "대응 종류": ", ".join(sorted({c.response_type for c in cs if c.response_type})) or "-"})
group_diff = pd.DataFrame(diff_rows)

# 대상 인구 조건 초안 (구조 상위에 "대상 성·연령 인구 비중 ≥ 도시 중앙값"을 거는 것). 결과를 보고 채택하지 않음 (rules.yaml)
DRAFT_RULES = load_rules({"thresholds.structure.target_population.apply_to_structure": True})
miss_rows, draft_rows = [], []
for city in CITIES:
    for g in ("중장년 남성 1인가구", "50~69세 남성"):
        evs = EV[(city, "추석 D-7", g)]
        facts = S.structure_facts([e for e in evs if S.input_of(e) == "structure_vulnerability" and e.status == "OK"],
                                  DRAFT_RULES, [e for e in evs if S.input_of(e) == "structure_target_population"])
        for adm, f in facts.items():
            if f.rate_top:
                miss_rows.append({"도시": city, "집단": g, "adm_cd": adm, "동": DS.adm_index[adm]["adm_nm"],
                                  "1인세대 순위": f"{f.rank}/{f.n}", "대상 인구 비중": round(f.target_share, 4),
                                  "도시 중앙값": round(f.target_median, 4), "초안이면 구조 상위": bool(f.top)})
        key = (city, "추석 D-7", g)
        base_c = [c for c in run(key, "D", 10) if c.candidate_type == "scheduled_access_review"]
        draft_c = [c for c in run_variant(EV[key], "D", REQ[key], DATES["추석 D-7"], 10, DRAFT_RULES).candidates
                   if c.candidate_type == "scheduled_access_review"]
        row = {"도시": city, "집단": g, "점검 후보 (채택: 표시만)": len(base_c), "점검 후보 (초안: 조건)": len(draft_c),
               "Level 3 (채택)": sum(c.candidate_level == "Level 3" for c in base_c),
               "Level 3 (초안)": sum(c.candidate_level == "Level 3" for c in draft_c)}
        if city == "강남" and SL.get(g):
            adms = sorted(SL[g])
            for name, cs in (("채택", base_c), ("초안", draft_c)):
                pr = dong_priority(cs, adms)
                x = [pr[a] for a in adms]
                row[f"시민생활 순위상관 ({name}, 탐색적)"] = spearmanr(x, [SL[g][a] for a in adms]).statistic if len(set(x)) > 1 else None
        draft_rows.append(row)
target_tab = pd.DataFrame(miss_rows)
draft_tab = pd.DataFrame(draft_rows)

# 7-2. 카드가 바꾼 판단: 추석 뒤 날마다 이탈 후보 (v1.0 재현 vs v1.1)
card_rows = []
for city in CITIES:
    for g in ("고령층", "중장년 남성 1인가구"):
        for d in pd.date_range("2025-10-10", "2025-10-24"):
            d = d.date()
            c = ctx_for(SID, city, d, g)
            ev = collect(c)
            old = {key_of(x): x for x in run_v10(ev, c.request, d) if x.candidate_type == "unexpected_activity_deviation"}
            new = {key_of(x): x for x in run_variant(ev, "D", c.request, d, 10).candidates
                   if x.candidate_type == "unexpected_activity_deviation"}
            for k in set(old) | set(new):
                a, b = old.get(k), new.get(k)
                card_rows.append({"도시": city, "집단": g, "기준일": f"{d:%m/%d}", "코호트": (a or b).analysis_cohort,
                                  "v1.0": a.rule_id if a else "없음", "v1.1": b.rule_id if b else "없음",
                                  "v1.1 대응": b.response_type if b else "-"})
# set 순서는 실행마다 달라질 수 있어(문자열 해시) 행 순서를 고정한다 (외부 점검 K-04). 내용은 같고 순서만 정함.
card_dec = (pd.DataFrame(card_rows).sort_values(["도시", "집단", "기준일", "코호트", "v1.0", "v1.1", "v1.1 대응"], kind="stable")
            .reset_index(drop=True))
card_changed = card_dec[card_dec["v1.0"] != card_dec["v1.1"]]

# 연휴 외지 손님 비중 (연휴 × 도시)
hol_set = {d.date() for d in DS.holidays()}
vis_rows = []
for b in S.holiday_blocks(DS):
    for city in CITIES:
        v = X.visitor_context(DS.card2_visitors(), city, b, hol_set, date(2025, 12, 31), RULES)
        vis_rows.append({"연휴": f"{b[2]} ({b[0]:%m/%d}~{b[1]:%m/%d})", "도시": city, "연휴 중 외지 손님 비중": v.holiday_share,
                         "직전 8주 주말": v.base_share, "주말 날별 표준편차": v.base_sd, "차이(%p)": v.diff * 100,
                         "판정 (기준 +3%p)": v.verdict})
visitor_tab = pd.DataFrame(vis_rows)

# 시간대 상대 리듬 (도시 × 달)
rh = []
for city in CITIES:
    t = X.time_rhythm(DS.card1(), city, date(2025, 12, 31), RULES["thresholds"]["card_context"]["rhythm_band"])
    for m, row in t.iterrows():
        rh.append({"도시": city, "달": f"{m}월", "고령 오전 비중": row["고령"], "40대 오전 비중": row["40대"], "차이(%p)": row["차이"] * 100})
rhythm_tab = pd.DataFrame(rh)

# 업종 다양성 (기준일 3개, 고령층 코호트)
div_rows = []
for key in EV:
    city, dname, g = key
    if g != "고령층":
        continue
    for e in EV[key]:
        if S.input_of(e) == "card_category_diversity" and e.status == "OK":
            div_rows.append({"도시": city, "기준일": dname, "코호트": e.analysis_cohort, "평소 대비": float(e.value)})
div_tab = pd.DataFrame(div_rows).pivot_table(index=["도시", "코호트"], columns="기준일", values="평소 대비").reset_index() \
    if div_rows else pd.DataFrame()

# 7-3. 폭염 특보 단계 (2025 여름, 상황 시연 기준: 그날·다음 날 실측을 예보 대신)
from bigcontest.rules.heat import heat_stage  # noqa: E402

hd = DS.heat()
heat_rows = []
for city in CITIES:
    h = hd[(hd["city"] == city) & (hd["date"] <= "2025-09-30")].set_index("date").sort_index()
    st, st_hi = [], []
    for i in range(len(h) - 1):
        days = [(h["at_max"].iloc[j], h["tmax"].iloc[j]) for j in (i, i + 1)]
        st.append(heat_stage(days, RULES))
        st_hi.append(heat_stage([(h["at_max_hi"].iloc[j], h["tmax"].iloc[j]) for j in (i, i + 1)], RULES))
    st = pd.Series(st)
    heat_rows.append({"도시": city, "최고기온 33℃ 이상 (예전 R4)": int((h["tmax"] >= 33).sum()),
                      "체감 33℃ 이상": int((h["at_max"] >= 33).sum()), "체감 35℃ 이상": int((h["at_max"] >= 35).sum()),
                      "폭염주의보 단계 날": int((st == "폭염주의보").sum()), "폭염경보 단계 날": int((st == "폭염경보").sum()),
                      "폭염중대경보 단계 날": int((st == "폭염중대경보").sum()),
                      "특보 단계 날 (평균 습도로 낸 위쪽 근사)": int(pd.Series(st_hi).notna().sum()),
                      "가장 높은 체감": float(h["at_max"].max())})
heat_tab = pd.DataFrame(heat_rows)
heat_tab = heat_tab.astype({c: int for c in heat_tab.columns if c not in ("도시", "가장 높은 체감")})

# 7-4. 명절 전·중·후 묶음 (추석 D-7 기준)
tl_rows = []
bundle = X.holiday_bundle(S.holiday_blocks(DS), DS.holiday_names(), DATES["추석 D-7"])
for city in CITIES:
    for elderly in (True, False):
        bid, steps = X.holiday_timeline(bundle, DATES["추석 D-7"], city, elderly)
        for s_ in steps:
            tl_rows.append({"묶음": bid, "집단": "고령층" if elderly else "그 밖 집단", "단계": s_["phase"], "규칙": s_["code"],
                            "기간": s_["label"], "추석 D-7 기준 상태": s_["status"], "후보 생김": "예" if s_["applies"] else "표시만"})
timeline_tab = pd.DataFrame(tl_rows)

# 7-5. 수급자 비율 (강남 22개 동, 구조 별도 칸)
recs = [e for e in EV[("강남", "추석 D-7", "중장년 남성 1인가구")] if S.input_of(e) == "recipient_rate" and e.status == "OK"]
st_ = {S.adm_of(e): float(e.value) for e in EV[("강남", "추석 D-7", "중장년 남성 1인가구")]
       if S.input_of(e) == "structure_vulnerability" and e.status == "OK"}
d1c = {c.adm_cd: c for c in run(("강남", "추석 D-7", "중장년 남성 1인가구"), "D", 10)
       if c.candidate_type == "scheduled_access_review"}
rec_rows = []
for e in recs:
    a = S.adm_of(e)
    rec_rows.append({"adm_cd": a, "동": DS.adm_index[a]["adm_nm"], "수급자 비율": float(e.value),
                     "수급자 순위": S._rank_of(e)[0], "1인세대 비율": st_.get(a), "경제 취약형": S.recipient_top(e, RULES),
                     "D1 점검 후보": a in d1c})
rec_tab = pd.DataFrame(rec_rows).sort_values("수급자 순위")
rec_rho = spearmanr(rec_tab["수급자 비율"], rec_tab["1인세대 비율"]).statistic
n_econ_d1 = int((rec_tab["경제 취약형"] & rec_tab["D1 점검 후보"]).sum())

# 7-6. 1인세대 월 순증 (추석 D-7 기준월)
net_rows = []
for city in CITIES:
    for e in EV[(city, "추석 D-7", "고령층")]:
        if S.input_of(e) == "one_person_net_change" and e.status == "OK":
            net_rows.append({"도시": city, "동": DS.adm_index[S.adm_of(e)]["adm_nm"], "1인세대 월 순증률": float(e.value)})
net_tab = pd.DataFrame(net_rows).sort_values(["도시", "1인세대 월 순증률"], ascending=[True, False])

# 7-7. "행 없음 = 0" 민감도: 카드 결제 행이 없는 (날짜, 코호트)를 0 대신 결측(미공급)으로 두고 경보를 다시 냄
from bigcontest.signals.alarm import Panel, records  # noqa: E402

obs = DS.card_observed()
n_missing_panel = int((~obs).to_numpy().sum())
Pn = Panel(P.Y.where(obs.to_numpy()), P.holidays, P.cfg)
rz0, rz1 = records(P, rain=DS.rain()), records(Pn, rain=DS.rain())


def alarm_keys(rr):
    return set(zip(rr["city"], rr["cohort"], pd.to_datetime(rr["alarm_date"]).dt.date))


zero_alarm = {"패널 칸 (도시×연령×성×일)": int(obs.size), "행 없음 칸": n_missing_panel,
              "경보 (0 가정)": len(rz0), "경보 (결측 가정)": len(rz1),
              "같은 경보": len(alarm_keys(rz0) & alarm_keys(rz1)),
              "검토 결과 같음": bool(rz0.groupby("review").size().equals(rz1.groupby("review").size()))}
c2_ = DS.card2()
zero_rows = []
for name, lvl in (("업종 묶음 8개", "cat"), ("업종 78개", "MCT_RY_CD")):
    gg = c2_.groupby(["date", "city", "age_g", "SEX_CCD", lvl]).size()
    full = pd.MultiIndex.from_product([pd.date_range("2025-07-01", "2025-12-31")] +
                                      [sorted(c2_[x].unique()) for x in ["city", "age_g", "SEX_CCD", lvl]])
    m = gg.reindex(full).isna()
    for (city, age), v in m.groupby(level=[1, 2]).mean().items():
        zero_rows.append({"단위": name, "도시": city, "연령": age, "행 없음 비율": v})
c1_ = DS.card1()
gg = c1_.groupby(["date", "city", "age_g", "SEX_CCD", "TIME_GB"]).size()
full = pd.MultiIndex.from_product([pd.date_range("2025-07-01", "2025-12-31")] +
                                  [sorted(c1_[x].unique()) for x in ["city", "age_g", "SEX_CCD", "TIME_GB"]])
m = gg.reindex(full).isna()
for (city, age), v in m.groupby(level=[1, 2]).mean().items():
    zero_rows.append({"단위": "카드1 시간대 4구간", "도시": city, "연령": age, "행 없음 비율": v})
zero_tab = pd.DataFrame(zero_rows).pivot_table(index=["단위", "도시"], columns="연령", values="행 없음 비율").reset_index()

# ------------------------------------------------------------ 리포트
r = Report("r1_등급규칙", f"R-1 후보·등급 규칙 평가 ({VER})")
r.p(f"후보 종류와 등급을 정하는 규칙을 [`configs/rules.yaml`](../../configs/rules.yaml) **{VER}**로 고정하고, "
    "두 도시 × 기준일 3개에서 결과를 냈습니다. 등급은 규칙 코드가 정하고 LLM은 정하지 않습니다. "
    "후보는 행정동·생애단계 집단 단위이며 **사람을 판정하지 않습니다.**")
r.p()
r.p("- 임계값은 서울 시민생활을 보지 않고 정했습니다. 값과 근거는 rules.yaml 주석에 있습니다.")
r.p("- 모든 후보에 `rule_id`·`rule_version`·근거 신뢰도·대응 종류가 붙습니다. 예: `L2-STRUCT-TRIGGER`, `L3-STRUCT-ACCESS-TRIGGER`.")
r.p("- 기준일은 simulated_replay(가정 공개일)로 냈습니다. 접근성은 2026년 원장이라 \"현재 원장 기준 접근성\"으로 표시됩니다.")
r.p("- **후보 수는 임계값에 따라 바뀝니다.** 아래 3장 범위와 함께만 말해 주세요.")

r.h("0. 규칙 요약")
th = RULES["thresholds"]
r.table(pd.DataFrame([
    ["구조 상위", f"도시 안 상위 {th['structure']['top_share']:.0%} + 1인세대 비율 {th['structure']['abs_min']:.2f} 이상",
     "순위 모집단 = 도시 × 기준월 행정동 전체 (조회 필터와 무관). 0.35는 운영상 정한 탐색 기준. 2023 인구주택총조사 1인가구 비율(35.5%)은 참고점일 뿐 다른 지표(가구 ≠ 주민등록 세대)라 \"전국 평균 이상\" 뜻이 아님. 0.30·0.40 민감도 공개"],
    ["접근성", f"관측 거점까지 직선 {th['access']['far_km']:g}km 이상", "걸어서 1시간 넘는 거리. 공개 시점 모르는 현재 원장은 strict 에서 안 셈"],
    ["이탈 크기", f"평소 대비 {th['deviation']['min_drop']:.0%} 이상 하락", "EDA-6 합성 주입: 10% 하락 탐지 77%, 5% 하락 32%"],
    ["기준선 학습", f"{th['deviation']['min_train_days']}일 이상", "EDA-6 경보 규칙과 같음"],
    ["연휴 뒤 기대 경로", f"명절(연휴 {th['holiday_path']['long_block_days']}일 이상) 뒤 {th['holiday_path']['long_path_days']}일",
     "2025 추석 뒤 도시 공통요인이 4일 낮고 5일째 회복 (4-1 표). 경로 뒤 회복이면 기각 가능, 아니면 확인 필요"],
    ["Level 2", "(구조 상위 또는 접근성) + (예정된 트리거 또는 반증 통과 이탈)", "설계 §5.2"],
    ["Level 3", "예정된 점검 + 구조 상위 + 접근성 + 필수 근거 충족 + 신뢰도 보통 이상", "이탈 후보는 도시 단위라 Level 2 상한"],
    ["상한", "부분 판정 → 최대 Level 2, 근거 신뢰도 낮음 → 최대 Level 2", "CAP-PARTIAL, CAP-CONF-LOW"],
    ["진입", "예정된 점검은 구조 상위 또는 접근성 근거가 있는 동만", "22개 동 전부 후보 문제"],
    ["남성 집단 대상 인구 (v1.1)", "대상 성·연령 인구 비중 표시만 (등급 조건 초안은 채택 안 함)", "중장년 남성(40~64)·50~69세 남성. 7-1"],
    ["청년 남성 (v1.1)", "도시 단위 안내 1건 (L2-CITY-GUIDANCE, Level 3 불가)", "1인세대 상위 동에서 뽑지 않음. 7장"],
    ["연휴 반증 맥락 (v1.1)", "도시 공통 하락 + 외지 손님 급증(+3%p) → HOLD-HOLIDAY-TRAVEL (판단 보류 유지)", "카드 데이터2 비지역 고객. 7-2"],
    ["대응 종류", "Level 3 현장 확인, Level 2 담당자 추가 분석, 청년 남성 Level 2·3 안내 강화, HOLD-HOLIDAY-TRAVEL 연휴 뒤 회복 확인", "청년 고립은 1인세대 밀집으로 잡히지 않음"],
], columns=["항목", "규칙", "근거"]).set_index("항목"))

r.h("1. 결과표 (비교안 D, 두 도시 × 기준일 3개)")
r.p("집단 4개(감시 3집단 + 50~69세 남성 보조 판)마다 후보 종류별 등급 수입니다. 기준일에 트리거가 없으면 예정된 점검은 0건입니다.")
show = res[res["후보 수"] > 0].copy()
r.table(show.set_index("도시"), index=True)
r.p(f"- 강남 추석 D-7 중장년 남성(D1): 예정된 점검 **{d1_n}곳** (Level 2 {d1_lv['Level 2']}곳, Level 3 {d1_lv['Level 3']}곳). "
    "임시 기준 때는 22개 동 전부가 후보(15곳 Level 1)였습니다.")
r.p("- 추석 뒤(10/13)에는 강남에 예정된 트리거가 없고, 춘천 고령층만 R3(연휴 뒤 회복 확인)이 있습니다.")
r.p("- 동절기(12/20) 예정된 점검은 춘천 읍·면에만 생깁니다. 강남 동절기·한파 규칙은 아직 없습니다.")
res.to_csv(r.dir / "결과표.csv", index=False, encoding="utf-8-sig")
topk.to_csv(r.dir / "TopK.csv", index=False, encoding="utf-8-sig")
r.h3("K별 목록 예 (K = 5, 강남·춘천 고령층)")
r.table(topk[(topk["K"] == 5) & (topk["집단"] == "고령층")].drop(columns=["K"]).set_index("도시"))
r.p("- 전체 K=5/10/20 목록은 [`TopK.csv`](TopK.csv). K를 바꾸면 선정만 바뀌고 등급은 그대로입니다(tests/test_rules.py).")

r.h("2. 비교안 A~D: 통신·카드는 주민등록에 무엇을 더하나")
r.p("같은 날짜·도시·집단·K(10)에서 쓰는 자료만 바꿨습니다. A = 주민등록 + 달력, B = A + 통신(구성·계절), "
    "C = B + 카드(시점·이탈), D = C + 자원(접근성).")
cm = cmp.groupby(["비교안", "쓰는 근거"])[["후보 수", "예정된 점검", "예상 밖 이탈", "Level 2 이상", "Level 3", "판단 보류",
                                       "잘못된 단위 주장", "필수 근거 누락 후보"]].sum()
cm["Top-10 겹침률(A 대비, 평균)"] = cmp.groupby(["비교안", "쓰는 근거"])["Top-10 겹침률(A 대비)"].mean().round(2)
r.table(cm.reset_index().set_index("비교안"))
cmp.to_csv(r.dir / "비교안.csv", index=False, encoding="utf-8-sig")
n_dev_c = int(cmp.loc[cmp["비교안"] == "C", "예상 밖 이탈"].sum())
n_dev_c_l2 = int(sum(c.candidate_level in ("Level 2", "Level 3") and c.candidate_type == "unexpected_activity_deviation"
                     for key in EV for c in run(key, "C", 10)))
wrong = int(cmp["잘못된 단위 주장"].sum())
b_change = cmp[(cmp["비교안"] == "B")]["Level 3"].sum() - cmp[(cmp["비교안"] == "A")]["Level 3"].sum()
r.p("**읽는 법 (쉽게)**")
r.p("- **'어디'는 주민등록이 정합니다.** 강남 동 순위는 A~D 모두 같습니다. 통신·카드는 동 단위 자료가 아니라 동 순위를 바꾸지 않습니다.")
r.p(f"- **B(통신)**: 춘천 동절기 점검의 필수 근거(읍·면 겨울 변화)를 채워 부분 판정을 없앱니다. 그 자체로는 Level 3를 만들지 않습니다(Level 3 변화 {int(b_change)}건).")
r.p(f"- **C(카드)**: '언제'와 '아닌지'를 더합니다. 예상 밖 이탈 후보 행 {n_dev_c}개(기준일·집단 합)가 새로 생기지만, 반증(연휴·도시 공통 변화·이탈 크기)을 거쳐 "
    f"Level 2 이상은 {n_dev_c_l2}행입니다. **이 기준일들에서 C가 검토 목록을 바꾸는 폭은 작습니다.** 카드의 몫은 주로 "
    "\"연휴 감소는 평소에도 있는 일인지\"를 걸러 주는 반증입니다(D3: 춘천 20대 여성 경보 기각, 강남 고령층 '확인 필요').")
r.p("- rules-v1.1부터 카드는 '아닌지'에 하나를 더합니다: 연휴 외지 손님 비중이 도시 공통 하락 경보의 다음 할 일을 바꿉니다(7-2, "
    "춘천 추석 뒤 16건). 시간대 상대 리듬·업종 다양성은 맥락 칸입니다.")
r.p("- **D(자원 접근성)**: 춘천 읍·면에서 Level 3(구조 + 접근성)이 생기는 단계입니다. 강남은 모든 동이 거점에서 가까워 바뀌지 않습니다.")
r.p(f"- 잘못된 단위 주장(카드 도시 단위 신호로 동 하나를 지목): **{wrong}건**. 이탈 후보의 동 목록은 모두 '먼저 확인할 동(공유 맥락)'입니다.")
r.p("- 미확인 자격 추천 수는 자원 추천 평가에서 셉니다. 이 표에는 없습니다.")
r.h3("강남 시민생활과의 순위 관계 (탐색적, 22개 동)")
r.p("시민생활은 등급 입력이 아니고 같은 공급자 자료라 독립 검증이 아닙니다. 관측 기간도 미확인입니다. **탐색적 참고**로만 봅니다.")
sl = cmp[cmp["시민생활 순위상관(탐색적)"].notna()][["집단", "비교안", "시민생활 순위상관(탐색적)", "시민생활 동 수"]]
if len(sl):
    r.table(sl.set_index("집단"), fmt={"시민생활 순위상관(탐색적)": "{:.2f}", "시민생활 동 수": "{:.0f}"})
r.table(struct_corr.set_index("집단"), fmt={"1인세대 비율 ↔ 시민생활 순위상관(탐색적)": "{:.2f}"})
r.p("- 청년 남성은 1인세대 비율이 높은 동일수록 시민생활 '외출·소통 모두 적음'이 오히려 낮게 나옵니다(음의 상관). "
    "청년 고립은 1인세대 밀집으로 잡히지 않는다는 뜻이고, 그래서 청년은 방문 점검이 아니라 '안내 강화'로 둡니다.")
r.p("- 등급은 몇 칸(Level 0~3)뿐이라 동 순위가 많이 묶입니다. 그래서 1인세대 비율 자체보다 상관이 낮게 나옵니다. "
    "순위상관은 \"일치율\"이 아닙니다.")

r.h("3. 민감도와 후보 수 범위")
r.p("임계값 하나씩 바꾸기(다른 값은 기본): 구조 상위 20/30/40%, 최소값 0.30/0.35/0.40, 직선 3/5/8km, 이탈 크기 5/10/15%. "
    "고령층·중장년 남성, 두 도시 × 기준일 3개.")
r.table(rng.set_index("도시"), fmt={"Top10겹침률_최소": "{:.2f}"})
rng.to_csv(r.dir / "후보수_범위.csv", index=False, encoding="utf-8-sig")
sens.to_csv(r.dir / "민감도_임계값.csv", index=False, encoding="utf-8-sig")
loo.to_csv(r.dir / "민감도_지표빼기.csv", index=False, encoding="utf-8-sig")
d1r = rng[(rng["도시"] == "강남") & (rng["기준일"] == "추석 D-7") & (rng["집단"] == "중장년 남성 1인가구")].iloc[0]
r.p(f"발표 문장 예: \"강남 추석 D-7 중장년 남성 예정된 점검 후보는 기본 {int(d1r['후보수_기본'])}곳 "
    f"(임계값에 따라 {int(d1r['후보수_최소'])}~{int(d1r['후보수_최대'])}곳)\"")
r.h3("지표 하나씩 빼기 (비교안 D 기준, Top-10 겹침률)")
lo_ = loo.groupby("뺀 지표")[["후보 수", "Level 2 이상", "Level 3"]].sum().astype(int)
lo_["Top-10 겹침률(평균)"] = loo.groupby("뺀 지표")["Top-10 겹침률"].mean().round(2)
r.table(lo_)
r.p("- 구조 지표를 빼면 강남 후보가 거의 사라집니다(강남은 접근성 근거가 약함). 접근성을 빼면 춘천 Level 3가 사라집니다.")

fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
for ax, city in zip(axes, CITIES):
    d = rng[rng["도시"] == city].reset_index(drop=True)
    lab = [f"{a}\n{b[:6]}" for a, b in zip(d["기준일"], d["집단"])]
    x = np.arange(len(d))
    ax.vlines(x, d["후보수_최소"], d["후보수_최대"], color=CITY_COLOR[city], lw=6, alpha=0.4, label="임계값 범위")
    ax.scatter(x, d["후보수_기본"], color=CITY_COLOR[city], zorder=3, label=f"기본 ({VER})")
    ax.set_xticks(x, lab, fontsize=7)
    ax.set_title(f"{city}: 후보 수와 범위")
    ax.legend(fontsize=7)
r.fig(fig, "후보수_범위.png", "기준일·집단별 후보 수 (점 = 기본, 막대 = 임계값 범위)")

r.h("4. 시점 규칙 점검")
r.h3("4-1. 연휴 뒤 기대 경로 (도시 공통요인)")
pv = paths.pivot_table(index=["연휴", "연휴 뒤 n일째"], columns="도시", values="도시 공통 평소 대비").reset_index()
pv["연휴 뒤 n일째"] = pv["연휴 뒤 n일째"].astype(int)
r.table(pv[pv["연휴 뒤 n일째"] <= 6].set_index("연휴"), fmt={c: "{:+.3f}" for c in CITIES})
paths.to_csv(r.dir / "연휴뒤_기대경로.csv", index=False, encoding="utf-8-sig")
r.p("- 추석(7일) 뒤 두 도시 모두 4일 동안 −5% 아래였고 5일째 돌아왔습니다. 광복절(3일) 뒤에는 낮은 날이 없었습니다. "
    "그래서 기대 경로를 **명절 뒤 4일**로 정했습니다. 추석 한 번만 본 값이라 여유를 더하지 않았습니다(길게 잡을수록 기각이 늘어남).")
if len(recheck):
    rc = recheck.groupby("기대 경로 판정").size().rename("경보 수").to_frame()
    r.p("2025 실제 경보(사후 목록) 중 '확인 필요'를 기대 경로로 다시 본 결과:")
    r.table(rc)
    r.p("- 사후로 보면 추석 뒤 '확인 필요' 경보 대부분이 기대 경로가 끝난 뒤 평소 수준으로 돌아왔습니다. "
        "이 규칙은 그런 경보를 기각할 수 있게 하지만, **경로 뒤 회복을 실제로 본 뒤에만** 기각합니다.")
    r.p("- '기대 경로 안' = 경로가 끝난 뒤 평소 수준으로 돌아오고 도시 대비 추가 이탈도 없음 → 기각 가능. "
        "기준일 카드에서는 경로 뒤 3일 + 공급 지연 3일이 지나야 판정이 나므로 그 전에는 '확인 필요'로 남습니다.")
r.h3("4-2. 이탈 후보의 지속 조건")
r.table(persist.set_index("규칙"), fmt={"탐지율": "{:.1%}", "지연 중앙값(일)": "{:.1f}"})
r.p(f"- 합성 주입: 코호트마다 14일 동안 15% 감소 {n_inject}회. '2일 연속' 조건을 더하면 평균 {extra_delay:.1f}일 늦어집니다. "
    "카드 공급 지연(가정 3일)까지 더하면 현장에 알리는 날이 더 늦어지므로 **기본은 끔**(rules.yaml persistence_days: 1). 합성 결과이며 실제 고립 탐지율이 아닙니다.")
persist.to_csv(r.dir / "지속조건.csv", index=False, encoding="utf-8-sig")

r.h3("4-3. 조회 필터와 무관한 순위")
r.p(f"두 도시 모든 동을 '도시 전체 조회'와 '그 동 하나만 조회'로 각각 냈을 때 구조 상위 판정이 다른 동: **{filter_mismatch}곳** "
    f"(전체 {len(filter_rank)}곳). 순위 근거에는 \"강남 22개 동 전체, 2025-08 주민등록 (조회 필터와 무관) 중 k/22위\"처럼 모집단과 기준월을 적습니다.")
filter_rank.to_csv(r.dir / "필터_순위.csv", index=False, encoding="utf-8-sig")

r.h3("4-4. 60~64세 남성")
r.p("중장년 남성 집단은 카드 40·50대라 60~64세가 빠지고, 고령층은 65세 이상입니다. 그래서 **50~69세 남성 보조 판**을 "
    "[`configs/cohorts.yaml`](../../configs/cohorts.yaml)에 두었습니다(카드 50·60대). 통신 60+는 70세 이상이 섞여 이 판에 쓰지 않습니다.")
r.p("주민등록 세대 자료에는 성·연령이 없어 **60~64세 남성 1인세대 수는 알 수 없습니다.** 대신 주민등록 성연령 인구로 동별 60~64세 남성 비중을 보입니다(2025-08).")
r.table(male6064.groupby("도시").head(5).set_index("도시"), fmt={"60~64세 남성 비중": "{:.3f}"})
male6064.to_csv(r.dir / "60_64남성_비중.csv", index=False, encoding="utf-8-sig")
r.p("- 보조 판 결과는 1장 결과표의 '50~69세 남성' 줄입니다. 예정된 점검의 동 순위는 1인세대 비율로 정하므로 중장년 판과 같습니다. "
    "v1.1부터 동마다 50~69세 남성 인구 비중을 표시합니다(등급 조건 아님, 7-1).")

r.h("5. 페르소나 주입 (합성 검증, 실제 고립 탐지율 아님)")
r.p("카드 패널의 한 코호트에 가짜 감소를 넣고, 같은 규칙(비교안 D)으로 후보·등급이 기대대로 나오는지 봅니다. "
    "**합성 검증이며 실제 고립을 찾는 비율이 아닙니다.** 카드는 도시 단위라 동은 '먼저 확인할 동(공유 맥락)'으로만 나옵니다.")
r.table(pers.set_index("페르소나"))
pers.to_csv(r.dir / "페르소나.csv", index=False, encoding="utf-8-sig")
n_ok = sum(ok)
r.p(f"- 기대와 맞은 페르소나: **{n_ok}/{len(ok)}** (P5는 한계 확인용이라 결과를 그대로 적음).")
r.p("- 추가 감소 없이 연휴에 평소만큼 줄어든 실제 경보(춘천 20대 여성 10/07)는 반증에서 기각됩니다(시연 D3). "
    "P2처럼 연휴 평소 감소보다 더 깊으면 기각하지 않습니다.")
r.p("- 먼저 확인할 동은 그 도시 1인세대 비율 상위 동(동 지역)입니다. 카드 신호는 도시 단위라 읍·면을 따로 가리키지 못합니다.")
r.p("- P5: 결제가 적은 춘천 80대+ 코호트는 20% 감소도 경보가 안 날 수 있습니다. 이 집단은 카드 신호보다 예정된 점검(동절기·연휴)으로 봐야 합니다.")

r.h("7. rules-v1.1에서 바뀐 것 (H3: 제공 데이터·도메인 반영)")
r.p("v1.1의 규칙과 기준값은 [`configs/rules.yaml`](../../configs/rules.yaml)에 먼저 적고 돌렸습니다. 다만 모두 '사전 설정'은 아닙니다: "
    "남성 집단 대상 인구 조건 false는 결과를 보고 정했고, 외지 손님 +3%p는 관련 숫자를 이미 본 뒤 적었고, 청년 남성 도시 안내는 "
    "시민생활 관계를 보고 설계한 탐색적 정책입니다(값별 구분은 7-1 끝 표). "
    "아래 표의 'v1.0'은 같은 근거에서 v1.1이 더한 입력(대상 인구 비중·연휴 외지 손님 비중)을 빼고 도시 단위 안내 집단을 비워 "
    "v1.0 동작을 다시 낸 값입니다(커밋된 v1.0 결과표와 같음).")
r.table(pd.DataFrame([
    ["남성 집단 대상 인구 비중", "주민등록 대상 성·연령 인구 비중 + 도시 중앙값을 후보 '구조 별도 칸'에 표시 (등급 조건 초안은 채택 안 함)",
     "초안을 걸면 강남 D1 7 → 1곳. 시민생활(탐색적)과 반대 방향이라 표시로 내림 (7-1)"],
    ["청년 남성", "동을 고르지 않는 도시 단위 '안내 강화' 1건 (L2-CITY-GUIDANCE, Level 3 불가)",
     "1인세대 비율과 청년 남성 고립 지표가 반대 방향(ρ −0.41). 연휴·기상 트리거만, 동절기(W)는 제외"],
    ["연휴 외지 손님 비중 (카드)", "도시 공통 하락 '확인 필요' + 연휴 창 + 외지 손님 ≥ 직전 8주 주말 +3%p → HOLD-HOLIDAY-TRAVEL",
     "판단 보류는 그대로, 다음 할 일만 '연휴 뒤 회복 확인'. 자동 기각 안 함"],
    ["시간대 상대 리듬·업종 다양성 (카드)", "맥락 칸만", "등급·반증에 안 씀"],
    ["R4 폭염", "기상청 특보 단계: 체감 33℃(주의보)·35℃(경보) 2일 이상, 체감 38℃ 또는 기온 39℃(중대경보)",
     "체감온도 = 기상청 여름철 식, 일 최고기온 × 일 최소 습도 근사. 예보 기록이 없어 상황 시연에서만"],
    ["명절 전·중·후", "R1·R2·R3 후보에 같은 묶음 ID와 3단계 시간표", "표시만"],
    ["수급자 비율·1인세대 순증", "구조 별도 칸·맥락 칸 (강남 수급자 상위 30% → 원인 유형 '경제 취약형')", "등급에 안 씀"],
], columns=["항목", "v1.1 규칙", "이유·한계"]).set_index("항목"))
if len(ver):
    r.h3("7-0. v1.0 → v1.1 후보 수가 바뀐 곳 (비교안 D)")
    r.table(ver.set_index("도시"))
    ver.to_csv(r.dir / "판비교_v10_v11.csv", index=False, encoding="utf-8-sig")
    r.p("- 후보 수가 바뀐 곳은 청년 남성(동 목록 → 도시 단위 안내 1건)뿐입니다. 이탈 후보의 규칙 이름 변화(HOLD-COUNTER → "
        "HOLD-HOLIDAY-TRAVEL)는 7-2에 따로 셉니다(이 표의 기준일 10/13에는 아직 '검토 중'이라 바뀌지 않음).")

r.h3("7-1. 집단 차별화 (추석 D-7, 예정된 점검)")
r.table(group_diff.set_index("도시"), fmt={"고령층과 같은 동 비율": "{:.2f}"})
group_diff.to_csv(r.dir / "집단차별화.csv", index=False, encoding="utf-8-sig")
cc_y = group_diff[(group_diff["도시"] == "춘천") & (group_diff["집단"] == "청년 남성")].iloc[0]
cc_m = group_diff[(group_diff["도시"] == "춘천") & (group_diff["집단"] == "중장년 남성 1인가구")].iloc[0]
r.p(f"- 춘천 청년 남성은 예전 16곳(중장년과 같은 동)에서 **도시 단위 안내 {int(cc_y['점검 후보'])}건**으로 바뀌었습니다. "
    f"중장년 남성은 {int(cc_m['점검 후보'])}곳(Level 3 {int(cc_m['Level 3'])}곳)으로 그대로입니다(동 순위는 1인세대 비율 + 접근성).")
r.p()
r.p("**남성 집단 대상 인구 조건 (초안, 채택 안 함)**: 결과를 보기 전에 '구조 상위 = 1인세대 상위 + 대상 성·연령 인구 비중 ≥ 도시 중앙값'을 "
    "적었습니다. 돌려 보니 강남에서 근거와 반대로 움직여 **등급 조건으로 채택하지 않고 후보의 '구조 별도 칸'(표시)으로만** 둡니다. "
    "결과를 보고 바꾼 결정이라 초안 결과를 그대로 공개합니다.")
r.table(draft_tab.set_index("도시"), fmt={c: "{:.2f}" for c in draft_tab.columns if "순위상관" in c})
draft_tab.to_csv(r.dir / "대상인구_초안비교.csv", index=False, encoding="utf-8-sig")
r.table(target_tab.set_index("도시"), fmt={"대상 인구 비중": "{:.3f}", "도시 중앙값": "{:.3f}"})
target_tab.to_csv(r.dir / "대상인구_조건.csv", index=False, encoding="utf-8-sig")
r.p("- 강남 1인세대 상위 동은 20·30대가 많아 40~64세 남성 '비중'이 중앙값 아래입니다. 그런데 시민생활(탐색적)은 1인세대 비율이 높은 동일수록 "
    "중장년 남성 고립 지표가 높다고 보여 줍니다(2장). 인구 '비중'은 1인세대 여부를 담지 못해 근거와 반대 방향이 됩니다.")
r.p("- 그래서 중장년 남성의 성별 신호는 카드 남성 코호트(이탈 경보·업종 다양성)와 이 표시 칸으로만 씁니다. "
    "춘천 집단별 후보가 같아지는 문제는 청년 남성 도시 단위 안내로 풀렸습니다(위 표).")
r.p()
r.p("**값마다 정한 시점** (`configs/rules.yaml` 주석과 같음). 결과 전 설정, 자료 노출 후 설정, 결과 후 결정을 하나의 '사전 설정'으로 묶지 않습니다.")
r.table(pd.DataFrame([
    ["구조 상위 30%·최소값 0.35·동 5곳 미만·세대 100", "사전 설정", "후보 결과·시민생활 비교 전 (v1.0)"],
    ["남성 집단 대상 인구 비중 중앙값 (초안)", "사전 설정", "v1.1 초안, 결과 전"],
    ["남성 집단 대상 인구 조건 false (등급에 안 씀)", "결과 후 결정", "초안 결과(강남 7 → 1곳)와 시민생활 방향을 보고 바꿈"],
    ["접근성 직선 5km", "자료 노출 후", "EDA I-3 거점 거리 분포(춘천 면 5~19km)를 본 뒤. 후보 결과 전"],
    ["이탈 크기 10%", "결과 후 결정", "2025 카드 결과(EDA-6 감지율)를 보고 정함"],
    ["지속 조건 1일 (끔)", "결과 후 결정", "2일 조건의 지연 증가를 본 뒤"],
    ["연휴 뒤 기대 경로 4일 (5일 이상 연휴)", "결과 후 결정", "2025 추석 뒤 자료를 보고 정함. 다음 명절에서 따로 확인 (불확실성 표 P-2)"],
    ["회복 3일 평균·−5%", "사전 설정", "EDA-6 기존 규칙 값을 그대로"],
    ["연휴 외지 손님 +3%p", "자료 노출 후", "조사 단계에서 평일 기준 숫자(춘천 17.7% → 28.6%)를 이미 봄"],
    ["청년 남성 도시 단위 안내", "탐색적 정책", "시민생활(강남 22개 동)에서 1인세대 비율과 반대 방향(ρ −0.41)을 보고 설계"],
    ["폭염 특보 단계 33·35·38℃", "사전 설정", "기상청 특보 기준을 그대로"],
], columns=["값", "정한 시점", "설명"]).set_index("값"))

r.h3("7-2. 카드가 바꾼 판단")
r.p("**(1) 연휴 외지 손님 비중** — 가맹점 도시 결제 중 다른 시도 거주 고객 비중(본사등록 업종 제외, 모든 연령). "
    "비교 기준은 연휴 직전 8주의 주말(토·일, 공휴일 아님)입니다.")
r.table(visitor_tab.set_index("연휴"), fmt={"연휴 중 외지 손님 비중": "{:.1%}", "직전 8주 주말": "{:.1%}",
                                          "주말 날별 표준편차": "{:.1%}", "차이(%p)": "{:+.1f}"})
visitor_tab.to_csv(r.dir / "연휴_외지손님.csv", index=False, encoding="utf-8-sig")
r.p("- 춘천은 추석·광복절 모두 외지 손님이 주말보다 크게 늘었고(주말 날별 흔들림의 몇 배), 강남은 기준(+3%p)에 못 미칩니다. "
    "도시 단위 신호라 '이 코호트 주민이 떠났다'는 뜻은 아닙니다.")
if len(card_changed):
    piv = card_changed.groupby(["도시", "집단", "v1.0", "v1.1", "v1.1 대응"]).size().rename("후보 수 (10/10~10/24 날마다 합)").reset_index()
    r.p()
    r.p("추석 뒤 날마다(10/10~10/24) 이탈 후보의 규칙이 v1.0과 달라진 경우:")
    r.table(piv.set_index("도시"))
card_dec.to_csv(r.dir / "카드_판단변화.csv", index=False, encoding="utf-8-sig")
n_dev_days = len(card_dec)
r.p(f"- 같은 기간 이탈 후보 {n_dev_days}건(날마다 셈) 중 {len(card_changed)}건의 규칙이 바뀌었습니다. 등급(판단 보류)은 같고 "
    "**다음 할 일**이 '추가 자료 확보'에서 '연휴 뒤 회복 확인'으로 바뀝니다. 강남은 외지 손님 급증이 없어 '연휴 이동으로 설명할 근거도 약함'이 사유에 붙습니다.")
r.p()
r.p("**(2) 시간대 상대 리듬** — 카드 데이터1 오전(6~11시) 결제 비중, 고령(60대·70대·80대+) − 40대. 같은 달 안의 차이라 일조 효과가 일부 지워집니다.")
r.table(rhythm_tab.set_index("도시"), fmt={"고령 오전 비중": "{:.3f}", "40대 오전 비중": "{:.3f}", "차이(%p)": "{:+.1f}"})
rhythm_tab.to_csv(r.dir / "시간대_상대리듬.csv", index=False, encoding="utf-8-sig")
r.p("- 춘천은 고령의 오전 쏠림이 강남보다 크고, 11~12월에 줄어듭니다. 동절기 점검 후보의 맥락 칸으로만 씁니다(외지인이 섞이고 한 해 관측).")
if len(div_tab):
    r.p()
    r.p("**(3) 업종 다양성** — 하루 결제 200건당 기대 업종 수(희박화 근사)의 최근 7일 ÷ 같은 요일 과거 8주 평균. 결제량이 줄어 업종 수가 기계적으로 줄어드는 효과를 뺍니다.")
    r.table(div_tab.set_index("도시"), fmt={c: "{:.2f}" for c in div_tab.columns if c not in ("도시", "코호트")})
    r.p("- 맥락 칸만. 1보다 낮으면 '평소보다 좁은 업종에서 결제'라는 뜻이고 고립·외출 감소를 뜻하지 않습니다.")

r.h3("7-3. 폭염 특보 단계 (R4)")
r.p("일 최고 체감온도(기상청 여름철 식, 일 최고기온 × 일 최소 상대습도) 기준. 2025-07-01~09-30, 그날과 다음 날 실측을 '예상' 대신 쓴 상황 시연 기준입니다.")
r.table(heat_tab.set_index("도시"), fmt={c: ("{:.1f}" if c == "가장 높은 체감" else "{:.0f}") for c in heat_tab.columns if c != "도시"})
heat_tab.to_csv(r.dir / "폭염_특보단계.csv", index=False, encoding="utf-8-sig")
r.p("- 단계는 후보의 `heat_stage` 칸과 트리거 문장에 남고, 단계별 안부 횟수는 대응 템플릿이 정합니다. 2025년 예보 기록이 없어 기준일 재생(기본)에서는 R4가 생기지 않습니다(변화 없음).")

r.h3("7-4. 명절 전·중·후 묶음")
r.table(timeline_tab.set_index("묶음"))
r.p("- R1·R2·R3 후보는 같은 `event_bundle_id`와 3칸 시간표(`event_timeline`)를 가집니다. R3 후보는 지금처럼 춘천 고령층만 생깁니다.")

r.h3("7-5. 기초생활수급자 비율 (강남, 구조 별도 칸)")
r.p("서울시 국민기초생활보장 수급자(동별, 2024년) 인원 ÷ 2025-07 주민등록 인구. 1인세대 비율과 합치지 않고 등급에 쓰지 않습니다. "
    "춘천은 동별 공개 자료가 없어 '자료 없음'으로 표시합니다.")
r.table(rec_tab.set_index("adm_cd"), fmt={"수급자 비율": "{:.3f}", "1인세대 비율": "{:.3f}"})
rec_tab.to_csv(r.dir / "수급자_비율.csv", index=False, encoding="utf-8-sig")
r.p(f"- 수급자 비율과 1인세대 비율의 순위상관 {rec_rho:.2f}. D1(강남 추석 D-7 중장년 남성) 7곳 중 '경제 취약형'이 붙는 동 {n_econ_d1}곳. "
    "분자(2024)와 분모(2025)의 연도가 다르고, 2024년 값은 2025-12-01 공개라 2025 가을 기준일에는 당시 없던 값입니다(근거 경고에 적음).")

r.h3("7-6. 1인세대 월 순증 (전입 맥락)")
r.table(net_tab.groupby("도시").head(5).set_index("도시"), fmt={"1인세대 월 순증률": "{:+.2%}"})
r.p("- 표시만. 순증에는 전입·전출·분가·사망이 섞이고, 이사 뒤 관계망 약화 근거는 방향이 섞여 위험 요인으로 쓰지 않습니다.")

r.h3("7-7. 카드 '행 없음 = 0' 가정의 민감도")
r.p("카드 daily()는 결제 행이 없는 (날짜, 코호트)를 0건으로 채웁니다. 이 칸을 0 대신 결측(미공급)으로 두고 경보를 다시 냈습니다.")
r.table(pd.DataFrame([zero_alarm]).T.rename(columns={0: "값"}))
r.p("업종·시간대로 더 쪼갠 단위에서 행이 없는 칸 비율 (등급 경로에는 쓰지 않는 단위):")
r.table(zero_tab.set_index("단위"), fmt={c: "{:.1%}" for c in zero_tab.columns if c not in ("단위", "도시")})
zero_tab.to_csv(r.dir / "행없음_비율.csv", index=False, encoding="utf-8-sig")
r.p(f"- 경보가 쓰는 도시 × 연령 × 성 × 일 패널에는 행이 없는 칸이 {n_missing_panel}칸이라 가정을 바꿔도 경보 {len(rz0)}건이 같습니다. "
    "'행 없음 = 0' 가정이 결과를 바꾸지 않는다는 뜻입니다.")
r.p("- 업종을 쪼개면 고령(특히 80대+)에서 행 없는 칸이 많습니다. 업종 단위 신호(예: 의료 세부)를 만들 때는 이 가정이 결과를 바꿀 수 있어 0 대신 결측으로 다뤄야 합니다. "
    "업종 다양성은 행 없는 업종이 기대 업종 수에 0으로 들어가 영향이 없습니다.")

r.h("6. 해석 상태")
r.table(pd.DataFrame([
    ["등급 규칙·임계값", "규칙으로 고정", "구조 임계값은 시민생활을 보기 전에 정함. 이탈 10%·기대 경로 4일·남성 조건 false는 결과 후, 5km·외지 +3%p는 자료 노출 후 (7-1 끝 표). 값은 민감도 범위와 함께만 말함"],
    ["강남 동 순위", "계산 재현", "주민등록만으로 정해짐. 통신·카드는 동 순위를 바꾸지 않음"],
    ["춘천 읍·면 Level 3", "계산 재현 (현재 원장 기준)", "접근성은 2026년 원장. 2025년 당시 확인 안 됨, strict 에서는 Level 2"],
    ["연휴 뒤 기대 경로 4일", "잠정", "추석 1회 관측. 설 연휴로 다시 확인 필요"],
    ["카드가 더하는 것", "확인 (작음)", "이 기준일들에서 목록을 바꾸는 폭은 작고, 반증(아닌지 걸러 주기)이 주된 몫"],
    ["연휴 외지 손님 기준 +3%p", "잠정 (자료 노출 후 설정)", "조사 단계에서 평일 기준 숫자를 이미 봄. 추석·광복절 2번 관측"],
    ["남성 집단 대상 인구 조건", "채택 안 함 (결과 후 결정)", "초안이 강남에서 시민생활(탐색적)과 반대. 표시로만"],
    ["청년 남성 도시 단위 안내", "탐색적 정책", "시민생활 관계를 보고 설계. 사전 설정 값이 아님"],
    ["폭염 특보 단계", "근사", "일자료로 낸 체감온도. 특보 발표 기록 아님, 상황 시연에서만"],
    ["수급자 비율", "표시만", "2024년 값, 강남만. 등급에 쓰지 않음"],
    ["시민생활 순위상관", "탐색적", "같은 공급자, 관측 기간 미확인, 22개 동"],
    ["페르소나", "합성 검증", "규칙이 설계대로 도는지 확인. 고립 탐지율 아님"],
], columns=["항목", "해석 상태", "설명"]).set_index("항목"))

out = r.save()
print("saved", out.relative_to(ROOT))
print(cm)
print(rng)
print(pers[["페르소나", "결과(이탈 후보)", "기대와 맞음"]])
