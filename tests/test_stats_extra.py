"""stats.py 보완 함수: 순열 p값 보정, 군집 부트스트랩 비율 CI."""

import numpy as np
import pytest

from bigcontest.stats import cluster_rate_ci, fmt_perm_p, fmt_rate_ci, perm_p


def test_perm_p_never_zero():
    assert perm_p(0, 60) == pytest.approx(1 / 61)
    assert perm_p(60, 60) == 1.0
    assert perm_p(3, 999) == pytest.approx(4 / 1000)


def test_perm_p_bad_input():
    with pytest.raises(ValueError):
        perm_p(5, 4)
    with pytest.raises(ValueError):
        perm_p(0, 0)


def test_fmt_perm_p():
    assert fmt_perm_p(0, 60, "이동") == "p≤0.016 (이동 60회 중 0회)"
    assert fmt_perm_p(9, 999, "무작위") == "p=0.010 (무작위 999회 중 9회)"


def test_cluster_rate_ci_point_and_bounds():
    s = np.array([27, 30, 20, 29, 10, 28])
    t = np.array([30, 30, 30, 30, 30, 30])
    rate, lo, hi = cluster_rate_ci(s, t, n_boot=2000, seed=1)
    assert rate == pytest.approx(s.sum() / t.sum())
    assert lo <= rate <= hi
    # 군집 사이 차이가 크면 CI가 시도 단위 이항 CI보다 넓어야 함
    binom_half = 1.96 * np.sqrt(rate * (1 - rate) / t.sum())
    assert (hi - lo) / 2 > binom_half


def test_cluster_rate_ci_deterministic_and_degenerate():
    a = cluster_rate_ci([1, 2], [2, 2], n_boot=500, seed=3)
    b = cluster_rate_ci([1, 2], [2, 2], n_boot=500, seed=3)
    assert a == b
    assert np.isnan(cluster_rate_ci([0, 0], [0, 0])[0])
    with pytest.raises(ValueError):
        cluster_rate_ci([1, 2], [2])


def test_fmt_rate_ci():
    assert fmt_rate_ci(0.9009, 0.805, 0.978) == "90% (CI 80~98%)"
    assert fmt_rate_ci(np.nan, np.nan, np.nan) == "—"
