"""외부 자료 출처 기록이 분석 표와 맞는지 (공휴일·ASOS 원문 다시 만들기)."""

import os
from pathlib import Path

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


# 제출 묶음 02_외부데이터 폴더 (레포 밖). 환경 변수 BC_EXTERNAL_ORIG 로 알려 주면 추출본 재현을 검사한다.
_ORIG = os.environ.get("BC_EXTERNAL_ORIG", "")


@pytest.mark.skipif(not _ORIG or not Path(_ORIG).exists(), reason="외부 원본 묶음이 없음 (BC_EXTERNAL_ORIG)")
def test_extracts_reproduce_from_originals():
    from bigcontest import extract_external as X
    res = Path(_ORIG) / "E01_E02_주민등록" / "원본"
    for k, p in X.RESIDENT.items():
        assert X.same_as(X.resident(res, k), p, dtype={"행정기관코드": str}), p.name
    sl = Path(_ORIG) / "E03_E04_E05_서울시민생활" / "원본"
    for k, p in X.SEOUL.items():
        assert X.same_as(X.seoul_living(sl, k), p), p.name
