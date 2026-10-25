"""S-1 LINK-Sim: 페르소나 기반 가상 데이터 생성·검증·활용 (모든 결과 합성)

실행: uv run python notebooks/s1_link_sim.py   (실제 자료 비교에 dev 스냅샷 data/interim 이 필요. 약 3분)
결과: reports/s1_LINK-Sim/ (README.md, figs/, *.csv), data/sim/ (정답 있는 합성 패널, git 추적 안 함)

1. 인구 합성 (IPF) 과 페르소나 배정
2. 충실도: 카드 (요일·월·공휴일·흔들림·자기상관·업종), 통신 (동 구성·월 지수·시간대·밤낮 비)
3. 유용성: 배경 경보율, 15% 계단 주입 감지율, 등급 규칙 후보 수 (실제 vs 합성)
4. (a) 최소 감지 고립 비율, 정답 있는 등급 순위 (Spearman)
5. (b) 스트레스: 연휴 + 한파, 카드 공급 지연, 한 달 빠짐
6. (d) M3 용 정답 있는 자료 + 기준 경보 규칙의 성능 (참고)
7. 안전성: 입력 목록, 보정 목표에 원천 값 없음, 가상 사람과 실제 칸, 실제·합성 일별 잔차 상관
"""

from __future__ import annotations

import os

os.environ.setdefault("LINKRADAR_RULES", "v1.1")  # rules-v1.1 결과 재현 (bigcontest.rules.version)

import time
from datetime import date

import numpy as np
import pandas as pd

from bigcontest.agent.tools.datasource import DataSource, dev_available, dev_snapshot_id
from bigcontest.config import ROOT
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.rules.compare import collect, ctx_for, run_variant
from bigcontest.signals.alarm import Panel, null_accounting, records
from bigcontest.sim import evaluate as EV
from bigcontest.sim import inject as I
from bigcontest.sim import params as P
from bigcontest.sim.adapter import SimDataSource, register
from bigcontest.sim.calibrate import TARGETS_FILE, load_targets
from bigcontest.sim.world import COHORTS, build_world

T0 = time.time()
assert dev_available(), "실제 자료 비교에 dev 스냅샷이 필요 (합성만 만들 때는 bigcontest.sim 을 바로 씀)"
REAL = DataSource(dev_snapshot_id())
OUT = ROOT / "reports" / "s1_LINK-Sim"
DATA = ROOT / "data" / "sim"
DATA.mkdir(parents=True, exist_ok=True)
rep = Report("s1_LINK-Sim", "S-1 LINK-Sim: 페르소나 기반 가상 데이터 (합성)")


def csv(df: pd.DataFrame, name: str, index: bool = False):
    df.to_csv(OUT / name, index=index, encoding="utf-8-sig")


def lap(msg):
    print(f"[{time.time() - T0:5.0f}s] {msg}")


# ------------------------------------------------------------ 0. 세계 만들기
W = build_world()
SIM = SimDataSource(W)
SID = register(SIM)
Yr = REAL.panel().Y
Ys = SIM.panel().Y
HOL = REAL.holidays()
lap(f"세계 {SID}: 가상 {len(W.people):,}명")

rep.p("> **이 리포트의 숫자는 모두 합성(synthetic)입니다.** 가상 사람의 결제·체류를 만들어 같은 파이프라인에 넣은 결과이며, "
      "실제 고립을 찾은 성능이 아닙니다. 생성 방식 설명은 [docs/LINK-Sim_생성방식.md](../../docs/LINK-Sim_생성방식.md).")
rep.p("")
rep.p("LINK-Sim은 공개 통계(주민등록·날씨·공휴일)와 제공 데이터의 **집계 통계**(비율·로그 효과·흔들림 크기)만으로 "
      "가상 사람을 만들고, 페르소나 7종의 상태(평소·위축·단절)에 따라 하루 결제·체류를 생성한 뒤, "
      "제공 데이터와 **같은 단위**(카드: 도시 × 연령 × 성 × 일 × 업종, 통신: 50m 칸 × 월)로 집계합니다. "
      "기존 경보·반증·등급 코드는 그대로 두고 자료 읽기(`SimDataSource`)만 바꿔 돌립니다.")
rep.p("")
rep.p(f"- 시드 {P.SEED}, 축소 배율 1:{P.SCALE} (가상 1명 = 실제 약 {P.SCALE}명 묶음), 기간 {P.START}~{P.END} (184일)")
rep.p(f"- 스냅샷 `{SID}`, 보정 목표 `{TARGETS_FILE.relative_to(ROOT).as_posix()}`, 생성기 `{P.SIM_VERSION}`")

# ------------------------------------------------------------ 1. 인구 합성
pf = EV.population_fidelity(W.pop_info)
pers = (W.people.groupby(["persona", "city"]).size().unstack(fill_value=0)
        .rename(index={k: f"{k} {P.PERSONAS[k].name}" for k in P.PERSONA_IDS}))
pers["합계"] = pers.sum(axis=1)
csv(pers.reset_index().rename(columns={"persona": "페르소나"}), "페르소나_배정.csv")
rep.h("1. 인구 합성 (IPF)")
rep.p("주민등록 **동 × 성 × 연령대 인구**와 **동 1인세대 수**(공개)를 주변분포로 IPF를 돌려 동 × 성 × 연령대 × 1인 여부 칸을 채우고, "
      f"1:{P.SCALE}로 줄여 확률 반올림으로 가상 사람을 뽑았습니다. 1인 생활 사전 확률(성 × 연령대)은 가정값이라 "
      "**동 총량은 정확하지만 연령 배분의 모양은 가정을 따릅니다**.")
rep.table(pd.DataFrame({"값": {k: (f"{v:.2e}" if isinstance(v, float) and v < 1e-3 else
                                   (f"{v:.1%}" if isinstance(v, float) else f"{v:,}")) for k, v in pf.items()}}))
rep.p("페르소나 배정 (배경 세계, 가상 사람 수). 확률은 모두 가정값이며 실제 유병률이 아닙니다.")
rep.table(pers)

# ------------------------------------------------------------ 2. 충실도: 카드
cat_r = EV.cat_shares(REAL.card2())
cat_s = EV.cat_shares(SIM.card2())
fid, fid_city = EV.card_fidelity(Yr, Ys, W.cal, HOL, cat_r, cat_s)
csv(fid.round(4), "충실도_카드_코호트.csv")
csv(fid_city.round(4), "충실도_카드_도시.csv")
lap("충실도 카드")
rep.h("2. 충실도 (fidelity)")
rep.h3("2-1. 카드: 코호트 28개, 실제 vs 합성 (같은 식으로 다시 잼)")
summ = pd.DataFrame({
    "요일 모양 상관 (최소)": [fid["요일 모양 상관"].min()], "공휴일 효과 차 (최대)": [fid["공휴일 차"].max()],
    "월 효과 차 (최대)": [fid["월 효과 최대 차"].max()],
    "local MAD 비 (범위)": [f"{fid['MAD 비'].min():.2f}~{fid['MAD 비'].max():.2f}"],
    "lag1 차 (최대)": [fid["lag1 차"].max()], "업종 비중 TVD (최대)": [fid["업종 비중 TVD"].max()],
    "통과 코호트": [f"{int(fid['통과'].sum())}/{len(fid)}"]})
rep.table(summ, {c: "{:.3f}" for c in summ.columns if "범위" not in c and "통과" not in c}, index=False)
rep.p(f"통과 기준(미리 정함): 요일 모양 상관 ≥ {EV.TOL['dow_corr']}, 공휴일 효과 차 ≤ {EV.TOL['hol_abs']}, 월 효과 차 ≤ {EV.TOL['month_abs']}, "
      f"local MAD 비 {EV.TOL['mad_ratio'][0]}~{EV.TOL['mad_ratio'][1]}, lag1 차 ≤ {EV.TOL['lag1_abs']}, 업종 비중 TVD ≤ {EV.TOL['cat_tvd']}. "
      "요일·월·공휴일·흔들림·업종 비중은 **보정한 값**이라 맞는 것이 당연합니다. 보정하지 않은 값(잔차 lag7·첨도, 아래 유용성)이 진짜 검사입니다.")
nc = pd.DataFrame({"잔차 lag7 실제 (중앙값)": [fid["잔차 lag7 실제"].median()], "잔차 lag7 합성 (중앙값)": [fid["잔차 lag7 합성"].median()],
                   "잔차 첨도 실제 (중앙값)": [fid["잔차 첨도 실제"].median()], "잔차 첨도 합성 (중앙값)": [fid["잔차 첨도 합성"].median()],
                   "실제·합성 일별 잔차 상관 (중앙값)": [fid["실제·합성 일별 잔차 상관"].median()]})
rep.p("보정하지 않은 모양 (코호트 중앙값)")
rep.table(nc, {c: "{:.3f}" for c in nc.columns}, index=False)
rep.p("- 첨도: 실제 잔차는 꼬리가 두껍고(가끔 큰 하루 변화), 합성 잡음은 정규 AR(1)이라 꼬리가 얇습니다 → 합성에서 헛경보가 덜 날 수 있습니다 (3절 배경 경보율).")
rep.p("- 실제·합성 일별 잔차 상관이 0에 가까움 = 합성이 실제의 날짜별 흔들림을 베끼지 않았다는 뜻 (7절 안전성).")
rep.table(fid_city.set_index("도시"), {c: "{:.3f}" for c in fid_city.columns if c not in ("도시", "통과")})

wp_r, wp_s = EV.weekday_profile(Yr, HOL), EV.weekday_profile(Ys, HOL)
fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), sharey=True)
for ax, c in zip(axes, P.CITIES):
    ax.plot(range(7), wp_r.loc[c], "-o", color=CITY_COLOR[c], label="실제")
    ax.plot(range(7), wp_s.loc[c], "--s", color="gray", label="합성")
    ax.set_xticks(range(7), list("월화수목금토일"))
    ax.set_title(f"{c}: 요일별 log 결제 − 평균 (코호트 평균)")
    ax.legend()
rep.fig(fig, "요일_모양.png", "요일 모양 실제 vs 합성")

fig, axes = plt.subplots(2, 1, figsize=(10, 5.6), sharex=True)
for ax, c in zip(axes, [("강남", "40대", "남성"), ("춘천", "80대+", "여성")]):
    r = np.log(Yr[c] + 1)
    s = np.log(Ys[c] + 1)
    ax.plot(Yr.index, r - r.mean(), color=CITY_COLOR[c[0]], lw=1, label="실제 (평균을 뺀 log)")
    ax.plot(Ys.index, s - s.mean(), color="gray", lw=1, alpha=0.8, label="합성")
    for h in HOL:
        ax.axvline(h, color="k", alpha=0.15)
    ax.set_title(" ".join(c))
    ax.legend(loc="lower left", fontsize=8)
rep.fig(fig, "시계열_예시.png", "코호트 시계열 예시 (세로선 = 공휴일). 수준은 맞추지 않으므로 평균을 뺀 log로 비교")

# ------------------------------------------------------------ 2-2. 충실도: 통신
tf = EV.tel_fidelity({n: REAL.tel(n) for n in ("tel_age", "tel_time", "tel_wkdy")},
                     {n: SIM.tel(n) for n in ("tel_age", "tel_time", "tel_wkdy")})
for k, v in tf.items():
    csv(v.round(4).reset_index(), f"충실도_통신_{k}.csv")
lap("충실도 통신")
rep.h3("2-2. 통신: 동 구성·월 지수(보정함), 시간대·밤낮 비(일부만 보정)")
tsum = pd.DataFrame({
    "동 60대+ 비중 차 최대(%p)": [tf["동_60대이상_비중"]["차"].max() * 100],
    "월 체류 지수 차 최대 (60대+, 도시×동유형×월)": [tf["월_체류지수"]["차"].max()],
    "시간대 모양 상관 최소": [tf["시간대_모양"]["시간대 모양 상관"].min()],
    "요일 모양 차 최대": [tf["요일_모양"]["요일 모양 최대 차"].max()]})
rep.table(tsum, {c: "{:.3f}" for c in tsum.columns}, index=False)
rep.p("250m 격자 밤(0~5시)/낮(11~15시) 비 분위수: 이 값은 보정하지 않았습니다 (가상 칸은 동 중심점 둘레에 만든 칸이라 실제 격자 모양과 다름).")
rep.table(tf["격자_밤낮비"], {c: "{:.3f}" for c in tf["격자_밤낮비"].columns})
rep.p("- 통신 합성은 '동 × 성연령 구성, 시간대·요일 모양, 월별 체류 지수'를 맞춘 가상 칸입니다. 칸 위치·격자 모양은 실제와 다르므로 "
      "격자 단위 분석(예: 핵심 주거 격자 고르기)의 결과는 실제와 같지 않을 수 있습니다.")

# ------------------------------------------------------------ 3. 유용성
ar = EV.alarm_rate(Yr, HOL).assign(자료="실제")
asy = EV.alarm_rate(Ys, HOL).assign(자료="합성")
alarm_tbl = pd.concat([ar, asy])
csv(alarm_tbl.round(4), "유용성_배경경보율.csv")
starts = EV.step_starts(W.cal, 10, seed=0)
det_rows = []
for drop in (0.05, 0.15):
    for name, Y in (("실제", Yr), ("합성", Ys)):
        d = EV.step_detection(Y, HOL, starts, drop)
        m, lo, hi = EV.boot_rate(d)
        d2 = d[~d["대조 경보"]]
        det_rows.append({"주입": f"계단 {drop:.0%} × 14일", "배경": name, "쌍": len(d2), "탐지율": m, "CI 하한": lo, "CI 상한": hi,
                         "지연 중앙값(일)": float(d2.loc[d2["탐지"], "지연"].median())})
        for c in P.CITIES:
            dc = d2[d2["도시"] == c]
            det_rows.append({"주입": f"계단 {drop:.0%} × 14일", "배경": f"{name} ({c})", "쌍": len(dc), "탐지율": dc["탐지"].mean(),
                             "CI 하한": None, "CI 상한": None, "지연 중앙값(일)": float(dc.loc[dc["탐지"], "지연"].median())})
det = pd.DataFrame(det_rows)
csv(det.round(4), "유용성_계단주입_탐지.csv")
lap("유용성 경보")

# 등급 규칙: 실제 vs 합성 후보 수
DATES = {"추석 D-7": date(2025, 9, 26), "추석 뒤": date(2025, 10, 13), "동절기": date(2025, 12, 20)}
GROUPS = ["고령층", "중장년 남성 1인가구"]
rule_rows = []
for src, sid in (("실제", REAL.snapshot_id), ("합성", SID)):
    for city in P.CITIES:
        for dn, d in DATES.items():
            for g in GROUPS:
                c = ctx_for(sid, city, d, g)
                cands = run_variant(collect(c), "D", c.request, d, 10).candidates
                sched = [x for x in cands if x.candidate_type == "scheduled_access_review"]
                dev = [x for x in cands if x.candidate_type == "unexpected_activity_deviation"]
                rule_rows.append({"자료": src, "도시": city, "기준일": dn, "집단": g, "예정된 점검 후보": len(sched),
                                  "점검 Level 3": sum(x.candidate_level == "Level 3" for x in sched),
                                  "이탈 후보": len(dev), "이탈 반증 통과": sum(x.counter_result == "통과" for x in dev),
                                  "이탈 Level 2+": sum(x.candidate_level in ("Level 2", "Level 3") for x in dev),
                                  "_sched": {x.adm_cd: x.candidate_level for x in sched}})
rules = pd.DataFrame(rule_rows)
key = ["도시", "기준일", "집단"]
agree = []
for _, g in rules.groupby(key):
    a = g[g["자료"] == "실제"]["_sched"].iloc[0]
    b = g[g["자료"] == "합성"]["_sched"].iloc[0]
    adms = set(a) | set(b)
    agree.append({**dict(zip(key, g.iloc[0][key])),
                  "점검 후보 동 같은 비율": len(set(a) & set(b)) / len(adms) if adms else None,
                  "같은 동의 등급 일치": (np.mean([a[x] == b[x] for x in set(a) & set(b)]) if set(a) & set(b) else None)})
agree = pd.DataFrame(agree)
rules = rules.drop(columns="_sched")
csv(rules, "유용성_등급규칙_후보수.csv")
csv(agree.round(3), "유용성_등급규칙_일치.csv")
lap("유용성 규칙")

rep.h("3. 유용성 (utility): 같은 파이프라인, 실제 vs 합성")
rep.h3("3-1. 배경 경보율 (주입 없음, 판정 가능한 코호트·일 중 either 경보)")
rep.table(alarm_tbl.pivot_table(index="도시", columns="자료", values="경보 비율(코호트·일)"), {"실제": "{:.2%}", "합성": "{:.2%}"})
rep.p("- 합성 배경에서 경보가 덜 나면(정규 잡음, 얇은 꼬리) 합성으로 낸 오경보 관련 숫자는 **낙관적**입니다.")
rep.h3("3-2. 계단형 하락 주입 감지율 (EDA-6 방식, 같은 시작일 280쌍, 코호트 단위 부트스트랩 CI)")
det_show = det.copy()
for c in ("탐지율", "CI 하한", "CI 상한"):
    det_show[c] = det_show[c].map(lambda v: "" if v is None or pd.isna(v) else f"{v:.1%}")
rep.table(det_show, {"지연 중앙값(일)": "{:.0f}"}, index=False)
rep.h3("3-3. 등급 규칙 (rules-v1.1, 비교안 D, K=10): 후보 수")
piv = rules.pivot_table(index=["도시", "기준일", "집단"], columns="자료",
                        values=["예정된 점검 후보", "점검 Level 3", "이탈 후보", "이탈 Level 2+"], aggfunc="sum")
piv.columns = [f"{a} ({b})" for a, b in piv.columns]
rep.table(piv.reset_index(), index=False)
rep.table(agree, {"점검 후보 동 같은 비율": "{:.2f}", "같은 동의 등급 일치": "{:.2f}"}, index=False)
rep.p("- 예정된 점검(구조·접근성·달력)은 주민등록·자원 원장이 공개 자료 그대로라 거의 같아야 정상입니다. 다른 곳은 통신(계절 수축) 합성 때문입니다.")
rep.p("- 이탈 후보는 카드 시계열에서 나오므로 합성과 실제가 **날짜별로 같을 이유가 없습니다** (합성은 실제의 날짜별 흔들림을 베끼지 않음). 개수·등급 분포의 크기만 비교합니다.")

# ------------------------------------------------------------ 4. (a) 최소 감지 고립 비율
curve = EV.detect_curve(W, {"합성": Ys, "실제": Yr}, HOL)
curve_s = curve[curve["배경"] == "합성"]
csv(curve.round(4), "최소감지_원자료.csv")
md, rate = EV.min_detectable(curve)
md_age, rate_age = EV.min_detectable(curve[curve["페르소나"] == "고립 진행형"], by=("배경", "도시", "연령"))
csv(md, "최소감지_비율.csv")
csv(rate.round(4), "최소감지_비율별_탐지율.csv")
csv(md_age, "최소감지_연령별.csv")
young = [c for c in COHORTS if c[1] in ("20대", "30대")]
c6 = []
for c in young:
    for od in EV.ONSET_DATES[:4]:
        t0 = int(W.cal.dates.get_loc(pd.Timestamp(od)))
        inj = I.prepare(W, c, "S6", t0, 0.3)
        p_ = Panel(Ys, HOL)
        j = p_.cols.index(c)
        for f in (0.1, 0.3):
            a = p_.score_one(c, I.cohort_counts(W, inj, f).sum(0))["either"]
            c6.append({"코호트": " ".join(c), "비율": f, "탐지": bool((a[t0: t0 + 28] == 1).any()),
                       "대조": bool((p_.alarm_any(j, "either")[t0: t0 + 28] == 1).any()),
                       "단절 비율": float(inj.w_frac(f)[t0])})
c6 = pd.DataFrame(c6)
csv(c6, "최소감지_은둔청년.csv")
lap("최소 감지")

rep.h("4. (a) 정답 있는 평가")
rep.h3("4-1. 최소 감지 고립 비율")
rep.p("코호트(도시 × 연령 × 성) 가상 사람 중 비율 f를 '고립 진행형'(몇 주에 걸쳐 위축→단절) 또는 '건강 악화형'(하루에 단절, 뒤에 일부 회복)으로 바꾸고, "
      "시작일 7개 × 코호트 28개에서 28일 안에 경보(either)가 울리는지 봤습니다. 대조(같은 날, 주입 없음)가 이미 울린 쌍은 뺐습니다. "
      "배경 '합성' = 합성 결제(같은 난수로 짝), '실제' = **실제 결제 × 합성 기대 변화 비율**(하이브리드: 실제 배경 + 가상 변화량).")
def fmt_md(t):
    t = t.copy()
    for c in t.columns:
        if "최소 비율" in c:
            t[c] = t[c].map(lambda v: "50%에서도 못 미침" if v is None or pd.isna(v) else f"{v:.0%}")
        elif c in ("가장 큰 비율에서 탐지율", "대조 경보율"):
            t[c] = t[c].map(lambda v: f"{v:.0%}")
    return t


rep.table(fmt_md(md), index=False)
fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
for ax, per in zip(axes, ["고립 진행형", "건강 악화형"]):
    for (bg, city), g in rate[rate["페르소나"] == per].groupby(["배경", "도시"]):
        ax.plot(g["배정 비율"] * 100, g["탐지율"], "-o" if bg == "실제" else "--s", color=CITY_COLOR[city], label=f"{city} ({bg} 배경)")
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 5, 10, 20, 50], ["1", "2", "5", "10", "20", "50"])
    ax.set_xlabel("코호트 중 페르소나로 바꾼 사람 비율 (%)")
    ax.set_title(per)
    ax.axhline(0.5, color="k", alpha=0.2)
    ax.legend(fontsize=7)
axes[0].set_ylabel("28일 안 탐지율")
rep.fig(fig, "최소감지_곡선.png", "코호트 안 고립 비율별 탐지율 (합성)")
rep.p("고립 진행형, 연령별 (탐지율 50%·80%를 넘는 최소 비율)")
rep.table(fmt_md(md_age), index=False)
s1 = rate[(rate["페르소나"] == "고립 진행형") & (rate["배경"] == "실제")].groupby("배정 비율")["탐지율"].mean()
rep.p(f"- 은둔 청년(S6, 처음부터 단절): 20·30대 코호트에 10%·30%를 넣어도 탐지 {c6[~c6['대조']]['탐지'].mean():.0%} "
      f"(쌍 {int((~c6['대조']).sum())}개). 처음부터 낮아 '변화'가 없으므로 이 시스템이 **원리상 못 잡는** 집단입니다.")
rep.p("- 읽는 법: '코호트 사람의 x%가 몇 주 안에 단절 상태로 가야 도시 단위 카드 경보가 절반쯤 울린다'. "
      "개인 한 명, 몇 명의 고립은 카드 집계로 보이지 않는다는 것을 숫자로 보여 줍니다. 이 숫자는 페르소나 배율(가정값)에 따라 달라집니다.")

# ------------------------------------------------------------ 4-2. 정답 있는 등급 순위
rank_rows = []
base_people = W.people
for scen, risk in EV.dong_risk_scenarios(base_people, seed=1).items():
    mix = P.Mix(s1_alone_mid_male=0.06, s1_alone_other=0.01, s2_alone_old=0.10, s3_old_rural=0.15, s3_old_urban=0.06,
                s4_old=0.04)
    wr = build_world(scale=10, mix=mix, dong_risk=risk, noise_params=W.noise_params)
    dsr = SimDataSource(wr, variant=f"rank-{scen}")
    sid_r = register(dsr)
    for city in P.CITIES:
        adms = sorted(wr.people.loc[wr.people["city"] == city, "adm_cd"].unique())
        for dn in ("추석 D-7", "동절기"):
            d = DATES[dn]
            t = int(wr.cal.dates.get_loc(pd.Timestamp(d)))
            for g, bands, sex in (("고령층", ["60대", "70대", "80대+"], None), ("중장년 남성 1인가구", ["40대", "50대", "60대"], "남성")):
                c = ctx_for(sid_r, city, d, g)
                cands = run_variant(collect(c), "D", c.request, d, 10).candidates
                b = EV.true_burden(wr, bands, sex, t)
                rank_rows.append({"정답 시나리오": scen, "도시": city, "기준일": dn, "집단": g, **EV.rank_quality(cands, b, adms)})
rank = pd.DataFrame(rank_rows)
csv(rank.round(3), "정답순위_Spearman.csv")
lap("정답 순위")
rep.h3("4-2. 정답 있는 등급 순위 (동 단위)")
rep.p("동마다 고립 부담을 다르게 준 두 세계(1:10 축소, 고립 페르소나 비율을 배경보다 높임)에서, 규칙의 동 순위(예정된 점검: 등급 → 검토 순서, "
      "후보가 아닌 동은 맨 뒤)와 **합성 정답**(기준일 앞 28일 대상 집단 중 단절 상태 비율·인원)의 Spearman 순위상관을 봤습니다.")
rep.p("- A 구조 일치: 1인 비율이 높은 동일수록 고립 페르소나가 더 많음 (규칙이 가정하는 구조와 같음)")
rep.p("- B 구조 무관: 동마다 무작위 배수, 1인 구성 효과도 상쇄 (규칙의 가정이 틀린 세계)")
rk = rank.groupby(["정답 시나리오", "도시"])[["Spearman(단절 비율)", "Spearman(단절 인원)", "정답 상위 25% 중 Level 3"]].mean()
rep.table(rk, {c: "{:.2f}" for c in rk.columns})
rep.table(rank, {c: "{:.2f}" for c in rank.columns if c.startswith(("Spearman", "Level 3 중", "정답 상위"))}, index=False)
rep.p("- 강남 동절기는 예정된 점검 후보가 없어(규칙상 동절기 점검은 춘천 읍·면) 순위상관을 계산하지 않았습니다(빈칸). "
      "B는 1인 구성 효과까지 상쇄해 만들었으므로 1인 비율 순위와 음의 상관이 나올 수 있습니다.")
rep.p("- A에서 순위상관이 양수이고 B에서 0 근처라면: 규칙의 동 순위는 **'1인 구조가 고립 부담을 따른다'는 가정이 맞을 때만** 정답을 따라갑니다. "
      "A의 상관은 생성 가정에서 나온 것이라 규칙의 실제 성능 근거가 아닙니다. B는 그 가정이 틀리면 순위가 아무 정보도 주지 않음을 보여 줍니다.")

# ------------------------------------------------------------ 5. (b) 스트레스 시험
extra = {"2025-12-29": "가상 연휴", "2025-12-30": "가상 연휴", "2025-12-31": "가상 연휴"}
cold = {c: {"cold": ["2025-12-29", "2025-12-30", "2025-12-31"]} for c in P.CITIES}
Wc = build_world(extra_holidays=extra, weather_override=cold, noise_params=W.noise_params)
hol_c = sorted(Wc.cal.holidays)
Yc = Wc.panel_frame()
rain_s = SIM.rain()
st = []
for name, Y, hol in (("평소 (배경)", Ys, HOL), ("가상 연휴 + 한파 (12/27~12/31)", Yc, hol_c)):
    p_ = Panel(Y, hol)
    rec = records(p_, rain=rain_s)
    late = rec[rec["alarm_date"] >= pd.Timestamp("2025-12-27")] if len(rec) else rec
    st.append({"조건": name, "12/27 뒤 경보 묶음": len(late),
               "반증 '기각'": int((late["review"] == "기각").sum()) if len(late) else 0,
               "'통과'(평소 하락으로 남음)": int((late["review"] == "통과").sum()) if len(late) else 0,
               "'확인 필요'·'검토 중'": int(late["review"].isin(["확인 필요", "검토 중", "보류"]).sum()) if len(late) else 0})
    for per, f, od in (("S1", 0.15, "2025-12-01"), ("S1", 0.15, "2025-11-10")):
        t0 = int(Wc.cal.dates.get_loc(pd.Timestamp(od)))
        wsrc = W if name.startswith("평소") else Wc
        hit = []
        for c in COHORTS:
            inj = I.prepare(wsrc, c, per, t0, f)
            a = p_.score_one(c, I.cohort_counts(wsrc, inj, f).sum(0))["either"]
            ctrl = p_.alarm_any(p_.cols.index(c), "either")
            if not (ctrl[t0: t0 + 31] == 1).any():
                hit.append(bool((a[t0: t0 + 31] == 1).any()))
        st[-1][f"고립 진행형 15% 시작 {od[5:]} 탐지"] = f"{np.mean(hit):.0%} ({len(hit)}쌍)"
stress1 = pd.DataFrame(st)
csv(stress1, "스트레스_연휴한파.csv")

lag_rows = []
cur = curve_s[(curve_s["페르소나"] == "고립 진행형") & (curve_s["배정 비율"].isin([0.1, 0.2, 0.3])) & ~curve_s["대조 경보"]]
for L in (0, 3, 7):
    for f, g in cur.groupby("배정 비율"):
        ok = g["탐지"] & (g["지연"].fillna(999) + L <= 20)
        lag_rows.append({"공급 지연(일)": L, "배정 비율": f, "시작 뒤 21일 안에 담당자가 볼 수 있는 탐지율": ok.mean(),
                         "볼 수 있는 날(시작 뒤) 중앙값": float((g.loc[g["탐지"], "지연"] + L).median())})
lag = pd.DataFrame(lag_rows)
lag["배정 비율"] = lag["배정 비율"].map(lambda v: f"{v:.0%}")
Ym = Ys.copy()
Ym.loc["2025-11-01":"2025-11-30"] = np.nan
pm = Panel(Ym, HOL)
na = null_accounting(pm)
miss = []
for od in ("2025-11-17", "2025-12-01", "2025-12-08"):
    t0 = int(W.cal.dates.get_loc(pd.Timestamp(od)))
    injs = {c: I.prepare(W, c, "S1", t0, 0.2) for c in COHORTS}
    for nm, p_, Y in (("빠짐 없음", Panel(Ys, HOL), Ys), ("11월 빠짐", pm, Ym)):
        hit, hold = [], []
        for c in COHORTS:
            y = I.cohort_counts(W, injs[c], 0.2).sum(0)
            y[np.isnan(Y[c].to_numpy())] = np.nan
            a = p_.score_one(c, y)["either"]
            hit.append(bool((a[t0: t0 + 28] == 1).any()))
            hold.append(float(np.isnan(a[t0: t0 + 28]).mean()))
        miss.append({"시작일": od, "자료": nm, "고립 진행형 20% 탐지": np.mean(hit), "창 안 보류 비율": np.mean(hold)})
miss = pd.DataFrame(miss)
csv(lag.round(4), "스트레스_공급지연.csv")
csv(miss.round(4), "스트레스_한달빠짐.csv")
lap("스트레스")
rep.h("5. (b) 스트레스 시험 (합성)")
rep.h3("5-1. 연휴 + 한파 겹침")
rep.p("12/29~12/31을 가상 공휴일로 넣어 12/27~12/31 연휴를 만들고, 같은 날 두 도시 모두 한파(최저 −12℃ 이하)로 바꿨습니다. "
      "배경 효과(공휴일·한파 로그 효과)는 보정값 그대로, 연휴 단절형·계절 수축형 페르소나도 반응합니다. 공휴일 달력은 미리 알 수 있으므로 경보 규칙에 넣었습니다.")
rep.table(stress1, index=False)
rep.p("- 연휴 + 한파를 겹치면 **배경(주입 없음)만으로도** 경보 묶음이 크게 늘고, 반증은 기각하지 않고 '확인 필요·검토 중'으로 남깁니다 "
      "(회복을 볼 날이 자료 끝에 걸림). 그래서 대조가 이미 울린 쌍이 많아져 12/01 시작 주입의 '짝 비교'가 거의 남지 않습니다 (쌍 수 참고). "
      "→ 겹친 계기 기간에는 카드 이탈 경보가 '고립 신호'로서 정보를 거의 주지 못하고, 예정된 점검(구조·달력) 쪽이 주된 근거가 되어야 합니다.")
rep.p("- 연말 연휴 묶음 뒤에는 기대값을 잴 과거 같은 요일이 공휴일로 비고, 회복을 볼 날이 자료 끝(12/31)에 걸려 '검토 중'으로 남기 쉽습니다.")
rep.h3("5-2. 카드 공급 지연")
rep.table(lag, {"시작 뒤 21일 안에 담당자가 볼 수 있는 탐지율": "{:.1%}", "볼 수 있는 날(시작 뒤) 중앙값": "{:.0f}"}, index=False)
rep.p("- 지연 L일이면 경보를 볼 수 있는 날이 L일 늦어집니다 (규칙은 관측일 기준이라 탐지 자체는 같음). '21일 안' 같은 대응 기한이 있으면 그만큼 놓칩니다.")
rep.h3("5-3. 한 달(11월) 빠짐")
rep.table(miss, {"고립 진행형 20% 탐지": "{:.0%}", "창 안 보류 비율": "{:.0%}"}, index=False)
rep.table(na.reset_index().rename(columns={"index": "11월 빠짐 패널"}), index=False)
rep.p("- 11/17 시작은 창(28일)의 절반 이상이 빠진 달이라 보류가 많고, 12월 자료가 들어온 뒤 늦게 울린 경우가 탐지로 잡혀 숫자가 오히려 조금 높습니다 (언제 볼 수 있느냐는 늦어짐).")
rep.p("- 빠진 달은 '관측 없음(미공급)'으로 보류하고 0으로 바꾸지 않습니다. 빠진 달 바로 뒤에는 과거 관측이 모자라 한동안 판정을 못 합니다.")

# ------------------------------------------------------------ 6. (d) M3 용 정답 있는 자료
panel, scen = I.labeled_dataset(W, n_scenarios=120, seed=0)
LAB = DATA / "link_sim_labeled_v1.parquet"
panel.to_parquet(LAB, index=False)
scen.to_parquet(DATA / "link_sim_scenarios_v1.parquet", index=False)
csv(scen, "정답자료_시나리오.csv")
# 기준: 지금 경보 규칙으로 정답을 얼마나 맞히나 (대상 코호트, 학습 기간 뒤)
conf = []
for s in scen["scenario_id"]:
    sc = scen.loc[s]
    Yw = I.wide(panel, s)
    p_ = Panel(Yw, HOL)
    col = (sc["city"], sc["age_g"], sc["SEX_CCD"])
    a = p_.alarm_any(p_.cols.index(col), "either")
    lab = panel[(panel["scenario_id"] == s) & panel["is_target"]]["label_deviation"].to_numpy()
    ok = ~np.isnan(a)
    conf.append({"scenario_id": s, "persona": sc["persona_name"], "frac": sc["inj_fraction"],
                 "tp": int(((a == 1) & lab & ok).sum()), "fp": int(((a == 1) & ~lab & ok).sum()),
                 "fn": int(((a == 0) & lab & ok).sum()), "tn": int(((a == 0) & ~lab & ok).sum())})
conf = pd.DataFrame(conf)
cs = conf.groupby("persona")[["tp", "fp", "fn", "tn"]].sum()
cs["재현율(정답 이탈일 중 경보)"] = cs["tp"] / (cs["tp"] + cs["fn"])
cs["정밀도(경보일 중 정답 이탈)"] = cs["tp"] / (cs["tp"] + cs["fp"])
cs = cs.rename(columns={"tp": "맞은 경보", "fp": "헛경보", "fn": "놓침", "tn": "맞은 무경보"})
csv(cs.reset_index().round(4), "정답자료_기준규칙.csv")
lap("정답 자료")
rep.h("6. (d) M3 이탈 분류기용 정답 있는 자료")
rep.p(f"`bigcontest.sim.inject.labeled_dataset(world, n_scenarios, seed)` → `{LAB.relative_to(ROOT).as_posix()}` "
      f"({len(panel):,}행 = 시나리오 {len(scen)}개 × 184일 × 코호트 28개, git 추적 안 함, 다시 만들기 몇 초). 형식은 생성 방식 문서 9절.")
rep.table(scen.groupby("persona_name").agg(시나리오=("scenario_id", "count"), 평균_비율=("inj_fraction", "mean"),
                                           최대_기대감소_중앙값=("max_exp_drop", "median")).reset_index(),
          {"평균_비율": "{:.2f}", "최대_기대감소_중앙값": "{:.3f}"}, index=False)
rep.p("지금 경보 규칙(either)을 이 자료에 그대로 돌린 기준 성능 (대상 코호트의 판정 가능한 날, 정답 = 자기 평소 대비 기대 결제 5% 이상 감소)")
rep.table(cs, {"재현율(정답 이탈일 중 경보)": "{:.1%}", "정밀도(경보일 중 정답 이탈)": "{:.1%}"})
rep.p("- M3는 이 자료로 학습·평가할 수 있지만, 정답은 **페르소나 배율(가정값)이 만든 정답**입니다. 여기서 좋은 분류기가 실제 고립을 잘 찾는다는 뜻이 아닙니다.")

# ------------------------------------------------------------ 7. 안전성
tg = load_targets()
flat_keys = set()


def walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            flat_keys.add(str(k))
            walk(v, f"{path}.{k}")


walk(tg)
from bigcontest.sim.adapter import PUBLIC_SOURCES  # noqa: E402

forbidden = [k for k in flat_keys if k.lower() in ("use_cnt", "ts_at", "y", "amount", "count", "체류량")]
cells = W.pop_info["cells"]
real_cell = W.pop_info["m1"].stack([0, 1]).rename_axis(["adm_cd", "sex", "band"]).rename("real")
pp = W.people.merge(real_cell.reset_index(), on=["adm_cd", "sex", "band"], how="left")
small_ipf = cells[(cells["ipf_n"] > 0) & (cells["ipf_n"] < 10)]
safety = pd.DataFrame({"항목": [
    "LINK-Sim 이 읽는 파일 (공개)", "합성 자료로 바꾸는 원천", "보정 목표(targets.json) 최상위 항목",
    "보정 목표에 원천 값 이름(USE_CNT·TS_AT·체류량 등)", "가상 사람 속성",
    "가상 사람이 속한 실제 칸(동×성×연령대) 인구 최솟값", "실제 인구 10명 미만 칸에 속한 가상 사람",
    "IPF 추정 10명 미만 칸(1인 여부 포함)에서 뽑힌 가상 사람", "실제·합성 일별 잔차 상관 (중앙값, 최대 절대값)"],
    "값": [", ".join(sorted(PUBLIC_SOURCES)), "card2, card1, card2_visitors, tel_age, tel_time, tel_wkdy",
          ", ".join(k for k in tg if k not in ("note",)), f"{len(forbidden)}개",
          "임의 ID(SIM-…), 행정동, 성, 10년 연령대, 1인 여부, 동 유형, 페르소나, 활동량 (이름·1세 나이·주소·실제 ID 없음)",
          f"{int(pp['real'].min()):,}명", f"{int((pp['real'] < 10).sum())}명",
          f"{int(small_ipf['sim_n'].sum())}명 (칸 {len(small_ipf)}개)",
          f"{fid['실제·합성 일별 잔차 상관'].median():.3f}, {fid['실제·합성 일별 잔차 상관'].abs().max():.3f}"]})
csv(safety, "안전성_점검.csv")
rep.h("7. 안전성 (privacy)")
rep.table(safety, index=False)
rep.p("- 입력은 공개 통계와 제공 데이터의 **집계 통계**뿐입니다 (개인 단위 행 입력 0개). 제공 데이터 자체도 이미 비식별 집계입니다.")
rep.p(f"- 가상 사람 1명은 실제 약 {P.SCALE}명 묶음이고, 칸 안에서 무작위로 뽑은 속성 조합이라 **어떤 행도 특정 실제 사람과 대응하지 않습니다**. "
      "1인 여부 칸이 작은(IPF 추정 10명 미만) 곳에서 뽑힌 가상 사람도 실제 그 사람의 기록이 아니라 확률 반올림 결과입니다.")
rep.p("- 합성 결제·체류의 날짜별 흔들림은 실제와 상관이 없습니다(위 잔차 상관). 공휴일·요일·날씨처럼 **모두가 아는 달력 효과**만 같습니다.")

# ------------------------------------------------------------ 8. 시연·재현
rep.h("8. (c) 원본 없는 시연과 재현")
rep.p("```bash")
rep.p("uv run python -m bigcontest.sim.serve          # 합성 스냅샷으로 API (X-Data-Source: synthetic)")
rep.p("uv run python -m bigcontest.sim.calibrate      # 보정 목표 다시 만들기 (원본 필요)")
rep.p("uv run python notebooks/s1_link_sim.py         # 이 리포트")
rep.p("```")
rep.p(f"실행 시간 {time.time() - T0:.0f}초.")
rep.save()
lap("끝")
