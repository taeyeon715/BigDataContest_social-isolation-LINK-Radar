"""M4 위험 요인 확인 모델 (강남 시민생활 60세 이상, 탐색적)

실행: uv run python notebooks/m4_risk_factors.py   (원본 데이터 없이 됨: 공개 시민생활 + 레포 집계 CSV)
결과: reports/m3_모델링결과/M4.md, m4_결과.csv, m4_반복.csv, m4_계수.csv, figs/m4_*.png
      (모델링 장 README 는 notebooks/m3_models.py 가 만들고 이 결과를 읽어 요약한다)

설계·검증 방법은 결과를 보기 전에 configs/models_m3.yaml (m4) 에 적고 커밋했다.
- 질문: 강남 고령층에서 '외출·소통 모두 적음' 비율 상위 동을 1인세대 비율 하나가 여러 동 특징만큼 가르나
- 정답: 같은 성·5세 연령 칸 안에서 시민생활 비율 상위 1/3 동 (SKT 추정 자료라 독립 정답이 아님)
- 등급에는 쓰지 않는다 (설계 §7.1: 한 자료는 입력이나 검증 중 하나로만). 임계값은 시민생활을 보기 전에 정했다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from bigcontest.config import ROOT
from bigcontest.external import seoul_living
from bigcontest.plot import plt
from bigcontest.report import Report

CFG = yaml.safe_load((ROOT / "configs" / "models_m3.yaml").read_text(encoding="utf-8"))["m4"]
REP = ROOT / "reports"
FULL = CFG["features_full"]
SINGLE = CFG["features_single"]
N_REP = CFG["validation"]["repeats"]
N_FOLD = CFG["validation"]["folds"]
N_BOOT = 2000

# ------------------------------------------------------------ 자료
sl = seoul_living()  # 품질 의심 칸은 NA (개포3동 등)
old = sl[sl["age"] >= 60].copy()
old["age_band"] = old["age"].clip(upper=75)
g = old.groupby(["adm_cd", "sex", "age_band"]).agg(iso=("iso_all", lambda s: s.sum(min_count=1)), pop=("pop", "sum")).reset_index()
g["rate"] = g["iso"] / g["pop"]
g = g.dropna(subset=["rate"])
g["rank"] = g.groupby(["sex", "age_band"])["rate"].rank(ascending=False, method="first")
g["n"] = g.groupby(["sex", "age_band"])["rate"].transform("size")
g["y"] = (g["rank"] <= g["n"] / 3).astype(int)

j4 = pd.read_csv(REP / "eda4_외부데이터결합" / "행정동_결합.csv", dtype={"adm_cd": str})
j2 = pd.read_csv(REP / "eda2_통신기초" / "행정동_프로필.csv", dtype={"adm_cd": str})
acc = pd.read_csv(REP / "w1_자원원장" / "동별_접근거리.csv")
acc = acc[acc["도시"] == "강남"].rename(columns={"안부·관계": "안부·관계 거점 거리 (규칙이 쓰는 거리)"})
feat = (j4[j4["city"] == "강남"][["adm_cd", "동", "1인세대 비율", "65세+"]].rename(columns={"65세+": "65세+ 비중"})
        .merge(j2[["adm_cd", "60대+", "20·30대", "심야(0~5시)", "주말/평일", "활동시간폭"]]
               .rename(columns={"60대+": "통신 60대+ 비중", "20·30대": "통신 20·30대 비중", "심야(0~5시)": "심야(0~5시) 비중"}),
               on="adm_cd")
        .merge(acc[["동", "안부·관계 거점 거리 (규칙이 쓰는 거리)"]], on="동"))
COLS = ["1인세대 비율", "65세+ 비중", "안부·관계 거점 거리 (규칙이 쓰는 거리)", "통신 60대+ 비중", "통신 20·30대 비중",
        "심야(0~5시) 비중", "주말/평일", "활동시간폭"]
assert len(COLS) == len(FULL) == 8
d = g.merge(feat, on="adm_cd", how="inner")
groups = d["adm_cd"].to_numpy()
y = d["y"].to_numpy()
n_dong = d["adm_cd"].nunique()


def model():
    c = CFG["model"]
    return make_pipeline(StandardScaler(), LogisticRegression(C=c["C"], max_iter=c["max_iter"]))


def oof(cols, seed):
    p = np.zeros(len(d))
    for tr, te in GroupKFold(n_splits=N_FOLD, shuffle=True, random_state=seed).split(d, y, groups):
        m = model().fit(d.iloc[tr][cols], y[tr])
        p[te] = m.predict_proba(d.iloc[te][cols])[:, 1]
    return p


reps, P = [], {"single": np.zeros(len(d)), "full": np.zeros(len(d))}
for seed in range(N_REP):
    ps = oof(["1인세대 비율"], seed)
    pf = oof(COLS, seed)
    P["single"] += ps / N_REP
    P["full"] += pf / N_REP
    reps.append({"반복": seed, "AUC 1인세대 비율만": roc_auc_score(y, ps), "AUC 8특징": roc_auc_score(y, pf)})
reps = pd.DataFrame(reps)
reps["차이(8특징 − 1인세대)"] = reps["AUC 8특징"] - reps["AUC 1인세대 비율만"]

# 동 군집 부트스트랩 (반복 평균 OOF 점수)
rng = np.random.default_rng(0)
dongs = np.unique(groups)
idx_by = {a: np.where(groups == a)[0] for a in dongs}
boot = []
for _ in range(N_BOOT):
    pick = np.concatenate([idx_by[a] for a in rng.choice(dongs, len(dongs), replace=True)])
    yy = y[pick]
    if yy.min() == yy.max():
        continue
    a_s, a_f = roc_auc_score(yy, P["single"][pick]), roc_auc_score(yy, P["full"][pick])
    boot.append((a_s, a_f, a_f - a_s))
boot = np.array(boot)
lo, hi = np.percentile(boot, [2.5, 97.5], axis=0)
auc_s, auc_f = roc_auc_score(y, P["single"]), roc_auc_score(y, P["full"])
res = pd.DataFrame([
    {"모델": "1인세대 비율 하나", "AUC": auc_s, "95% CI 하한": lo[0], "95% CI 상한": hi[0],
     "반복 범위": f"{reps['AUC 1인세대 비율만'].min():.2f}~{reps['AUC 1인세대 비율만'].max():.2f}",
     "Brier": brier_score_loss(y, P["single"])},
    {"모델": "동 특징 8개", "AUC": auc_f, "95% CI 하한": lo[1], "95% CI 상한": hi[1],
     "반복 범위": f"{reps['AUC 8특징'].min():.2f}~{reps['AUC 8특징'].max():.2f}", "Brier": brier_score_loss(y, P["full"])},
    {"모델": "차이 (8개 − 1개)", "AUC": auc_f - auc_s, "95% CI 하한": lo[2], "95% CI 상한": hi[2],
     "반복 범위": f"{reps['차이(8특징 − 1인세대)'].min():+.2f}~{reps['차이(8특징 − 1인세대)'].max():+.2f}", "Brier": np.nan},
])
base = y.mean()

# 계수 (전체 자료로 한 번 맞춤, 표준화 계수: 방향 설명용)
mf = model().fit(d[COLS], y)
coef = pd.DataFrame({"특징": COLS, "표준화 계수": mf[-1].coef_[0]}).sort_values("표준화 계수", key=np.abs, ascending=False)
one = model().fit(d[["1인세대 비율"]], y)
coef_one = float(one[-1].coef_[0][0])

out = REP / "m3_모델링결과"
out.mkdir(parents=True, exist_ok=True)
res.to_csv(out / "m4_결과.csv", index=False, encoding="utf-8-sig")
reps.to_csv(out / "m4_반복.csv", index=False, encoding="utf-8-sig")
coef.to_csv(out / "m4_계수.csv", index=False, encoding="utf-8-sig")

# ------------------------------------------------------------ 문서 (M4.md)
r = Report("m3_모델링결과", "M4 위험 요인 확인 모델 (강남 60세 이상, 탐색적)")
r.lines[1] = "[← 모델링 결과](README.md)\n"
r.p("**등급에 쓰지 않습니다.** 등급 규칙의 구조 축(S1 1인세대 비율)을 왜 하나로 정했는지 모델로 확인하는 탐색적 실험입니다. "
    "설계는 결과를 보기 전에 [`configs/models_m3.yaml`](../../configs/models_m3.yaml) `m4` 에 적었습니다.")
r.p()
r.p(f"- 자료: 서울 시민생활 '외출·소통 모두 적은 집단(전체)' ÷ 총인구, 강남 {n_dong}개 동 × 남녀 × 60~64·65~69·70~74·75+ (6개월 합). "
    f"품질 의심 칸(개포3동 등)은 뺐습니다. 행 {len(d)}개.")
r.p(f"- 정답: 같은 성·연령 칸 안에서 비율 상위 1/3 동 = 1 (기저율 {base:.2f}). 시민생활은 SKT 통신 기반 추정치라 **독립된 고립 정답이 아닙니다.**")
r.p(f"- 모델: 로지스틱(표준화). ① 1인세대 비율 하나 ② 동 특징 8개({', '.join(COLS)}).")
r.p(f"- 검증: 행정동 GroupKFold {N_FOLD}겹(같은 동은 같은 접기), 동 배정을 바꿔 {N_REP}번 반복. "
    f"CI = 반복 평균 out-of-fold 점수로 동 군집 부트스트랩 {N_BOOT:,}번.")
r.h("결과")
r.table(res.set_index("모델"), fmt={"AUC": "{:.2f}", "95% CI 하한": "{:.2f}", "95% CI 상한": "{:.2f}", "반복 범위": "{}", "Brier": "{:.3f}"})
r.table(coef.set_index("특징"), fmt={"표준화 계수": "{:+.2f}"})
fig, axes = plt.subplots(1, 2, figsize=(10, 3.4))
ax = axes[0]
ax.errorbar([0, 1], [auc_s, auc_f], yerr=[[auc_s - lo[0], auc_f - lo[1]], [hi[0] - auc_s, hi[1] - auc_f]], fmt="o", capsize=5,
            color="#2b6cb0")
ax.axhline(0.5, ls="--", color="#a0aec0", lw=0.8)
ax.set_xticks([0, 1], ["1인세대 비율 하나", "동 특징 8개"])
ax.set_xlim(-0.5, 1.5)
ax.set_ylim(0.3, 1.0)
ax.set_ylabel("AUC (동 묶음 교차검증)")
ax.set_title("강남 60세+ '외출·소통 모두 적음' 상위 동 가르기", fontsize=9)
ax = axes[1]
cc = coef.iloc[::-1]
ax.barh(cc["특징"], cc["표준화 계수"], color=["#2b6cb0" if v > 0 else "#c05621" for v in cc["표준화 계수"]])
ax.axvline(0, color="#4a5568", lw=0.8)
ax.set_title("8특징 모델 표준화 계수 (전체 자료, 방향 설명용)", fontsize=9)
ax.tick_params(axis="y", labelsize=7)
r.fig(fig, "m4_auc.png", "M4: AUC 비교와 계수")
same = lo[2] <= 0 <= hi[2]
r.h("읽는 법")
r.p(f"- 고령층에서 **1인세대 비율 하나(AUC {auc_s:.2f})가 동 특징 8개를 섞은 모델(AUC {auc_f:.2f})과 "
    + ("비슷하게** 맞힙니다(차이 CI가 0을 포함)." if same else ("보다 낫습니다**." if auc_s > auc_f else "보다 낮습니다**."))
    + " 등급 규칙이 구조 축을 1인세대 비율 하나로 둔 선택을 뒷받침하는 방향입니다.")
r.p(f"- 1인세대 비율 계수는 {'양(+)' if coef_one > 0 else '음(−)'}입니다(1인세대가 많은 동일수록 상위 동일 가능성).")
r.p(f"- {n_dong}개 동이라 CI가 넓습니다. 이 결과로 특징을 더하거나 빼지 않습니다.")
r.h("한계")
r.p("- 22개 동, 강남만. 춘천에는 시민생활 자료가 없어 옮겨 쓰지 않습니다.")
r.p("- 시민생활은 같은 통신 공급자의 모형 추정치이고 관측 기간·모형이 공개되지 않았습니다. 통신 특징(60대+·20·30대·심야 등)과 같은 자료에서 나와 그 특징의 힘은 과대평가될 수 있습니다.")
r.p("- 등급 임계값은 시민생활을 보기 전에 정했습니다. 이 모델은 등급·순서에 쓰지 않습니다.")
(out / "M4.md").write_text("\n".join(r.lines) + "\n", encoding="utf-8")
print(res.to_string())
print(coef.to_string())
