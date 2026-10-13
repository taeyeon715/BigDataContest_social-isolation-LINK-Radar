import pandas as pd
import pytest

from bigcontest.categories import EXCLUDED, categorize
from bigcontest.config import load_config
from bigcontest.ingest import _adm_type

INTERIM = load_config()["interim_dir"]


def test_adm_type():
    assert _adm_type("강남", "11230510") == "동"
    assert _adm_type("춘천", "32010110") == "읍"
    assert _adm_type("춘천", "32010390") == "면"
    assert _adm_type("춘천", "32010510") == "동"


def test_categorize():
    assert categorize("편의점") == "생필품"
    assert categorize("쇼핑몰") == EXCLUDED
    assert categorize("주유소") == "기타소매서비스"


@pytest.mark.skipif(not (INTERIM / "tel_time.parquet").exists(), reason="적재 먼저 실행")
def test_telecom_no_duplicates():
    for kind in ["age", "time", "wkdy"]:
        df = pd.read_parquet(INTERIM / f"tel_{kind}.parquet")
        assert not df.duplicated(["STD_YM", "BLOCK_CD", "X_COORD", "Y_COORD"]).any()
        assert set(df["city"]) == {"강남", "춘천"}


@pytest.mark.skipif(not (INTERIM / "card2.parquet").exists(), reason="적재 먼저 실행")
def test_card_files_not_double_counted():
    # 데이터2는 데이터1 개인 결제를 거주지별로 다시 나눈 것 → 금액 합이 거의 같아야 함
    c1 = pd.read_parquet(INTERIM / "card1.parquet", columns=["TS_AT", "corp"])
    c2 = pd.read_parquet(INTERIM / "card2.parquet", columns=["TS_AT"])
    ratio = c2["TS_AT"].sum() / c1.loc[~c1["corp"], "TS_AT"].sum()
    assert abs(ratio - 1) < 0.01


def test_fmt_p():
    from bigcontest.stats import fmt_p

    assert fmt_p(0.0002) == "p<0.001"
    assert fmt_p(0.0412) == "p=0.041"


def test_spearman_report_perfect():
    from bigcontest.stats import spearman_report

    x = pd.Series(range(10), dtype=float)
    rep = spearman_report(x, x * 2, n_perm=200, n_boot=200)
    assert rep["rho"] == pytest.approx(1.0)
    assert rep["p_perm"] < 0.05


def test_daily_fills_missing_days_with_zero():
    from bigcontest.card import daily

    d = pd.DataFrame({"date": pd.to_datetime(["2025-07-01", "2025-07-03"]), "city": ["강남", "강남"], "USE_CNT": [5, 6]})
    out = daily(d, ["city"])
    assert len(out) == 184
    assert out.loc[out["date"] == "2025-07-02", "USE_CNT"].item() == 0


def test_weekday_weights_sum_to_days():
    from bigcontest.telecom import weekday_weights

    assert weekday_weights("202510").sum() == 31
    assert weekday_weights("202502").sum() == 28
