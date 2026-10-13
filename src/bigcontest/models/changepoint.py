"""PELT(ruptures) 변화점 분석. 경보가 아니라 **사후 분석**으로만 쓴다.

- posthoc: 잔차 전체 구간에 적용 (사후). 결과는 "그 시점에 알 수 있었던 것"이 아님
- prefix: 시점 d에서 쓰려면 d까지의 구간에만 적용. 변화점 추정일과 발견일(d)을 따로 기록
패널티 = beta × σ² × ln(n), σ = 잔차 차분의 robust 표준편차 / √2
"""

import numpy as np
import ruptures as rpt

MAD_K = 1.4826
LABEL = "사후 변화점 분석 (경보 아님)"


def noise_sd(r: np.ndarray) -> float:
    d = np.diff(r[~np.isnan(r)])
    if len(d) < 3:
        return np.nan
    return float(np.median(np.abs(d - np.median(d))) * MAD_K / np.sqrt(2))


def pelt(r: np.ndarray, beta: float, min_size: int = 5, sd: float | None = None) -> list[int]:
    """NaN을 뺀 잔차에 PELT(l2). 반환: 변화점 위치(원래 배열 기준, 새 구간이 시작하는 자리)."""
    ok = np.where(~np.isnan(r))[0]
    x = r[ok]
    if len(x) < 2 * min_size:
        return []
    sd = noise_sd(r) if sd is None else sd
    if not sd or np.isnan(sd):
        return []
    pen = beta * sd**2 * np.log(len(x))
    bk = rpt.Pelt(model="l2", min_size=min_size, jump=1).fit(x).predict(pen=pen)
    return [int(ok[b]) for b in bk[:-1]]


def prefix_discovery(r: np.ndarray, s: int, last: int, beta: float, tol: int = 3, min_size: int = 5, sd: float | None = None):
    """d = s..last마다 r[:d+1]에만 PELT를 적용. s ± tol 안의 변화점이 처음 나온 d(발견일)와 그 추정 위치.
    반환: (발견일 위치 또는 None, 추정 변화점 위치 또는 None)."""
    for d in range(s, last + 1):
        cps = pelt(r[: d + 1], beta, min_size, sd)
        near = [c for c in cps if abs(c - s) <= tol]
        if near:
            return d, near[0]
    return None, None
