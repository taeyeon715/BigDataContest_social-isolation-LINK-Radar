"""R-2 위험도 결과표 (동 × 집단 × 기준일), 지도·산점도, 지역×연령 비교 매트릭스, 카드·통신이 바꾼 판단

실행: uv run python notebooks/r2_risk_results.py   (dev 스냅샷 자료가 있어야 함. R-1·EDA-3·5·6·M1 리포트를 먼저 만듦)
결과: reports/r2_위험도결과/ (README.md, figs/, 동별_등급표.csv, 이탈_후보.csv, 요약표.csv, 판단변화.csv, 지역연령_매트릭스.csv)

- 등급은 R-1 과 같은 함수(rules.compare.run_variant, 비교안 D)로 다시 낸다. 손으로 옮겨 적은 숫자는 없다.
  그래서 요약표의 등급별 후보 수는 R-1 결과표와 같아야 한다 (tests/test_r2_results.py).
- 거리 칸은 규칙이 쓰는 값(get_access_gap: 안부·관계 유형 관측 거점 = 노인복지관·사회복지관 등까지 직선거리)이다.
  EDA-4 의 '가장 가까운 거점' 거리와 다르다.
- 인원 칸은 공개 주민등록(성·연령별 인구, 세대원수별 세대수) 값이다. 주민등록 세대 자료에는 연령·성이 없어
  "65세 이상 1인세대 수"·"중장년 남성 1인세대 수"는 동 단위로 알 수 없다. 그래서 1인세대 수(전 연령)와
  대상 집단 인구(성·연령)를 따로 적는다. 10 미만 값은 "<10" 으로 가린다.
- 원천(카드·통신) 값은 쓰지 않는다. 비율·순위·거리·등급·건수(후보 수)·공개 인구만.
"""

from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import pandas as pd
from matplotlib.collections import PolyCollection
from matplotlib.patches import Patch

from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.datasource import dev_available, dev_snapshot_id, open_datasource
from bigcontest.config import ROOT
from bigcontest.contracts import cohorts_config
from bigcontest.external import resident_households, resident_population
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.resources.match import load_needs, need_mask
from bigcontest.rules import load_rules, rules_version
from bigcontest.rules.compare import CARD_CONTEXT, collect, ctx_for, key_of, level_counts, run_variant

assert dev_available(), "dev 스냅샷 자료가 없음"
SID = dev_snapshot_id()
DS = open_datasource(SID)
RULES = load_rules()
VER = rules_version(RULES)
CITIES = ["강남", "춘천"]
# R-1 과 같은 기준일·집단 (notebooks/r1_level_rules.py)
DATES = {"추석 D-7": date(2025, 9, 26), "추석 뒤": date(2025, 10, 13), "동절기": date(2025, 12, 20)}
GROUPS = list(cohorts_config()["groups"])
K = max(RULES["selection"]["budgets"])
TYPE_KO = {"scheduled_access_review": "예정된 점검", "unexpected_activity_deviation": "예상 밖 이탈"}
LEVELS = ["Level 3", "Level 2", "Level 1", "Level 0", "판단 보류"]
NOT_CAND = "후보 아님"
LV_COLOR = {"Level 3": "#1F5FA8", "Level 2": "#5E9BD9", "Level 1": "#B9D3F0", "Level 0": "#DCE3EE",
            "판단 보류": "#9AA1AE", NOT_CAND: "#F4F5F7"}
SMALL = 10
CARD_IN = {"cohort_deviation", "card_cohort_series", "expected_activity", "holiday_expectation", "city_common_factor",
           "weather_asos", "academic_calendar"} | CARD_CONTEXT
COUNTER_IN = {"holiday_expectation", "city_common_factor", "holiday_visitor_context"}
TEL_IN = {"winter_contraction", "mobility_layer"}

names = DS.adm_names().set_index("adm_cd")


def small(v):
    """공개 인구라도 10 미만 칸은 가림 (작은 칸 규칙)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return "<10" if v < SMALL else int(round(v))


# ------------------------------------------------------------ 근거 모으기 (R-1 과 같은 순서·같은 도구)
EV, REQ = {}, {}
for city in CITIES:
    for dname, d in DATES.items():
        for g in GROUPS:
            c = ctx_for(SID, city, d, g)
            REQ[(city, dname, g)] = c.request
            EV[(city, dname, g)] = collect(c)
print("근거 수집 끝", len(EV))


def run(key, drop=None):
    city, dname, g = key
    return run_variant(EV[key], "D", REQ[key], DATES[dname], K, drop=drop).candidates


CANDS = {key: run(key) for key in EV}

# ------------------------------------------------------------ 공개 인구 (주민등록)
hh = resident_households()
pop = resident_population()
SERVICE = {g: v["service"] for g, v in cohorts_config()["groups"].items()}


def group_pop(month: int, g: str) -> pd.Series:
    s = SERVICE[g]
    p = pop[pop["month"] == month]
    m = p["age"] >= s["age_min"]
    if s.get("age_max") is not None:
        m &= p["age"] <= s["age_max"]
    if s.get("sex"):
        m &= p["sex"] == s["sex"]
    return p[m].groupby("adm_cd")["pop"].sum()


# 동 안 안부·관계 자원 수 (원장, 좌표로 배정한 동). 보호 자원 참고 (등급 입력 아님)
led = DS.ledger()
care_mask = need_mask(led, "안부·관계", load_needs()).to_numpy()
care_n = led[care_mask & (led["adm_cd"] != "")].groupby("adm_cd").size()

# ------------------------------------------------------------ 1. 동 × 집단 × 기준일 표
rows, dev_rows = [], []
for key, cands in CANDS.items():
    city, dname, g = key
    ev = EV[key]
    structs = [e for e in ev if S.input_of(e) == "structure_vulnerability" and e.status == "OK"]
    facts = S.structure_facts(structs)
    month = structs[0].observed_from.month if structs else None
    acc = {S.adm_of(e): S.access_axis(e) for e in ev if S.input_of(e) == "access_gap" and e.status == "OK"}
    def by_adm(key):
        return {S.adm_of(e): e for e in ev if S.input_of(e) == key and e.status == "OK" and S.adm_of(e)}

    rec_e, net_e, tp_e = by_adm("recipient_rate"), by_adm("one_person_net_change"), by_adm("structure_target_population")
    trig = next((e.value for e in ev if S.input_of(e) == "calendar_trigger" and e.status == "OK"), None)
    sched = {c.adm_cd: c for c in cands if c.candidate_type == "scheduled_access_review"}
    devs = [c for c in cands if c.candidate_type == "unexpected_activity_deviation"]
    first = {a for c in devs for a in c.check_first_adm}
    gp = group_pop(month, g) if month else pd.Series(dtype=float)
    h = hh[hh["month"] == month].set_index("adm_cd") if month else None
    adms = sorted(set(names.index[names["city"] == city]))
    for adm in adms + ([None] if None in sched else []):
        c = sched.get(adm)
        f = facts.get(adm) if adm else None
        km, counts, _, _ = acc.get(adm, (None, False, False, False))
        nm = names.loc[adm, "adm_nm"] if adm else "(도시 전체)"
        typ = names.loc[adm, "adm_type"] if adm else "-"
        one = h.loc[adm, "1인세대"] if (h is not None and adm in h.index) else None
        rows.append({
            "도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "adm_cd": adm or "", "동": nm, "유형": typ,
            "등급": c.candidate_level if c else NOT_CAND, "규칙 id": c.rule_id if c else "-",
            "대응 종류": c.response_type if c else "정기 모니터링", "근거 신뢰도": c.evidence_confidence if c else "-",
            "입력 상태": c.input_status if c else "-",
            "S1 1인세대 비율": round(f.value, 4) if f else None, "S1 도시 안 순위": f.rank if f else None,
            "S1 동 수": f.n if f else None, "S1 구조 상위": bool(f.top) if f else None,
            "A1 안부·관계 거점 거리(km)": round(km, 2) if km is not None else None, "A1 접근성 기준 넘음": bool(counts),
            "T1 트리거": c.trigger if c and c.trigger else (trig or "-"),
            "이탈 후보의 먼저 확인할 동": adm in first,
            "주민등록 기준월": f"2025-{month:02d}" if month else "-",
            "1인세대 수(전 연령)": small(one),
            "대상 집단 인구(성·연령)": small(gp.get(adm)) if adm else None,
            "대상 집단 정의": SERVICE[g]["label"],
            "동 안 안부·관계 자원 수(원장)": int(care_n.get(adm, 0)) if adm else None,
            "맥락: 대상 성·연령 인구 비중": round(float(tp_e[adm].value), 4) if adm in tp_e else None,
            "맥락: 기초생활수급자 비율(강남, 2024)": round(float(rec_e[adm].value), 4) if adm in rec_e else None,
            "맥락: 경제 취약형(수급자 비율 상위 30%)": bool(S.recipient_top(rec_e[adm])) if adm in rec_e else None,
            "맥락: 1인세대 월 순증률": round(float(net_e[adm].value), 4) if adm in net_e else None,
        })
    for c in devs:
        size = next((rc.value for rc in c.rule_checks if rc.condition == "이탈 크기 최소값"), None)
        dev_rows.append({
            "도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "분석 코호트": c.analysis_cohort,
            "반증 결과": c.counter_result, "반증 사유": c.counter_reason or "-", "등급": c.candidate_level,
            "규칙 id": c.rule_id, "대응 종류": c.response_type, "이탈 크기(평소 대비 하락)": round(size, 3) if isinstance(size, float) else None,
            "먼저 확인할 동(공유 맥락)": ", ".join(names.loc[a, "adm_nm"] for a in c.check_first_adm if a in names.index) or "-"})

tab = pd.DataFrame(rows)
dev = pd.DataFrame(dev_rows)

# 요약표 (R-1 결과표와 같은 모양): 도시 × 기준일 × 집단 × 후보 종류 → 등급별 후보 수
sm = []
for key, cands in CANDS.items():
    city, dname, g = key
    for t, tko in TYPE_KO.items():
        cs = [c for c in cands if c.candidate_type == t]
        sm.append({"도시": city, "기준일": f"{dname} ({DATES[dname]:%m/%d})", "집단": g, "후보 종류": tko, "후보 수": len(cs),
                   **level_counts(cs)})
summary = pd.DataFrame(sm)
# 표에서 다시 센 값이 엔진 결과와 같은지 (예정된 점검 = 동 행, 이탈 = 이탈 행)
chk = tab[tab["등급"] != NOT_CAND].groupby(["도시", "기준일", "집단", "등급"]).size()
for (city, dl, g, lv), n in chk.items():
    row = summary[(summary["도시"] == city) & (summary["기준일"] == dl) & (summary["집단"] == g) & (summary["후보 종류"] == "예정된 점검")]
    assert int(row[lv].iloc[0]) == n, (city, dl, g, lv)

# ------------------------------------------------------------ 2. 카드·통신이 바꾼 판단 (근거를 빼고 다시 냄)
chg = []
for key in EV:
    city, dname, g = key
    base = {key_of(c): c for c in CANDS[key]}
    alt = {name: {key_of(c): c for c in run(key, drop)} for name, drop in
           [("카드 반증 자료 뺌", COUNTER_IN), ("카드 전체 뺌", CARD_IN), ("통신 뺌", TEL_IN)]}
    for k in sorted(set(base) | {k for a in alt.values() for k in a}):
        b = base.get(k)
        lv = {n: (a[k].candidate_level if k in a else "후보 없음") for n, a in alt.items()}
        now = b.candidate_level if b else "후보 없음"
        if any(v != now for v in lv.values()):
            city_, kind, where, cohort = k.split("|")
            chg.append({"도시": city, "기준일": dname, "집단": g, "후보": "이탈" if kind == "이탈" else "예정된 점검",
                        "동": names.loc[where, "adm_nm"] if where in names.index else "(도시 단위)", "분석 코호트": cohort,
                        "전체 근거 등급": now, "전체 근거 규칙": b.rule_id if b else "-",
                        "반증 결과": b.counter_result if b else "-", **lv})
changes = pd.DataFrame(chg)

# EDA-6 실제 경보 38건의 반증 결과 (카드가 '아닌지'를 가린 몫)
alarms = pd.read_csv(ROOT / "reports" / "eda6_사전경보재평가" / "경보_목록.csv")
alarm_rev = alarms["review"].value_counts()
alarm_common = int(alarms["city_common_flag"].sum())

# ------------------------------------------------------------ 3. 지역×연령 비교 매트릭스 (이미 있는 결과 모으기)


def md_table(path, heading: str, nth: int = 0) -> pd.DataFrame:
    """리포트 README 에서 heading 이 들어간 제목 아래(다음 '## ' 제목 전까지) nth 번째 표를 읽는다.
    리포트를 다시 만들면 값도 따라 바뀐다 (손으로 옮겨 적지 않음)."""
    lines = (ROOT / "reports" / path / "README.md").read_text(encoding="utf-8").splitlines()
    i = next(j for j, ln in enumerate(lines) if ln.lstrip().startswith("#") and heading in ln)
    tables, cur = [], []
    for ln in lines[i + 1:] + [""]:
        if ln.startswith("## "):
            break
        if ln.startswith("|"):
            cur.append(ln)
        elif cur:
            tables.append(cur)
            cur = []
    if cur:
        tables.append(cur)
    t = tables[nth]
    head = [c.strip() for c in t[0].strip().strip("|").split("|")]
    body = [[c.strip() for c in r_.strip().strip("|").split("|")] for r_ in t[2:]]
    return pd.DataFrame(body, columns=head)


def pct(s: str) -> float:
    return float(str(s).replace("%", "").replace("+", "")) / 100


AGES = ["20대", "30대", "40대", "50대", "60대", "70대", "80대+"]
mix = md_table("eda3_카드기초", "1. 연령대별 소비 구성").set_index("연령")
trend = md_table("eda3_카드기초", "6. 월별 추세").set_index("연령")
hol = md_table("eda5_인사이트재현", "3. 연휴 공백").set_index("연령")
det = pd.read_csv(ROOT / "reports" / "eda6_사전경보재평가" / "탐지_결과.csv")
det = det[det["path"] == "local"].set_index(["city", "age_g"])
m1 = pd.read_csv(ROOT / "reports" / "m1_기대활동모델" / "예측_지표_연령.csv")
m1 = m1[(m1["model"] == "0b") & (m1["forecast_horizon"] == 1)].set_index(["city", "age_g"])
p8 = pop[pop["month"] == 8].copy()
p8["city"] = names.reindex(p8["adm_cd"])["city"].to_numpy()
p8["band"] = pd.cut(p8["age"], [19, 29, 39, 49, 59, 69, 79, 200], labels=AGES)
share = p8.dropna(subset=["band"]).groupby(["city", "band"], observed=True)["pop"].sum()
share = share / p8.groupby("city")["pop"].sum()

mat = []
for a in AGES:
    row = {"연령": a}
    for city in CITIES:
        row[f"인구 비중 {city}"] = share.get((city, a))
    for city in CITIES:
        row[f"의료 결제 비중 {city}"] = pct(mix.loc[a, f"{city} 의료"])
    for city in CITIES:
        row[f"12월 결제 비중 변화 {city}(7월=100)"] = float(trend.loc[a, f"{city} 12월"])
    for city in CITIES:
        row[f"연휴 병원·의원 깊이 {city}"] = pct(hol.loc[a, f"깊이: 병원·의원 {city}"])
    for city in CITIES:
        row[f"연휴 뒤 생필품 회복 {city}"] = pct(hol.loc[a, f"회복: 생필품 {city}"])
    for city in CITIES:
        row[f"15% 하락 감지율 {city}"] = det.loc[(city, a), "15% 탐지"]
    for city in CITIES:
        row[f"평소 예측 오차 {city}(h=1 상대 MAE)"] = m1.loc[(city, a), "rel_mae"]
    mat.append(row)
matrix = pd.DataFrame(mat).set_index("연령")

# 생애단계 작은 표: 강남 시민생활 (외출·소통) + 통신 60+ 겨울 (춘천 읍·면)
sl = DS.seoul_living()
sl = sl[sl["adm_cd"] != ""]
STAGE = [("청년(20~39)", 20, 39), ("중장년(40~64)", 40, 64), ("노년(65+)", 65, 200)]
stage_rows = []
for nm_, lo, hi in STAGE:
    d = sl[(sl["age"] >= lo) & (sl["age"] <= hi)]
    r_ = {"생애단계": nm_}
    for sx in ("남", "여"):
        x = d[d["sex"] == sx]
        r_[f"1인가구 비율 {sx}"] = x["single"].sum() / x["pop"].sum()
        r_[f"1인가구 중 소통 적음 {sx}"] = x["comm_low_single"].sum() / x["single"].sum()
    r_["소통 적음 남/여 배율"] = r_["1인가구 중 소통 적음 남"] / r_["1인가구 중 소통 적음 여"]
    stage_rows.append(r_)
stage = pd.DataFrame(stage_rows).set_index("생애단계")
winter = md_table("eda5_인사이트재현", "4. 춘천 읍·면 주민 거주지의 겨울 감소")

# ------------------------------------------------------------ 리포트
r = Report("r2_위험도결과", "R-2 위험도 결과표: 동 × 집단 × 기준일")
r.p(f"R-1 등급 규칙([`configs/rules.yaml`](../../configs/rules.yaml) **{VER}**)을 동 이름·근거 값·대상 인구와 한 줄로 붙인 결과표입니다. "
    "**위험도는 점수가 아니라 5단계 대응 체계**(Level 3 우선 현장 확인 / Level 2 담당자 추가 분석 / Level 1 추세 관찰 / "
    "Level 0 정기 모니터링 / 판단 보류 자료 더 필요)입니다. 등급은 규칙이 정하고, 사람이나 가구를 판정하지 않습니다.")
r.p()
r.p("- 지표 정의(S1 구조·A1 접근성·T1 시점·D1 이탈·C1 반증·W1 날씨·Q1 근거 신뢰도)는 [위험 지표 정의서](../../docs/위험_지표.md)에 있습니다.")
r.p("- 등급은 R-1 과 같은 함수(비교안 D)로 다시 냈습니다. 아래 요약표의 등급별 후보 수는 [R-1 결과표](../r1_등급규칙/README.md)와 같습니다(테스트로 확인).")
r.p("- **A1 거리**는 규칙이 쓰는 '안부·관계 유형 관측 거점(노인복지관·사회복지관·경로당·돌봄 수행기관 등)까지 직선거리'입니다. "
    "EDA-4 의 '가장 가까운 거점'(행정복지센터 포함)과 다른 값입니다. 행정복지센터는 신청 창구라 안부·관계 거점으로 세지 않습니다.")
r.p("- 청년 남성은 rules-v1.1 부터 동을 고르지 않고 도시 단위 '안내 강화' 후보 한 건(`L2-CITY-GUIDANCE`)입니다. 그래서 동 행은 모두 '후보 아님'이고 '(도시 전체)' 행이 따로 있습니다.")
r.p("- '맥락:' 칸(대상 성·연령 인구 비중, 수급자 비율, 경제 취약형, 1인세대 월 순증)은 rules-v1.1 의 표시용 칸이며 등급·순서에 쓰지 않습니다.")
r.p("- 접근성은 2026년 수집 원장이라 '현재 원장 기준 접근성'입니다(2025년 당시 운영·위치는 확인 안 됨).")
r.p("- **인원 칸**: 주민등록 세대 자료에는 연령·성이 없어 '65세 이상 1인세대 수'·'중장년 남성 1인세대 수'는 동 단위로 알 수 없습니다. "
    "그래서 **1인세대 수(전 연령)**와 **대상 집단 인구(주민등록 성·연령, 1인 여부 모름)**를 따로 적었습니다. 10 미만은 '<10'으로 가립니다.")

r.h("1. 요약: 도시 × 기준일 × 집단 (등급별 후보 수)")
show = summary[summary["후보 수"] > 0].copy()
r.table(show.set_index("도시"))
summary.to_csv(r.dir / "요약표.csv", index=False, encoding="utf-8-sig")


def people(city, dl, g, levels=("Level 3", "Level 2")):
    t = tab[(tab["도시"] == city) & (tab["기준일"] == dl) & (tab["집단"] == g) & tab["등급"].isin(levels)
            & (tab["adm_cd"].fillna("") != "")]  # 동 행만 (청년 남성 도시 단위 후보는 따로 셈)

    def num(col):  # "<10" 칸은 더하지 않음 (가린 값)
        return int(pd.to_numeric(t[col], errors="coerce").fillna(0).sum())
    return len(t), num("1인세대 수(전 연령)"), num("대상 집단 인구(성·연령)")


r.h3("후보 동에 사는 사람 (공개 주민등록, Level 2 이상)")
pp_rows = []
for city in CITIES:
    for dname in DATES:
        dl = f"{dname} ({DATES[dname]:%m/%d})"
        for g in GROUPS[:3]:
            n, one, gp_ = people(city, dl, g)
            n3, one3, gp3 = people(city, dl, g, ("Level 3",))
            n_city_any = ((tab["도시"] == city) & (tab["기준일"] == dl) & (tab["집단"] == g) & (tab["adm_cd"].fillna("") == "")).any()
            if n or n_city_any:
                n_city = int(((tab["도시"] == city) & (tab["기준일"] == dl) & (tab["집단"] == g)
                              & (tab["adm_cd"].fillna("") == "") & tab["등급"].isin(["Level 3", "Level 2"])).sum())
                pp_rows.append({"도시": city, "기준일": dl, "집단": g, "도시 단위 후보": n_city, "Level 2 이상 동": n, "그 동 1인세대 수": one,
                                "그 동 대상 집단 인구": gp_, "Level 3 동": n3, "Level 3 동 1인세대 수": one3,
                                "Level 3 동 대상 집단 인구": gp3})
ppl = pd.DataFrame(pp_rows)
r.table(ppl.set_index("도시"), fmt={c: "{:,}" for c in ppl.columns if c not in ("도시", "기준일", "집단")})
r.p("- '대상 집단 인구'는 그 동에 등록된 해당 성·연령 인구 전체입니다(혼자 사는지 모름). '1인세대 수'는 모든 연령의 1인세대입니다. "
    "두 값을 곱하거나 나눠 '고령 1인세대 수'를 만들지 않습니다.")
r.p("- '도시 단위 후보'는 청년 남성 안내 강화처럼 동을 고르지 않는 후보입니다. 동 인원 칸에 넣지 않습니다.")
ppl.to_csv(r.dir / "후보동_인구.csv", index=False, encoding="utf-8-sig")

r.h("2. 결과표 A: 동 × 집단 × 기준일 (Level 2 이상, 추석 D-7 · 고령층)")
n_city_rows = int((tab["동"] == "(도시 전체)").sum())
r.p(f"전체 {len(tab)}행 = **{len(tab) - n_city_rows}개 동·집단·기준일 행**(두 도시 47개 동 × 집단 {len(GROUPS)} × 기준일 {len(DATES)}) "
    f"+ **도시 단위 {n_city_rows}행**(청년 남성 안내 강화, 동을 고르지 않음)이고 [`동별_등급표.csv`](동별_등급표.csv)에 있습니다. "
    "본문에는 추석 D-7 고령층의 Level 2 이상만 싣습니다.")
COLS = ["동", "유형", "등급", "규칙 id", "S1 1인세대 비율", "S1 도시 안 순위", "S1 동 수", "A1 안부·관계 거점 거리(km)",
        "1인세대 수(전 연령)", "대상 집단 인구(성·연령)", "동 안 안부·관계 자원 수(원장)"]
d7 = tab[(tab["기준일"].str.startswith("추석 D-7")) & (tab["집단"] == "고령층") & tab["등급"].isin(["Level 3", "Level 2"])]
d7 = d7.sort_values(["도시", "등급", "S1 도시 안 순위"], ascending=[True, False, True])
fmt = {"S1 1인세대 비율": "{:.2f}", "S1 도시 안 순위": "{:.0f}", "S1 동 수": "{:.0f}", "A1 안부·관계 거점 거리(km)": "{:.1f}"}
r.table(d7.set_index("도시")[COLS], fmt=fmt)
tab.to_csv(r.dir / "동별_등급표.csv", index=False, encoding="utf-8-sig")

r.h3("예상 밖 이탈 후보 (카드, 도시 단위)")
r.p("카드 자료는 도시 × 성 × 연령 단위라 동을 직접 가리키지 않습니다. 동은 '먼저 확인할 동(공유 맥락)'으로만 붙고, 등급은 Level 2가 상한입니다.")
if len(dev):
    r.table(dev.drop(columns=["반증 사유"]).set_index("도시"), fmt={"이탈 크기(평소 대비 하락)": "{:.1%}"})
    r.p("- 반증 사유 전문은 [`이탈_후보.csv`](이탈_후보.csv)에 있습니다. 이탈 크기 = 경보 창 평균이 평소보다 낮은 비율(D1).")
dev.to_csv(r.dir / "이탈_후보.csv", index=False, encoding="utf-8-sig")

# ---- 그림: 지도 2장
geo = json.loads((ROOT / "ref" / "adm_boundary.geojson").read_text(encoding="utf-8"))


def polys(feat):
    g = feat["geometry"]
    parts = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    return [np.array(p[0]) for p in parts]


def city_map(city: str, group: str = "고령층"):
    feats = [f for f in geo["features"] if f["properties"]["city"] == city]
    fig, axes = plt.subplots(1, len(DATES), figsize=(4.2 * len(DATES), 4.6 if city == "강남" else 5.2))
    for ax, (dname, d) in zip(axes, DATES.items()):
        sub = tab[(tab["도시"] == city) & (tab["기준일"] == f"{dname} ({d:%m/%d})") & (tab["집단"] == group)]
        lv = dict(zip(sub["adm_cd"], sub["등급"]))
        verts, cols, labels = [], [], []
        for f in feats:
            a = f["properties"]["adm_cd"]
            for p in polys(f):
                verts.append(p)
                cols.append(LV_COLOR[lv.get(a, NOT_CAND)])
            big = city == "강남" or f["properties"]["adm_nm"].endswith(("읍", "면"))
            if lv.get(a) in ("Level 3", "Level 2") and big:
                xy = np.vstack(polys(f)).mean(axis=0)
                labels.append((xy, f["properties"]["adm_nm"], lv[a]))
        ax.add_collection(PolyCollection(verts, facecolors=cols, edgecolors="#555", linewidths=0.5))
        for xy, nm_, l_ in labels:
            ax.text(xy[0], xy[1], nm_, fontsize=6.5, ha="center", va="center",
                    color="white" if l_ == "Level 3" else "#1a202c")
        ax.autoscale_view()
        ax.set_aspect(1 / np.cos(np.radians(37.5)))
        ax.axis("off")
        small_l2 = [nm_ for a, nm_ in zip(sub["adm_cd"], sub["동"]) if lv.get(a) in ("Level 3", "Level 2")
                    and city == "춘천" and not nm_.endswith(("읍", "면"))]
        if small_l2:
            ax.text(0.0, -0.02, "시내 동: " + "·".join(small_l2), transform=ax.transAxes, fontsize=6.5, va="top")
        n3, n2 = (sub["등급"] == "Level 3").sum(), (sub["등급"] == "Level 2").sum()
        ax.set_title(f"{dname} ({d:%m/%d}) · L3 {n3} · L2 {n2}", fontsize=9)
    handles = [Patch(facecolor=LV_COLOR[k], edgecolor="#555", label=k) for k in ["Level 3", "Level 2", "Level 1", NOT_CAND]]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(f"{city} {group}: 예정된 점검 등급 ({VER}, 현재 원장 기준 접근성)", fontsize=10)
    return fig


r.h("3. 지도와 산점도")
r.fig(city_map("강남"), "f01_map_gangnam.png", "강남 고령층 등급 지도 (기준일 3개)")
r.fig(city_map("춘천"), "f02_map_chuncheon.png", "춘천 고령층 등급 지도 (기준일 3개)")
r.p("- 색 = 등급(대시보드와 같은 색). 흰 칸 = 후보 아님(정기 모니터링). 동 경계: vuski/admdongkor (CC BY 4.0).")

# ---- 그림: 구조 × 접근성 산점도
fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), sharey=True)
th = RULES["thresholds"]
for ax, city in zip(axes, CITIES):
    sub = tab[(tab["도시"] == city) & (tab["기준일"].str.startswith("추석 D-7")) & (tab["집단"] == "고령층") & (tab["adm_cd"] != "")]
    for lv_ in [NOT_CAND, "Level 1", "Level 2", "Level 3"]:
        s_ = sub[sub["등급"] == lv_]
        if len(s_):
            ax.scatter(s_["S1 1인세대 비율"], s_["A1 안부·관계 거점 거리(km)"].clip(lower=0.05), s=46,
                       c=LV_COLOR[lv_], edgecolors=np.where(s_["S1 구조 상위"], "#1a202c", "#a0aec0"), linewidths=1.0, label=lv_)
    for _, x in sub[sub["등급"].isin(["Level 3", "Level 2"])].iterrows():
        ax.annotate(x["동"], (x["S1 1인세대 비율"], max(x["A1 안부·관계 거점 거리(km)"], 0.05)), fontsize=6.5,
                    xytext=(3, 3), textcoords="offset points")
    ax.axvline(th["structure"]["abs_min"], ls="--", color="#718096", lw=0.8)
    ax.axhline(th["access"]["far_km"], ls="--", color="#718096", lw=0.8)
    cut = sub[sub["S1 구조 상위"] == True]["S1 1인세대 비율"].min()  # noqa: E712
    if pd.notna(cut):
        ax.axvline(cut, ls=":", color=CITY_COLOR[city], lw=0.8)
    ax.set_yscale("log")
    ticks = [0.05, 0.1, 0.3, 1, 3, 5, 10, 20]
    ax.set_yticks(ticks, [f"{t:g}" for t in ticks])
    ax.minorticks_off()
    ax.set_xlabel("S1 1인세대 비율 (점선 0.35, 점 테두리 진함 = 도시 안 상위 30%)")
    ax.set_title(f"{city}: 구조 × 접근성 (추석 D-7 · 고령층)", fontsize=10)
axes[0].set_ylabel("A1 안부·관계 거점까지 직선거리 km (log, 점선 5km)")
hs = [plt.Line2D([], [], marker="o", ls="", markerfacecolor=LV_COLOR[k], markeredgecolor="#1a202c", label=k)
      for k in ["Level 3", "Level 2", "Level 1", NOT_CAND]]
fig.legend(handles=hs, loc="lower center", ncol=4, fontsize=8, frameon=False, bbox_to_anchor=(0.5, -0.04))
r.fig(fig, "f03_structure_access.png", "구조(S1) × 접근성(A1) 산점도, 색 = 등급")
gn_sub = tab[(tab["도시"] == "강남") & (tab["기준일"].str.startswith("추석 D-7")) & (tab["집단"] == "고령층") & (tab["adm_cd"] != "")]
r.p("- 오른쪽 위(1인세대 상위 + 거점 5km 이상)에 있으면 Level 3, 한쪽만 넘으면 Level 2입니다. "
    f"강남은 모든 동이 거점 {gn_sub['A1 안부·관계 거점 거리(km)'].max():.2f}km 이내라 Level 3가 없습니다.")

# ---- 해석 문단 4개 (숫자는 위 표에서 계산)
r.h("4. 읽는 법 (해석 문단)")


def lv_names(city, dname, g, lv):
    t = tab[(tab["도시"] == city) & (tab["기준일"].str.startswith(dname)) & (tab["집단"] == g) & (tab["등급"] == lv)]
    return t.sort_values("S1 도시 안 순위")["동"].tolist()


gn_l2 = lv_names("강남", "추석 D-7", "고령층", "Level 2")
gn_all = tab[(tab["도시"] == "강남") & (tab["기준일"].str.startswith("추석 D-7")) & (tab["집단"] == "고령층") & (tab["adm_cd"] != "")]
gn_maxkm = gn_all["A1 안부·관계 거점 거리(km)"].max()
rng_csv = pd.read_csv(ROOT / "reports" / "r1_등급규칙" / "후보수_범위.csv")
gr = rng_csv[(rng_csv["도시"] == "강남") & (rng_csv["기준일"] == "추석 D-7") & (rng_csv["집단"] == "고령층")].iloc[0]
n_gn, one_gn, gp_gn = people("강남", f"추석 D-7 ({DATES['추석 D-7']:%m/%d})", "고령층")
r.p(f"**① 강남은 '어디'가 1인세대로 정해지고 Level 3가 없습니다.** 추석 D-7 고령층 후보 {len(gn_l2)}곳({'·'.join(gn_l2)})은 모두 "
    f"1인세대 비율 상위 동이고, 22개 동 모두 안부·관계 거점이 {gn_maxkm:.2f}km 이내라 접근성 근거가 없습니다. 그래서 대응은 '담당자 추가 분석'입니다. "
    f"이 {n_gn}개 동의 전 연령 1인세대는 {one_gn:,}세대, 65세 이상 주민등록 인구 전체는 {gp_gn:,}명입니다(1인 여부와 별개). "
    "청년 남성은 동을 고르지 않고 도시 단위 '안내 강화' 1건입니다. "
    f"임계값을 바꾸면 후보 수(이탈 후보 포함)는 {int(gr['후보수_최소'])}~{int(gr['후보수_최대'])}건입니다(기본 {int(gr['후보수_기본'])}건).")
r.p()
cc3 = {d: lv_names("춘천", d, "고령층", "Level 3") for d in DATES}
same3 = sorted(set.intersection(*(set(v) for v in cc3.values())))
cc_rows = tab[(tab["도시"] == "춘천") & (tab["기준일"].str.startswith("추석 D-7")) & (tab["집단"] == "고령층") & tab["동"].isin(cc3["추석 D-7"])]
kms = cc_rows["A1 안부·관계 거점 거리(km)"]
n_cc, one_cc, gp_cc = people("춘천", f"추석 D-7 ({DATES['추석 D-7']:%m/%d})", "고령층", ("Level 3",))
if len(cc3["추석 D-7"]):
    r.p(f"**② 춘천은 면 지역 {len(cc3['추석 D-7'])}곳이 '우선 현장 확인'(Level 3)입니다.** {'·'.join(cc3['추석 D-7'])}은 1인세대 비율 상위이면서 "
        f"안부·관계 거점까지 {kms.min():.0f}~{kms.max():.0f}km입니다. 세 기준일 모두 Level 3인 곳: {'·'.join(same3) or '없음'}. "
        f"이 {n_cc}곳의 65세 이상 주민등록 인구 전체는 {gp_cc:,}명, 전 연령 1인세대는 {one_cc:,}세대입니다(고령 1인세대 수가 아님). "
        "거리는 2026년 원장 기준이라, 이 Level 3는 2025 생활 신호 + 현재 원장으로 계산한 대응 우선순위 시연입니다.")
else:
    r.p("**② 경로당 보강 뒤 춘천 고령층 Level 3는 0곳입니다.** 기존 남면·남산면·북산면은 공식 경로당을 포함하면 가장 가까운 관측 관계 거점이 5km 안으로 들어와 Level 2가 됩니다. 이는 안전 판정이 아니라 거리 근거에 의한 상향 조건이 사라졌다는 뜻입니다. 운영·회원 가입·휴일 개방은 별도 확인합니다. [접근성 감사](../a1_접근성검증/README.md)에 전후 비교를 공개합니다.")

r.p()
gn_w = summary[(summary["도시"] == "강남") & summary["기준일"].str.startswith("동절기")]["후보 수"].sum()
r.p(f"**③ 강남 동절기 후보는 {int(gn_w)}곳입니다.** 강남에는 동절기·한파 규칙이 아직 없기 때문이지 '강남은 겨울에 안전하다'는 뜻이 아닙니다. "
    "동절기(W) 트리거는 춘천 읍·면 겨울 체류 변화(통신, W1)를 근거로 하는 춘천 규칙입니다.")
r.p()
cc_after = dev[(dev["도시"] == "춘천") & dev["기준일"].str.startswith("추석 뒤") & (dev["집단"] == "고령층")]
n_hold = int((cc_after["등급"] == "판단 보류").sum())
n_l2d = int((cc_after["등급"] == "Level 2").sum())
r.p(f"**④ 생활 변화 신호(카드)는 동을 가리키지 않습니다.** 카드가 도시 단위라 이탈 후보는 Level 2가 상한이고, 추석 뒤 춘천 고령층 신호 "
    f"{len(cc_after)}건 중 {n_hold}건은 '자료 더 필요'(판단 보류), {n_l2d}건은 반증을 통과해 Level 2입니다. "
    f"2025년 하반기 실제 경보 {len(alarms)}건 중 {alarm_common}건은 도시 전체가 함께 낮아진 '도시 공통 변화'로 분류됐습니다(EDA-6). "
    f"rules-v1.1 의 연휴 이동 보류(`HOLD-HOLIDAY-TRAVEL`, 외지 손님 급증)는 이 기준일들에서 {int(dev['규칙 id'].astype(str).str.startswith('HOLD-HOLIDAY-TRAVEL').sum())}건입니다.")

r.h("5. 카드·통신이 바꾼 판단")
r.p("같은 근거에서 카드·통신 근거만 빼고 규칙을 다시 돌렸을 때 등급이 달라지는 후보입니다. 바뀌지 않은 후보는 싣지 않습니다. "
    "('카드 반증 자료 뺌' = 연휴 기대값·도시 공통요인·연휴 외지 손님 비중만 뺌, '카드 전체 뺌' = 결제 시계열·이탈·반증 모두 뺌, '통신 뺌' = 체류 구성·읍·면 겨울 변화 뺌)")
if len(changes):
    ct_ = changes.copy()
    ct_["바뀐 것"] = np.select(
        [ct_["후보"] == "이탈", ct_["후보"] == "예정된 점검"],
        ["이탈: " + ct_["전체 근거 등급"] + " → 반증 자료 없으면 " + ct_["카드 반증 자료 뺌"],
         "예정된 점검: " + ct_["전체 근거 등급"] + " → 통신 없으면 " + ct_["통신 뺌"]], default="-")
    ct_ = ct_[((ct_["후보"] == "이탈") & (ct_["카드 반증 자료 뺌"] != ct_["전체 근거 등급"]))
              | ((ct_["후보"] == "예정된 점검") & (ct_["통신 뺌"] != ct_["전체 근거 등급"]))]
    agg = ct_.groupby(["도시", "기준일", "바뀐 것"]).agg(후보_수=("동", "size"),
                                                       예=("동", lambda x: ", ".join(sorted(set(x)))[:40])).reset_index()
    agg = agg.rename(columns={"후보_수": "후보 수(집단 합)", "예": "동"})
    r.table(agg.set_index("도시"))
    n_dev_all = int((changes["후보"] == "이탈").sum())
    n_dev_hold = int(((changes["후보"] == "이탈") & (changes["카드 반증 자료 뺌"] == "판단 보류")).sum())
    n_dev_now_hold = int(((changes["후보"] == "이탈") & (changes["전체 근거 등급"] == "판단 보류")).sum())
    tel_mask = (changes["후보"] == "예정된 점검") & (changes["통신 뺌"] != changes["전체 근거 등급"])
    n_tel = int(tel_mask.sum())
    n_tel_dong, n_tel_grp = changes[tel_mask]["동"].nunique(), changes[tel_mask]["집단"].nunique()
    r.p(f"- **카드 반증**: 기준일·집단을 합친 이탈 후보 행 {n_dev_all}개는 반증 자료(연휴 기대값·도시 공통요인, 카드)가 없으면 모두 '판단 보류'입니다. "
        f"반증 자료가 있으면 {n_dev_all - n_dev_now_hold}행이 결론(통과 → Level 1·2)을 얻고, {n_dev_now_hold}행은 연휴 뒤 회복을 아직 못 봐 보류로 남습니다 "
        "(행 수이지 고립 사례 수가 아님). "
        "카드를 모두 빼면 이탈 후보 자체가 없습니다(예정된 점검은 그대로).")
    r.p(f"- **통신**: 춘천 동절기 Level 3 {n_tel}행(후보 동 {n_tel_dong}곳 × 집단 {n_tel_grp}개)은 읍·면 겨울 체류 변화(W1, 통신)가 없으면 필수 근거가 빠져 "
        "부분 판정(CAP-PARTIAL) → Level 2입니다. 통신은 동 순위를 바꾸지 않지만 Level 3의 필수 근거를 채웁니다.")
changes.to_csv(r.dir / "판단변화.csv", index=False, encoding="utf-8-sig")
r.p(f"- EDA-6 실제 경보 {len(alarms)}건의 반증 결과: " + " · ".join(f"{k} {v}" for k, v in alarm_rev.items())
    + f". '확인 필요' {alarm_rev.get('확인 필요', 0)}건은 모두 도시 공통 변화(도시 대비 추가 이탈 없음)입니다.")
r.p("- 역할 정리는 [데이터 역할](../../docs/데이터_역할.md)에 있습니다: 어디 = 주민등록, 언제 = 달력·날씨, 아닌지 = 통신·카드, 무엇을 = 자원.")

r.h("6. 지역 × 연령 비교 매트릭스")
r.p("이미 있는 리포트의 숫자를 한 장에 모았습니다(새 분석 없음). 출처: 주민등록(인구 비중, 2025-08), "
    "[EDA-3](../eda3_카드기초/README.md) 1·6절, [EDA-5](../eda5_인사이트재현/README.md) 3절, [EDA-6](../eda6_사전경보재평가/README.md) 4절, "
    "[M1](../m1_기대활동모델/README.md) 1절. 카드 연령은 '가맹점 도시 × 고객 거주 시도' 기준입니다.")
FMT_M = {c: "{:.0%}" for c in matrix.columns}
FMT_M.update({c: "{:.0f}" for c in matrix.columns if "7월=100" in c})
FMT_M.update({c: "{:.1%}" for c in matrix.columns if "오차" in c or "인구 비중" in c})
r.table(matrix, fmt=FMT_M)
matrix.reset_index().to_csv(r.dir / "지역연령_매트릭스.csv", index=False, encoding="utf-8-sig")

hm_cols = [c for c in matrix.columns if not c.startswith("인구 비중")]
fig, ax = plt.subplots(figsize=(13, 3.8))
z = matrix[hm_cols].astype(float)
metric = {c: re.sub(r" (강남|춘천)", "", c) for c in hm_cols}  # 같은 지표의 두 도시 칸은 같은 눈금으로 색칠
zn = z.copy()
for m_ in set(metric.values()):
    cs_ = [c for c in hm_cols if metric[c] == m_]
    lo_, hi_ = z[cs_].min().min(), z[cs_].max().max()
    zn[cs_] = (z[cs_] - lo_) / (hi_ - lo_)
ax.imshow(zn.to_numpy(), cmap="Blues", aspect="auto", vmin=-0.1, vmax=1.2)
for i in range(z.shape[0]):
    for j, c in enumerate(hm_cols):
        v = z.iloc[i, j]
        if pd.notna(v):
            txt = f"{v:.0f}" if "7월=100" in c else (f"{v:.1%}" if "오차" in c else f"{v:.0%}")
            ax.text(j, i, txt, ha="center", va="center", fontsize=7, color="white" if zn.iloc[i, j] > 0.65 else "#1a202c")
ax.set_xticks(range(len(hm_cols)), [c.replace(" 강남", "\n강남").replace(" 춘천", "\n춘천") for c in hm_cols], fontsize=6.5)
ax.set_yticks(range(len(AGES)), AGES)
ax.set_title("지역 × 연령 비교 (지표마다 두 도시를 같은 눈금으로 색칠: 진할수록 큰 값)", fontsize=10)
r.fig(fig, "f04_region_age.png", "지역 × 연령 비교 히트맵")
young, old = "20대", "80대+"
r.p(f"- **나이가 많을수록, 춘천일수록 연휴 진료 공백이 깊습니다**: 연휴 병원·의원 결제가 평소의 {matrix.loc[young, '연휴 병원·의원 깊이 강남']:.0%}(강남 20대) → "
    f"{matrix.loc[old, '연휴 병원·의원 깊이 춘천']:.0%}(춘천 80대+).")
r.p(f"- **춘천 80대+는 연휴 뒤 회복이 가장 느립니다**: 생필품 {matrix.loc[old, '연휴 뒤 생필품 회복 춘천']:.0%} (강남 80대+ {matrix.loc[old, '연휴 뒤 생필품 회복 강남']:.0%}). 추석 한 번 관측.")
r.p(f"- **춘천 80대+는 결제 하락 경보로 잘 안 잡힙니다**: 15% 하락 감지 {matrix.loc[old, '15% 하락 감지율 춘천']:.0%} (강남 80대+ {matrix.loc[old, '15% 하락 감지율 강남']:.0%}). "
    "그래서 이 집단은 예정된 점검(연휴·동절기)으로 봅니다. 감지율은 합성 하락 주입 값이며 실제 고립 탐지율이 아닙니다.")
tr_cols = [c for c in matrix.columns if "7월=100" in c]
r.p(f"- 6개월 동안 특정 연령 결제가 도시보다 꾸준히 줄어드는 흐름은 없습니다(12월 비중 {matrix[tr_cols].min().min():.0f}~{matrix[tr_cols].max().max():.0f}). "
    "고령층은 '지속 감소'보다 '특정 시점 공백'을 봅니다.")

r.h3("자료가 한쪽에만 있는 비교")
r.p("시민생활(외출·소통)은 강남만, 통신 연령은 60대+ 한 묶음이라 위 표에 넣지 않고 따로 적습니다.")
r.table(stage, fmt={c: ("{:.2f}" if "배율" in c else "{:.1%}") for c in stage.columns})
stage.reset_index().to_csv(r.dir / "강남_생애단계_시민생활.csv", index=False, encoding="utf-8-sig")
r.p("- 강남 시민생활(SKT 추정, 6개월 합): 1인가구 중 '소통 적음' 비율. 같은 생애단계 안 남녀 비교만 합니다. EDA-5 1절과 같은 계산(개포3동 포함 여부에 따라 소수점 차이).")
r.table(winter.set_index(winter.columns[0]))
r.p("- 춘천 통신(60대+): 읍·면 주민형 격자 겨울 체류 변화. 강남에는 같은 계산이 없습니다(동절기 규칙 없음).")

r.h("7. 한계")
r.p("- 접근성은 2026년 원장(좌표는 도로명주소 건물 출입구, 일부 원천 제공 위경도). 2025년 당시 거점 운영은 확인하지 않았습니다.")
r.p("- 기준일 3개는 2025년 하반기의 재생(simulated_replay)입니다. 추석 한 번, 6개월 자료입니다.")
r.p("- 인원 칸은 '그 동에 사는 사람 수'이지 위험한 사람 수가 아닙니다. 고령 1인세대 수는 동 단위 공개 자료가 없어 만들지 않았습니다.")
r.p("- 춘천 독거노인 현황(공공데이터포털 15081202) 파일은 같은 동·연령이 서로 다른 값으로 두 번 나오고, 일부 읍·면은 독거노인 수가 "
    "1인세대 수보다 많아 정의를 확인하지 못했습니다. 그래서 이 표의 인원 칸에 쓰지 않았습니다.")
r.p("- 결과표는 후보 목록이고 판정이 아닙니다. 담당자가 확인할 순서를 정하는 데만 씁니다.")

out = r.save()
print("saved", out.relative_to(ROOT))
print(summary[summary["후보 수"] > 0].to_string())
print(changes.to_string())
