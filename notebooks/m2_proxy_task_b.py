"""M2 대리 과제 B (보조 과제)

실행: uv run python notebooks/m2_proxy_task_b.py
결과: reports/m2_대리과제B/

질문: 통신 격자의 '시간대별 체류 패턴'(시간대 표)만 보고 그 격자의 '60대+ 체류 비중'(성·연령 표)을 맞힐 수 있나?
- 보조 과제: 통신 지표끼리의 예측 가능성이다. 고립 예측 성능이 아니다.
- 시간대 표와 성·연령 표는 서로 다른 표다. 둘을 곱해 '시간대 × 연령'을 만들지 않는다.
  같은 격자·같은 달을 따로 잰 두 값으로만 격자 단위에서 붙인다.
- 평가: 행정동 단위 GroupKFold (같은 동의 격자·달은 같은 접기에만 들어감)
"""

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from bigcontest.config import load_config
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.telecom import AGE_COLS, TIME_COLS, age_cols, grid_id

D = load_config()["interim_dir"]
MIN_TOTAL = 20  # EDA-2와 같은 기준: 하루 체류량 20 미만 격자·달은 뺌
N_FOLD = 5
LGB_PARAMS = dict(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=30, subsample=0.8, subsample_freq=1,
                  colsample_bytree=0.8, random_state=0, n_jobs=1, verbose=-1)
TAG = "보조 과제: 통신 지표끼리의 예측 가능성, 고립 예측 아님"

r = Report("m2_대리과제B", "M2 대리 과제 B (보조 과제)")
r.p(f"> **{TAG}**")
r.p()
r.p("통신 격자의 **시간대별 체류 패턴**만 보고 그 격자의 **60대+ 체류 비중**을 얼마나 맞힐 수 있는지 봤습니다. 시간대 자료만 있는 곳에서 고령 밀집 격자를 짐작할 수 있는지 보는 보조 실험입니다.")
r.p("- 맞히는 값(목표): 성·연령 표의 60대+(남+여) 체류량 ÷ 전체 체류량 (같은 격자·같은 달)")
r.p("- 쓰는 값(특징): 시간대 표의 24개 시간대 비중(그 시간 체류량 ÷ 하루 합), 하루 합(log), 심야(0~5시)/낮(10~16시) 비율")
r.p("- **두 표를 곱하지 않습니다.** 시간대 표와 성·연령 표는 따로 집계된 표라 '고령의 시간대 패턴'은 알 수 없습니다. 같은 격자·같은 달을 따로 잰 두 값을 격자 단위로만 붙였습니다.")

# ------------------------------------------------------------ 자료
age = pd.read_parquet(D / "tel_age.parquet")
tim = pd.read_parquet(D / "tel_time.parquet")
for d in (age, tim):
    d["g"] = grid_id(d["X_COORD"], d["Y_COORD"])
age["total"] = age[AGE_COLS].sum(axis=1)
age["s60"] = age[age_cols("60GU")].sum(axis=1)
A = age.groupby(["city", "g", "STD_YM"])[["total", "s60"]].sum()
# 격자의 행정동 = 체류량이 가장 큰 행정동 (6개월 합)
dong = age.groupby(["city", "g", "adm_cd"])["total"].sum().reset_index().sort_values("total").drop_duplicates(["city", "g"], keep="last")
dong = dong.set_index(["city", "g"])["adm_cd"]
adm_type = age.drop_duplicates("adm_cd").set_index("adm_cd")["adm_type"]
T = tim.groupby(["city", "g", "STD_YM"])[TIME_COLS].sum()
X = T.div(T.sum(axis=1), axis=0).add_prefix("share_")
X["log_total_time"] = np.log10(T.sum(axis=1).clip(lower=0.01))
night = T[[f"TMST_{h:02d}" for h in range(0, 6)]].sum(axis=1)
day = T[[f"TMST_{h:02d}" for h in range(10, 17)]].sum(axis=1)
X["night_day"] = np.log2(((night + 0.01) / (day + 0.01)).clip(1 / 64, 64))
df = A.join(X, how="inner")
n_before = len(df)
df = df[df["total"] >= MIN_TOTAL].copy()
df["y"] = df["s60"] / df["total"]
df = df.reset_index()
df["adm_cd"] = df.set_index(["city", "g"]).index.map(dong)
df["adm_type"] = df["adm_cd"].map(adm_type)
FEATS = [c for c in X.columns]

r.h("1. 자료")
cnt = df.groupby("city").agg(격자달=("y", "size"), 격자=("g", "nunique"), 행정동=("adm_cd", "nunique"), 목표_평균=("y", "mean"), 목표_표준편차=("y", "std"))
r.p(f"250m 격자 × 월(2025-07~12). 두 표에 모두 있고 하루 체류량(성·연령 표) {MIN_TOTAL} 이상인 격자·달만 씁니다({n_before:,} → {len(df):,}행). 격자의 행정동은 6개월 체류량이 가장 큰 행정동입니다.")
r.table(cnt.rename(columns={"목표_평균": "60대+ 비중 평균", "목표_표준편차": "60대+ 비중 표준편차"}),
        {"격자달": "{:,}", "격자": "{:,}", "행정동": "{}", "60대+ 비중 평균": "{:.1%}", "60대+ 비중 표준편차": "{:.1%}"})
r.p("- 전체 체류량에는 10대가 들어 있습니다. 같은 격자를 6개월 반복해 쓰므로 행들은 독립이 아닙니다. 그래서 행정동 단위로 접기를 나눕니다.")


# ------------------------------------------------------------ 평가
def oof(d: pd.DataFrame, make):
    pred = np.full(len(d), np.nan)
    for tr, te in GroupKFold(n_splits=N_FOLD).split(d, groups=d["adm_cd"]):
        if make is None:
            pred[te] = d["y"].iloc[tr].mean()
        else:
            m = make()
            m.fit(d[FEATS].iloc[tr], d["y"].iloc[tr])
            pred[te] = m.predict(d[FEATS].iloc[te])
    return pred


def lgbm():
    import lightgbm as lgb

    return lgb.LGBMRegressor(**LGB_PARAMS)


MODELS = {"평균(학습 접기 평균)": None, "릿지(선형)": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)), "LightGBM": lgbm}
rows, preds = [], {}
for c in ["강남", "춘천"]:
    d = df[df["city"] == c].reset_index(drop=True)
    for name, mk in MODELS.items():
        p = oof(d, mk)
        preds[(c, name)] = (d, p)
        err = d["y"] - p
        rho = spearmanr(d["y"], p).statistic if name != "평균(학습 접기 평균)" else np.nan
        rows.append({"도시": c, "모델": name, "MAE(%p)": np.abs(err).mean() * 100, "R²": 1 - (err**2).sum() / ((d["y"] - d["y"].mean()) ** 2).sum(),
                     "순위상관 ρ": rho, "행": len(d), "행정동(접기 단위)": d["adm_cd"].nunique()})
res = pd.DataFrame(rows).set_index("도시")
res.to_csv(r.dir / "결과.csv", encoding="utf-8-sig")
r.h("2. 결과 (행정동 GroupKFold, 접기 밖 예측)")
r.p(f"행정동 단위 {N_FOLD}겹 GroupKFold. 평가할 때 그 행정동의 격자는 학습에 한 번도 쓰이지 않았습니다. 하이퍼파라미터는 고정(튜닝 안 함).")
show = res.assign(**{"순위상관 ρ": res["순위상관 ρ"].map(lambda v: "—" if np.isnan(v) else f"{v:.2f}")})
r.table(show, {"모델": "{}", "MAE(%p)": "{:.2f}", "R²": "{:.2f}", "순위상관 ρ": "{}", "행": "{:,}", "행정동(접기 단위)": "{}"})
r.p("- 순위상관(ρ)은 격자 순위가 얼마나 비슷한지일 뿐 '일치율'이나 '설명력'이 아닙니다.")

# 춘천 동·읍·면
sub = []
for c in ["강남", "춘천"]:
    d, p = preds[(c, "LightGBM")]
    for t, g in d.assign(p=p).groupby("adm_type"):
        e = g["y"] - g["p"]
        sub.append({"도시": c, "행정동 종류": t, "행": len(g), "MAE(%p)": e.abs().mean() * 100,
                    "R²(그 종류 안)": 1 - (e**2).sum() / ((g["y"] - g["y"].mean()) ** 2).sum()})
r.h3("행정동 종류별 (LightGBM)")
r.table(pd.DataFrame(sub).set_index("도시"), {"행정동 종류": "{}", "행": "{:,}", "MAE(%p)": "{:.2f}", "R²(그 종류 안)": "{:.2f}"})

fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
for ax, c in zip(axes, ["강남", "춘천"]):
    d, p = preds[(c, "LightGBM")]
    ax.scatter(d["y"] * 100, p * 100, s=3, alpha=0.3, color=CITY_COLOR[c])
    lim = [0, max(d["y"].max(), p.max()) * 100]
    ax.plot(lim, lim, color="#718096", lw=1)
    ax.set_xlabel("실제 60대+ 비중 (%)")
    ax.set_ylabel("예측 (접기 밖, %)")
    ax.set_title(f"{c}: LightGBM (보조 과제)")
r.fig(fig, "f01_oof_scatter.png", "접기 밖 예측 대 실제")

# ------------------------------------------------------------ 모델 카드·한계
best = res.reset_index().sort_values("R²", ascending=False).groupby("도시").head(1).set_index("도시")
r.h("모델 카드")
r.p("| 항목 | 내용 |")
r.p("|---|---|")
r.p(f"| 이름·버전 | 대리 과제 B v1 ({TAG}) |")
r.p("| 학습 자료 | SKT 통신 성·연령 표(목표), 시간대 표(특징), 2025-07~12, 250m 격자 × 월 |")
r.p("| trained_until | 2025-12-31 (6개월 전체를 행정동 접기로 나눔. 시간 순서 평가가 아님) |")
r.p("| calibrated_until | 없음 (임계값을 정하지 않음) |")
r.p("| 특징 | 시간대 비중 24개, 하루 합(log), 심야/낮 비율(log2) |")
r.p(f"| 모델 | 평균, 릿지(α=1), LightGBM({', '.join(f'{k}={v}' for k, v in LGB_PARAMS.items() if k in ('n_estimators', 'learning_rate', 'num_leaves', 'min_child_samples'))}) |")
r.p("| 평가 | 행정동 GroupKFold 5겹, 접기 밖 MAE·R²·순위상관 |")
r.p("| 한계 | 아래 |")
r.p()
r.p("**한계**")
r.p("- 두 표 모두 같은 통신사 자료입니다. 시간대 패턴으로 연령 비중을 맞힌다는 것은 **통신 지표끼리 서로 관련 있다**는 뜻일 뿐, 고립이나 고령 1인가구를 맞힌다는 뜻이 아닙니다.")
r.p("- 체류는 거주가 아닙니다. 60대+ 비중이 높은 격자가 고령 주민이 많은 곳이라는 보장은 없습니다.")
r.p("- 같은 격자를 6개월 반복해 쓰고, 행정동 안 격자들은 서로 비슷합니다. 행정동 단위로 나눴지만 이웃 행정동끼리도 비슷할 수 있어 성능이 조금 낙관적일 수 있습니다.")
r.p("- 시간대 표와 성·연령 표의 하루 합이 다를 수 있습니다(따로 집계, 작은 값 처리 차이). 곱하거나 나누지 않았습니다.")

r.h("정리")
r.p("| 결과 | 값 | 해석 상태 |")
r.p("|---|---|---|")
for c in ["강남", "춘천"]:
    b = best.loc[c]
    base = res[(res.index == c) & (res["모델"] == "평균(학습 접기 평균)")].iloc[0]
    r.p(f"| {c}: 시간대 패턴 → 60대+ 비중 (보조) | 가장 나은 모델 {b['모델']}: R² {b['R²']:.2f}, MAE {b['MAE(%p)']:.2f}%p (평균 예측 MAE {base['MAE(%p)']:.2f}%p), ρ {b['순위상관 ρ']:.2f} | 계산 재현 |")
r.p("| 고립 예측 성능 | 이 과제로는 알 수 없음 | 미확인 |")
r.save()
print("done")
