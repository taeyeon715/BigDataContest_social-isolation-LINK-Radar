"""②~⑤ 페르소나 상태 전이 → 하루 활동(결제·체류) → 제공 데이터와 같은 단위로 집계.

흐름
1. 가상 사람(population.synthesize) + 페르소나 배정(assign_personas)
2. 상태 전이: 사람마다 A(평소)/R(위축)/W(단절) 마르코프. 계기(시작일·연휴·겨울·사건·전입)에 계기 행렬
3. 결제: 코호트(도시 × 연령 × 성) × 업종 묶음 × 일 기대 건수
     λ = Σ_사람 (활동량 × 업종 비중 × 상태 배율 × 거주 여부) × exp(요일 + 월 + 연휴·날씨 효과 + 도시 공통 잡음 + 코호트 잡음)
   건수 = 포아송(λ). 사람별 포아송의 합은 합의 포아송과 분포가 같으므로 코호트 단위에서 한 번 뽑는다
   (같은 난수표 cnt_u 로 역누적분포를 쓰므로, 주입 실험에서 배경 잡음이 그대로 짝지어진다)
4. 통신: 사람의 외출 수준(상태별) × 계절 체류 지수 → 집·활동 칸 × 월 × (성연령 / 시간대 / 요일)
5. 집계: 카드 데이터2·데이터1 행, 통신 3종 격자 행 (제공 데이터와 같은 열 이름·단위)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import poisson

from bigcontest.sim import params as P
from bigcontest.sim.calendar import Calendar, load_calendar
from bigcontest.sim.calibrate import load_targets
from bigcontest.sim.population import assign_personas, synthesize

COHORTS = [(c, a, s) for c in P.CITIES for a in P.CARD_AGES for s in P.SEXES]
CIDX = {c: i for i, c in enumerate(COHORTS)}
K = len(P.CATS)


@dataclass
class World:
    people: pd.DataFrame
    cal: Calendar
    targets: dict
    seed: int
    U: np.ndarray  # 사람 × 일 상태 전이 난수 (주입 실험에서 같은 난수 재사용)
    states: np.ndarray  # 사람 × 일 (0 A, 1 R, 2 W)
    present: np.ndarray  # 사람 × 일 (전입 전 False)
    hetero: np.ndarray  # 사람 활동량
    cohort_of: np.ndarray  # 사람 → 코호트 번호 (10대 = −1)
    cnt_u: np.ndarray  # 코호트 × 업종 × 일 포아송 난수
    log_cal: np.ndarray  # 코호트 × 일 달력·날씨 로그 효과
    noise: np.ndarray  # 코호트 × 일 잡음 (도시 공통 + 코호트)
    noise_params: dict
    lam0: np.ndarray  # 코호트 × 업종 × 일 사람 합 (달력 효과 전)
    pop_info: dict = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.cal.dates)

    def counts(self, lam0: np.ndarray | None = None) -> np.ndarray:
        """코호트 × 업종 × 일 결제건수 (합성)."""
        lam = (self.lam0 if lam0 is None else lam0) * np.exp(self.log_cal + self.noise)[:, None, :]
        return poisson.ppf(self.cnt_u, np.maximum(lam, 1e-9)).astype(float)

    def panel_frame(self, cnt: np.ndarray | None = None) -> pd.DataFrame:
        """날짜 × 코호트 결제건수 (경보 패널 입력 모양)."""
        c = self.counts() if cnt is None else cnt
        cols = pd.MultiIndex.from_tuples(COHORTS, names=["city", "age_g", "SEX_CCD"])
        return pd.DataFrame(c.sum(1).T, index=self.cal.dates, columns=cols)


# ---------------------------------------------------------------- 상태 전이


def _matrices() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    P.check_personas()
    base = np.array([np.asarray(p.base, float) for p in P.PERSONAS.values()])
    trig = np.array([np.asarray(p.trigger if p.trigger is not None else p.base, float) for p in P.PERSONAS.values()])
    p0 = np.array([p.p0 for p in P.PERSONAS.values()], float)
    return base, trig, p0


def trigger_mask(people: pd.DataFrame, cal: Calendar) -> np.ndarray:
    """사람 × 일: 계기 행렬을 쓰는 날."""
    n, T = len(people), len(cal.dates)
    kind = np.array([P.PERSONAS[k].trigger_kind for k in people["persona"]])
    onset = people["onset"].to_numpy()
    t = np.arange(T)[None, :]
    out = np.zeros((n, T), bool)
    m = kind == "onset"
    out[m] = t >= onset[m, None]
    m = kind == "holiday"
    out[m] = cal.off_block[None, :]
    m = kind == "event"
    out[m] = t == onset[m, None]
    m = kind == "winter"
    if m.any():
        city = people["city"].to_numpy()
        for c in P.CITIES:
            mc = m & (city == c)
            out[mc] = (cal.winter | cal.cold[c] | cal.heat[c])[None, :]
    return out


def simulate_states(people: pd.DataFrame, cal: Calendar, U: np.ndarray, init: np.ndarray | None = None,
                    start: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """마르코프 상태 (사람 × 일)와 거주 여부. 같은 U 면 같은 결과.
    init·start: start 전 날의 상태를 init 으로 고정하고 start 부터 이 페르소나로 전이 (주입: 시작일 전에는 원래 삶 그대로)."""
    base, trig, p0 = _matrices()
    pk = people["persona"].map({k: i for i, k in enumerate(P.PERSONA_IDS)}).to_numpy()
    n, T = U.shape
    tm = trigger_mask(people, cal)
    S = np.zeros((n, T), np.int8)
    S[:, 0] = (U[:, 0][:, None] > np.cumsum(p0[pk], 1)).sum(1).clip(0, 2)
    if init is not None and start > 0:
        S[:, :start] = init[:, :start]
    for t in range(max(1, start), T):
        prev = S[:, t - 1]
        M = np.where(tm[:, t][:, None], trig[pk, prev], base[pk, prev])
        S[:, t] = (U[:, t][:, None] > np.cumsum(M, 1)).sum(1).clip(0, 2)
    move = (people["persona"] == "S7").to_numpy()
    onset = people["onset"].to_numpy()
    present = np.ones((n, T), bool)
    present[move] = np.arange(T)[None, :] >= onset[move, None]
    # 전입한 날은 위축(R)에서 시작
    rows = np.where(move)[0]
    for r in rows:
        o = onset[r]
        if o < T:
            S[r, o] = P.R
            for t in range(o + 1, T):
                M = base[pk[r], S[r, t - 1]]
                S[r, t] = min(int((U[r, t] > np.cumsum(M)).sum()), 2)
    return S, present


# ---------------------------------------------------------------- 결제 기대값


def _cat_share(targets: dict) -> np.ndarray:
    return np.array([[targets["card"]["|".join(c)]["cat_share"][k] for k in P.CATS] for c in COHORTS])


def person_lambda(world_like, idx: np.ndarray, persona: np.ndarray, S: np.ndarray, present: np.ndarray) -> np.ndarray:
    """사람들(idx)의 업종 × 일 기대 결제 (사람 × 업종 × 일, 달력 효과 전)."""
    mult = P.mult_table()
    pk = pd.Series(persona).map({k: i for i, k in enumerate(P.PERSONA_IDS)}).to_numpy()
    share = _cat_share(world_like.targets)[world_like.cohort_of[idx]]  # 사람 × 업종
    base = (world_like.hetero[idx] * P.RATE_PER_DAY)[:, None] * share
    m = mult[pk[:, None], S]  # 사람 × 일 × 업종
    return base[:, :, None] * np.moveaxis(m, 2, 1) * present[:, None, :]


def cohort_lambda(people: pd.DataFrame, cohort_of: np.ndarray, hetero: np.ndarray, S: np.ndarray,
                  present: np.ndarray, targets: dict) -> np.ndarray:
    """코호트 × 업종 × 일 사람 합."""
    mult = P.mult_table()
    pk = people["persona"].map({k: i for i, k in enumerate(P.PERSONA_IDS)}).to_numpy()
    ok = cohort_of >= 0
    C, T = len(COHORTS), S.shape[1]
    A = sparse.csr_matrix((np.ones(ok.sum()), (cohort_of[ok], np.where(ok)[0])), shape=(C, len(people)))
    share = _cat_share(targets)
    out = np.zeros((C, K, T))
    for k in range(K):
        w = hetero[:, None] * mult[pk[:, None], S, k] * present
        out[:, k, :] = (A @ w) * share[:, k][:, None] * P.RATE_PER_DAY
    return out


def calendar_log(cal: Calendar, targets: dict) -> np.ndarray:
    """코호트 × 일: 요일 + 월(달 가운데를 잇는 선형 보간) + 연휴·날씨 효과 (targets 의 회귀값)."""
    T = len(cal.dates)
    months = sorted(set(cal.month))
    mid = [np.where(cal.month == m)[0].mean() for m in months]
    # 달 가운데 점을 잇는 선형 보간의 '달 평균'이 목표 월 효과와 같도록 매듭 값을 푼다 (계단 대신 매끈한 추세)
    basis = np.array([np.interp(np.arange(T), mid, np.eye(len(months))[j]) for j in range(len(months))])  # 매듭 × 일
    A = np.array([[basis[j][cal.month == m].mean() for j in range(len(months))] for m in months])
    out = np.zeros((len(COHORTS), T))
    for i, c in enumerate(COHORTS):
        t = targets["card"]["|".join(c)]
        e = t["effects"]
        out[i] = (np.asarray(t["dow"])[cal.dow] + np.linalg.solve(A, np.asarray(t["month"])) @ basis
                  + e["hol"] * cal.is_hol + e["block"] * (cal.off_block & ~cal.is_hol) + e["pre"] * cal.pre_hol
                  + e["heat"] * cal.heat[c[0]] + e["cold"] * cal.cold[c[0]] + e["rain"] * cal.rain[c[0]])
    return out


# ---------------------------------------------------------------- 잡음 (보정)


def _ar1(z: np.ndarray, sd: float, phi: float) -> np.ndarray:
    phi = float(np.clip(phi, -0.9, 0.95))
    x = np.empty_like(z)
    x[0] = z[0] * sd
    e = z * sd * np.sqrt(1 - phi**2)
    for i in range(1, len(z)):
        x[i] = phi * x[i - 1] + e[i]
    return x


def make_noise(zc: dict, zi: np.ndarray, prm: dict) -> np.ndarray:
    out = np.zeros_like(zi)
    for i, c in enumerate(COHORTS):
        out[i] = _ar1(zc[c[0]], prm["city_sd"][c[0]], prm["city_phi"][c[0]]) + _ar1(zi[i], prm["sd"][i], prm["phi"][i])
    return out


def calibrate_noise(w: World, n_iter: int = 6) -> dict:
    """합성 잡음 보정. 출발값 = 실제 회귀 잔차의 도시 공통·코호트 몫 크기와 자기상관(targets resid_*).
    그 뒤 경보 규칙이 보는 잔차(local MAD·lag-1, 도시 공통 변화 MAD·lag-1)가 targets 와 맞도록 곱셈·덧셈으로 고친다."""
    from bigcontest.sim.calibrate import rule_stats

    rng = np.random.default_rng(w.seed + 7)
    zc = {c: rng.standard_normal(w.T) for c in P.CITIES}
    zi = rng.standard_normal((len(COHORTS), w.T))
    tg = w.targets
    card = [tg["card"]["|".join(c)] for c in COHORTS]
    prm = {"city_sd": {c: tg["city_noise"][c]["resid_sd"] for c in P.CITIES},
           "city_phi": {c: tg["city_noise"][c]["resid_phi"] for c in P.CITIES},
           "sd": np.array([x["resid_sd"] for x in card]), "phi": np.array([x["resid_phi"] for x in card])}
    tgt_sd = np.array([x["local_mad"] for x in card])
    tgt_phi = np.array([x["local_lag1"] for x in card])
    for _ in range(n_iter):
        w.noise = make_noise(zc, zi, prm)
        rs = rule_stats(w.panel_frame(), w.cal.holidays)
        got_sd = np.array([rs["cohort"]["|".join(c)]["local_mad"] for c in COHORTS])
        got_phi = np.array([rs["cohort"]["|".join(c)]["local_lag1"] for c in COHORTS])
        prm["sd"] = prm["sd"] * np.clip(tgt_sd / got_sd, 0.5, 2.0)
        prm["phi"] = np.clip(prm["phi"] + (tgt_phi - got_phi), -0.5, 0.95)
        for c in P.CITIES:
            g, t = rs["city"][c], tg["city_noise"][c]
            prm["city_sd"][c] = float(prm["city_sd"][c] * np.clip(t["shock_mad"] / g["shock_mad"], 0.5, 2.0))
            prm["city_phi"][c] = float(np.clip(prm["city_phi"][c] + t["shock_lag1"] - g["shock_lag1"], -0.5, 0.95))
    w.noise = make_noise(zc, zi, prm)
    return {"city_sd": prm["city_sd"], "city_phi": prm["city_phi"], "sd": prm["sd"].tolist(), "phi": prm["phi"].tolist()}


# ---------------------------------------------------------------- 세계 만들기


def build_world(seed: int = P.SEED, scale: int = P.SCALE, mix: P.Mix = P.BASE_MIX, dong_risk=None,
                extra_holidays: dict | None = None, weather_override: dict | None = None,
                targets: dict | None = None, calibrate: bool = True, noise_params: dict | None = None) -> World:
    cal = load_calendar(extra_holidays=extra_holidays, weather_override=weather_override)
    targets = targets or load_targets()
    people, info = synthesize(seed, scale)
    people = assign_personas(people, cal, seed, mix, dong_risk)
    rng = np.random.default_rng(seed + 2)
    n, T = len(people), len(cal.dates)
    U = rng.random((n, T)).astype(np.float32)
    hetero = rng.gamma(P.HETERO_SHAPE, 1 / P.HETERO_SHAPE, n)
    people["hetero"] = hetero.round(4)
    S, present = simulate_states(people, cal, U)
    keys = list(zip(people["city"], people["band"], people["sex"]))
    cohort_of = np.array([CIDX.get(k, -1) for k in keys])
    lam0 = cohort_lambda(people, cohort_of, hetero, S, present, targets)
    cnt_u = np.random.default_rng(seed + 3).random((len(COHORTS), K, T))
    w = World(people, cal, targets, seed, U, S, present, hetero, cohort_of, cnt_u, calendar_log(cal, targets),
              np.zeros((len(COHORTS), T)), {}, lam0, info)
    if noise_params is not None:
        rng = np.random.default_rng(seed + 7)
        zc = {c: rng.standard_normal(T) for c in P.CITIES}
        zi = rng.standard_normal((len(COHORTS), T))
        prm = {**noise_params, "sd": np.asarray(noise_params["sd"]), "phi": np.asarray(noise_params["phi"])}
        w.noise = make_noise(zc, zi, prm)
        w.noise_params = noise_params
    elif calibrate:
        w.noise_params = calibrate_noise(w)
    return w


# ---------------------------------------------------------------- 정답 (라벨)


def burden(w: World, by: str = "cohort", t: int | None = None) -> pd.DataFrame:
    """단절(W) 상태 비율 = 합성 정답. by = cohort / adm_cd. t 가 없으면 날짜별 평균."""
    inw = (w.states == P.LABEL_STATE) & w.present
    if by == "cohort":
        ok = w.cohort_of >= 0
        g = pd.DataFrame(inw[ok], index=w.cohort_of[ok]).groupby(level=0).mean()
        g.index = pd.MultiIndex.from_tuples([COHORTS[i] for i in g.index], names=["city", "age_g", "SEX_CCD"])
    else:
        g = pd.DataFrame(inw, index=w.people["adm_cd"].to_numpy()).groupby(level=0).mean()
    g.columns = w.cal.dates
    return g if t is None else g.iloc[:, t]
