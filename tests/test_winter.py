"""rules-v2.0 W 읍·면별 겨울 체류 (src/bigcontest/winter.py). data/interim 이 없으면 건너뜀."""

import pandas as pd
import pytest

from bigcontest.config import ROOT

INTERIM = ROOT / "data" / "interim"
pytestmark = pytest.mark.skipif(not (INTERIM / "tel_age.parquet").exists(), reason="data/interim 없음 (원본 필요)")


@pytest.fixture(scope="module")
def tel():
    return tuple(pd.read_parquet(INTERIM / f"{n}.parquet") for n in ("tel_age", "tel_time", "tel_wkdy"))


def test_no_winter_month_returns_empty(tel):
    from bigcontest.winter import winter_by_area

    assert winter_by_area(*tel, ["202507", "202508", "202509"]).empty


def test_per_area_values_and_rule(tel):
    from bigcontest.winter import DECLINE, winter_by_area

    w = winter_by_area(*tel, ["202507", "202508", "202509", "202510", "202511"])
    assert len(w) == 10 and set(w["adm_type"]) <= {"읍", "면"}
    assert (w["w_ok"] == (w["w60"] < DECLINE)).all()
    # 읍·면마다 값이 다르다 (v1.1은 모든 읍·면이 같은 값 하나)
    assert w.loc[~w["shared"], "w60"].nunique() == (~w["shared"]).sum()
