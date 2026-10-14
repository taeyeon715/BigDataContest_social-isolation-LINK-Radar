"""정답 있는 주입: 코호트 사람 중 일부를 특정 페르소나로 바꿔(또는 새로 들여) 결제 시계열과 정답을 함께 만든다.

- 같은 사람 난수(U)·같은 포아송 난수(cnt_u)를 다시 쓰므로 주입 전후가 **짝지어진다** (차이는 주입 효과뿐).
- 비율 f 를 키울 때 사람을 같은 순서로 더하므로 (중첩 표본) 비율별 결과가 단조롭게 비교된다.
- S7(이사 직후형)은 바꾸지 않고 **새 사람을 더한다** (전입). S6(은둔 청년)은 처음부터 W 라 변화가 없다.

정답(label)
- inj_w_frac(t): 주입한 사람 중 그날 W(단절)인 사람 수 ÷ 코호트 사람 수  (= 코호트 안 '새로 생긴 단절' 비율)
- exp_drop(t): 기대 결제 감소율 1 − λ_f(t)/λ_0(t) (잡음 없는 기대값 기준)
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd

from bigcontest.sim import params as P
from bigcontest.sim.world import COHORTS, World, person_lambda, simulate_states


@dataclass
class Injection:
    cohort: tuple
    persona: str
    onset: int
    order: np.ndarray  # 주입 후보 사람 (순서 고정)
    dlam: np.ndarray  # 사람 수 누적 λ 변화 (k명 → dlam[k], 업종 × 일)
    dW: np.ndarray  # k명 주입 시 W 인 주입 사람 수 (k × 일)
    n_cohort: int

    def k_of(self, frac: float) -> int:
        return int(min(round(frac * self.n_cohort), len(self.order)))

    def lam(self, w: World, frac: float) -> np.ndarray:
        """코호트 업종 × 일 λ0 (달력 효과 전)."""
        return w.lam0[COHORTS.index(self.cohort)] + self.dlam[self.k_of(frac)]

    def w_frac(self, frac: float) -> np.ndarray:
        return self.dW[self.k_of(frac)] / max(self.n_cohort, 1)


def prepare(w: World, cohort: tuple, persona: str, onset: int, max_frac: float = 0.5, seed: int = 0) -> Injection:
    ci = COHORTS.index(cohort)
    members = np.where(w.cohort_of == ci)[0]
    n_c = len(members)
    rng = np.random.default_rng([w.seed, seed, ci, onset, P.PERSONA_IDS.index(persona)])
    T = w.T
    k_max = int(round(max_frac * n_c))
    if persona == "S7":  # 새 사람 전입: 기존 사람과 같은 속성, 새 활동량·난수
        src = members[rng.integers(0, n_c, k_max)]
        sub = w.people.iloc[src].copy()
        sub["persona"], sub["onset"] = "S7", onset
        U = rng.random((k_max, T)).astype(np.float32)
        S_new, pres = simulate_states(sub, w.cal, U)
        lam_new = _lam_for(w, src, sub["persona"].to_numpy(), S_new, pres, hetero=rng.gamma(P.HETERO_SHAPE, 1 / P.HETERO_SHAPE, k_max))
        diff = lam_new
        W_new = ((S_new == P.W) & pres).astype(float)
        order = src
    else:
        normal = members[(w.people["persona"].to_numpy()[members] == "S5")]
        order = rng.permutation(normal)[:k_max]
        sub = w.people.iloc[order].copy()
        sub["persona"], sub["onset"] = persona, onset
        start = 0 if persona == "S6" else onset  # 시작일 전에는 원래 삶 그대로 (은둔 청년은 처음부터)
        S_new, pres = simulate_states(sub, w.cal, w.U[order], init=w.states[order], start=start)
        old = person_lambda(w, order, w.people["persona"].to_numpy()[order], w.states[order], w.present[order])
        new = person_lambda(w, order, sub["persona"].to_numpy(), S_new, pres)
        diff = new - old
        W_new = ((S_new == P.W) & pres).astype(float)
    K = diff.shape[1]
    dlam = np.concatenate([np.zeros((1, K, T)), np.cumsum(diff, 0)], 0)
    dW = np.concatenate([np.zeros((1, T)), np.cumsum(W_new, 0)], 0)
    return Injection(cohort, persona, onset, order, dlam, dW, n_c)


def _lam_for(w: World, src, persona, S, present, hetero) -> np.ndarray:
    """새 사람(전입) λ: person_lambda 가 읽는 칸(활동량·코호트·목표)만 바꾼 가벼운 객체로 계산."""
    view = SimpleNamespace(hetero=hetero, cohort_of=w.cohort_of[src], targets=w.targets)
    return person_lambda(view, np.arange(len(src)), persona, S, present)


def cohort_counts(w: World, inj: Injection, frac: float) -> np.ndarray:
    """주입한 코호트의 업종 × 일 결제건수 (같은 포아송 난수)."""
    from scipy.stats import poisson

    ci = COHORTS.index(inj.cohort)
    lam = inj.lam(w, frac) * np.exp(w.log_cal[ci] + w.noise[ci])[None, :]
    return poisson.ppf(w.cnt_u[ci], np.maximum(lam, 1e-9)).astype(float)


def expected_ratio(w: World, inj: Injection, frac: float) -> np.ndarray:
    """기대 결제 비율 λ_f/λ_0 (일). 실제 배경에 '가상 변화량'을 곱할 때 쓴다 (하이브리드)."""
    ci = COHORTS.index(inj.cohort)
    return inj.lam(w, frac).sum(0) / np.maximum(w.lam0[ci].sum(0), 1e-9)


# ---------------------------------------------------------------- M3 용 정답 있는 자료


INJ_PERSONAS = ("S1", "S2", "S3", "S4", "S6", "S7")
FRACS = (0.0, 0.02, 0.05, 0.1, 0.2, 0.3)
LABEL_DROP = 0.05  # 정답 '이탈': 기대 결제가 자기 평소(첫 28일 기대값) 대비 5% 이상 낮아진 날


def labeled_dataset(w: World, n_scenarios: int = 120, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """정답 있는 합성 패널 (M3 이탈 분류기 학습·평가용).

    반환 (panel, scenarios)
    - panel: 시나리오 × 날짜 × 코호트 한 행. 열
      scenario_id, date, city, age_g, SEX_CCD, sim_pay_index(합성 결제건수, 원천 아님), is_target,
      inj_persona, inj_fraction, onset_date, inj_w_frac, exp_drop,
      label_deviation(exp_drop − 첫 28일 exp_drop 중앙값 ≥ 0.05),
      bg_w_frac(배경 페르소나 포함 그날 W 비율), is_holiday, off_block, heat, cold
    - scenarios: 시나리오 한 행 (대상 코호트·페르소나·비율·시작일·시드)
    """
    rng = np.random.default_rng([w.seed, seed, 31])
    dates = w.cal.dates
    T = len(dates)
    base_cnt = w.counts()
    base_tot = base_cnt.sum(1)  # 코호트 × 일
    inw = (w.states == P.W) & w.present
    ok = w.cohort_of >= 0
    bg = np.zeros((len(COHORTS), T))
    np.add.at(bg, w.cohort_of[ok], inw[ok])
    n_c = np.bincount(w.cohort_of[ok], minlength=len(COHORTS))
    bg = bg / np.maximum(n_c, 1)[:, None]
    rows, scen = [], []
    for s in range(n_scenarios):
        ci = int(rng.integers(len(COHORTS)))
        persona = str(rng.choice(INJ_PERSONAS))
        frac = float(rng.choice(FRACS))
        onset = int(rng.integers(35, T - 21))
        tot = base_tot.copy()
        inj_w = np.zeros(T)
        drop = np.zeros(T)
        if frac > 0:
            inj = prepare(w, COHORTS[ci], persona, onset, max_frac=frac, seed=s)
            tot[ci] = cohort_counts(w, inj, frac).sum(0)
            inj_w = inj.w_frac(frac)
            drop = 1 - expected_ratio(w, inj, frac)
        label = (drop - np.median(drop[:28])) >= LABEL_DROP  # 자기 평소(첫 28일) 대비 변화. S6 처럼 처음부터 낮으면 False
        scen.append({"scenario_id": s, "city": COHORTS[ci][0], "age_g": COHORTS[ci][1], "SEX_CCD": COHORTS[ci][2],
                     "inj_persona": persona if frac > 0 else "없음", "persona_name": P.PERSONAS[persona].name if frac > 0 else "대조",
                     "inj_fraction": frac, "onset_date": dates[onset].date(), "max_exp_drop": round(float(drop.max()), 4),
                     "detectable_by_design": bool(frac > 0 and P.PERSONAS[persona].detectable)})
        for j, c in enumerate(COHORTS):
            tgt = j == ci
            rows.append(pd.DataFrame({
                "scenario_id": s, "date": dates, "city": c[0], "age_g": c[1], "SEX_CCD": c[2],
                "sim_pay_index": tot[j], "is_target": tgt,
                "inj_persona": (persona if frac > 0 else "없음") if tgt else "없음",
                "inj_fraction": frac if tgt else 0.0, "onset_date": dates[onset] if tgt else pd.NaT,
                "inj_w_frac": inj_w if tgt else 0.0, "exp_drop": drop if tgt else 0.0,
                "label_deviation": label if tgt else False, "bg_w_frac": bg[j],
                "is_holiday": w.cal.is_hol, "off_block": w.cal.off_block,
                "heat": w.cal.heat[c[0]], "cold": w.cal.cold[c[0]]}))
    panel = pd.concat(rows, ignore_index=True)
    panel["data_source"] = "synthetic"
    return panel, pd.DataFrame(scen)


def wide(panel: pd.DataFrame, scenario_id: int) -> pd.DataFrame:
    """한 시나리오를 경보 패널 입력 모양(날짜 × (도시, 연령, 성))으로."""
    d = panel[panel["scenario_id"] == scenario_id]
    return d.pivot_table(index="date", columns=["city", "age_g", "SEX_CCD"], values="sim_pay_index").astype(float)
