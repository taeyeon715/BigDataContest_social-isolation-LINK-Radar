"""행정동 코드 결합 검사. external/·ref/ 파일만 쓰므로 원본 없이 돈다."""

import pandas as pd
import pytest

from bigcontest.config import ROOT
from bigcontest.external import code_map, join_check, map_codes, seoul_living, seoul_living_quality


def test_map_table_columns_and_unique_keys():
    m = pd.read_csv(ROOT / "ref" / "adm_code_map.csv", dtype=str)
    assert list(m.columns) == ["source_system", "source_code", "target_adm_cd", "valid_from", "valid_to", "change_type", "evidence"]
    assert not m.duplicated(["source_system", "source_code"]).any()
    assert set(m["source_system"]) == {"mois_adm_cd2", "seoul_living_adm7"}


def test_regression_sinsa_and_gaepo3():
    m = code_map("seoul_living_adm7").set_index("source_code")
    assert m.loc["1123051", "target_adm_cd"] == "11230510"  # 신사동
    assert m.loc["1123074", "target_adm_cd"] == "11230740"  # 개포3동 (원본 이름: 일원2동)
    assert m.loc["1123074", "change_type"] == "명칭변경"
    mo = code_map("mois_adm_cd2").set_index("source_code")
    assert mo.loc["1168051000", "target_adm_cd"] == "11230510"


def test_seoul_living_join_is_bijection_and_keeps_population():
    raw = pd.read_csv(ROOT / "external" / "seoul_living" / "시민생활_10개관심집단_강남_2025-07_12.csv", dtype=str)
    chk = join_check(raw["adm_cd7"], "seoul_living_adm7", pd.to_numeric(raw["총인구"]))
    assert chk["n_source_codes"] == 22
    assert chk["bijection"]
    assert chk["unmatched"] == [] and chk["one_to_many"] == [] and chk["map_key_duplicates"] == 0
    assert chk["pop_before"] == pytest.approx(chk["pop_after"])
    sl = seoul_living()
    assert sl["adm_cd"].nunique() == 22 and sl["adm_cd"].notna().all()
    assert sl["pop"].sum() == pytest.approx(chk["pop_before"])


def test_resident_join_is_bijection():
    raw = pd.read_csv(ROOT / "external" / "resident" / "주민등록_세대원수별세대수_2025-07_12.csv", dtype=str)
    chk = join_check(raw["행정기관코드"], "mois_adm_cd2", pd.to_numeric(raw["전체세대수"]))
    assert chk["n_source_codes"] == 47 and chk["bijection"]
    assert chk["pop_before"] == pytest.approx(chk["pop_after"])


def test_unmatched_code_stops_join():
    with pytest.raises(ValueError):
        map_codes(pd.Series(["1123051", "9999999"]), "seoul_living_adm7")


def test_gaepo3_quality_suspect_only_seven_indicators():
    q = seoul_living_quality()
    sus = q[q["품질 의심"]]
    assert set(sus["adm_cd7"]) == {"1123074"}
    assert len(sus) == 7
    assert "외출-커뮤니케이션이 모두 적은 집단(전체)" not in set(sus["지표"])
    sl = seoul_living()
    g = sl[sl["adm_cd"] == "11230740"]
    assert g["comm_low_single"].isna().all() and (g["comm_low_single_state"] == "quality_suspect").all()
    assert g["iso_all"].notna().all()  # 다른 지표는 22개 동 유지


def test_to_number_keeps_failures_as_na():
    from bigcontest.external import to_number

    v, why = to_number(pd.Series(["1,234", "*", None, "abc", "0", "＊", "-*"]))
    assert v.iloc[0] == 1234 and v.iloc[4] == 0
    assert v.iloc[[1, 2, 3, 5, 6]].isna().all()
    assert list(why.iloc[[1, 2, 3, 5, 6]]) == ["비공개(*)", "원본 결측", "숫자 변환 실패", "비공개(*)", "비공개(*)"]


def test_std_rate_warns_on_missing_strata():
    from bigcontest.external import std_rate

    df = pd.DataFrame({"adm_cd": ["a", "a", "b", "b"], "sex": ["남", "여", "남", "여"], "age": [60, 60, 60, 60],
                       "num": [1.0, 2.0, 3.0, None], "pop": [10.0, 10.0, 10.0, 10.0]})
    with pytest.warns(UserWarning):
        out = std_rate(df, "num", "pop")
    assert out.loc["a", "coverage"] == pytest.approx(1.0)
    assert out.loc["b", "coverage"] == pytest.approx(0.5)
