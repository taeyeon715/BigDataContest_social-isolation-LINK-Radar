"""R-1 후보·등급 규칙 평가 (configs/rules.yaml rules-v1.0)

실행: uv run python notebooks/r1_level_rules.py   (dev 스냅샷 자료가 있어야 함)
결과: reports/r1_등급규칙/ (README.md, figs/, *.csv). CSV 에는 비율·순위·등급·건수(후보 수)만 (원천 값 없음).

1. 결과표: 두 도시 × 기준일 3개 × 감시 집단 × 후보 종류 → 등급별 후보 수, K=5/10/20 목록 (rule_id 포함)
2. 비교안 A~D: Top-K 구성 변화(Jaccard), 근거 종류별 후보 수, 잘못된 단위 주장, 근거 누락, 강남 시민생활과의 순위 관계(탐색적)
3. 민감도: 임계값 ±, 지표 하나씩 빼기 → Top-10 겹침률, 후보 수 범위
4. 연휴 뒤 기대 경로, 이탈 지속 조건의 지연, 전체/필터 조회 같은 등급, 60~64세 남성, D1 후보 수
5. 페르소나 주입 (합성 검증, 실제 고립 탐지율 아님)
"""

from __future__ import annotations

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

# ------------------------------------------------------------ 리포트
r = Report("r1_등급규칙", "R-1 후보·등급 규칙 평가 (rules-v1.0)")
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
     "순위 모집단 = 도시 × 기준월 행정동 전체 (조회 필터와 무관). 0.35 = 전국 1인가구 비율(2023 인구주택총조사 35.5%)"],
    ["접근성", f"관측 거점까지 직선 {th['access']['far_km']:g}km 이상", "걸어서 1시간 넘는 거리. 공개 시점 모르는 현재 원장은 strict 에서 안 셈"],
    ["이탈 크기", f"평소 대비 {th['deviation']['min_drop']:.0%} 이상 하락", "EDA-6 합성 주입: 10% 하락 탐지 77%, 5% 하락 32%"],
    ["기준선 학습", f"{th['deviation']['min_train_days']}일 이상", "EDA-6 경보 규칙과 같음"],
    ["연휴 뒤 기대 경로", f"명절(연휴 {th['holiday_path']['long_block_days']}일 이상) 뒤 {th['holiday_path']['long_path_days']}일",
     "2025 추석 뒤 도시 공통요인이 4일 낮고 5일째 회복 (4-1 표). 경로 뒤 회복이면 기각 가능, 아니면 확인 필요"],
    ["Level 2", "(구조 상위 또는 접근성) + (예정된 트리거 또는 반증 통과 이탈)", "설계 §5.2"],
    ["Level 3", "예정된 점검 + 구조 상위 + 접근성 + 필수 근거 충족 + 신뢰도 보통 이상", "이탈 후보는 도시 단위라 Level 2 상한"],
    ["상한", "부분 판정 → 최대 Level 2, 근거 신뢰도 낮음 → 최대 Level 2", "CAP-PARTIAL, CAP-CONF-LOW"],
    ["진입", "예정된 점검은 구조 상위 또는 접근성 근거가 있는 동만", "22개 동 전부 후보 문제"],
    ["대응 종류", "Level 3 현장 확인, Level 2 담당자 추가 분석, 청년 남성 Level 2·3 안내 강화", "청년 고립은 1인세대 밀집으로 잡히지 않음"],
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
r.p(f"- **C(카드)**: '언제'와 '아닌지'를 더합니다. 예상 밖 이탈 후보 {n_dev_c}건이 새로 생기지만, 반증(연휴·도시 공통 변화·이탈 크기)을 거쳐 "
    f"Level 2 이상은 {n_dev_c_l2}건입니다. **이 기준일들에서 C가 검토 목록을 바꾸는 폭은 작습니다.** 카드의 몫은 주로 "
    "\"연휴 감소는 평소에도 있는 일인지\"를 걸러 주는 반증입니다(D3: 춘천 20대 여성 경보 기각, 강남 고령층 '확인 필요').")
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
    ax.scatter(x, d["후보수_기본"], color=CITY_COLOR[city], zorder=3, label="기본 (rules-v1.0)")
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
r.p("- 보조 판 결과는 1장 결과표의 '50~69세 남성' 줄입니다. 예정된 점검의 동 순위는 1인세대 비율로 정하므로 중장년 판과 같습니다.")

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

r.h("6. 해석 상태")
r.table(pd.DataFrame([
    ["등급 규칙·임계값", "규칙으로 고정", "시민생활을 보기 전에 정함. 값은 민감도 범위와 함께만 말함"],
    ["강남 동 순위", "계산 재현", "주민등록만으로 정해짐. 통신·카드는 동 순위를 바꾸지 않음"],
    ["춘천 읍·면 Level 3", "계산 재현 (현재 원장 기준)", "접근성은 2026년 원장. 2025년 당시 확인 안 됨, strict 에서는 Level 2"],
    ["연휴 뒤 기대 경로 4일", "잠정", "추석 1회 관측. 설 연휴로 다시 확인 필요"],
    ["카드가 더하는 것", "확인 (작음)", "이 기준일들에서 목록을 바꾸는 폭은 작고, 반증(아닌지 걸러 주기)이 주된 몫"],
    ["시민생활 순위상관", "탐색적", "같은 공급자, 관측 기간 미확인, 22개 동"],
    ["페르소나", "합성 검증", "규칙이 설계대로 도는지 확인. 고립 탐지율 아님"],
], columns=["항목", "해석 상태", "설명"]).set_index("항목"))

out = r.save()
print("saved", out.relative_to(ROOT))
print(cm)
print(rng)
print(pers[["페르소나", "결과(이탈 후보)", "기대와 맞음"]])
