"""도로명주소 좌표 (resources/juso.py): 좌표 변환, 검색어 만들기, 결과 고르기, 원장 반영 규칙."""

import json

import pandas as pd
import pytest

from bigcontest.resources import juso as J
from bigcontest.resources import ledger as L

# pyproj (EPSG:5179 → 4326) 로 구한 값
KNOWN = [
    (1019257.9546095666, 1985367.9889265331, 37.867918080, 127.718953310),  # 춘천남부노인복지관 출입구 (juso 응답)
    (958000.0, 1945000.0, 37.503313819, 127.024813947),
    (1000000.0, 2000000.0, 38.0, 127.5),  # 원점
    (1100000.0, 1700000.0, 35.290512166, 128.599749014),
]


@pytest.mark.parametrize("x,y,lat,lon", KNOWN)
def test_utmk_to_wgs84_matches_pyproj(x, y, lat, lon):
    la, lo = J.utmk_to_wgs84(x, y)
    assert J.haversine_m(la, lo, lat, lon) < 0.01


def test_parse_address():
    p = J.parse_address("서울특별시 강남구 도곡로 405 , 삼환아르누보2 2층 204호  (대치동)")
    assert (p["keyword"], p["rn"], p["mn"], p["sn"], p["ug"]) == ("서울특별시 강남구 도곡로 405", "도곡로", "405", "0", "0")
    assert J.parse_address("서울특별시 강남구 삼성로 51길 37")["keyword"] == "서울특별시 강남구 삼성로51길 37"
    p = J.parse_address("서울특별시 강남구 학동로 지하102, 논현역 212호 (논현동)")
    assert (p["keyword"], p["ug"]) == ("서울특별시 강남구 학동로 지하 102", "1")
    assert J.parse_address("강원특별자치도 춘천시 스무숲2길 16-3 (석사동)")["sn"] == "3"
    assert J.parse_address("강원특별자치도 춘천시 백령로 156-  (효자동,(효자동))")["keyword"] == "강원특별자치도 춘천시 백령로 156"
    p = J.parse_address("강원특별자치도 춘천시 서면 안보리 490")
    assert (p["rn"], p["dong"], p["mn"], p["sn"]) == ("", "안보리", "490", "0")
    assert J.parse_address("강원특별자치도 춘천시 후평동 동산아파트 102동 603")["keyword"] == "강원특별자치도 춘천시 후평동 동산아파트"


def _res(**kw):
    base = {"sggNm": "춘천시", "rn": "방송길", "admCd": "5111012100", "rnMgtSn": "511104454247", "udrtYn": "0",
            "buldMnnm": "86", "buldSlno": "0", "roadAddr": "강원특별자치도 춘천시 방송길 86 (온의동)", "bdMgtSn": "B1",
            "emdNm": "온의동", "liNm": "", "lnbrMnnm": "6", "lnbrSlno": "33", "mtYn": "0"}
    return base | kw


def test_choose_is_conservative():
    p = J.parse_address("강원특별자치도 춘천시 방송길 86 (온의동)")
    assert J.choose([_res()], "춘천", p)[0] == "일치"
    assert J.choose([_res(), _res(bdMgtSn="B2")], "춘천", p)[0] == "일치"  # 같은 건물 키 (출입구 하나)
    assert J.choose([_res(sggNm="강남구")], "춘천", p)[0] == "없음"  # 다른 시군구
    assert J.choose([_res(buldMnnm="88")], "춘천", p)[0] == "없음"  # 다른 건물번호
    q = J.parse_address("강원특별자치도 춘천시 온의동 6-33")
    assert J.choose([_res(), _res(rnMgtSn="X", buldMnnm="1")], "춘천", q)[0] == "여러 건"
    assert J.choose([_res(), _res(lnbrSlno="34", rnMgtSn="X")], "춘천", q)[0] == "일치"


def test_record_of_converts_and_flags():
    body = {"keyword": "강원특별자치도 춘천시 방송길 86", "city": "춘천",
            "search": {"results": {"common": {"errorCode": "0"}, "juso": [_res()]}},
            "coord": {"results": {"common": {"errorCode": "0"},
                                  "juso": [{"entX": str(KNOWN[0][0]), "entY": str(KNOWN[0][1])}]}}}
    r = J.record_of(json.loads(json.dumps(body)))
    assert r["status"] == "일치" and abs(float(r["lat"]) - KNOWN[0][2]) < 1e-6
    body["coord"]["results"]["juso"] = []
    assert J.record_of(body)["status"] == "좌표 없음"
    body["search"]["results"] = {"common": {"errorCode": "E0013"}, "juso": None}
    assert J.record_of(body)["status"] == "검색어 오류"


def test_new_coords_only_replaces_geocoder_rows():
    d = pd.DataFrame([
        {"rid": "A", "city": "춘천", "address": "강원특별자치도 춘천시 방송길 86", "lat": "37.1", "lon": "127.1",
         "coord_source": "ArcGIS World Geocoder 번지점(키 없음·탐색용)"},
        {"rid": "B", "city": "춘천", "address": "강원특별자치도 춘천시 방송길 86", "lat": "37.2", "lon": "127.2",
         "coord_source": "원천 제공 위경도"},
        {"rid": "C", "city": "춘천", "address": "강원특별자치도 춘천시 없는길 1", "lat": "37.3", "lon": "127.3",
         "coord_source": "ArcGIS World Geocoder 번지점(키 없음·탐색용)"},
        {"rid": "D", "city": "춘천", "address": "강원특별자치도 춘천시 방송길 86", "lat": "", "lon": "", "coord_source": "none"},
    ])
    t = pd.DataFrame([{"keyword": "강원특별자치도 춘천시 방송길 86", "city": "춘천", "status": "일치", "lat": "37.8679181",
                       "lon": "127.7189533"},
                      {"keyword": "강원특별자치도 춘천시 없는길 1", "city": "춘천", "status": "없음", "lat": "", "lon": ""}])
    n = J.new_coords(d, t, "rid").set_index("rid")
    assert n.loc["A", "coord_source"] == J.JUSO_SOURCE and n.loc["A", "lat"] == "37.8679181"
    assert n.loc["B", "coord_source"] == "원천 제공 위경도" and n.loc["B", "lat"] == "37.2"  # 원천 값은 그대로
    assert n.loc["C", "coord_source"].endswith(J.UNMATCHED) and n.loc["C", "lat"] == "37.3"  # 미매칭은 탐색용으로 남김
    assert n.loc["D", "coord_source"] == J.JUSO_SOURCE  # 좌표 없던 방문형 행은 채움
    again = J.new_coords(d.assign(coord_source=n["coord_source"].to_numpy()), t, "rid").set_index("rid")
    assert again.loc["C", "coord_source"].count(J.UNMATCHED) == 1  # 두 번 돌려도 같음


def test_ledger_coordinates_from_juso_table():
    """원장의 도로명주소 좌표는 좌표표와 같고, 지오코더 좌표는 미매칭 표시가 있을 때만 남는다."""
    t = J.load_table()
    look = J.lookup(t)
    d = L.load()
    juso = d[d["coord_source"] == J.JUSO_SOURCE]
    assert len(juso) > 0 and (juso["coordinate_status"].isin(["확인(juso)", "권역 외"])).all()
    for r in juso.to_dict("records"):
        hit = look[(J.keyword_of(r["address"]), r["city"])]
        assert hit["status"] == "일치" and (hit["lat"], hit["lon"]) == (r["lat"], r["lon"])
    geo = d[d["coord_source"].str.contains("ArcGIS|Photon")]
    assert geo["coord_source"].str.endswith(J.UNMATCHED).all()


def test_apply_is_idempotent():
    out = J.apply(write=False)
    assert out["src"].equals(L.read_source())
    assert out["supplement"].equals(L.read_supplement())
