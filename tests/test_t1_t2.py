"""T-1 태연 분석 이전 · T-2 교차 검증 점검 (리포트 CSV 와 공개 외부 자료만 읽음, 원본 데이터 없이 돎).

- T-1: 옮긴 결과 파일이 있고, reports/t1_2축위험도 복사본이 analysis/taeyeon_2axis/outputs 와 같고, 원천 값 열이 없음
- T-2: 결합 키가 빠짐없이 붙고, 핵심 숫자가 다시 계산해도 같음
"""

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
T1 = ROOT / "reports" / "t1_2축위험도"
T2 = ROOT / "reports" / "t2_교차검증"
SRC = ROOT / "analysis" / "taeyeon_2axis"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cnr = _load("check_no_raw", ROOT / "scripts" / "check_no_raw.py")
t2 = _load("t2_cross_validation", ROOT / "notebooks" / "t2_cross_validation.py")

T1_FILES = [
    "위험도_2축_강남_춘천_동x성x연령.csv", "검증결과_2축모델.csv", "검증프로토콜_사전등록.json", "모델설정_2축.json",
    "모델설정_동결.json", "복지자원_추천_유형x생애단계.csv", "우선대상_추천서비스.csv", "에이전트_브리핑_강남구.md",
    "에이전트_브리핑_춘천시.md", "위험도_강남_춘천_동x성x연령.csv", "시점신호_탐지성능.csv",
    "가설_전연령고립_사전등록.json", "가설검증_전연령고립.csv", "복지자원_공급공백_생애단계.csv",
]
T2_FILES = ["결합표_동x집단.csv", "순위상관.csv", "상위겹침.csv", "함께지목_불일치.csv", "동단위_지표.csv",
            "독립기준_채점.csv", "Q4_미포함집단.csv"]


def _csv(p):
    return pd.read_csv(p, encoding="utf-8-sig")


def _norm(p):
    return p.read_bytes().replace(b"\r\n", b"\n")


# ---------------------------------------------------------------- T-1
def test_t1_files_present_and_same_as_outputs():
    for f in T1_FILES:
        assert (T1 / f).exists(), f
        assert (SRC / "outputs" / f).exists(), f
        assert _norm(T1 / f) == _norm(SRC / "outputs" / f), f"복사본이 다름: {f} (sync_reports.py 실행)"
    assert (T1 / "README.md").exists() and (SRC / "README.md").exists()


def test_t1_main_result_shape():
    d = _csv(T1 / "위험도_2축_강남_춘천_동x성x연령.csv")
    assert len(d) == 470 and d["SGIS코드"].nunique() == 47
    assert d.groupby("지역")["SGIS코드"].nunique().to_dict() == {"서울 강남구": 22, "강원 춘천시": 25}
    assert d["H"].between(0, 1).all() and d["V"].between(0, 1).all()
    v = _csv(T1 / "검증결과_2축모델.csv")
    assert len(v) == 19 and (v["판정"] == "통과").sum() == 18
    h = _csv(T1 / "가설검증_전연령고립.csv")
    assert len(h) == 10 and h["H1 성립"].sum() == 6 and set(h["종합판정"]) == {"부분 지지"}


def test_migrated_csvs_have_no_raw_columns():
    paths = [p for p in cnr.tracked_csvs() if T1 in p.parents or T2 in p.parents or SRC in p.parents]
    assert any(T1 in p.parents for p in paths) and any(T2 in p.parents for p in paths)
    assert any((SRC / "external") in p.parents for p in paths)
    assert not any("csv" in p.relative_to(SRC).parts[:1] for p in paths if SRC in p.parents), "대회 원본 변환본 csv/ 가 목록에 있음"
    assert cnr.check(paths) == []


def test_raw_contest_files_are_ignored():
    import subprocess
    for rel in ["analysis/taeyeon_2axis/csv/SK_유동인구_성연령.csv", "analysis/taeyeon_2axis/원본데이터/x.zip"]:
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT)
        assert r.returncode == 0, rel


# ---------------------------------------------------------------- T-2
@pytest.fixture(scope="module")
def res():
    return t2.run(write=False)


def test_t2_join_keys_complete(res):
    j = res["join"]
    assert len(j) == 188 and (j["_merge"] == "both").all()
    assert j.groupby("도시")["SGIS코드"].nunique().to_dict() == {"강남": 22, "춘천": 25}
    assert set(j["집단"]) == set(t2.COHORT_MAP)
    assert not j[["H", "V", "S1 1인세대 비율", "등급 추석 D-7", "등급 추석 뒤", "등급 동절기"]].isna().any().any()
    assert (j["칸 수"] == 2).all()
    assert (j["2축 기준"] == t2.TY_BASIS).all()


def test_t2_outputs_present():
    for f in T2_FILES + ["README.md", "분석계획.md"]:
        assert (T2 / f).exists(), f


@pytest.mark.parametrize("key,file", [("rank", "순위상관.csv"), ("overlap", "상위겹침.csv"), ("criteria", "독립기준_채점.csv"),
                                      ("q4", "Q4_미포함집단.csv"), ("agree", "함께지목_불일치.csv")])
def test_t2_outputs_reproduce(res, key, file):
    import io
    buf = io.StringIO()
    res[key].to_csv(buf, index=False)
    now = pd.read_csv(io.StringIO(buf.getvalue()))
    saved = _csv(T2 / file)
    pd.testing.assert_frame_equal(now, saved, check_exact=False, rtol=1e-9, atol=1e-12, check_dtype=False)


def test_t2_key_numbers(res):
    ag = res["agree"]["판정"].value_counts().to_dict()
    assert ag == {"두 방법이 함께 지목": 45, "2축만": 27, "LINK-Radar만": 8}
    cr = res["criteria"].set_index(["방법", "지표", "기준", "도시"])["ρ"]
    assert cr[("LINK-Radar", "LINK-Radar S1", "춘천 인구 이동률(%) (2024.12)", "춘천")] == pytest.approx(0.192, abs=1e-3)
    assert cr[("LINK-Radar", "LINK-Radar S1", "춘천 장애인 비율(%) (2024.12)", "춘천")] == pytest.approx(0.478, abs=1e-3)
    assert cr[("2축", "2축 H(적용)", "춘천 인구 이동률(%) (2024.12)", "춘천")] == pytest.approx(0.505, abs=1e-3)
    # 태연 사전 등록 결과와 같은 값이 나와야 함 (H1b 0.714, H2 0.505, D1 춘천 0.488)
    assert cr[("2축(참고)", "2축 H(사전 등록)", "춘천 인구 이동률(%) (2024.12)", "춘천")] == pytest.approx(0.7138, abs=1e-3)
    assert cr[("2축", "2축 V", "춘천 장애인 비율(%) (2024.12)", "춘천")] == pytest.approx(0.5046, abs=1e-3)
    assert cr[("2축(참고)", "2축 H(사전 등록)", "|ρ(지표, log 동 인구)| < 0.3", "춘천")] == pytest.approx(0.4882, abs=1e-3)
    # 강남 혼자 사는 비율은 2축 자체 기준으로만 (LINK-Radar 채점 없음)
    c = res["criteria"]
    assert not ((c["방법"].str.startswith("LINK-Radar")) & (c["기준"].str.contains("혼자 사는"))).any()
    joint = res["agree"][res["agree"]["판정"] == "두 방법이 함께 지목"]
    assert set(joint.loc[joint["도시"] == "강남", "동"]) == {"역삼1동", "논현1동", "논현2동", "대치4동", "수서동", "일원1동", "삼성2동"}
    assert joint.loc[joint["도시"] == "춘천", "동"].nunique() == 11
