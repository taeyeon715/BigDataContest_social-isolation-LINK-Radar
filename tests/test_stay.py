"""rules-v2.0 H축 (src/bigcontest/stay.py). 대회 원본 정리본(data/interim)이 없으면 건너뜀."""

import numpy as np
import pandas as pd
import pytest

from bigcontest.config import ROOT
from bigcontest.external import resident_population

INTERIM = ROOT / "data" / "interim"
pytestmark = pytest.mark.skipif(not (INTERIM / "tel_age.parquet").exists(), reason="data/interim 없음 (원본 필요)")


@pytest.fixture(scope="module")
def inputs():
    return (pd.read_parquet(INTERIM / "tel_age.parquet"), pd.read_parquet(INTERIM / "tel_time.parquet"),
            resident_population())


def test_reproduces_taeyeon_h_v2(inputs):
    """7~12월 전체 + 겨울 = 12월이면 태연 04 H_v2(reports/t1_2축위험도)와 같다 (이식 확인)."""
    from bigcontest.stay import h_axis

    h = h_axis(*inputs, months=[7, 8, 9, 10, 11, 12], winter=(12,))
    t1 = pd.read_csv(ROOT / "reports" / "t1_2축위험도" / "위험도_2축_강남_춘천_동x성x연령.csv", dtype={"SGIS코드": str})
    t1["sex"] = t1["성별"].map({"남성": "남", "여성": "여"})
    j = h.merge(t1, left_on=["adm_cd", "sex", "age5"], right_on=["SGIS코드", "sex", "연령5"])
    assert len(j) == 470
    assert np.allclose(j["h"], j["H_v2"], atol=1e-9)
    assert np.allclose(j["stay_adj"], j["보정 체류도"], atol=1e-9)


def test_before_winter_uses_stay_only(inputs):
    from bigcontest.stay import h_axis

    h = h_axis(*inputs, months=[7, 8, 9])
    assert (h["h_basis"] == "체류도만 (겨울 달 아직 없음)").all()
    assert h["winter_adj"].isna().all()
    obs = h[h["h_state"] == "observed"]
    per = obs.groupby(["city", "sex", "age5"]).agg(n=("h", "size"), top=("h_top", "sum"))
    assert (per["top"] <= np.ceil(per["n"] / 3)).all()


def test_uses_only_given_months(inputs):
    """기준월 뒤 통신을 바꿔도 앞 기준월의 H는 같다 (미래 자료를 쓰지 않음)."""
    from bigcontest.stay import h_axis

    ta, tt, pop = inputs
    a = h_axis(ta, tt, pop, months=[7, 8, 9])
    ta2 = ta.copy()
    later = ta2["STD_YM"].str[4:].astype(int) > 9
    num = ta2.select_dtypes("number").columns.difference(["X_COORD", "Y_COORD"])
    ta2.loc[later, num] = ta2.loc[later, num] * 3
    b = h_axis(ta2, tt, pop, months=[7, 8, 9])
    assert np.allclose(a["h"].fillna(-1), b["h"].fillna(-1))
