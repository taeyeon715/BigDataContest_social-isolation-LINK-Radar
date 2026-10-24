"""rules-v2.0 칸 단위와 S축 (src/bigcontest/cells.py). 공개 주민등록 자료만 써서 원본 없이 돈다."""

import numpy as np
import pandas as pd
import pytest

from bigcontest import cells as C
from bigcontest.external import resident_single_by_age, resident_population


@pytest.fixture(scope="module")
def data():
    return resident_population(), resident_single_by_age()


def test_cell_index_is_470():
    idx = C.cell_index()
    assert len(idx) == 470
    assert idx.groupby("city")["adm_cd"].nunique().to_dict() == {"강남": 22, "춘천": 25}


def test_age5_mapping():
    assert [C.age5(a) for a in (19, 20, 39, 59, 60, 100)] == [None, "20대", "30대", "50대", "60대+", "60대+"]


def test_one_person_loader_covers_months_and_bands(data):
    _, one = data
    assert sorted(one["month"].unique()) == [7, 8, 9, 10, 11, 12]
    assert one["adm_cd"].nunique() == 47
    assert set(one["sex"]) == {"남", "여"}
    assert one["n"].notna().all()


def test_one_person_not_above_population(data):
    pop, one = data
    for m in (7, 12):
        p = C.cell_population(pop, m).set_index(["adm_cd", "sex", "age5"])["pop"]
        o = C.cell_one_person(one, m).set_index(["adm_cd", "sex", "age5"])["one_person"]
        j = pd.concat([p, o], axis=1).dropna()
        assert (j["one_person"] <= j["pop"]).all()


def test_eb_shrinks_toward_city_rate():
    k = pd.Series([1, 50, 200])
    n = pd.Series([2, 400, 1000])
    al, be = C.eb_beta_prior(k, n)
    m = k.sum() / n.sum()
    post = (k + al) / (n + al + be)
    raw = k / n
    # 수축 값은 관측 비율과 도시 평균 사이에 있고, 작은 칸일수록 더 많이 당겨진다
    assert all(min(r, m) - 1e-12 <= p <= max(r, m) + 1e-12 for r, p in zip(raw, post))
    assert abs(post[0] - raw[0]) / abs(m - raw[0]) > abs(post[2] - raw[2]) / abs(m - raw[2])


def test_s_axis_top_share_and_floor(data):
    pop, one = data
    s = C.s_axis(pop, one, 9)
    assert len(s) == 470
    obs = s[s["s_state"] == "observed"]
    # 상위 1/3 이하만 섬, 그리고 섬은 도시 같은 성·연령 비율 이상
    per = obs.groupby(["city", "sex", "age5"]).agg(n=("s_eb", "size"), top=("s_top", "sum"))
    assert (per["top"] <= np.ceil(per["n"] / 3)).all()
    assert (obs.loc[obs["s_top"], "s_eb"] >= obs.loc[obs["s_top"], "s_city_rate"]).all()
    assert s["s_small"].dtype == bool
