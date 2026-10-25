"""T-1.5 집단별 S1 점검 (공개 주민등록 자료와 리포트 CSV 만 읽음, 대회 원본 없이 돎).

- 외부 자료: 성·연령별 1인세대 추출본이 47개 동 × 6개월이고 동 합계가 세대원수별 1인세대 수와 같음
- 집단별 S1 값이 공개 자료로 다시 계산해도 같음
- 비교표의 'v1.1' 칸이 R-2 공식 등급표와 같음 (공식 등급을 바꾸지 않음)
- 사전 등록이 결과보다 먼저 커밋됨, 핵심 숫자, 원천 값 열 없음
"""

import importlib.util
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "t1_5_집단별S1"
R2 = ROOT / "reports" / "r2_위험도결과"


def _load(name, path):
    """노트북은 맨 위에서 LINKRADAR_RULES=v1.1 을 정한다. 다른 시험에 새지 않게 불러온 뒤 되돌린다."""
    before = os.environ.get("LINKRADAR_RULES")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    finally:
        if before is None:
            os.environ.pop("LINKRADAR_RULES", None)
        else:
            os.environ["LINKRADAR_RULES"] = before
    return mod


nb = _load("t1_5_cohort_s1", ROOT / "notebooks" / "t1_5_cohort_s1.py")
cnr = _load("check_no_raw", ROOT / "scripts" / "check_no_raw.py")


def _csv(name, **kw):
    return pd.read_csv(OUT / name, encoding="utf-8-sig", **kw)


def test_outputs_present():
    for f in [*nb.FILES.values(), "README.md", "사전등록.md", "사전등록.json"]:
        assert (OUT / f).exists(), f


def test_single_extract_matches_household_totals():
    from bigcontest.external import resident_single_by_age

    a = pd.read_csv(ROOT / "external/resident/주민등록_성연령별1인세대_2025-07_12.csv", dtype={"행정기관코드": str})
    h = pd.read_csv(ROOT / "external/resident/주민등록_세대원수별세대수_2025-07_12.csv", dtype={"행정기관코드": str})
    assert len(a) == 282 and a["행정기관코드"].nunique() == 47
    m = a.merge(h[["행정기관코드", "기준연월", "1인세대"]], on=["행정기관코드", "기준연월"], how="outer", validate="1:1")
    assert (m["계"] == m["1인세대"]).all()
    assert (a["남자"] + a["여자"] == a["계"]).all()
    long = resident_single_by_age()
    assert long["n"].notna().all() and len(long) == 282 * 42
    assert long.groupby("month")["n"].sum().to_dict() == a.groupby(a["기준연월"].str[5:7].astype(int))["계"].sum().to_dict()


def test_cohort_s1_recomputes_from_public_data():
    from bigcontest.external import resident_population, resident_single_by_age

    single, pop = resident_single_by_age(), resident_population()
    t = _csv("집단별S1_동x집단x기준월.csv", dtype={"adm_cd": str})
    assert set(t["집단"]) == set(nb.COHORTS) and set(t["기준월"]) == {"2025-08", "2025-09", "2025-11"}
    assert len(t) == 47 * len(nb.COHORTS) * 3
    for (g, m), x in t.groupby(["집단", "기준월"]):
        s1 = nb.cohort_s1(single, pop, int(m[5:]), nb.COHORTS[g])["S1"].round(4)
        assert np.allclose(x.set_index("adm_cd")["집단별 S1"], s1.reindex(x["adm_cd"]).to_numpy()), (g, m)


def test_v11_columns_equal_official_r2():
    c = _csv("등급비교_동x집단x기준일.csv", dtype={"adm_cd": str}).fillna({"adm_cd": ""})
    r2 = pd.read_csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str}, encoding="utf-8-sig").fillna({"adm_cd": ""})
    keys = ["도시", "기준일", "집단", "adm_cd", "동"]
    j = c.merge(r2[keys + ["등급", "규칙 id"]], on=keys, how="outer", validate="1:1", indicator=True)
    assert len(j) == 566 and (j["_merge"] == "both").all()
    assert (j["등급 (v1.1)"] == j["등급"]).all() and (j["규칙 id (v1.1)"] == j["규칙 id"]).all()


def test_key_numbers():
    c = _csv("등급비교_동x집단x기준일.csv")
    assert int(c["등급 바뀜"].sum()) == 22
    # 청년 남성: 도시 단위 안내 그대로 (동 후보 없음) → 동 행 등급은 두 판 모두 후보 아님
    y = c[(c["집단"] == "청년 남성") & c["adm_cd"].notna()]
    assert (y["등급 (대안)"] == "후보 아님").all()
    sl = _csv("시민생활_상관.csv").set_index("집단")
    assert sl.loc["청년 남성", "ρ 지금 S1"] == pytest.approx(-0.413, abs=1e-3)  # R-1 2장 값과 같음
    assert sl.loc["청년 남성", "ρ 집단별 S1"] < 0
    q = _csv("Q4_판정.csv")
    assert not q["조건 A"].any() and not q["조건 B"].any()  # 사전 등록 판정: 추가하지 않음
    cr = _csv("독립기준_채점.csv").set_index(["지표", "기준", "도시"])["ρ"]
    assert cr[("지금 S1 (동 전체, T-2와 같음)", "춘천 장애인 비율(%) (2024.12)", "춘천")] == pytest.approx(0.478, abs=1e-3)
    assert not _csv("독립기준_채점.csv")["기준"].str.contains("혼자 사는").any()
    x = _csv("집단간_겹침.csv")
    s = x[(x["집단 1"] == "고령층") & (x["집단 2"] == "중장년 남성 1인가구") & (x["집합"] == "구조 상위")]
    assert (s["v1.1 Jaccard"] == 1.0).all() and (s["대안 Jaccard"] < 1.0).all()


def test_prereg_committed_before_results():
    def first_commit(path):
        r = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H", "--", path], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.split()[-1] if r.returncode == 0 and r.stdout.strip() else None

    pre, res = first_commit("reports/t1_5_집단별S1/사전등록.json"), first_commit("reports/t1_5_집단별S1/README.md")
    if pre is None or res is None:
        pytest.skip("git 기록 없음")
    assert pre != res
    assert subprocess.run(["git", "merge-base", "--is-ancestor", pre, res], cwd=ROOT).returncode == 0


def test_outputs_have_no_raw_columns():
    paths = sorted(OUT.glob("*.csv"))
    assert paths and cnr.check(paths) == []


def test_resident_single_parse_offline():
    from bigcontest import resident_single as rs

    if not all(rs.raw_path(m).exists() for m in rs.MONTHS):
        pytest.skip("원문 xlsx 없음 (external/resident/raw, git 추적 안 함)")
    assert rs.same_as(rs.build(), rs.CSV)


def test_single_cli_fails_on_mismatched_extract(monkeypatch):
    from bigcontest import resident_single as rs

    monkeypatch.setattr(rs, "build", lambda: pd.DataFrame())
    monkeypatch.setattr(rs, "same_as", lambda df, path: False)
    assert rs.main(["--offline"]) == 1
    monkeypatch.setattr(rs, "same_as", lambda df, path: True)
    assert rs.main(["--offline"]) == 0


def test_single_cli_explicit_write_accepts_new_extract(monkeypatch, tmp_path):
    from bigcontest import resident_single as rs

    target = tmp_path / "updated.csv"
    monkeypatch.setattr(rs, "CSV", target)
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    monkeypatch.setattr(rs, "build", lambda: pd.DataFrame({"sample": [1]}))
    monkeypatch.setattr(rs, "same_as", lambda df, path: False)
    assert rs.main(["--offline", "--write"]) == 0
    assert pd.read_csv(target)["sample"].tolist() == [1]
