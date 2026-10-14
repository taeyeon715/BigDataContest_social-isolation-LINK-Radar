"""복지자원 원장 745건(external/resources/복지자원_원장.csv)을 원본 파일 12개에서 다시 만든다.

실행: uv run python -m bigcontest.resources.build_ledger [--raw <원본 폴더>] [--check]
  원본 폴더 기본값: external/resources/raw/원장_원본/  (git 추적 안 함). 환경 변수 BC_LEDGER_RAW 로도 지정.
  원본 12개의 이름·SHA256 은 external/resources/원본_출처목록.csv 에 있다. 하나라도 다르면 멈춘다.
  --check: 지금 원장과 칸별로 비교만 하고 쓰지 않음 (기본도 쓰지 않음. --write 일 때만 씀)

단계 (2026-10-13 탐색 때 쓴 스크립트를 그대로 옮김)
  1) 원본 → 행 (`rows_from_sources`). 분류(type·subtype·대상·자격)는 아래 TAX 표와 원본별 규칙.
     - 보건복지부 사회복지시설(15160109): 주소가 '서울특별시 강남구'·'강원특별자치도 춘천시'로 시작하는 행.
       시설종류 → TAX 표. 표에 없는 종류는 '기타(아동·장애 등)' / 해당 대상 / 시설별
     - 서울시 경로당(OA-15052): 시군구 '강남구'. 이름에 '경로당'이 없으면 붙임
     - 춘천시 행정복지센터(15076325): 이름 앞에 '춘천시 ', 주소 '강원도' → '강원특별자치도'
     - 강원 정신보건 기관(15160211 원문 xlsx): 정신건강복지센터·중독센터 중 춘천, 정신의료기관 춘천 14곳(표 6~19행)
     - 강원 지역보건의료기관(15159040): 춘천시. 이름 = '기관명 기관유형', 운영시간 칸 → hours
     - 정신건강복지센터 목록(15167096): 서울 강남구만 (춘천은 위 강원 자료와 겹침). 원천 위경도를 씀
     - 손으로 넣은 행 9건 (강남구보건소·1인가구 커뮤니티센터·청소년심리지원센터, 전화 상담 6건): MANUAL 표
     - (city, name, address)가 같은 행은 처음 것만. 순서대로 rid = R0000…
     나머지 원본(15074349 한부모, 15129863 노인일자리, 15155517 스마트복지관 카탈로그, 15158908 강원 노인복지시설,
     강원 노인여가복지시설 xlsx)은 원장 행을 만들지 않는다 (참고·교차검증용). 15074350 춘천 노인복지관은 좌표 확인용.
  2) 좌표: 2026-10-13 에 Esri ArcGIS World Geocoder(키 없음)로 얻은 값. **이 단계는 다시 돌리지 않는다.**
     Esri 약관상 키 없는 결과의 저장·재배포가 걸릴 수 있어 지오코더 응답을 레포에 두지 않는다.
     그래서 좌표(lat·lon·coord_source)는 지금 원장에서 rid 로 가져온다 (`coords=` 로 다른 좌표표를 줄 수 있음.
     도로명주소 좌표 API 로 바꿀 때 여기에 넣는다). 원천 위경도가 있는 행(15167096, 15074350)은 원천 값.
  3) 행정동 배정 (`assign_dongs`): 좌표를 ref/adm_boundary.geojson 경계에 넣어 찾고, 주소 괄호 속 법정동(강남)·
     읍면·법정동 이름(춘천)과 맞춰 본다. 규칙은 탐색 스크립트와 같음 (좌표+주소일치 / 좌표 / 좌표(번지점)+법정동≠행정동 경계 /
     주소텍스트 / 미배정 / 비공간).

차이 (원장 745건과 비교, tests/test_build_ledger.py 가 고정)
  - 1)·3)의 결과는 원장과 같아야 한다 (원본이 있을 때만 검사).
  - 탐색 때는 경계를 EPSG:5179 로 바꿔 판정했고, 여기서는 위경도 그대로 판정한다. 경계선에 아주 가까운 점은 다를 수 있다
    (지금 745건에서는 차이 없음).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources.ledger import SRC
from bigcontest.resources.sources import Boundary

RAW = ROOT / "external" / "resources" / "raw" / "원장_원본"
MANIFEST = ROOT / "external" / "resources" / "원본_출처목록.csv"
BOUNDARY = ROOT / "ref" / "adm_boundary.geojson"
NAMES = ROOT / "ref" / "adm_cd_names.csv"
COLLECTED = "2026-10-13"
COLS = ["rid", "name", "type", "subtype", "hub", "target_stage", "eligibility", "city", "address", "adm_cd", "adm_nm",
        "dong_method", "lat", "lon", "coord_source", "phone", "hours", "source_url", "verified_date", "collected_date", "src"]
F = {
    "mohw": "datagokr_15160109_보건복지부_사회복지시설 정보_20260318.csv",
    "sl_kr": "seoul_OA-15052_서울시경로당현황_2025-06말.xlsx",
    "cc_adm": "datagokr_15076325_강원특별자치도_춘천시_행정복지센터_20260304 .csv",
    "cc_eld": "datagokr_15074350_강원특별자치도_춘천시_노인복지관_20251101.csv",
    "gw_mh": "gwd_정신보건관련기관현황_2026.xlsx",
    "gw_hl": "datagokr_15159040_강원특별자치도_지역보건의료기관.csv",
    "kohi_mh": "datagokr_15167096_정신건강복지센터기초데이터_보정_20260921.csv",
}
URL = {
    "mohw": "https://www.data.go.kr/data/15160109/fileData.do",
    "cc_adm": "https://www.data.go.kr/data/15076325/fileData.do",
    "gw_mh": "https://www.data.go.kr/data/15160211/fileData.do (원문 state.gwd.go.kr 정신보건관련 기관 현황 xlsx)",
    "sl_kr": "https://data.seoul.go.kr/dataList/OA-15052/S/1/datasetView.do",
    "gw_hl": "https://www.data.go.kr/data/15159040/fileData.do",
}
VERIFIED = {"mohw": "2026-03-18", "cc_adm": "2026-03-04", "gw_mh": "2026-05-13", "sl_kr": "2025-06-30",
            "gw_hl": "2026(분기갱신)"}
# 시설종류 → (type, target_stage, eligibility)
TAX = {
    "노인복지관": ("관계형성·여가", "노년", "만 60세 이상"), "노인교실": ("관계형성·여가", "노년", "만 60세 이상"),
    "재가노인복지시설": ("노인돌봄", "노년", "장기요양등급 또는 노인맞춤돌봄 대상 여부"),
    "노인요양시설": ("노인돌봄", "노년", "장기요양 등급(입소)"), "노인요양공동생활가정": ("노인돌봄", "노년", "장기요양 등급(입소)"),
    "노인공동생활가정": ("노인돌봄", "노년", "입소"), "양로시설": ("노인돌봄", "노년", "입소"), "노인복지주택": ("노인돌봄", "노년", "입소"),
    "노인일자리지원기관": ("관계형성·여가", "노년", "만 60/65세 이상(사업별)"),
    "사회복지관": ("생활지원", "전 생애", "지역주민(저소득·취약 우선)"),
    "지역자활센터": ("생활지원", "청년·중장년", "수급자·차상위 근로능력자"),
    "노숙인요양시설": ("생활지원", "중장년", "노숙인"), "노숙인일시보호시설": ("생활지원", "중장년", "노숙인"),
    "주간재활시설": ("상담/정신건강", "청년·중장년", "정신질환 회복자"),
}
OTHER = ("기타(아동·장애 등)", "해당 대상", "시설별")
HUB = {"노인복지관", "사회복지관", "정신건강복지센터", "1인가구지원(청년·중장년)", "청소년 심리지원", "중독관리통합지원센터"}
# 손으로 넣은 행 (공식 보도·안내 기준, 재확인 필요)
MANUAL = [
    dict(name="강남구보건소", type="의료", subtype="보건소", target_stage="전 생애", eligibility="누구나(평일 주간)", city="강남",
         address="서울특별시 강남구 선릉로 668", src="manual", source_url="https://health.gangnam.go.kr (주소 재확인 필요)",
         verified_date="수기(재확인 필요)"),
    dict(name="강남구 1인가구 커뮤니티센터", type="중장년", subtype="1인가구지원(청년·중장년)", target_stage="청년·중장년",
         eligibility="강남구 거주 1인가구", city="강남", address="서울특별시 강남구 테헤란로8길 36", src="manual",
         source_url="https://mediahub.seoul.go.kr/archives/2011364", verified_date="2024 보도(재확인 필요)"),
    dict(name="강남구 청소년심리지원센터 사이쉼", type="상담/정신건강", subtype="청소년 심리지원", target_stage="청소년·청년",
         eligibility="청소년(만 9~24세)", city="강남", address="서울특별시 강남구 도곡로 420", src="manual",
         source_url="https://www.gangnam.go.kr/board/B_000031/1074769/view.do?mid=ID01", verified_date="보도 기준(재확인 필요)"),
]
PHONE = [  # (이름, type, 대상, 자격, 전화, 출처, 도시)
    ("외로움안녕120(서울시 24시간 외로움 상담)", "상담/정신건강", "전 생애", "서울시민 누구나, 24시간", "☎120",
     "https://mediahub.seoul.go.kr/archives/2019485", "강남"),
    ("서울잇다플레이스(성동구, 외로움·고립 지원 거점)", "관계형성·여가", "청년·중장년", "서울시민", "02-120",
     "https://mediahub.seoul.go.kr/archives/2019485", "강남"),
    ("정신건강위기상담전화", "상담/정신건강", "전 생애", "누구나, 24시간", "☎1577-0199", "https://www.mentalhealth.go.kr", "강남"),
    ("정신건강위기상담전화", "상담/정신건강", "전 생애", "누구나, 24시간", "☎1577-0199", "https://www.mentalhealth.go.kr", "춘천"),
    ("자살예방상담전화", "상담/정신건강", "전 생애", "누구나, 24시간", "☎109", "https://www.mohw.go.kr", "강남"),
    ("자살예방상담전화", "상담/정신건강", "전 생애", "누구나, 24시간", "☎109", "https://www.mohw.go.kr", "춘천"),
]


def raw_dir() -> Path:
    return Path(os.environ.get("BC_LEDGER_RAW") or RAW)


def verify_sources(raw: Path, manifest: Path = MANIFEST) -> list[str]:
    """원본 폴더의 파일을 출처목록 SHA256 과 맞춘다. 문제 목록 (빈 목록이면 통과)."""
    m = pd.read_csv(manifest, dtype=str, encoding="utf-8-sig")
    bad = []
    for r in m.itertuples():
        p = Path(raw) / r.file
        if not p.exists():
            bad.append(f"없음: {r.file}")
        elif hashlib.sha256(p.read_bytes()).hexdigest() != r.sha256:
            bad.append(f"SHA256 다름: {r.file}")
    return bad


def available(raw: Path | None = None) -> bool:
    raw = raw or raw_dir()
    return raw.exists() and not verify_sources(raw)


def _csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 알 수 없음: {path}")


def rows_from_sources(raw: Path) -> pd.DataFrame:
    """1단계: 원본 → 원장 행 (좌표·행정동 전). rid 까지 붙인다."""
    raw = Path(raw)
    rows: list[dict] = []
    d = _csv(raw / F["mohw"])
    for city, prefix in [("강남", "서울특별시 강남구"), ("춘천", "강원특별자치도 춘천시")]:
        for r in d[d["시설주소"].str.startswith(prefix, na=False)].itertuples():
            typ, stage, elig = TAX.get(r.시설종류, OTHER)
            rows.append(dict(name=r.시설명, type=typ, subtype=r.시설종류, target_stage=stage, eligibility=elig, city=city,
                             address=r.시설주소, src="mohw"))
    x = pd.read_excel(raw / F["sl_kr"], header=None)
    for r in x[x[2] == "강남구"].itertuples(index=False):
        nm = str(r[4]).strip()
        rows.append(dict(name=nm if "경로당" in nm else nm + " 경로당", type="관계형성·여가", subtype="경로당",
                         target_stage="노년", eligibility="만 65세 이상(회원제)", city="강남",
                         address="서울특별시 " + str(r[5]).strip(), src="sl_kr"))
    a = _csv(raw / F["cc_adm"])
    for r in a.itertuples():
        rows.append(dict(name="춘천시 " + r.읍면동명, type="행정창구", subtype="행정복지센터", target_stage="전 생애",
                         eligibility="관할 주민", city="춘천", address=r.소재지도로명주소.replace("강원도 ", "강원특별자치도 "),
                         phone=r.대표전화번호, src="cc_adm"))
    mh = pd.read_excel(raw / F["gw_mh"], sheet_name=None, header=None)
    v = mh["정신건강증진센터"]
    for r in v[v[4].astype(str).str.startswith("춘천시")].itertuples(index=False):
        rows.append(dict(name=r[2], type="상담/정신건강", subtype="정신건강복지센터", target_stage="전 생애",
                         eligibility="누구나(무료 상담·사례관리)", city="춘천", address="강원특별자치도 " + r[4], phone=r[5],
                         src="gw_mh"))
    v = mh["중독관리통합지원센터"]
    for r in v[v[0].astype(str) == "춘천시"].itertuples(index=False):
        rows.append(dict(name=r[1], type="상담/정신건강", subtype="중독관리통합지원센터", target_stage="청년·중장년",
                         eligibility="누구나", city="춘천", address="강원특별자치도 " + r[3], phone=r[4], src="gw_mh"))
    for r in mh["정신의료기관"].iloc[5:19].itertuples(index=False):  # 춘천 14개소 (시군명 칸이 첫 행에만 있음)
        rows.append(dict(name=r[2], type="의료", subtype="정신의료기관", target_stage="전 생애", eligibility="진료(건강보험)",
                         city="춘천", address="강원특별자치도 " + str(r[3]), phone=r[4], src="gw_mh"))
    h = _csv(raw / F["gw_hl"])
    for r in h[h["시군명"] == "춘천시"].to_dict("records"):
        rows.append(dict(name=f"{r['기관명']} {r['기관유형']}", type="의료", subtype=r["기관유형"], target_stage="전 생애",
                         eligibility="누구나(평일 주간 운영)", city="춘천",
                         address=r["소재지 도로명주소"].replace("강원 ", "강원특별자치도 "), phone=r["전화번호"],
                         hours=r["운영시간(진료일정은 해당 기관 문의)"], src="gw_hl"))
    mc = _csv(raw / F["kohi_mh"])
    for r in mc[(mc["시군구"] == "강남구") & (mc["시도"] == "서울특별시")].itertuples():
        rows.append(dict(name=r.기관명, type="상담/정신건강", subtype="정신건강복지센터", target_stage="전 생애",
                         eligibility="누구나(무료 상담·사례관리)", city="강남", address="서울특별시 강남구 " + r.도로명주소,
                         phone=r.전화번호, lat=r.위도, lon=r.경도, src="kohi_mh",
                         source_url="https://www.data.go.kr/data/15167096/fileData.do", verified_date="2026-09-21"))
    rows += [dict(m) for m in MANUAL]
    for nm, typ, st, el, ph, url, city in PHONE:
        rows.append(dict(name=nm, type=typ, subtype="전화·광역(비공간)", target_stage=st, eligibility=el, city=city,
                         address="", phone=ph, src="manual_phone", source_url=url,
                         verified_date="2026 공식 안내(재확인 필요)"))
    R = pd.DataFrame(rows)
    for c in ["source_url", "verified_date", "phone", "hours", "lat", "lon"]:
        if c not in R:
            R[c] = None
    R["source_url"] = R["source_url"].fillna(R["src"].map(URL))
    R["verified_date"] = R["verified_date"].fillna(R["src"].map(VERIFIED))
    R["collected_date"] = COLLECTED
    R = R.drop_duplicates(["city", "name", "address"]).reset_index(drop=True)
    R["rid"] = [f"R{i:04d}" for i in range(len(R))]
    R["hub"] = R["subtype"].isin(HUB).map(str)
    return R


def source_coords(raw: Path) -> dict[str, tuple[str, str]]:
    """원천이 위경도를 준 춘천 노인복지관 (이름 → (lat, lon))."""
    e = _csv(Path(raw) / F["cc_eld"])
    return {r.노인복지관명: (r.위도, r.경도) for r in e.itertuples()}


# ------------------------------------------------------------ 행정동 배정
GN = {"신사동": ["신사동"], "압구정동": ["압구정동"], "청담동": ["청담동"], "세곡동": ["세곡동"], "자곡동": ["세곡동"],
      "율현동": ["세곡동"], "수서동": ["수서동"], "논현동": ["논현1동", "논현2동"], "역삼동": ["역삼1동", "역삼2동"],
      "삼성동": ["삼성1동", "삼성2동"], "대치동": ["대치1동", "대치2동", "대치4동"], "도곡동": ["도곡1동", "도곡2동"],
      "개포동": ["개포1동", "개포2동", "개포3동", "개포4동"], "일원동": ["일원본동", "일원1동", "개포3동"]}
CN = {"석사동": ["석사동"], "퇴계동": ["퇴계동"], "후평동": ["후평1동", "후평2동", "후평3동"],
      "효자동": ["효자1동", "효자2동", "효자3동"], "교동": ["교동"], "근화동": ["근화동"], "우두동": ["신사우동"],
      "사농동": ["신사우동"], "조양동": ["조운동"], "운교동": ["조운동"], "약사동": ["약사명동"], "죽림동": ["약사명동"],
      "요선동": ["약사명동"], "온의동": ["강남동", "퇴계동"], "칠전동": ["강남동"], "송암동": ["강남동"], "삼천동": ["강남동"],
      "소양로": ["소양동"], "근화": ["근화동"]}
EUP = ["신북읍", "동면", "동산면", "신동면", "동내면", "남면", "남산면", "서면", "사북면", "북산면"]
CITY_CODE = {"강남": "11", "춘천": "32"}


def text_candidates(city: str, addr: str) -> list[str]:
    """주소 글자 → 행정동 이름 후보 (강남: 괄호 속 법정동, 춘천: 읍면 또는 법정동 이름)."""
    addr = addr if isinstance(addr, str) else ""
    if city == "춘천":
        m = re.search(r"춘천시\s+(\S+(?:읍|면))\s", addr)
        if m and m.group(1) in EUP:
            return [m.group(1)]
        for k, v in CN.items():
            if re.search(r"[\(\s,]" + k, addr):
                return v
        return []
    m = re.search(r"\(([^,\)]+)", addr)
    if m and m.group(1).strip() in GN:
        return GN[m.group(1).strip()]
    return []


def assign_dongs(R: pd.DataFrame, bd: Boundary | None = None, names: pd.DataFrame | None = None) -> pd.DataFrame:
    """3단계: lat·lon·coord_source 가 있는 표 → adm_cd·adm_nm·dong_method."""
    bd = bd or Boundary(BOUNDARY)
    names = pd.read_csv(NAMES, dtype=str) if names is None else names
    nm2cd = {(r.city, r.adm_nm): r.adm_cd for r in names.itertuples()}
    cd2nm = dict(zip(names["adm_cd"], names["adm_nm"]))
    out = R.copy()
    cds, hows = [], []
    for r in out.itertuples():
        tc = [nm2cd.get((r.city, t)) for t in text_candidates(r.city, r.address)]
        pc = None
        if r.lat and r.lon:
            pc = bd.locate(float(r.lat), float(r.lon))[0] or None
        if pc and pc[:2] != CITY_CODE[r.city]:
            pc = None
        exact = "ArcGIS" in r.coord_source or r.coord_source == "원천 제공 위경도"
        if pc and (not tc or pc in tc):
            cds.append(pc)
            hows.append("좌표+주소일치" if tc else "좌표")
        elif pc and tc and exact:
            cds.append(pc)
            hows.append("좌표(번지점)+법정동≠행정동 경계")
        elif pc and tc:
            cds.append(tc[0] if len(tc) == 1 else pc)
            hows.append("좌표-주소 불일치→주소우선(좌표폐기)" if len(tc) == 1 else "좌표-주소 불일치(좌표유지, 검토)")
        elif tc and len(tc) == 1:
            cds.append(tc[0])
            hows.append("주소텍스트")
        else:
            cds.append("")
            hows.append("비공간" if r.subtype == "전화·광역(비공간)" else "미배정")
    out["adm_cd"] = cds
    out["adm_nm"] = [cd2nm.get(c, "") for c in cds]
    out["dong_method"] = hows
    return out


def build(raw: Path | None = None, coords: pd.DataFrame | None = None) -> pd.DataFrame:
    """원본 12개 → 원장 (칸 순서·문자열 형식은 복지자원_원장.csv 와 같음).

    coords: rid, lat, lon, coord_source 표. 없으면 지금 원장의 좌표 (2026-10-13 ArcGIS, 다시 돌리지 않음).
    """
    raw = Path(raw or raw_dir())
    bad = verify_sources(raw)
    if bad:
        raise FileNotFoundError("원본이 출처목록과 다름: " + "; ".join(bad))
    R = rows_from_sources(raw)
    if coords is None:
        coords = pd.read_csv(SRC, dtype=str, keep_default_na=False)[["rid", "name", "lat", "lon", "coord_source"]]
        if not coords["name"].equals(R["name"]):
            raise ValueError("원본에서 만든 행 순서가 지금 원장과 달라 좌표를 rid 로 옮길 수 없음")
    c = coords.set_index("rid")
    eld = source_coords(raw)
    lat, lon, cs = [], [], []
    for r in R.itertuples():
        if isinstance(r.lat, str) and r.lat:  # 원천 위경도 (15167096)
            la, lo, s = r.lat, r.lon, "원천 제공 위경도"
        elif r.name in eld:  # 원천 위경도 (15074350)
            (la, lo), s = eld[r.name], "원천 제공 위경도"
        else:
            la, lo, s = c.at[r.rid, "lat"], c.at[r.rid, "lon"], c.at[r.rid, "coord_source"]
        lat.append(la)
        lon.append(lo)
        cs.append(s)
    R["lat"], R["lon"], R["coord_source"] = lat, lon, cs
    R = assign_dongs(R)
    R = R.fillna("")
    return R[COLS].astype(str)


def compare(new: pd.DataFrame, old: pd.DataFrame) -> dict:
    """칸별로 다른 행 수. 좌표는 숫자로 비교 (문자열 자리수 차이 무시)."""
    diff = {}
    if len(new) != len(old):
        return {"rows": (len(new), len(old))}
    for col in COLS:
        a, b = new[col].reset_index(drop=True), old[col].reset_index(drop=True)
        if col in ("lat", "lon"):
            n = int((pd.to_numeric(a, errors="coerce").fillna(0) - pd.to_numeric(b, errors="coerce").fillna(0)).abs()
                    .gt(1e-9).sum())
        else:
            n = int((a != b).sum())
        if n:
            diff[col] = n
    return diff


def main(argv=None):
    ap = argparse.ArgumentParser(description="복지자원 원장 745건을 원본 12개에서 다시 만들기")
    ap.add_argument("--raw", type=Path, default=None, help="원본 12개가 있는 폴더")
    ap.add_argument("--write", action="store_true", help="지금 원장과 다를 때도 덮어씀 (기본은 비교만)")
    a = ap.parse_args(argv)
    new = build(a.raw)
    old = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    diff = compare(new, old)
    print(f"원본에서 만든 원장 {len(new)}건, 지금 원장 {len(old)}건, 다른 칸: {diff or '없음'}")
    if a.write:
        new.to_csv(SRC, index=False, encoding="utf-8-sig")
        print(f"썼음: {SRC.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
