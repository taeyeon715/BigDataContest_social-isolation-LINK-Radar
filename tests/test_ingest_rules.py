"""적재 규칙 테스트. 원본 없이 tmp_path 의 작은 가짜 자료로 돈다."""

import pandas as pd
import pytest

from bigcontest.ingest import MONTHS, IngestRuleError, MonthStore, check_new_months, drop_full_duplicates


def _month(ym: str, n: int = 4, dup: int = 0) -> pd.DataFrame:
    df = pd.DataFrame(
        {"STD_YM": [ym] * n, "BLOCK_CD": [f"1123051{i:03d}" for i in range(n)], "TMST_00": [float(i + int(ym[4:])) for i in range(n)]}
    )
    return pd.concat([df, df.head(dup)], ignore_index=True)


def _monthly_total(store: MonthStore, months: list[str]) -> pd.Series:
    return store.read("tel_time", months).groupby("STD_YM")["TMST_00"].sum()


def test_month_sequence_rules():
    assert check_new_months([], MONTHS) == MONTHS
    assert check_new_months(["202507"], ["202508", "202509"]) == ["202507", "202508", "202509"]
    assert check_new_months(["202511"], ["202512", "202601"])[-1] == "202601"
    cases = [
        ([], ["202507", "202507"], "DUPLICATE_MONTH"),
        (["202507", "202508"], ["202508"], "MONTH_ALREADY_INGESTED"),
        (["202507"], ["202509"], "MONTH_GAP"),
        ([], ["202507", "202509"], "MONTH_GAP"),
        ([], ["2025-07"], "BAD_MONTH"),
    ]
    for existing, incoming, code in cases:
        with pytest.raises(IngestRuleError) as e:
            check_new_months(existing, incoming)
        assert e.value.code == code


def test_known_full_duplicates_are_cleaning_rule():
    # 12월 파일처럼 행이 통째로 두 번 → 정리 규칙으로 제거 (재적재 거절과 다른 규칙)
    df, n = drop_full_duplicates(_month("202512", n=4, dup=4))
    assert n == 4 and len(df) == 4


def test_store_rejects_duplicate_and_gap_without_writing(tmp_path):
    store = MonthStore(tmp_path)
    store.append("tel_time", {"202507": _month("202507"), "202508": _month("202508")})
    before = {p.name: p.read_bytes() for p in (tmp_path / "tel_time").iterdir()}

    with pytest.raises(IngestRuleError) as e:
        store.append("tel_time", {"202508": _month("202508")})
    assert e.value.code == "MONTH_ALREADY_INGESTED"
    with pytest.raises(IngestRuleError) as e:
        store.append("tel_time", {"202510": _month("202510")})
    assert e.value.code == "MONTH_GAP"

    after = {p.name: p.read_bytes() for p in (tmp_path / "tel_time").iterdir()}
    assert before == after  # 거절된 적재는 아무것도 안 씀


def test_new_month_keeps_past_results(tmp_path):
    store = MonthStore(tmp_path)
    store.append("tel_time", {"202507": _month("202507"), "202508": _month("202508")})
    past = _monthly_total(store, ["202507", "202508"])
    hashes = {ym: v["sha256"] for ym, v in store.months("tel_time").items()}

    store.append("tel_time", {"202509": _month("202509", dup=2)})
    assert list(store.months("tel_time")) == ["202507", "202508", "202509"]
    assert store.months("tel_time")["202509"]["dropped_full_duplicates"] == 2
    pd.testing.assert_series_equal(_monthly_total(store, ["202507", "202508"]), past)
    for ym, h in hashes.items():
        assert store.months("tel_time")[ym]["sha256"] == h


def test_same_ingest_twice_gives_same_result(tmp_path):
    frames = {ym: _month(ym, dup=1) for ym in MONTHS[:3]}
    a, b = MonthStore(tmp_path / "a"), MonthStore(tmp_path / "b")
    a.append("tel_time", frames)
    b.append("tel_time", frames)
    assert a.months("tel_time") == b.months("tel_time")
    pd.testing.assert_frame_equal(a.read("tel_time"), b.read("tel_time"))
