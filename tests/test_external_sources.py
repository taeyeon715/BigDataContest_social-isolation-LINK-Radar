"""외부 자료 출처 기록이 분석 표와 맞는지 (공휴일·ASOS 원문 다시 만들기)."""

import pandas as pd
import pytest

from bigcontest import holidays as H
from bigcontest import weather as W


def test_holidays_match_official_table():
    t = pd.read_csv(H.CSV, dtype=str)
    assert sorted(t["date"]) == H.project_holidays()
    assert t["source_url"].eq(H.URL).all()


@pytest.mark.skipif(not H.RAW.exists(), reason="월력요항 페이지 사본이 없음 (external/holidays/raw)")
def test_holidays_parse_saved_page():
    assert H.check(H.parse())["same"]


@pytest.mark.skipif(W.latest_raw() is None, reason="ASOS 원문이 없음 (external/weather/raw)")
def test_asos_offline_rebuild_same_as_csv():
    assert W.same_as_csv(W.parse_raw(W.latest_raw()))


def _supplement_raw_ok() -> bool:
    from bigcontest.resources import sources as S
    cfg = S.load_config()
    return all(S.last_success(cfg, f.source) is not None for f in S.fetchers(cfg, ""))


@pytest.mark.skipif(not _supplement_raw_ok(), reason="E-Gen·복지로 원문이 없음 (external/resources/raw)")
def test_supplement_offline_rebuild_same_as_csv():
    from bigcontest.resources import sources as S
    cfg = S.load_config()
    new = S.build_supplement(cfg, offline=True)
    old = pd.read_csv(cfg["supplement"], dtype=str, keep_default_na=False)
    assert S.diff_supplement(new, old)["same"]
