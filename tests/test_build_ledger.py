"""원장 745건을 원본 12개에서 다시 만들면 지금 원장과 같아야 한다.

원본은 레포에 없다 (external/resources/raw/원장_원본/ 또는 BC_LEDGER_RAW). 없으면 건너뛴다.
"""

import pandas as pd
import pytest

from bigcontest.resources import build_ledger as B
from bigcontest.resources.ledger import SRC

needs_raw = pytest.mark.skipif(not B.available(), reason="원장 원본 12개가 없음 (external/resources/raw/원장_원본)")


def test_text_candidates():
    assert B.text_candidates("강남", "서울특별시 강남구 선릉로 706 (청담동)") == ["청담동"]
    assert B.text_candidates("강남", "서울특별시 강남구 테헤란로 310, 805호 (역삼동)") == ["역삼1동", "역삼2동"]
    assert B.text_candidates("춘천", "강원특별자치도 춘천시 신북읍 율문길 75") == ["신북읍"]
    assert B.text_candidates("춘천", "") == []


def test_manifest_lists_twelve_files():
    m = pd.read_csv(B.MANIFEST, dtype=str, encoding="utf-8-sig")
    assert len(m) == 12 and m["sha256"].str.fullmatch(r"[0-9a-f]{64}").all()


@needs_raw
def test_rows_from_sources_match_ledger():
    old = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    rows = B.rows_from_sources(B.raw_dir()).fillna("").astype(str)
    assert len(rows) == 745
    for c in ["rid", "name", "type", "subtype", "hub", "target_stage", "eligibility", "city", "address", "phone",
              "hours", "source_url", "verified_date", "collected_date", "src"]:
        assert rows[c].tolist() == old[c].tolist(), c


@needs_raw
def test_full_rebuild_identical():
    old = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    new = B.build()
    assert B.compare(new, old) == {}
    assert new.equals(old)


def test_original_rows_non_coordinate_fields_fixed():
    """원래 원장 745건: 좌표를 도로명주소로 바꿔도 좌표·행정동 밖의 칸은 그대로 (2026-10-13 원장과 같은 값)."""
    import hashlib

    d = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    assert list(d.columns) == B.COLS and len(d) == 745
    cols = [c for c in d.columns if c not in ("adm_cd", "adm_nm", "dong_method", "lat", "lon", "coord_source")]
    h = hashlib.sha256(d[cols].to_csv(index=False, lineterminator="\n").encode()).hexdigest()
    assert h == "6154ff0e82c17455c13602145a010ffd8bf0ae5dce52116176aba02cdc39c1a6"
