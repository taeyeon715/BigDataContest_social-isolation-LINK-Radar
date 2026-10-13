"""자원 원장 보강: 공공 API 원문 해석, 보강 자원 칸, 매칭. 네트워크 없이 돈다."""

import pandas as pd

from bigcontest.resources import ledger
from bigcontest.resources import sources as S
from bigcontest.resources.collect import RawPage, mask_secrets
from bigcontest.resources.match import select_resources

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
    return ledger.derive(pd.DataFrame([{**BASE, **r} for r in rows]))

PHARM_XML = """<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>00</resultCode>
<resultMsg>NORMAL SERVICE.</resultMsg></header><body><items>
<item><dutyAddr>서울특별시 강남구 테헤란로 1 (역삼동)</dutyAddr><dutyName>휴일약국</dutyName><dutyTel1>02-000-0000</dutyTel1>
<dutyTime1s>0900</dutyTime1s><dutyTime1c>2000</dutyTime1c><dutyTime8s>1000</dutyTime8s><dutyTime8c>1800</dutyTime8c>
<hpid>C0000001</hpid><wgs84Lat>37.5005</wgs84Lat><wgs84Lon>127.0365</wgs84Lon></item>
<item><dutyAddr>서울특별시 강남구 테헤란로 2</dutyAddr><dutyName>평일약국</dutyName>
<dutyTime1s>0900</dutyTime1s><dutyTime1c>1800</dutyTime1c><hpid>C0000002</hpid>
<wgs84Lat>37.5005</wgs84Lat><wgs84Lon>127.0365</wgs84Lon></item>
</items><numOfRows>500</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""

WELFARE_XML = """<?xml version="1.0" encoding="UTF-8"?><wantedList><totalCount>3</totalCount><resultCode>0</resultCode>
<servList><servId>WLF1</servId><servNm>노인맞춤돌봄서비스</servNm><lifeArray>노년</lifeArray><jurMnofNm>보건복지부</jurMnofNm>
<rprsCtadr>129</rprsCtadr><servDtlLink>https://www.bokjiro.go.kr/x?wlfareInfoId=WLF1</servDtlLink></servList>
<servList><servId>WLF2</servId><servNm>일본군'위안부' 피해자 지원</servNm></servList>
<servList><servId>WLF3</servId><servNm>위기청년(고립은둔) 지원</servNm><lifeArray>청년</lifeArray>
<servDtlLink>https://www.bokjiro.go.kr/x?wlfareInfoId=WLF3</servDtlLink></servList>
</wantedList>"""


def _parse(xml: str, tag: str = "item") -> list[dict]:
    f = S.DataGoKrFetcher("t", "", {}, 500, "")
    f.item_tag = tag
    return f.parse(RawPage(xml.encode(), "xml"))


def test_egen_keeps_only_holiday_hours_and_assigns_dong():
    cfg = S.load_config()
    rows = S.egen_rows("pharmacy", "강남", _parse(PHARM_XML), "2026-10-14", cfg, S.Boundary(cfg["boundary"]))
    assert [r["resource_id"] for r in rows] == ["EP-C0000001"]  # 공휴일 시간이 없는 약국은 넣지 않음 (아니오로 단정 안 함)
    r = rows[0]
    assert r["holiday_open"] == "예" and "공휴일 10:00-18:00" in r["schedule"] and "명절" in r["note"]
    assert r["adm_cd"].startswith("11230") and r["coordinate_status"] == "탐색용"
    assert r["last_verified"] == "2026-10-14" and r["eligibility_rule_status"] == "확인됨"
    assert list(r) == list(ledger.load().columns)


def test_welfare_picks_by_name_and_sets_eligibility():
    cfg = S.load_config()
    rows = S.welfare_rows("bokjiro_national", _parse(WELFARE_XML, "servList"), "2026-10-14", cfg)
    ids = sorted(r["resource_id"] for r in rows)
    assert ids == ["WN-WLF1-CC", "WN-WLF1-GN", "WN-WLF3-CC", "WN-WLF3-GN"]  # '위안부'의 '안부'는 고르지 않음
    r = {x["resource_id"]: x for x in rows}
    assert r["WN-WLF1-GN"]["eligibility"] == ledger.ELIG_CARE and r["WN-WLF1-GN"]["coordinate_status"] == "해당 없음"
    assert r["WN-WLF1-GN"]["resource_kind"] == "제도·사업" and r["WN-WLF1-GN"]["delivery_channel"] == "신청"
    verdicts, _ = ledger.ELIGIBILITY_RULES[r["WN-WLF3-GN"]["eligibility"]]
    assert verdicts == {"고령층": "해당 안 됨", "중장년 남성 1인가구": "해당 안 됨", "청년 남성": "확인 필요"}


def test_boundary_nearest_for_points_on_the_line():
    cfg = S.load_config()
    bd = S.Boundary(cfg["boundary"])
    assert bd.locate(37.5005, 127.0365)[0].startswith("11230")
    assert bd.locate(37.5665, 126.9780) == ("", "")  # 서울시청: 권역 밖
    assert bd.nearest(37.5665, 126.9780) == ("", "")  # 0.3km 안 경계가 없음


def test_mask_secrets_on_service_key():
    assert "abc" not in mask_secrets("https://apis.data.go.kr/x?serviceKey=abc&pageNo=1")


# ------------------------------------------------------------ 매칭
PROGRAM = {"type": "제도·사업", "subtype": "노인맞춤돌봄서비스", "eligibility": ledger.ELIG_CARE, "address": "",
           "adm_cd": "", "adm_nm": "", "lat": "", "lon": "", "coord_source": "none"}


def _with_kind(d: pd.DataFrame, kinds: dict) -> pd.DataFrame:
    d = d.copy()
    for rid, (kind, channel) in kinds.items():
        i = d.index[d["resource_id"] == rid]
        d.loc[i, ["resource_kind", "delivery_channel"]] = [kind, channel]
        d.loc[i, "coordinate_status"] = d.loc[i].apply(ledger.coord_status, axis=1)
    return d


def test_program_is_nonspatial_and_elderly_first():
    d = make(
        {"rid": "H1", "name": "어느 행정복지센터", "type": "행정창구", "subtype": "행정복지센터", "eligibility": "관할 주민"},
        {"rid": "P1", "name": "노인맞춤돌봄서비스", **PROGRAM},
    )
    d = _with_kind(d, {"P1": ("제도·사업", "신청")})
    assert d.set_index("rid").loc["P1", "coordinate_status"] == "해당 없음" and not ledger.validate(d)["errors"]
    sel = select_resources(d, GN, "고령층", "안부·관계", origin=ORIGIN)
    p1 = sel.ranked[0]
    assert p1.resource_id == "P1" and "위치 확인 필요" not in p1.flags and p1.distance_km is None


def test_each_need_gets_its_best_resource_first():
    d = make(
        *[{"rid": f"M{i}", "name": f"휴일 약국 {i}", "type": "의료", "subtype": f"약국{i}", "eligibility": "누구나",
           "lat": f"37.50{i}"} for i in range(5)],
        {"rid": "T1", "name": "외로움 상담", "type": "상담/정신건강", "subtype": "1인가구지원(청년·중장년)",
         "eligibility": "누구나, 24시간", "address": "", "adm_cd": "", "lat": "", "lon": "", "coord_source": "none"},
    )
    d.loc[d["rid"].str.startswith("M"), "subtype"] = "보건소"
    d = _with_kind(d, {"T1": ("전화 상담", "전화")})
    d["holiday_open"] = "예"
    sel = select_resources(d, GN, "고령층", cause_type="달력 공백형", origin=ORIGIN, limit=3, per_subtype=None)
    assert "T1" in [m.resource_id for m in sel.ranked]  # 의료 접근만 3곳이 아니라 안부·관계 자원도 하나
    assert [m.resource_id for m in sel.ranked][:2] == ["M0", "M1"]  # 순서는 원래 순위 그대로


def test_holiday_open_first_for_calendar_gap():
    d = make(
        {"rid": "A", "name": "가까운 보건소", "type": "의료", "subtype": "보건소", "eligibility": "누구나", "lat": "37.5001"},
        {"rid": "B", "name": "휴일 여는 약국", "type": "의료", "subtype": "휴일 운영 약국", "eligibility": "누구나", "lat": "37.5030"},
    )
    d.loc[d["rid"] == "B", "holiday_open"] = "예"
    sel = select_resources(d, GN, "고령층", "의료 접근", cause_type="달력 공백형", origin=ORIGIN)
    assert [m.resource_id for m in sel.ranked] == ["B", "A"]


def test_real_supplement_counts_and_unverified_people():
    d = ledger.load().set_index("resource_id")
    sup = ledger.read_supplement().set_index("resource_id")
    assert {"EP", "EH", "EE", "WN", "WL", "MC", "CP", "PP"} <= {i.split("-")[0] for i in sup.index}
    people = sup[sup["resource_kind"] == "사람"]
    unverified = people[people["last_verified"] == ""]
    assert len(unverified) >= 1 and unverified["verified_date"].str.startswith("미확인").all()
    assert (sup.loc[sup["subtype"].isin(["휴일 운영 약국", "휴일 운영 병·의원", "응급의료기관"]), "holiday_open"] == "예").all()
    assert d.loc["R0740", "resource_kind"] == "시설"  # 정정 기록 (물리 거점)
