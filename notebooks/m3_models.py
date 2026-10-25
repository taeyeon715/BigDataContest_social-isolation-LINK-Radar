"""M3 모델링 장: 경보를 분류기로 보기 + M3 이탈 분류 모델 + 모델 카드·평가 숫자 한 장

실행: uv run python notebooks/m3_models.py
      (카드 원본 정리본 data/interim 이 있어야 함. M4·W-2 요약은 notebooks/m4_risk_factors.py,
       notebooks/w2_resource_eval.py 결과 CSV 를 읽음 → 그 둘을 먼저 실행)
결과: reports/m3_모델링결과/ (README.md, figs/, 경보_혼동행렬.csv, 경보_임계값곡선.csv, m3_*.csv, 평가숫자.csv, 모델카드.csv)

원칙
- 등급은 규칙(configs/rules.yaml)이 정한다. 여기 모델은 규칙의 근거·검증이며 등급을 바꾸지 않는다.
- 정답은 모두 **합성 주입**(일부러 넣은 결제 하락)이다. 실제 고립 탐지율이 아니다.
- M3 설계·특징·검증·채택 기준은 결과를 보기 전에 configs/models_m3.yaml 에 커밋했다 (git 기록으로 확인).
- 경보 임계값 곡선은 "왜 −2.5, 3일인가"를 보여 주는 그림이다. 결과를 보고 운영점을 바꾸지 않는다.
- 리포트 CSV 에는 비율·건수(쌍 수)·점수만 남긴다 (결제건수 원천 값 없음).
"""

from __future__ import annotations

import subprocess

import numpy as np
import pandas as pd
import yaml
from lightgbm import LGBMClassifier
from sklearn.calibration import calibration_curve
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from bigcontest.card import AGE_ORDER, daily, load_card2
from bigcontest.config import ROOT, load_config
from bigcontest.plot import CITY_COLOR, plt
from bigcontest.report import Report
from bigcontest.signals.alarm import (
    AlarmConfig,
    Panel,
    _nanmedian,
    combine_any,
    one_step_resid,
    robust_z,
    synthetic_normal,
    window_alarm,
)
from bigcontest.weather import load as load_weather

RULES_VER = yaml.safe_load((ROOT / "configs" / "rules.yaml").read_text(encoding="utf-8"))["version"]
M3 = yaml.safe_load((ROOT / "configs" / "models_m3.yaml").read_text(encoding="utf-8"))["m3"]
HOL = [pd.Timestamp(k) for k in load_config()["holidays"]]
KEYS = ["city", "age_g", "SEX_CCD"]
CITIES = ["강남", "춘천"]
INJ_LEN, N_START, SUCCESS_DAYS = 14, 30, 14  # EDA-6 와 같은 값
N_SYN = 5
cfg = AlarmConfig()
OUT = ROOT / "reports" / "m3_모델링결과"

c2 = load_card2()
wide = daily(c2, KEYS).pivot_table(index="date", columns=KEYS, values="USE_CNT").astype(float)
cols = list(wide.columns)
n = len(wide)
p0 = Panel(wide, HOL, cfg)


def window_outcome(alarm: np.ndarray, s: int) -> tuple[int, int, float]:
    """(주입 전 경보 중, 창 안 경보, 첫 경보까지 일수). EDA-6 와 같은 정의."""
    pre = int(alarm[s - 1] == 1)
    win_ = alarm[s:s + SUCCESS_DAYS]
    hit = np.where(win_ == 1)[0]
    return pre, int(len(hit) > 0), (float(hit[0]) if len(hit) else np.nan)


# ============================================================ ① 경보를 분류기로 (EDA-6 와 같은 쌍)
rng = np.random.default_rng(0)
cand = [s for s in range(cfg.min_train + cfg.win, n - INJ_LEN + 1) if not p0.near_hol[s:s + INJ_LEN].any()]
pairs = [(col, int(s)) for col in cols for s in sorted(rng.choice(cand, N_START, replace=False))]
syn = [synthetic_normal(wide, HOL, seed=seed) for seed in range(N_SYN)]


def alarm_rate(p: Panel, path: str = "local") -> float:
    a = np.column_stack([p.alarm_any(j, path) for j in range(len(p.cols))])
    ev = ~np.isnan(a)
    return float((a[ev] == 1).sum() / ev.sum())


def run_config(z_thr: float, k: int, drop: float = 0.15) -> tuple[dict, pd.DataFrame]:
    p = p0 if (z_thr == cfg.z_thr and k == cfg.k) else p0.with_config(z_thr=z_thr, k=k)
    rows = []
    for col, s in pairs:
        j = p.cols.index(col)
        pre, c_hit, _ = window_outcome(p.alarm_any(j, "local"), s)
        y = wide[col].to_numpy().copy()
        y[s:s + INJ_LEN] *= 1 - drop
        _, i_hit, dly = window_outcome(p.score_one(col, y)["local"], s)
        rows.append((*col, s, pre, c_hit, i_hit, dly))
    df = pd.DataFrame(rows, columns=KEYS + ["start", "pre", "ctrl", "inj", "delay"])
    new = df[df["pre"] == 0]
    c00 = int(((new["ctrl"] == 0) & (new["inj"] == 0)).sum())
    c01 = int(((new["ctrl"] == 0) & (new["inj"] == 1)).sum())
    syn_fa = np.mean([alarm_rate(Panel(sy, HOL, p.cfg)) for sy in syn])
    out = {"z 임계값": z_thr, "창 조건(7일 중)": k, "운영점": z_thr == cfg.z_thr and k == cfg.k,
           "탐지율(0→1 / 대조 음성)": c01 / (c00 + c01), "새 탐지": c01, "대조 음성": c00 + c01,
           "주입 쌍 경보율(TPR)": new["inj"].mean(), "대조 쌍 경보율(14일 창)": new["ctrl"].mean(),
           "합성 오경보율(코호트·일)": syn_fa, "배경 경보율(실제, 코호트·일)": alarm_rate(p),
           "지연 중앙값(일)": new.loc[(new["ctrl"] == 0) & (new["inj"] == 1), "delay"].median()}
    return out, df


curve, base_pairs = [], None
for z in (-2.0, -2.5, -3.0):
    for k in (2, 3, 4):
        res_, df_ = run_config(z, k)
        curve.append(res_)
        if res_["운영점"]:
            base_pairs = df_
curve = pd.DataFrame(curve)
op = curve[curve["운영점"]].iloc[0]
new = base_pairs[base_pairs["pre"] == 0]
TP, FN = int(new["inj"].sum()), int((new["inj"] == 0).sum())
FP, TN = int(new["ctrl"].sum()), int((new["ctrl"] == 0).sum())
conf = pd.DataFrame({"경보 남": [TP, FP], "경보 안 남": [FN, TN]},
                    index=pd.Index(["15% 하락 주입 (양성)", "주입 없음 = 대조 (음성)"], name="합성 정답 \\ 경보 규칙"))
paired = pd.crosstab(new["ctrl"].map({0: "대조 경보 없음", 1: "대조 경보 있음"}), new["inj"].map({0: "주입 뒤 경보 없음", 1: "주입 뒤 경보 있음"}))
eda6 = pd.read_csv(ROOT / "reports" / "eda6_사전경보재평가" / "하락폭별_탐지.csv")
e15 = eda6[(eda6["범위"] == "전체") & (eda6["하락"] == "15%")]
same_as_eda6 = len(e15) == 1 and int(e15["새 탐지"].iloc[0]) == int(op["새 탐지"]) and int(e15["대조 음성"].iloc[0]) == int(op["대조 음성"])

# ============================================================ ② M3 이탈 분류 모델 (합성 정답)
w = load_weather()
rain = w.pivot_table(index="date", columns="city", values="rain").reindex(wide.index)
hol_idx = np.where(p0.dates.isin(pd.DatetimeIndex(HOL)))[0]
age_ord = {a: i for i, a in enumerate(AGE_ORDER)}
DROPS = [0.05, 0.10, 0.15, 0.20]
rng_m3 = np.random.default_rng(M3["label"]["seed"])


def scores(j: int, col, y: np.ndarray | None):
    """(e, 도시 공통요인 s, z_abs, z_local, 둘 중 하나 경보). y=None 이면 원 자료 (패널 값 그대로)."""
    if y is None:
        return (p0.E[:, j], p0.S[:, j], p0.res["absolute"]["z"][:, j], p0.res["local"]["z"][:, j],
                p0.alarm_any(j, "either"))
    e = one_step_resid(y, p0.dow, p0.is_hol, cfg)
    same = np.where(p0.cities == p0.cities[j])[0]
    M = p0.E[:, same].copy()
    M[:, list(same).index(j)] = e
    s = _nanmedian(M, 1)
    miss = np.isnan(y)
    za = robust_z(e, p0.is_hol, cfg, miss)[0]
    zl = robust_z(e - s, p0.is_hol, cfg, miss)[0]
    return e, s, za, zl, combine_any(window_alarm(za, cfg)[0], window_alarm(zl, cfg)[0])


def nan_stat(f, x):
    x = x[~np.isnan(x)]
    return float(f(x)) if len(x) else np.nan


def features(j, col, s, sc) -> dict:
    e, sh, za, zl, either = sc
    t = s + 6
    w_ = slice(s, t + 1)
    out = {}
    for nm, z in (("local", zl), ("abs", za)):
        zz = z[w_]
        out.update({f"z_{nm}_최소": nan_stat(np.min, zz), f"z_{nm}_평균": nan_stat(np.mean, zz),
                    f"z_{nm}_-2.5미만일수": float(np.nansum(zz < -2.5)), f"z_{nm}_-2.0미만일수": float(np.nansum(zz < -2.0)),
                    f"z_{nm}_마지막": z[t]})
    out["잔차_local_평균"] = nan_stat(np.mean, (e - sh)[w_])
    out["잔차_abs_평균"] = nan_stat(np.mean, e[w_])
    out["도시공통_평균"] = nan_stat(np.mean, sh[w_])
    out["공휴일까지_일수"] = float(np.min(np.abs(hol_idx - t))) if len(hol_idx) else 60.0
    out["창_공휴일무렵_일수"] = float(p0.near_hol[w_].sum())
    rr = rain[col[0]].to_numpy()[w_]
    out["창_비온날"] = float(np.nansum(rr >= 1.0))
    out["연령"] = float(age_ord[col[1]])
    out["춘천"] = float(col[0] == "춘천")
    out["남성"] = float(col[2] in ("남성", "1", 1, "M"))
    out["_rule"] = float(np.nanmax(np.nan_to_num(either[w_], nan=0.0)) == 1)
    return out


rows = []
starts = range(cfg.min_train + cfg.win, n - INJ_LEN + 1)
for j, col in enumerate(cols):
    base = scores(j, col, None)
    y0 = wide[col].to_numpy()
    for s in starts:
        if base[4][s - 1] == 1:  # 대조에서 이미 경보 중 → 뺌
            continue
        f0 = features(j, col, s, base)
        rows.append({**f0, "_y": 0, "_drop": 0.0, "_t": s + 6, "_cohort": " ".join(map(str, col)), "_city": col[0], "_age": col[1]})
        d = float(rng_m3.choice(DROPS))
        y = y0.copy()
        y[s:s + INJ_LEN] *= 1 - d
        f1 = features(j, col, s, scores(j, col, y))
        rows.append({**f1, "_y": 1, "_drop": d, "_t": s + 6, "_cohort": " ".join(map(str, col)), "_city": col[0], "_age": col[1]})
D = pd.DataFrame(rows)
FEAT = [c for c in D.columns if not c.startswith("_")]
t0 = D["_t"].min()
D["_block"] = (D["_t"] - t0) // M3["validation"]["block_days"]
D["_date"] = p0.dates[D["_t"].to_numpy()]


def m_logistic():
    c = M3["models"]["logistic"]
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=c["C"], max_iter=c["max_iter"]))


def m_lgbm():
    return LGBMClassifier(**M3["models"]["lightgbm"])


MODELS = {"logistic": m_logistic, "lightgbm": m_lgbm}
oof = []
perm = []
for b in sorted(D["_block"].unique()):
    if b < M3["validation"]["first_test_block"]:
        continue
    te = D["_block"] == b
    start_t = D.loc[te, "_t"].min()
    tr = D["_t"] < start_t - 14  # 주입 14일이 평가 블록과 겹치지 않게
    Dtr, Dte = D[tr], D[te]
    fpr_rule_tr = Dtr.loc[Dtr["_y"] == 0, "_rule"].mean()
    blk = Dte[["_y", "_rule", "_drop", "_t", "_cohort", "_city", "_age", "_block", "_date"]].copy()
    for name, mk in MODELS.items():
        inner = cross_val_predict(mk(), Dtr[FEAT], Dtr["_y"], cv=GroupKFold(5), groups=Dtr["_cohort"], method="predict_proba")[:, 1]
        neg = inner[Dtr["_y"].to_numpy() == 0]
        thr = np.quantile(neg, 1 - fpr_rule_tr) if fpr_rule_tr > 0 else neg.max() + 1e-9
        m = mk().fit(Dtr[FEAT], Dtr["_y"])
        blk[f"p_{name}"] = m.predict_proba(Dte[FEAT])[:, 1]
        blk[f"flag_{name}"] = (blk[f"p_{name}"] > thr).astype(int)
        blk[f"thr_{name}"] = thr
        if name == M3["models"]["candidate"]:
            pi = permutation_importance(m, Dte[FEAT], Dte["_y"], scoring="average_precision", n_repeats=10, random_state=0)
            perm.append(pd.Series(pi.importances_mean, index=FEAT) * len(Dte))
    blk["train_n"] = len(Dtr)
    blk["rule_fpr_train"] = fpr_rule_tr
    oof.append(blk)
O = pd.concat(oof, ignore_index=True)
imp = (sum(perm) / len(O)).sort_values(ascending=False)
CAND = M3["models"]["candidate"]


def ece(y, p, bins=10):
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    return float(sum(abs(y[idx == b].mean() - p[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any()))


def summary(o: pd.DataFrame) -> dict:
    y = o["_y"].to_numpy()
    out = {"표본": len(o), "양성": int(y.sum()),
           "규칙 탐지율(TPR)": o.loc[o["_y"] == 1, "_rule"].mean(), "규칙 오경보율(FPR)": o.loc[o["_y"] == 0, "_rule"].mean()}
    for name in MODELS:
        p = o[f"p_{name}"].to_numpy()
        out[f"{name} PR-AUC"] = average_precision_score(y, p)
        out[f"{name} ROC-AUC"] = roc_auc_score(y, p)
        out[f"{name} Brier"] = brier_score_loss(y, p)
        out[f"{name} ECE"] = ece(y, p)
        out[f"{name} 탐지율(같은 FPR 임계값)"] = o.loc[o["_y"] == 1, f"flag_{name}"].mean()
        out[f"{name} 오경보율"] = o.loc[o["_y"] == 0, f"flag_{name}"].mean()
    # 규칙의 PR 한 점: 정밀도 = 양성 중 경보 / 전체 경보
    out["규칙 정밀도"] = o.loc[o["_rule"] == 1, "_y"].mean()
    return out


S_all = summary(O)
# 코호트 군집 부트스트랩
rng_b = np.random.default_rng(0)
coh = O["_cohort"].unique()
by_c = {c: O.index[O["_cohort"] == c].to_numpy() for c in coh}
bs = []
for _ in range(2000):
    pick = np.concatenate([by_c[c] for c in rng_b.choice(coh, len(coh), replace=True)])
    o = O.loc[pick]
    pos = o["_y"] == 1
    bs.append({"diff": o.loc[pos, f"flag_{CAND}"].mean() - o.loc[pos, "_rule"].mean(),
               "prauc": average_precision_score(o["_y"], o[f"p_{CAND}"]),
               "fpr_diff": o.loc[~pos, f"flag_{CAND}"].mean() - o.loc[~pos, "_rule"].mean()})
bs = pd.DataFrame(bs)
diff = S_all[f"{CAND} 탐지율(같은 FPR 임계값)"] - S_all["규칙 탐지율(TPR)"]
diff_lo, diff_hi = bs["diff"].quantile([0.025, 0.975])
pr_lo, pr_hi = bs["prauc"].quantile([0.025, 0.975])
fpr_diff = S_all[f"{CAND} 오경보율"] - S_all["규칙 오경보율(FPR)"]

old80 = []
for city in CITIES:
    o = O[(O["_city"] == city) & (O["_age"] == "80대+") & (O["_y"] == 1)]
    old80.append({"도시": city, "양성 수": len(o), "규칙 탐지율": o["_rule"].mean(), f"{CAND} 탐지율": o[f"flag_{CAND}"].mean(),
                  "logistic 탐지율": o["flag_logistic"].mean()})
old80 = pd.DataFrame(old80)
by_drop = O[O["_y"] == 1].groupby("_drop").agg(양성=("_y", "size"), 규칙=("_rule", "mean"),
                                               lightgbm=("flag_lightgbm", "mean"), logistic=("flag_logistic", "mean")).reset_index()
by_drop["_drop"] = (by_drop["_drop"] * 100).round().astype(int).astype(str) + "%"
by_drop = by_drop.rename(columns={"_drop": "주입 하락", "규칙": "규칙 탐지율", "lightgbm": "lightgbm 탐지율", "logistic": "logistic 탐지율"})
by_block = O.groupby("_block").apply(lambda o: pd.Series({
    "평가 기간": f"{o['_date'].min():%m/%d}~{o['_date'].max():%m/%d}", "학습 표본": int(o["train_n"].iloc[0]), "평가 표본": len(o),
    "규칙 TPR": o.loc[o["_y"] == 1, "_rule"].mean(), "규칙 FPR": o.loc[o["_y"] == 0, "_rule"].mean(),
    f"{CAND} TPR": o.loc[o["_y"] == 1, f"flag_{CAND}"].mean(), f"{CAND} FPR": o.loc[o["_y"] == 0, f"flag_{CAND}"].mean(),
    f"{CAND} PR-AUC": average_precision_score(o["_y"], o[f"p_{CAND}"])}), include_groups=False).reset_index()

ad = M3["adoption"]
c1 = diff * 100 >= ad["margin_pp"] and diff_lo > 0
c2_ = bool((old80[f"{CAND} 탐지율"] >= old80["규칙 탐지율"]).all())
c3 = fpr_diff * 100 <= ad["fpr_tolerance_pp"]
adopted = c1 and c2_ and c3
prob_ok = S_all[f"{CAND} ECE"] <= ad["ece_max"]
decision = pd.DataFrame([
    {"조건": f"(1) 같은 오경보율 임계값에서 탐지율 +{ad['margin_pp']}%p 이상, CI 하한 > 0",
     "값": f"{diff * 100:+.1f}%p (95% CI {diff_lo * 100:+.1f}~{diff_hi * 100:+.1f})", "충족": c1},
    {"조건": "(2) 80대+ (두 도시 각각) 탐지율이 규칙보다 낮지 않음",
     "값": "; ".join(f"{r_['도시']} {r_[f'{CAND} 탐지율']:.0%} vs 규칙 {r_['규칙 탐지율']:.0%}" for _, r_ in old80.iterrows()), "충족": c2_},
    {"조건": f"(3) 평가 블록 오경보율이 규칙보다 {ad['fpr_tolerance_pp']}%p 넘게 높지 않음",
     "값": f"{S_all[f'{CAND} 오경보율']:.1%} vs 규칙 {S_all['규칙 오경보율(FPR)']:.1%} ({fpr_diff * 100:+.1f}%p)", "충족": c3},
    {"조건": f"(참고) 확률을 숫자로 보이려면 ECE ≤ {ad['ece_max']}", "값": f"{S_all[f'{CAND} ECE']:.3f}", "충족": prob_ok},
])

# ============================================================ ④ 평가 숫자 한 장 + 모델 카드
mm = yaml.safe_load((ROOT / "configs" / "models.yaml").read_text(encoding="utf-8"))["decisions"]["rows"]
m2 = pd.read_csv(ROOT / "reports" / "m2_대리과제B" / "결과.csv")
m2r = m2[m2["모델"] == "릿지(선형)"].set_index("도시")
pers = pd.read_csv(ROOT / "reports" / "r1_등급규칙" / "페르소나.csv")
m4p = OUT / "m4_결과.csv"
m4 = pd.read_csv(m4p) if m4p.exists() else None
w2p = ROOT / "reports" / "w2_자원추천평가" / "쌍별_결과.csv"
w2 = pd.read_csv(w2p) if w2p.exists() else None
rng_csv = pd.read_csv(ROOT / "reports" / "r1_등급규칙" / "후보수_범위.csv")
e10 = eda6[(eda6["범위"] == "전체") & (eda6["하락"] == "10%")]
e5 = eda6[(eda6["범위"] == "전체") & (eda6["하락"] == "5%")]


def ci_txt(r_):
    return f"{r_['탐지율'].iloc[0]:.0%} (CI {r_['CI 하한'].iloc[0]:.0%}~{r_['CI 상한'].iloc[0]:.0%})"


nums = [
    ["경보 규칙 (EDA-6, local)", f"15% 하락 {ci_txt(e15)}, 10% {ci_txt(e10)}, 5% {ci_txt(e5)}",
     "코호트 결제가 그만큼 줄면 몇 %를 잡나 (합성 하락)", "실제 고립 탐지율, 개인 변화", "EDA-6 4-1절"],
    ["경보 규칙 = 분류기 (이 장 ①)", f"TPR {TP / (TP + FN):.0%}, 대조 14일 창 경보율 {FP / (FP + TN):.0%}, 합성 오경보 {op['합성 오경보율(코호트·일)']:.2%}/코호트·일",
     "임계값을 바꿨을 때 탐지와 오경보가 어떻게 바뀌나", "현장 정확도 (정답이 합성)", "이 리포트 ①"],
    ["M1 기대 활동 (채택 0b)", f"h=1 상대 MAE 강남 {mm['강남 h=1']['0b']:.1%} · 춘천 {mm['춘천 h=1']['0b']:.1%} (STL·LightGBM 미채택)",
     "평소 결제를 얼마나 맞히나", "고립 예측", "M1 1·2절"],
    [f"M3 이탈 분류 ({CAND}, 합성 정답)", (f"PR-AUC {S_all[f'{CAND} PR-AUC']:.2f} (CI {pr_lo:.2f}~{pr_hi:.2f}). 학습 블록에서 규칙과 FPR을 맞춘 임계값으로 탐지율 차 {diff * 100:+.1f}%p, "
      f"그러나 평가 블록 오경보율 {S_all[f'{CAND} 오경보율']:.1%} vs 규칙 {S_all['규칙 오경보율(FPR)']:.1%} ({fpr_diff * 100:+.1f}%p, 허용 +{ad['fpr_tolerance_pp']}%p) → {'채택' if adopted else '미채택'}"),
     "규칙보다 나은 분류가 가능한가 (미리 정한 기준)", "실제 고립 분류 성능", "이 리포트 ②"],
    ["M2 대리 과제 B (릿지)", f"순위상관 ρ 강남 {m2r.loc['강남', '순위상관 ρ']:.2f} · 춘천 {m2r.loc['춘천', '순위상관 ρ']:.2f} (GroupKFold)",
     "격자 고령 비중을 다른 칸으로 맞히나", "고립", "M2"],
]
if m4 is not None:
    s1, s8 = m4.iloc[0], m4.iloc[1]
    nums.append(["M4 위험 요인 (강남 60+, 탐색적)", f"AUC 1인세대 하나 {s1['AUC']:.2f} ({s1['95% CI 하한']:.2f}~{s1['95% CI 상한']:.2f}) vs 8특징 {s8['AUC']:.2f}",
                 "구조 축을 1인세대 비율 하나로 둔 선택의 근거", "개인 고립, 춘천", "M4.md"])
gr = rng_csv[(rng_csv["도시"] == "강남") & (rng_csv["기준일"] == "추석 D-7") & (rng_csv["집단"] == "고령층")].iloc[0]
nums.append(["등급 규칙 (R-1)", f"강남 추석 D-7 고령층 후보 {int(gr['후보수_기본'])}건 (예정된 점검·이탈 합, 임계값 범위 {int(gr['후보수_최소'])}~{int(gr['후보수_최대'])}건), 페르소나 {int((pers['기대와 맞음'] == '예').sum())}/{len(pers)}",
             "규칙이 일관되게 돌고 임계값에 얼마나 민감한가", "등급의 정확도 (정답 없음)", "R-1 1·3·5절"])
if w2 is not None:
    nums.append(["자원 추천 (W-2, 30쌍)", (f"상위 5개 충족도 (k-정규화 Recall@5, 분모 min(5, 정답 수)) {w2['k-정규화 Recall@5'].mean():.4f}; 정답 전체 재현율 {w2['정답 전체 재현율'].mean():.4f}, "
                  f"정밀도@5 {w2['정밀도@5'].mean():.2f}, Hit@5 {w2['Hit@5'].mean():.2f}; 명시적 부적격 추천 {int(w2['부적격 추천'].sum())}건, 자격 확인 필요 {int(w2['자격 확인 필요 추천'].sum())}건"),
                 "미리 적은 기준대로 고르나 (공개 원장 정답)", "정답 전체의 75% 회수, 자원이 실제로 도움이 되나·이용 가능한가", "W-2"])
nums = pd.DataFrame(nums, columns=["평가", "숫자", "입증하는 것", "입증하지 못하는 것", "출처"])

cards = pd.DataFrame([
    ["경보 규칙 (EDA-6)", "규칙 기반 이진 분류기", "카드2 28개 코호트 × 184일", "z < −2.5 가 7일 중 3일 (local·absolute)",
     "합성 주입 탐지율·합성 오경보율·배경 경보율", "D1 이탈 신호 (등급 입력, 반증 뒤)", "합성 정답, 추석 1회, 도시 단위"],
    ["M1 기대 활동", "시계열 회귀 (기준선·STL·LightGBM)", "같음", "rolling-origin, 채택 기준 선커밋 (models.yaml)",
     "상대 MAE·구간 포함률·같은 쌍 탐지율", "경보 기대값 = EDA-6 규칙 유지, 예측 0b", "184일, 공휴일 학습 1일"],
    [f"M3 이탈 분류 ({CAND})", "이진 분류 (로지스틱 기준 + LightGBM)", "합성 주입 표본 (위 패널)", "시간 블록 4주 전진 검증, 채택 기준 선커밋 (models_m3.yaml)",
     "PR-AUC·ROC-AUC·학습 블록 FPR을 맞춘 임계값의 탐지율·평가 블록 FPR·Brier·ECE·permutation importance", "채택되면 다음 판 D1 입력 후보 (이번 판 등급 불변)" if adopted else "미채택 (규칙 유지)",
     "정답이 합성, 실제 고립 아님"],
    ["M2 대리 과제 B", "회귀 (릿지·LightGBM)", "통신 격자", "행정동 GroupKFold", "R²·MAE·ρ", "보조 (등급 입력 아님)", "대리 과제"],
    ["M4 위험 요인", "로지스틱 (탐색적)", "강남 시민생활 60+ × 22개 동", "동 GroupKFold 5겹 × 50번, 동 군집 부트스트랩",
     "AUC·Brier·계수", "요인 설명만 (등급 입력 아님)", "22개 동, 같은 공급자 추정치"],
    [f"등급 규칙 {RULES_VER}", "결정 규칙", "주민등록·달력·통신·카드·원장 근거", "민감도·지표 빼기·비교안 A~D·페르소나",
     "후보 수 범위·Top-K 겹침률", "등급 결정 (유일한 결정 주체)", "정답이 없어 정확도 아님"],
], columns=["모델", "종류", "자료", "검증", "지표", "쓰는 곳", "한계"])

# ============================================================ 리포트
r = Report("m3_모델링결과", "M3 모델링 결과: 분류 성능과 위험 요인 확인")
r.p("**등급은 규칙이 정하고, 이 장의 모델은 규칙의 근거와 검증입니다.** 고립 정답 자료가 없으므로 분류 성능은 모두 "
    "**합성 정답**(일부러 넣은 코호트 결제 하락)으로 쟀습니다. 실제 고립 탐지율이 아닙니다.")
r.p()
commit = subprocess.run(["git", "log", "--format=%h %ad", "--date=short", "-1", "--", "configs/models_m3.yaml"],
                        cwd=ROOT, capture_output=True, text=True).stdout.strip()
r.p(f"- M3·M4 설계와 채택 기준은 결과를 보기 전에 [`configs/models_m3.yaml`](../../configs/models_m3.yaml)에 커밋했습니다 (최근 커밋 {commit or '-'}).")
r.p("- 결과 보기: ① 경보 규칙을 분류기로 → ② M3 이탈 분류 모델 → ③ [M4 위험 요인 확인 모델](M4.md) → ④ 모델 카드와 평가 숫자 한 장.")

r.h("① 경보 규칙을 분류기로 보기 (EDA-6 와 같은 쌍)")
r.p(f"EDA-6 의 짝지은 주입 실험({len(pairs):,}쌍, 같은 난수)을 분류 언어로 다시 적습니다. 양성 = 15% 계단 하락 14일 주입, 음성 = 같은 쌍의 원 자료(대조). "
    "시작 전날 이미 경보 중인 쌍은 뺍니다. 경로는 local 입니다.")
r.table(conf, fmt={"경보 남": "{:,}", "경보 안 남": "{:,}"})
r.p(f"- TPR(주입 쌍 중 경보) {TP / (TP + FN):.1%}, 대조 쌍 경보율(14일 창) {FP / (FP + TN):.1%}. "
    f"EDA-6 의 '탐지율'은 대조에서 울린 쌍을 뺀 0→1 / (0→0+0→1) = {int(op['새 탐지'])}/{int(op['대조 음성'])} = {op['탐지율(0→1 / 대조 음성)']:.1%}입니다"
    + (" (EDA-6 하락폭별 표와 같음)." if same_as_eda6 else " (EDA-6 하락폭별 표와 다름: EDA-6 를 다시 실행해야 함)."))
r.table(paired.rename_axis(index="대조 \\ 주입"), fmt={c: "{:,}" for c in paired.columns})
fig, ax = plt.subplots(figsize=(4.2, 3.2))
mtx = conf.to_numpy()
ax.imshow(mtx / mtx.sum(axis=1, keepdims=True), cmap="Blues", vmin=0, vmax=1)
for i in range(2):
    for jj in range(2):
        ax.text(jj, i, f"{mtx[i, jj]:,}\n({mtx[i, jj] / mtx[i].sum():.0%})", ha="center", va="center",
                color="white" if mtx[i, jj] / mtx[i].sum() > 0.5 else "#1a202c", fontsize=9)
ax.set_xticks([0, 1], ["경보 남", "경보 안 남"])
ax.set_yticks([0, 1], ["15% 하락 주입", "대조"])
ax.set_title("경보 규칙 혼동행렬 (합성 정답, 14일 창)", fontsize=9)
r.fig(fig, "f01_confusion.png", "경보 규칙 혼동행렬")

r.h3("z 임계값 곡선: 왜 −2.5, 7일 중 3일인가")
r.p(f"z 임계값 −2.0/−2.5/−3.0 × 창 조건 2/3/4일 9개 설정으로 같은 쌍을 다시 돌렸습니다. 합성 오경보율은 EDA-6 와 같은 AR(1) 정상 합성 시계열 {N_SYN}개 평균(코호트·일당). "
    "**결과를 보고 운영점을 바꾸지 않습니다** (운영점은 EDA-6 에서 이미 정함).")
cfmt = {"z 임계값": "{:.1f}", "창 조건(7일 중)": "{:.0f}", "운영점": "{}", "탐지율(0→1 / 대조 음성)": "{:.1%}", "새 탐지": "{:,.0f}",
        "대조 음성": "{:,.0f}", "주입 쌍 경보율(TPR)": "{:.1%}", "대조 쌍 경보율(14일 창)": "{:.1%}", "합성 오경보율(코호트·일)": "{:.2%}",
        "배경 경보율(실제, 코호트·일)": "{:.2%}", "지연 중앙값(일)": "{:.0f}"}
r.table(curve.set_index("z 임계값"), fmt=cfmt)
curve.to_csv(OUT / "경보_임계값곡선.csv", index=False, encoding="utf-8-sig")
conf.reset_index().to_csv(OUT / "경보_혼동행렬.csv", index=False, encoding="utf-8-sig")
fig, axes = plt.subplots(1, 2, figsize=(10, 3.8))
for k, mk_ in zip((2, 3, 4), ("o", "s", "^")):
    c_ = curve[curve["창 조건(7일 중)"] == k]
    axes[0].plot(c_["합성 오경보율(코호트·일)"] * 100, c_["탐지율(0→1 / 대조 음성)"] * 100, marker=mk_, label=f"7일 중 {k}일")
    axes[1].plot(c_["z 임계값"], c_["지연 중앙값(일)"], marker=mk_, label=f"7일 중 {k}일")
    for _, rr in c_.iterrows():
        axes[0].annotate(f"{rr['z 임계값']:.1f}", (rr["합성 오경보율(코호트·일)"] * 100, rr["탐지율(0→1 / 대조 음성)"] * 100),
                         fontsize=7, xytext=(3, -8), textcoords="offset points")
axes[0].scatter([op["합성 오경보율(코호트·일)"] * 100], [op["탐지율(0→1 / 대조 음성)"] * 100], s=160, facecolors="none",
                edgecolors="#c53030", lw=2, label="운영점 (-2.5, 3일)", zorder=5)
axes[0].set_xlabel("합성 오경보율 (% / 코호트·일)")
axes[0].set_ylabel("15% 하락 탐지율 (%)")
axes[0].legend(fontsize=7)
axes[0].set_title("탐지율 vs 오경보율 (합성)", fontsize=9)
axes[1].set_xlabel("z 임계값")
axes[1].set_ylabel("지연 중앙값 (일)")
axes[1].invert_xaxis()
axes[1].set_title("지연", fontsize=9)
r.fig(fig, "f02_threshold_curve.png", "z 임계값 곡선 (탐지율·합성 오경보율·지연)")
r.p("- 왼쪽 위로 갈수록 좋습니다. 운영점은 탐지율이 높은 구간에서 오경보율을 낮게 둔 자리입니다. 숫자는 위 표를 그대로 읽습니다.")

r.h("② M3 이탈 분류 모델 (합성 정답)")
r.p(f"질문: 코호트 × 7일 창이 '평소와 다른 하락'인지 확률로 맞힐 수 있나, 지금 경보 규칙보다 나은가. 표본 {len(D):,}개 "
    f"(양성 {int(D['_y'].sum()):,} = 5·10·15·20% 하락 무작위 주입, 음성 = 같은 코호트·시작일의 원 자료), 결정일 = 주입 7일째. "
    f"특징 {len(FEAT)}개는 결정일까지의 자료만 씁니다: {', '.join(FEAT)}.")
r.p(f"검증: 결정일 4주 블록으로 나눠 앞 블록으로 학습하고 다음 블록을 평가(평가 블록 {O['_block'].nunique()}개, 평가 표본 {len(O):,}), "
    "학습 표본 중 결정일이 평가 블록 시작 14일 전 이후인 것은 뺐습니다. 임계값은 학습 블록 안의 교차 예측(코호트 묶음)에서 "
    "규칙의 학습 오경보율과 같아지게 정했습니다. CI 는 코호트 군집 부트스트랩 2,000번.")
tbl = pd.DataFrame([
    {"모델": "경보 규칙 (둘 중 하나, 7일 안)", "PR-AUC": np.nan, "ROC-AUC": np.nan, "탐지율": S_all["규칙 탐지율(TPR)"],
     "오경보율": S_all["규칙 오경보율(FPR)"], "Brier": np.nan, "ECE": np.nan},
    *[{"모델": nm, "PR-AUC": S_all[f"{nm} PR-AUC"], "ROC-AUC": S_all[f"{nm} ROC-AUC"], "탐지율": S_all[f"{nm} 탐지율(같은 FPR 임계값)"],
       "오경보율": S_all[f"{nm} 오경보율"], "Brier": S_all[f"{nm} Brier"], "ECE": S_all[f"{nm} ECE"]} for nm in MODELS],
]).set_index("모델")
r.table(tbl, fmt={"PR-AUC": "{:.3f}", "ROC-AUC": "{:.3f}", "탐지율": "{:.1%}", "오경보율": "{:.1%}", "Brier": "{:.3f}", "ECE": "{:.3f}"})
r.p("- 여기 규칙 오경보율이 ①보다 높은 이유: ①은 공휴일 무렵 주입을 빼고 local 경로·14일 창이지만, M3 표본은 공휴일 무렵을 포함하고 "
    "'둘 중 하나' 경로가 결정일까지 7일 안에 한 번이라도 울렸는지를 봅니다(반증 전 원 경보).")
r.p(f"- {CAND} PR-AUC 95% CI {pr_lo:.3f}~{pr_hi:.3f}. 규칙의 PR 한 점: 재현율 {S_all['규칙 탐지율(TPR)']:.1%}, 정밀도 {S_all['규칙 정밀도']:.1%} "
    f"(양성:음성 = 1:1 표본이라 정밀도는 실제 운영 값이 아님).")
tbl.reset_index().to_csv(OUT / "m3_성능.csv", index=False, encoding="utf-8-sig")
r.h3("채택 판정 (미리 정한 기준)")
r.table(decision.set_index("조건"))
r.p(f"- 읽을 때 주의: 임계값은 **학습 블록**에서 규칙과 오경보율을 맞춘 값입니다. 평가 블록에서는 {CAND} {S_all[f'{CAND} 오경보율']:.1%} 대 "
    f"규칙 {S_all['규칙 오경보율(FPR)']:.1%}로 같지 않았습니다(조건 3). 그래서 (1)의 {diff * 100:+.1f}%p를 '평가에서도 같은 오경보율로 더 많이 잡았다'로 읽지 않습니다.")
r.p(f"**판정: {CAND} {'채택. 다음 판에서 D1 이탈 신호의 입력 후보로 검토. 이번 판 등급·반증·순서는 그대로' if adopted else '미채택. 경보 규칙을 그대로 씁니다'}.** "
    "채택돼도 등급을 모델이 정하지 않습니다.")
decision.to_csv(OUT / "m3_채택판정.csv", index=False, encoding="utf-8-sig")
r.h3("하락 폭별·80대+·블록별")
r.table(by_drop.set_index("주입 하락"), fmt={"양성": "{:,.0f}", "규칙 탐지율": "{:.1%}", "lightgbm 탐지율": "{:.1%}", "logistic 탐지율": "{:.1%}"})
r.table(old80.set_index("도시"), fmt={"양성 수": "{:,.0f}", "규칙 탐지율": "{:.1%}", f"{CAND} 탐지율": "{:.1%}", "logistic 탐지율": "{:.1%}"})
r.table(by_block.set_index("_block"), fmt={"평가 기간": "{}", "학습 표본": "{:,.0f}", "평가 표본": "{:,.0f}", "규칙 TPR": "{:.1%}", "규칙 FPR": "{:.1%}",
                                          f"{CAND} TPR": "{:.1%}", f"{CAND} FPR": "{:.1%}", f"{CAND} PR-AUC": "{:.3f}"})
by_drop.to_csv(OUT / "m3_하락폭별.csv", index=False, encoding="utf-8-sig")
by_block.to_csv(OUT / "m3_블록별.csv", index=False, encoding="utf-8-sig")
old80.to_csv(OUT / "m3_80대이상.csv", index=False, encoding="utf-8-sig")

fig, axes = plt.subplots(1, 3, figsize=(14, 4))
yv = O["_y"].to_numpy()
for nm, col_ in (("lightgbm", "#2b6cb0"), ("logistic", "#718096")):
    pr, rc, _ = precision_recall_curve(yv, O[f"p_{nm}"])
    axes[0].plot(rc, pr, color=col_, label=f"{nm} (PR-AUC {S_all[f'{nm} PR-AUC']:.2f})")
    fr, mp = calibration_curve(yv, O[f"p_{nm}"], n_bins=10)
    axes[1].plot(mp, fr, marker="o", color=col_, label=f"{nm} (ECE {S_all[f'{nm} ECE']:.3f})")
axes[0].scatter([S_all["규칙 탐지율(TPR)"]], [S_all["규칙 정밀도"]], color="#c53030", s=60, zorder=5, label="경보 규칙 (한 점)")
axes[0].set_xlabel("재현율 (합성 하락 중 잡은 비율)")
axes[0].set_ylabel("정밀도 (1:1 표본)")
axes[0].legend(fontsize=7)
axes[0].set_title("PR 곡선 (시간 블록 평가 묶음)", fontsize=9)
axes[1].plot([0, 1], [0, 1], ls="--", color="#a0aec0")
axes[1].set_xlabel("예측 확률 (구간 평균)")
axes[1].set_ylabel("실제 양성 비율")
axes[1].legend(fontsize=7)
axes[1].set_title("보정 곡선", fontsize=9)
top = imp.head(12).iloc[::-1]
axes[2].barh(top.index, top.values, color="#2b6cb0")
axes[2].set_title(f"{CAND} permutation importance (PR-AUC 감소)", fontsize=9)
axes[2].tick_params(axis="y", labelsize=7)
r.fig(fig, "f03_m3_pr_calibration.png", "M3: PR 곡선, 보정 곡선, permutation importance")
imp.rename("PR-AUC 감소").reset_index().rename(columns={"index": "특징"}).to_csv(OUT / "m3_중요도.csv", index=False, encoding="utf-8-sig")
r.p(f"- 중요도 상위: {', '.join(imp.head(5).index)}. 모두 경보 규칙이 쓰는 것과 같은 잔차·z 요약입니다. "
    "모델은 새 자료를 쓰지 않고 같은 신호를 연속값으로 읽어 작은 하락(5~10%)을 더 잡습니다.")
worst = by_block.loc[by_block["규칙 FPR"].idxmax()]
r.p(f"- 오경보는 기간에 따라 크게 흔들립니다: 규칙 FPR 이 가장 높은 블록은 {worst['평가 기간']}({worst['규칙 FPR']:.0%}, 추석 연휴 포함). "
    "학습 블록에서 맞춘 임계값이 다음 블록에서 그대로 지켜지지 않아 (3) 조건을 넘지 못했습니다.")
r.p(f"- 보정 곡선이 대각선 아래에 있으면 확률이 실제보다 높게 나온다는 뜻입니다(ECE {S_all[f'{CAND} ECE']:.3f}). 그래서 채택되더라도 확률 숫자를 화면에 보이지 않습니다.")
r.p("- **한계**: 정답이 합성 계단 하락이라 쉬운 과제입니다. 실제 고립은 천천히 줄 수 있고, 결제 하락이 고립을 뜻하지도 않습니다. "
    "5% 하락은 규칙·모델 모두 잘 못 잡습니다(위 하락 폭별 표). 블록이 4개뿐이고 첫 블록은 학습 표본이 적습니다.")

r.h("③ M4 위험 요인 확인 모델 (강남 60세 이상, 탐색적)")
if m4 is not None:
    r.table(m4.set_index("모델"), fmt={"AUC": "{:.2f}", "95% CI 하한": "{:.2f}", "95% CI 상한": "{:.2f}", "반복 범위": "{}", "Brier": "{:.3f}"})
    r.p("- 1인세대 비율 하나가 동 특징 8개 모델과 비슷하게 맞힙니다 → 구조 축을 1인세대 비율 하나로 둔 등급 규칙의 근거. "
        "등급에는 쓰지 않습니다. 자세한 내용: [M4.md](M4.md)")
else:
    r.p("M4 결과가 없습니다. `uv run python notebooks/m4_risk_factors.py` 를 먼저 실행하세요.")

r.h("④ 모델 카드와 평가 숫자 한 장")
r.h3("평가 숫자 한 장")
r.p("고립 정답 자료가 없으므로 하나의 '정확도'로 합치지 않고, 평가마다 입증하는 것과 입증하지 못하는 것을 함께 적습니다.")
r.table(nums.set_index("평가"))
nums.to_csv(OUT / "평가숫자.csv", index=False, encoding="utf-8-sig")
r.h3("모델 카드")
r.table(cards.set_index("모델"))
cards.to_csv(OUT / "모델카드.csv", index=False, encoding="utf-8-sig")
r.p("- M1 의 자세한 모델 카드는 [M1 리포트](../m1_기대활동모델/README.md#모델-카드)에 있습니다.")
r.p("- **하지 않는 것**: 모델 출력으로 등급을 바꾸기, '고립 확률'을 내기, 합성 정답 성능을 '고립 탐지율'로 부르기, 강남 시민생활 모델을 춘천에 옮기기.")
r.save()
print(curve.to_string())
print(tbl.to_string())
print(decision.to_string())
print(nums.to_string())
