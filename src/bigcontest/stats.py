"""검증용 통계: 순위상관 + 순열 p값 + 부트스트랩 신뢰구간 + 하나씩 빼기."""

import numpy as np
import pandas as pd
from scipy import stats


def spearman_report(x: pd.Series, y: pd.Series, n_perm: int = 5000, n_boot: int = 2000, seed: int = 0) -> dict:
    df = pd.concat([x, y], axis=1).dropna()
    a, b = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
    rho = stats.spearmanr(a, b).statistic
    rng = np.random.default_rng(seed)
    perm = np.array([stats.spearmanr(a, rng.permutation(b)).statistic for _ in range(n_perm)])
    p = (np.sum(np.abs(perm) >= abs(rho)) + 1) / (n_perm + 1)
    idx = np.arange(len(a))
    boot = []
    for _ in range(n_boot):
        s = rng.choice(idx, len(idx), replace=True)
        if len(np.unique(a[s])) > 2 and len(np.unique(b[s])) > 2:
            boot.append(stats.spearmanr(a[s], b[s]).statistic)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    loo = [stats.spearmanr(np.delete(a, i), np.delete(b, i)).statistic for i in idx]
    return {"n": len(a), "rho": rho, "p_perm": p, "ci_lo": lo, "ci_hi": hi, "loo_min": min(loo), "loo_max": max(loo)}


def partial_spearman(x: pd.Series, y: pd.Series, z: pd.Series) -> float:
    """z를 통제한 x-y 순위 편상관."""
    df = pd.concat([x, y, z], axis=1).dropna().rank()
    r = df.corr().to_numpy()
    rxy, rxz, ryz = r[0, 1], r[0, 2], r[1, 2]
    return (rxy - rxz * ryz) / np.sqrt((1 - rxz**2) * (1 - ryz**2))


def fmt_p(p: float) -> str:
    return "p<0.001" if p < 0.001 else f"p={p:.3f}"


def fmt_report(d: dict) -> str:
    return (
        f"ρ = {d['rho']:.2f} (n={d['n']}, 순열 {fmt_p(d['p_perm'])}, 95% CI {d['ci_lo']:.2f}~{d['ci_hi']:.2f}, "
        f"하나씩 빼기 {d['loo_min']:.2f}~{d['loo_max']:.2f})"
    )


# ------------------------------------------------------------ 보완
def perm_p(n_extreme: int, n_perm: int) -> float:
    """순열·이동 검정 p값: (k+1)/(N+1). 귀무 분포에서 실제만큼 극단인 경우 k번, 시도 N번.

    실제 배치도 귀무 분포의 한 경우로 세므로 p는 0이 될 수 없다. 가장 작은 값은 1/(N+1).
    """
    if n_perm < 1 or n_extreme < 0 or n_extreme > n_perm:
        raise ValueError("0 ≤ n_extreme ≤ n_perm, n_perm ≥ 1 이어야 함")
    return (n_extreme + 1) / (n_perm + 1)


def fmt_perm_p(n_extreme: int, n_perm: int, what: str = "시도") -> str:
    """'p≤0.016 (이동 60회 중 0회)' 꼴. k=0이면 하한이라 '≤'로 적는다."""
    p = perm_p(n_extreme, n_perm)
    op = "≤" if n_extreme == 0 else "="
    return f"p{op}{p:.3f} ({what} {n_perm:,}회 중 {n_extreme:,}회)"


def cluster_rate_ci(success, total, n_boot: int = 5000, seed: int = 0, level: float = 0.95) -> tuple[float, float, float]:
    """군집(예: 코호트)별 성공 수·시도 수로 비율과 군집 부트스트랩 CI를 구한다.

    군집을 복원 추출하고 sum(성공)/sum(시도)를 다시 계산한다. 같은 군집 안의 시도(겹치는 시작일 등)는
    독립이 아니므로 시도 단위가 아니라 군집 단위로 재표집한다. 반환: (비율, 하한, 상한).
    """
    s = np.asarray(success, dtype=float)
    t = np.asarray(total, dtype=float)
    if s.shape != t.shape or s.ndim != 1 or len(s) == 0:
        raise ValueError("success·total은 같은 길이의 1차원 배열이어야 함")
    if t.sum() <= 0:
        return np.nan, np.nan, np.nan
    rate = s.sum() / t.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(n_boot, len(s)))
    num, den = s[idx].sum(axis=1), t[idx].sum(axis=1)
    bs = num[den > 0] / den[den > 0]
    a = (1 - level) / 2 * 100
    lo, hi = np.percentile(bs, [a, 100 - a])
    return float(rate), float(lo), float(hi)


def fmt_rate_ci(rate: float, lo: float, hi: float) -> str:
    """'90% (CI 81~98%)' 꼴. 겹치는 시도에서 소수점 자리는 거짓 정밀도라 0자리로 적는다."""
    if np.isnan(rate):
        return "—"
    return f"{rate:.0%} (CI {lo * 100:.0f}~{hi * 100:.0f}%)"
