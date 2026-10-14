"""① 인구 합성: 주민등록 집계(공개) → IPF → 축소 표본 가상 사람.

입력 (모두 공개 집계)
- 주민등록 성·연령별 인구: 행정동 × 성 × 1세 연령 (2025-07 기준)
- 주민등록 세대원수별 세대수: 행정동 1인세대 수 (1인세대 1개 = 혼자 사는 사람 1명)
- 1인 생활 사전 확률(성 × 연령대, params.P_ALONE_SEED, 가정값)

IPF 칸: 행정동 × 성 × 연령대(10대~80대+) × 1인 여부. 맞추는 주변분포 두 개
- (동, 성, 연령대) 인구 = 주민등록 인구 (10세 이상)
- (동, 1인 여부) = 1인세대 수 / 나머지
→ 반복 비례 조정(raking). 맞춘 칸 값을 SCALE로 나눠 확률 반올림(시드 고정)으로 가상 사람 수를 정한다.

가상 사람 속성: 동·성·연령대(10년)·1인 여부·동 유형·페르소나·임의 ID. 이름·나이(1세)·주소·실제 ID 없음.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from bigcontest.config import ROOT
from bigcontest.sim import params as P

PUBLIC_FILES = {
    "resident_pop": "external/resident/주민등록_성연령별인구_2025-07_12.csv",
    "resident_hh": "external/resident/주민등록_세대원수별세대수_2025-07_12.csv",
    "adm_names": "ref/adm_cd_names.csv",
    "adm_code_map": "ref/adm_code_map.csv",
}
SEX_KO = {"남": "남성", "여": "여성"}


def age_band(age: np.ndarray) -> np.ndarray:
    a = np.asarray(age)
    idx = np.clip(a // 10 - 1, 0, len(P.AGE_BANDS) - 1)
    return np.array(P.AGE_BANDS, dtype=object)[idx]


def marginals(files: dict | None = None, month: int = 7) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """(동 × 성 × 연령대 인구, 동 1인세대 수, 동 정보)."""
    from bigcontest.external import resident_households, resident_population

    f = files or {k: ROOT / v for k, v in PUBLIC_FILES.items()}
    pop = resident_population(f["resident_pop"], f["adm_code_map"])
    pop = pop[(pop["month"] == month) & (pop["age"] >= 10)].copy()
    pop["band"] = age_band(pop["age"].to_numpy())
    pop["sex"] = pop["sex"].map(SEX_KO)
    m1 = pop.groupby(["adm_cd", "sex", "band"])["pop"].sum().unstack(["sex", "band"]).fillna(0.0)
    m1 = m1.reindex(columns=pd.MultiIndex.from_product([P.SEXES, P.AGE_BANDS]), fill_value=0.0)
    hh = resident_households(f["resident_hh"], f["adm_code_map"])
    hh1 = hh[hh["month"] == month].set_index("adm_cd")["1인세대"]
    names = pd.read_csv(f["adm_names"], dtype=str).set_index("adm_cd")
    keep = m1.index.intersection(hh1.index).intersection(names.index)
    return m1.loc[keep], hh1.loc[keep].astype(float), names.loc[keep]


def ipf(m1: pd.DataFrame, hh1: pd.Series, tol: float = 1e-10, max_iter: int = 500) -> tuple[np.ndarray, dict]:
    """반복 비례 조정. 반환: n[동, 칸(성×연령대), 1인여부(0=아님,1=혼자)], 수렴 정보."""
    pop = m1.to_numpy(float)  # D × K
    p = np.array([P.P_ALONE_SEED[s][b] for s, b in m1.columns])  # K
    n = np.stack([pop * (1 - p), pop * p], axis=2)  # D × K × 2 (seed)
    tgt_one = np.stack([pop.sum(1) - hh1.to_numpy(), hh1.to_numpy()], axis=1)  # D × 2
    if (tgt_one < 0).any():
        raise ValueError("1인세대 수가 10세 이상 인구보다 많은 동이 있음")
    err = np.inf
    it = 0
    for it in range(1, max_iter + 1):
        # 주변분포 1: (동, 칸)
        s = n.sum(2, keepdims=True)
        n = np.where(s > 0, n * (pop[:, :, None] / np.where(s > 0, s, 1)), 0)
        # 주변분포 2: (동, 1인 여부)
        s2 = n.sum(1, keepdims=True)
        n = np.where(s2 > 0, n * (tgt_one[:, None, :] / np.where(s2 > 0, s2, 1)), 0)
        err = max(np.abs(n.sum(2) - pop).max(), np.abs(n.sum(1) - tgt_one).max())
        if err < tol:
            break
    info = {"iterations": it, "max_abs_error": float(err),
            "max_rel_error_cell": float(np.max(np.abs(n.sum(2) - pop) / np.maximum(pop, 1)))}
    return n, info


def synthesize(seed: int = P.SEED, scale: int = P.SCALE, files: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """가상 사람 표 (한 행 = 가상 1명 = 실제 약 scale명). 두 번째 값: IPF·표본 정보 (검증용 집계)."""
    m1, hh1, names = marginals(files)
    n, info = ipf(m1, hh1)
    rng = np.random.default_rng(seed)
    exp = n / scale
    cnt = np.floor(exp).astype(int) + (rng.random(exp.shape) < (exp - np.floor(exp)))
    D, K, _ = n.shape
    d_idx, k_idx, o_idx = np.nonzero(cnt)
    reps = cnt[d_idx, k_idx, o_idx]
    d_idx, k_idx, o_idx = (np.repeat(x, reps) for x in (d_idx, k_idx, o_idx))
    cols = list(m1.columns)
    adm = m1.index.to_numpy()[d_idx]
    people = pd.DataFrame({
        "adm_cd": adm,
        "city": names["city"].to_numpy()[d_idx],
        "adm_type": names["adm_type"].to_numpy()[d_idx],
        "sex": [cols[k][0] for k in k_idx],
        "band": [cols[k][1] for k in k_idx],
        "alone": o_idx.astype(bool),
    })
    perm = rng.permutation(len(people))  # 행 순서와 ID가 원래 칸 순서를 드러내지 않게
    people = people.iloc[perm].reset_index(drop=True)
    people.insert(0, "person_id", [f"SIM-{i:06d}" for i in range(len(people))])
    # 검증용 집계: 실제 칸 크기 (안전성 점검: 가상 사람이 속한 칸의 실제 인구)
    cell = pd.DataFrame({"adm_cd": np.repeat(m1.index.to_numpy(), K * 2),
                         "sex": np.tile(np.repeat([c[0] for c in cols], 2), D),
                         "band": np.tile(np.repeat([c[1] for c in cols], 2), D),
                         "alone": np.tile([False, True], D * K),
                         "ipf_n": n.reshape(-1), "sim_n": cnt.reshape(-1)})
    info.update({"scale": scale, "seed": seed, "n_people": int(len(people)), "n_adm": int(D),
                 "pop_10plus": float(m1.to_numpy().sum()), "one_person_hh": float(hh1.sum())})
    return people, {"ipf": info, "cells": cell, "m1": m1, "hh1": hh1, "names": names}


def assign_personas(people: pd.DataFrame, cal, seed: int = P.SEED, mix: P.Mix = P.BASE_MIX,
                    dong_risk: pd.Series | None = None) -> pd.DataFrame:
    """배경 세계 페르소나 배정과 계기 날짜(onset).
    dong_risk: 동 → 배수 (정답 순위 평가에서 동마다 고립 부담을 다르게 줄 때). 없으면 1."""
    rng = np.random.default_rng(seed + 1)
    n = len(people)
    band = people["band"].to_numpy()
    sex = people["sex"].to_numpy()
    alone = people["alone"].to_numpy()
    rural = people["adm_type"].isin(["읍", "면"]).to_numpy()
    mid = np.isin(band, ["40대", "50대", "60대"])
    old60 = np.isin(band, ["60대", "70대", "80대+"])
    old70 = np.isin(band, ["70대", "80대+"])
    young = np.isin(band, ["20대", "30대"])
    adult = np.isin(band, ["20대", "30대", "40대", "50대"])
    male = sex == "남성"
    prob = np.zeros((n, 7))
    prob[:, 0] = np.where(alone & mid & male, mix.s1_alone_mid_male, np.where(alone & adult, mix.s1_alone_other, 0))
    prob[:, 1] = np.where(alone & old70, mix.s2_alone_old, 0)
    prob[:, 2] = np.where(old60, np.where(rural, mix.s3_old_rural, mix.s3_old_urban), 0)
    prob[:, 3] = np.where(old60, mix.s4_old, 0)
    prob[:, 5] = np.where(young & male & alone, mix.s6_young_male_alone, np.where(young, mix.s6_young_other, 0))
    prob[:, 6] = np.where(alone & adult, mix.s7_alone_adult, 0)
    if dong_risk is not None:
        r = people["adm_cd"].map(dong_risk).fillna(1.0).to_numpy()
        prob[:, [0, 1, 2, 3]] *= r[:, None]
    tot = prob.sum(1, keepdims=True)
    prob = np.where(tot > 0.95, prob * 0.95 / np.maximum(tot, 1e-12), prob)
    prob[:, 4] = 1 - prob.sum(1)
    u = rng.random(n)
    k = (u[:, None] > np.cumsum(prob, 1)).sum(1).clip(0, 6)
    out = people.copy()
    out["persona"] = np.array(P.PERSONA_IDS)[k]
    T = len(cal.dates)
    out["onset"] = rng.integers(28, T - 14, n)  # 계기 날 (S1 시작, S4 사건, S7 전입). 학습 기간 뒤
    return out
