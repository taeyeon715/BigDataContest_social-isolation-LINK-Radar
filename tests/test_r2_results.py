"""R-2 위험도 결과표·W-2 자원 추천 평가·M3 사전 기준·재현 스크립트 점검 (리포트 CSV 만 읽음, 원본 데이터 없이 돎).

- R-2 요약표의 등급별 후보 수 = R-1 결과표 (같은 규칙 엔진으로 다시 낸 값이어야 함)
- 동 표·이탈 표를 다시 세면 요약표와 같음
- 거리 칸은 규칙이 쓰는 안부·관계 거점 거리 (W-1 동별 접근거리), EDA-4 거리 아님
- 인원 칸 작은 칸 규칙 (<10)
"""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
R1 = REP / "r1_등급규칙" / "결과표.csv"
R2 = REP / "r2_위험도결과"
LEVELS = ["Level 0", "Level 1", "Level 2", "Level 3", "판단 보류"]
KEY = ["도시", "기준일", "집단", "후보 종류"]

need_r2 = pytest.mark.skipif(not (R2 / "요약표.csv").exists(), reason="R-2 리포트 없음")


def _csv(p, **kw):
    return pd.read_csv(p, encoding="utf-8-sig", **kw)


@need_r2
def test_r2_level_counts_equal_r1():
    r1 = _csv(R1).set_index(KEY)[["후보 수", *LEVELS]].sort_index()
    r2 = _csv(R2 / "요약표.csv").set_index(KEY)[["후보 수", *LEVELS]].sort_index()
    assert list(r1.index) == list(r2.index)
    pd.testing.assert_frame_equal(r1, r2, check_dtype=False)


@need_r2
def test_r2_tables_recount_to_summary():
    sm = _csv(R2 / "요약표.csv")
    tab = _csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str})
    dev = _csv(R2 / "이탈_후보.csv")
    sched = tab[tab["등급"] != "후보 아님"].groupby(["도시", "기준일", "집단", "등급"]).size()
    devc = dev.groupby(["도시", "기준일", "집단", "등급"]).size()
    for kind, got in (("예정된 점검", sched), ("예상 밖 이탈", devc)):
        s = sm[sm["후보 종류"] == kind].set_index(["도시", "기준일", "집단"])
        for (city, d, g), row in s.iterrows():
            for lv in LEVELS:
                assert got.get((city, d, g, lv), 0) == row[lv], (kind, city, d, g, lv)


@need_r2
def test_r2_distance_is_rule_distance():
    tab = _csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str})
    acc = _csv(REP / "w1_자원원장" / "동별_접근거리.csv").set_index(["도시", "동"])["안부·관계"]
    t = tab[tab["adm_cd"].notna() & (tab["adm_cd"] != "")].drop_duplicates(["도시", "동"])
    for _, r in t.iterrows():
        assert r["A1 안부·관계 거점 거리(km)"] == pytest.approx(acc[(r["도시"], r["동"])], abs=0.006), r["동"]
    # 동내면: EDA-4 '가장 가까운 거점'으로는 5km 넘지만 안부·관계 거점은 5km 안 → 접근성 기준 안 넘음
    dn = t[t["동"] == "동내면"].iloc[0]
    assert dn["A1 안부·관계 거점 거리(km)"] < 5 and not dn["A1 접근성 기준 넘음"]


@need_r2
def test_r2_level3_has_both_axes():
    tab = _csv(R2 / "동별_등급표.csv", dtype={"adm_cd": str})
    l3 = tab[tab["등급"] == "Level 3"]
    assert len(l3) > 0
    assert l3["S1 구조 상위"].astype(bool).all()
    assert (l3["A1 안부·관계 거점 거리(km)"] >= 5).all()


@need_r2
def test_r2_small_cells_hidden():
    tab = _csv(R2 / "동별_등급표.csv", dtype=str, keep_default_na=False)
    for c in ("1인세대 수(전 연령)", "대상 집단 인구(성·연령)"):
        for v in tab[c]:
            if v in ("", "<10"):
                continue
            assert float(v) >= 10, (c, v)


W2 = REP / "w2_자원추천평가"


@pytest.mark.skipif(not (W2 / "쌍별_결과.csv").exists(), reason="W-2 리포트 없음")
def test_w2_recall_recomputes():
    cfg = yaml.safe_load((ROOT / "configs" / "resource_eval.yaml").read_text(encoding="utf-8"))
    lab = _csv(W2 / "정답세트.csv", dtype=str, keep_default_na=False)
    top = _csv(W2 / "추천_상위5.csv", dtype=str, keep_default_na=False)
    res = _csv(W2 / "쌍별_결과.csv")
    assert len(res) == len(cfg["pairs"]) == 30
    k = cfg["metrics"]["k"]
    for _, r in res.iterrows():
        key = (lab["도시"] == r["도시"]) & (lab["동"] == r["동"]) & (lab["집단"] == r["집단"]) & (lab["필요 유형"] == r["필요 유형"])
        gold = set(lab[key]["resource_id"]) - {""}
        tk = (top["도시"] == r["도시"]) & (top["동"] == r["동"]) & (top["집단"] == r["집단"]) & (top["필요 유형"] == r["필요 유형"])
        got = list(top[tk]["resource_id"])
        assert len(got) <= k
        assert r["맞은 수"] == len([x for x in got if x in gold])
        if gold:
            assert r[f"Recall@{k}"] == pytest.approx(r["맞은 수"] / min(k, len(gold)))


def test_m3_adoption_criteria_fixed():
    """채택 기준은 결과 전에 커밋한 값 그대로 (바꾸려면 version 을 올리고 리포트에 이유를 적음)."""
    cfg = yaml.safe_load((ROOT / "configs" / "models_m3.yaml").read_text(encoding="utf-8"))
    assert cfg["version"] == "m3-1"
    ad = cfg["m3"]["adoption"]
    assert (ad["margin_pp"], ad["fpr_tolerance_pp"], ad["ece_max"]) == (5, 1, 0.05)
    assert cfg["m3"]["label"]["kind"] == "synthetic_injection"
    assert cfg["m4"]["status"] == "exploratory"


def _reproduce():
    spec = importlib.util.spec_from_file_location("reproduce", ROOT / "scripts" / "reproduce.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reproduce"] = mod  # dataclass 가 모듈을 찾음
    spec.loader.exec_module(mod)
    return mod


def test_reproduce_plan_without_raw(monkeypatch):
    rp = _reproduce()
    monkeypatch.setattr(rp, "interim_ready", lambda: False)
    monkeypatch.setattr(rp, "raw_ready", lambda: False)
    args = rp.argparse.Namespace(only=None, ingest=False, relabel=False, no_raw=False)
    run = [s.key for s, why in rp.plan(args) if why is None]
    assert run == [s.key for s in rp.STEPS if s.needs == "none" and s.key != "w2_labels"]
    assert "r2" not in run and "m3" not in run and "m4" in run


def test_reproduce_hash_roundtrip(tmp_path):
    rp = _reproduce()
    rep = tmp_path / "reports" / "a"
    rep.mkdir(parents=True)
    (rep / "x.csv").write_bytes(b"a,b\r\n1,2\r\n")
    (tmp_path / "reports" / "재현_기록.csv").write_text("단계\n", encoding="utf-8")
    h = rp.result_hashes(tmp_path)
    assert list(h) == ["a/x.csv"]
    (rep / "x.csv").write_bytes(b"a,b\n1,2\n")  # 줄 끝만 다르면 같은 해시
    assert rp.result_hashes(tmp_path) == h
    f = tmp_path / "h.txt"
    rp.write_hashes(h, f)
    assert rp.read_hashes(f) == h
    (rep / "x.csv").write_bytes(b"a,b\n1,3\n")
    c = rp.compare_hashes(rp.result_hashes(tmp_path), h)
    assert c["다름"] == ["a/x.csv"]
