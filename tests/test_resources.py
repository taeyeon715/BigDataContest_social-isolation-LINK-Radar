"""자원 원장·매칭·수집 테스트. 원본 데이터(data/) 없이 돈다."""

import json

import pandas as pd
import pytest

from bigcontest.resources import collect, ledger
from bigcontest.resources.collect import Fetcher, RawPage, add_correction, load_corrections, run_collection
from bigcontest.resources.ledger import NEW_COLS, derive, validate
from bigcontest.resources.match import check_eligibility, find_resources, match_resources

GN = "11230650"  # 강남 역삼2동
ORIGIN = (37.50, 127.04)

BASE = {
    "rid": "", "name": "", "type": "관계형성·여가", "subtype": "노인교실", "hub": "False", "target_stage": "노년",
    "eligibility": "만 60세 이상", "city": "강남", "address": "서울특별시 강남구 어딘가", "adm_cd": GN, "adm_nm": "역삼2동",
    "dong_method": "좌표", "lat": "37.501", "lon": "127.041", "coord_source": "ArcGIS World Geocoder 번지점(키 없음·탐색용)",
    "phone": "", "hours": "", "source_url": "https://www.data.go.kr/data/15160109/fileData.do",
    "verified_date": "2026-03-18", "collected_date": "2026-10-13", "src": "mohw",
}


def make(*rows) -> pd.DataFrame:
    """작은 가짜 원장 → 상태 칸까지 만든 것."""
    src = pd.DataFrame([{**BASE, **r} for r in rows])
    return derive(src)


@pytest.fixture
def small():
    return make(
        {"rid": "R1", "name": "노인교실"},
        {"rid": "R2", "name": "어느 장애인시설", "type": "기타(아동·장애 등)", "subtype": "장애인 보호작업장",
         "target_stage": "해당 대상", "eligibility": "시설별"},
        {"rid": "R3", "name": "외로움 상담전화", "type": "상담/정신건강", "subtype": "전화·광역(비공간)",
         "target_stage": "전 생애", "eligibility": "누구나, 24시간", "address": "", "adm_cd": "", "adm_nm": "",
         "lat": "", "lon": "", "coord_source": "none", "phone": "☎109"},
        {"rid": "R4", "name": "좌표 없는 거점", "type": "관계형성·여가", "subtype": "노인복지관",
         "lat": "", "lon": "", "adm_cd": "", "coord_source": "none"},
    )


def test_unknown_eligibility_is_check_needed(small):
    assert small.set_index("rid").loc["R2", "eligibility_rule_status"] == "미상"
    assert check_eligibility(small, "R2", "고령층")["verdict"] == "확인 필요"
    m = {x.resource_id: x for x in match_resources(small, GN, "고령층")}
    assert m["R2"].decision == "확인 필요"
    assert check_eligibility(small, "R1", "청년 남성")["verdict"] == "해당 안 됨"


def test_phone_kept_with_coordinate_not_applicable(small):
    r3 = small.set_index("rid").loc["R3"]
    assert (r3["delivery_channel"], r3["coordinate_status"]) == ("전화", "해당 없음")
    found = find_resources(small, GN, "고령층", "정신건강 상담", origin=ORIGIN, max_km=1)
    assert [m.resource_id for m in found] == ["R3"]
    assert found[0].distance_km is None and "위치 확인 필요" not in found[0].flags
    # 거점 원거리형: 필요 유형이 달라도 전화형은 남고 맨 앞
    far = find_resources(small, GN, "고령층", "생필품", cause_type="거점 원거리형", origin=ORIGIN)
    assert far[0].resource_id == "R3"


def test_physical_without_coords_excluded_from_distance(small):
    r4 = small.set_index("rid").loc["R4"]
    assert (r4["delivery_channel"], r4["coordinate_status"]) == ("방문", "미확인")
    found = {m.resource_id: m for m in find_resources(small, GN, "고령층", "안부·관계", origin=ORIGIN, max_km=0.5)}
    assert "R4" in found and found["R4"].distance_km is None
    assert "위치 확인 필요" in found["R4"].flags
    assert found["R1"].distance_km is not None
    assert len(ledger.spatial(small)) == 2


def test_out_of_area_physical_never_recommended(small):
    d = small.copy()
    i = d.index[d["rid"] == "R4"][0]
    d.loc[i, "coordinate_status"] = "권역 외"  # 관할 밖 물리 거점 (좌표 없음)
    d.loc[i, "eligibility"] = "누구나"  # 자격은 해당됨이어도
    d.loc[i, "eligibility_rule_status"] = "확인됨"
    assert validate(d)["errors"] == []
    found = {m.resource_id: m for m in find_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN)}
    assert found["R4"].decision == "확인 필요" and found["R4"].distance_km is None
    assert any("권역 외" in x for x in found["R4"].reasons)
    # 실제 원장: 서울잇다플레이스(성동구)는 강남 동에서 추천되지 않음, 전화형은 그대로 추천
    real = {m.resource_id: m for m in find_resources(ledger.load(), "11230640", "중장년 남성 1인가구", "안부·관계")}
    assert real["R0740"].decision == "확인 필요"
    assert real["R0739"].decision == "추천"


def test_validate_catches_missing_source():
    d = make({"rid": "R1"}, {"rid": "R2", "source_url": ""}, {"rid": "R3", "verified_date": ""})
    errs = validate(d)["errors"]
    assert any("source_url" in e and "R2" in e for e in errs)
    assert any("verified_date" in e and "R3" in e for e in errs)
    assert validate(make({"rid": "R1"}))["errors"] == []


def test_validate_catches_bad_coordinate_status():
    d = make({"rid": "R1", "lat": "", "lon": "", "adm_cd": ""})
    d.loc[0, "coordinate_status"] = "해당 없음"  # 방문형인데 좌표 없음을 '해당 없음'으로 둔 경우
    assert validate(d)["errors"]


def test_real_ledger_v2():
    full = ledger.load()
    v = validate(full)
    assert v["errors"] == []
    src = ledger.read_source()
    d = full.iloc[: len(src)].reset_index(drop=True)  # 원래 원장 745건은 맨 앞, 보강 자원은 뒤
    assert d[src.columns].equals(src)  # 기존 칸·값·행 순서 그대로
    assert len(d) == 745 and len(ledger.spatial(d)) == 738 and (d["lat"] == "").sum() == 7
    sup = ledger.read_supplement()
    assert len(full) == len(src) + len(sup) and not full["resource_id"].duplicated().any()
    assert set(sup["resource_kind"]) <= {"시설", "제도·사업", "사람"}
    nc = d[d["lat"] == ""].set_index("resource_id")
    assert (nc["coordinate_status"] == "해당 없음").sum() == 5
    assert set(nc.index[nc["coordinate_status"] == "미확인"]) == {"R0267"}
    assert nc.loc["R0740", "coordinate_status"] == "권역 외" and nc.loc["R0740", "delivery_channel"] == "방문"
    assert set(NEW_COLS) <= set(d.columns)


# ------------------------------------------------------------ 수집
class FakeFetcher(Fetcher):
    source = "fake"

    def __init__(self, pages, fail_times=0):
        self.pages, self.fail_times, self.calls = pages, fail_times, 0

    def fetch(self, page):
        self.calls += 1
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("가짜 오류")
        recs = self.pages[page - 1]
        return RawPage(body=json.dumps(recs, ensure_ascii=False).encode(), last=page == len(self.pages))

    def parse(self, raw):
        return json.loads(raw.body.decode())


@pytest.fixture
def cfg(tmp_path):
    return {"max_calls": 10, "max_retries": 1, "retry_wait_sec": 0, "raw_dir": tmp_path / "raw",
            "run_log": tmp_path / "수집_기록.csv", "corrections": tmp_path / "정정_기록.csv"}


@pytest.fixture
def led():
    d = make({"rid": "R1", "name": "가", "phone": "1", "src": "fake"}, {"rid": "R2", "name": "나", "phone": "2", "src": "fake"})
    d["source_record_id"] = ["S1", "S2"]
    return d


def test_collect_failure_keeps_previous(led, cfg):
    f = FakeFetcher([[{"source_record_id": "S1", "phone": "999"}]], fail_times=5)
    res = run_collection(led, f, ["phone"], cfg=cfg)
    assert res.run["status"] == "실패" and res.run["fail"] == 2 and res.run["calls"] == 2
    assert res.ledger.equals(led)  # 이전 값·last_verified 그대로
    log = pd.read_csv(cfg["run_log"], dtype=str)
    assert log.iloc[0]["status"] == "실패"


def test_collect_retry_then_success_and_vanished(led, cfg):
    from datetime import datetime

    f = FakeFetcher([[{"source_record_id": "S1", "phone": "999"}], [{"source_record_id": "S9", "phone": "7"}]], fail_times=1)
    res = run_collection(led, f, ["phone"], cfg=cfg, now=datetime(2026, 11, 1))
    assert res.run["status"] == "성공" and res.run["calls"] == 3
    out = res.ledger.set_index("resource_id")
    assert out.loc["R1", "phone"] == "999" and out.loc["R1", "last_verified"] == "2026-11-01"
    assert out.loc["R2", "operating_status"] == "확인 필요"  # 종료가 아님
    assert out.loc["R2", "last_verified"] == "2026-03-18"
    assert res.vanished == ["R2"] and list(res.new_records["source_record_id"]) == ["S9"]
    assert len(list((cfg["raw_dir"] / "fake").glob("*/page*.json"))) == 2  # 원문 보관


def test_collect_budget_stops_without_update(led, cfg):
    cfg["max_calls"] = 1
    f = FakeFetcher([[{"source_record_id": "S1"}], [{"source_record_id": "S2"}]])
    res = run_collection(led, f, ["phone"], cfg=cfg)
    assert res.run["status"] == "한도 초과"
    assert res.ledger.equals(led)


def test_correction_appends_and_is_protected(led, cfg):
    p = cfg["corrections"]
    add_correction(p, "R1", "phone", "1", "02-000", "전화 확인", "담당자A", "2026-10-20")
    add_correction(p, "R1", "phone", "02-000", "02-111", "다시 확인", "담당자B", "2026-10-21")
    corr = load_corrections(p)
    assert len(corr) == 2 and list(corr["old_value"]) == ["1", "02-000"]  # 덮어쓰지 않고 쌓임
    fixed = collect.apply_corrections(led, corr)
    assert fixed.set_index("resource_id").loc["R1", "phone"] == "02-111"
    with pytest.raises(ValueError):
        add_correction(p, "R1", "phone", "a", "b", "", "누군가")
    # 수집해도 정정한 칸은 바꾸지 않음
    f = FakeFetcher([[{"source_record_id": "S1", "phone": "999"}, {"source_record_id": "S2", "phone": "888"}]])
    res = run_collection(fixed, f, ["phone"], cfg=cfg, corrections=corr)
    out = res.ledger.set_index("resource_id")
    assert out.loc["R1", "phone"] == "02-111" and out.loc["R2", "phone"] == "888"
