"""복지자원 원장 v2: 원래 원장에 상태 칸을 붙이고 검사한다.

만들기: uv run python -m bigcontest.resources.ledger
  external/resources/복지자원_원장.csv (원본, 손대지 않음. EDA-4가 그대로 씀)
  + 아래 규칙으로 만든 상태 칸 + 정정_기록.csv
  → external/resources/복지자원_원장_v2.csv (기존 칸·값·행 순서는 그대로, 칸만 추가)

상태 칸을 채우는 규칙 (원장에 있는 칸만 보고 정함. 알 수 없으면 "미상", 추측하지 않음)
- resource_id: rid 그대로 (고정 ID)
- source_record_id: 원천 자료의 행 ID. 지금 원장에는 없어 빈 값. API 수집 때 채움
- delivery_channel: subtype "전화·광역(비공간)" → 전화
    재가노인복지시설: 이름에 "주간보호·주야간·데이케어" → 방문, "방문" → 찾아가는 서비스, 그 밖 → 미상
    (재가 시설은 방문요양·주야간보호·복지용구가 섞여 있어 원장만으로 나눌 수 없음)
    나머지 시설 → 방문
- coordinate_status: 좌표 없음 + 전화·온라인 → 해당 없음 / 좌표 없음 + 방문형 → 미확인
    coord_source에 juso → 확인(juso) / 그 밖의 좌표(ArcGIS·Photon·원천 제공) → 탐색용
    좌표가 있는데 도시의 행정동에 배정되지 않음 → 권역 외 (좌표가 없어도 주소상 권역 밖이면 정정 기록으로 권역 외)
- eligibility_rule_status: eligibility 문구가 ELIGIBILITY_RULES에 있으면 확인됨, "시설별"·빈 값·모르는 문구는 미상
    (원장의 문구는 시설 유형별 일반 기준이며, 시설마다 확인한 값은 아님)
- operating_status: 원장에는 운영 여부를 확인한 자료가 없어 모두 미상. 목록에 있다 ≠ 운영 중
- schedule: hours 값. 비어 있으면 eligibility의 "24시간"·"평일 주간"을 옮김. 그래도 없으면 미상
- holiday_open: 운영 시간에 "평일"만 있으면 아니오, 나머지는 미상 ("24시간"도 휴일 운영은 따로 확인)
- service_area: 행정복지센터 → 관할 행정동, 보건소·시군구 정신건강복지센터·중독센터 → 시·구 전체,
    광역 정신건강복지센터 → 도 전체, "서울시민" 자원 → 서울시 전역, 전국 상담전화 → 전국,
    "강남구 거주" → 강남구 전체, 그 밖 → 미상
- last_verified: verified_date가 날짜(YYYY-MM-DD)면 그 값, 아니면 빈 값 ("재확인 필요" 등).
    공식 페이지에서 다시 확인하면 정정 기록으로 그 날짜를 넣는다 (실행일을 그냥 넣지 않음)
- verified_url / page_collected_at / operation_confirmed_at: 처음에는 빈 값. 공식 페이지로 확인했을 때만 정정 기록으로 채움
    verified_url = 확인한 공식 페이지 (원장 source_url과 다를 수 있음), page_collected_at = 그 페이지를 읽은 날,
    operation_confirmed_at = 페이지가 운영을 밝힌 날짜(게시일 등). 페이지에 날짜가 없으면 빈 값
- resource_kind: 자원 묶음. 시설 / 전화 상담 / 제도·사업 / 사람. 원래 원장은 전화 → 전화 상담, 나머지 → 시설
- note: 쓸 때 알아 둘 점 (예: E-Gen 공휴일 진료시간은 명절 당일과 다를 수 있음). 원래 원장은 빈 값

보강 자원 (resources/sources.py): 추가자원_원장.csv 를 원래 원장 745건 **뒤에** 붙인다.
  휴일 운영 약국·병의원·응급의료기관(E-Gen), 복지로 제도·사업, 노인맞춤돌봄 수행기관, 사람 자원.
  이 행들은 이미 위 칸을 모두 갖고 있다 (sources.py 가 같은 규칙 함수로 채움). 원래 745건의 칸·값·순서는 그대로.
그다음 정정_기록.csv를 위에서부터 적용한다 (예: 서울잇다플레이스는 전화형이 아니라 물리 거점).
"""

import re
from pathlib import Path

import pandas as pd

from bigcontest.agent.schemas import CITIES
from bigcontest.config import ROOT
from bigcontest.resources.collect import apply_corrections, load_corrections

RES = ROOT / "external" / "resources"
SRC = RES / "복지자원_원장.csv"
V2 = RES / "복지자원_원장_v2.csv"
SUPPLEMENT = RES / "추가자원_원장.csv"
CITY_PREFIX = {"강남": "11230", "춘천": "32010"}
assert set(CITY_PREFIX) == set(CITIES)

ENUMS = {
    "delivery_channel": ["방문", "전화", "온라인", "찾아가는 서비스", "신청", "미상"],  # 신청: 제도·사업 (주민센터·온라인 신청)
    "coordinate_status": ["확인(juso)", "탐색용", "미확인", "지오코딩 실패", "권역 외", "해당 없음"],
    "eligibility_rule_status": ["확인됨", "미상"],
    "operating_status": ["운영 중", "중단", "미상", "확인 필요"],
    "holiday_open": ["예", "아니오", "미상"],
    "resource_kind": ["시설", "전화 상담", "제도·사업", "사람"],
}
NONSPATIAL_KINDS = {"제도·사업", "사람"}  # 위치가 없는 것이 정상인 자원 (관할 전체·사람 연결)
NONSPATIAL_CHANNELS = {"전화", "온라인", "신청"}
SPATIAL = {"확인(juso)", "탐색용"}  # 거리 계산에 쓰는 좌표 상태
NEW_COLS = [
    "resource_id", "source_record_id", "delivery_channel", "coordinate_status", "eligibility_rule_status",
    "operating_status", "schedule", "holiday_open", "service_area", "last_verified",
    "verified_url", "page_collected_at", "operation_confirmed_at", "resource_kind", "note",
]
REQUIRED = ["resource_id", "name", "type", "subtype", "city", "target_stage", "eligibility", "source_url", "verified_date"]

# eligibility 문구 → 집단별 판정 (해당됨 / 해당 안 됨 / 확인 필요) 과 이유. 모르는 문구는 모두 확인 필요.
# 집단은 configs/need_types.yaml groups와 같은 이름.
_ALL = {"고령층": "해당됨", "중장년 남성 1인가구": "해당됨", "청년 남성": "해당됨"}
_OLD = {"고령층": "해당됨", "중장년 남성 1인가구": "해당 안 됨", "청년 남성": "해당 안 됨"}
_CHECK = {"고령층": "확인 필요", "중장년 남성 1인가구": "확인 필요", "청년 남성": "확인 필요"}
_CARE = {"고령층": "확인 필요", "중장년 남성 1인가구": "해당 안 됨", "청년 남성": "해당 안 됨"}
ELIGIBILITY_RULES: dict[str, tuple[dict, str]] = {
    "누구나": (_ALL, "누구나 이용"),
    "누구나, 24시간": (_ALL, "누구나 이용"),
    "누구나(평일 주간)": (_ALL, "누구나 이용"),
    "누구나(평일 주간 운영)": (_ALL, "누구나 이용"),
    "누구나(무료 상담·사례관리)": (_ALL, "누구나 이용"),
    "진료(건강보험)": (_ALL, "건강보험 진료"),
    "관할 주민": (_ALL, "관할 주민 (관할은 따로 거름)"),
    "서울시민": (_ALL, "서울시민"),
    "서울시민 누구나, 24시간": (_ALL, "서울시민 누구나"),
    "만 60세 이상": (_OLD, "만 60세 이상"),
    "만 65세 이상(회원제)": (_OLD, "만 65세 이상, 회원 가입 필요"),
    "만 60/65세 이상(사업별)": (_OLD, "만 60/65세 이상 (사업마다 다름)"),
    "장기요양등급 또는 노인맞춤돌봄 대상 여부": (_CARE, "장기요양 등급·노인맞춤돌봄 선정은 개인별 확인"),
    "장기요양 등급(입소)": (_CARE, "장기요양 등급은 개인별 확인"),
    "입소": (_CHECK, "입소 기준 개인별 확인"),
    "지역주민(저소득·취약 우선)": (_CHECK, "저소득·취약 우선이라 소득 기준 확인"),
    "수급자·차상위 근로능력자": (_CHECK, "수급·차상위 여부 확인"),
    "노숙인": (_CHECK, "노숙인 대상"),
    "정신질환 회복자": (_CHECK, "정신질환 회복자 대상"),
    "강남구 거주 1인가구": (
        {"고령층": "확인 필요", "중장년 남성 1인가구": "해당됨", "청년 남성": "확인 필요"},
        "강남구 거주 1인가구 (1인가구 여부 확인)",
    ),
    "청소년(만 9~24세)": (
        {"고령층": "해당 안 됨", "중장년 남성 1인가구": "해당 안 됨", "청년 남성": "확인 필요"},
        "만 9~24세 (청년 남성 중 20~24세만 해당)",
    ),
}
# 보강 자원의 자격 문구 (sources.py 가 씀)
ELIG_CARE = "만 65세 이상 중 돌봄 필요(기초연금·차상위·수급 등, 개인별 판정)"
ELIG_CRISIS = "위기 사유와 소득·재산 기준(개인별 판정)"
ELIG_ENERGY = "소득·세대원 기준(사업별 판정)"
ELIG_PROGRAM_UNKNOWN = "사업별 대상(생애주기 미상)"
ELIGIBILITY_RULES[ELIG_CARE] = (_CARE, "만 65세 이상 돌봄 필요 여부는 개인별 판정")
ELIGIBILITY_RULES[ELIG_CRISIS] = (_CHECK, "위기 사유·소득·재산 기준 개인별 확인")
ELIGIBILITY_RULES[ELIG_ENERGY] = (_CHECK, "소득·세대원 기준 확인")
ELIGIBILITY_RULES[ELIG_PROGRAM_UNKNOWN] = (_CHECK, "사업별 대상 확인")
# 복지로 생애주기 → 집단. 생애주기에 그 집단이 있으면 '확인 필요'(사업별 기준), 없으면 '해당 안 됨'
LIFE_OF_GROUP = {"고령층": "노년", "중장년 남성 1인가구": "중장년", "청년 남성": "청년"}
LIFE_ORDER = ["청년", "중장년", "노년"]


def program_eligibility(stages) -> str:
    """복지로 생애주기 목록 → 자격 문구 (ELIGIBILITY_RULES 에 있는 것). 세 집단과 겹치는 단계만 봄."""
    st = [s for s in LIFE_ORDER if s in set(stages)]
    return f"사업별 대상(생애주기: {'·'.join(st)})" if st else ELIG_PROGRAM_UNKNOWN


for _n in range(1, 1 << len(LIFE_ORDER)):
    _st = [s for i, s in enumerate(LIFE_ORDER) if _n >> i & 1]
    ELIGIBILITY_RULES[program_eligibility(_st)] = (
        {g: ("확인 필요" if life in _st else "해당 안 됨") for g, life in LIFE_OF_GROUP.items()},
        f"복지로 생애주기 {'·'.join(_st)} 대상 (세부 기준은 사업별 확인)",
    )
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _channel(r) -> str:
    if r["subtype"] == "전화·광역(비공간)":
        return "전화"
    if r["subtype"] == "재가노인복지시설":
        if re.search(r"주간보호|주야간|데이케어", r["name"]):
            return "방문"
        if "방문" in r["name"]:
            return "찾아가는 서비스"
        return "미상"
    return "방문"


def _kind(r) -> str:
    return "전화 상담" if r["delivery_channel"] == "전화" else "시설"


def coord_status(r) -> str:
    """좌표 상태. 보강 자원(sources.py)도 같은 함수를 쓴다 (resource_kind 가 있으면 함께 봄)."""
    if r["lat"] == "" or r["lon"] == "":
        nonspatial = r["delivery_channel"] in NONSPATIAL_CHANNELS or r.get("resource_kind", "") in NONSPATIAL_KINDS
        return "해당 없음" if nonspatial else "미확인"
    if r["adm_cd"] == "" or not r["adm_cd"].startswith(CITY_PREFIX.get(r["city"], "-")):
        return "권역 외"
    return "확인(juso)" if "juso" in r["coord_source"].lower() else "탐색용"


def _schedule(r) -> str:
    if r["hours"]:
        return r["hours"]
    if "24시간" in r["eligibility"]:
        return "24시간"
    if "평일 주간" in r["eligibility"]:
        return "평일 주간"
    return "미상"


def _holiday(r) -> str:
    s = r["hours"] or r["eligibility"]
    if "평일" in s and not re.search(r"주말|토|일요일|휴일|공휴", s):
        return "아니오"
    return "미상"


def _service_area(r) -> str:
    sub, el, name = r["subtype"], r["eligibility"], r["name"]
    if sub == "행정복지센터":
        return "관할 행정동"
    if "서울시민" in el:
        return "서울시 전역"
    if sub == "전화·광역(비공간)" and el.startswith("누구나"):
        return "전국"
    if "강남구 거주" in el:
        return "강남구 전체"
    if sub in ("정신건강복지센터", "중독관리통합지원센터") and "광역" in name:
        return "도 전체"
    if sub in ("보건소", "정신건강복지센터", "중독관리통합지원센터"):
        return "시·구 전체"
    return "미상"


def read_source() -> pd.DataFrame:
    return pd.read_csv(SRC, dtype=str, keep_default_na=False)


def derive(src: pd.DataFrame) -> pd.DataFrame:
    """원래 원장 → 상태 칸을 붙인 원장 (정정 적용 전)."""
    d = src.copy()
    d["resource_id"] = d["rid"]
    d["source_record_id"] = ""
    d["delivery_channel"] = d.apply(_channel, axis=1)
    d["coordinate_status"] = d.apply(coord_status, axis=1)
    d["eligibility_rule_status"] = d["eligibility"].map(lambda e: "확인됨" if e in ELIGIBILITY_RULES else "미상")
    d["operating_status"] = "미상"
    d["schedule"] = d.apply(_schedule, axis=1)
    d["holiday_open"] = d.apply(_holiday, axis=1)
    d["service_area"] = d.apply(_service_area, axis=1)
    d["last_verified"] = d["verified_date"].where(d["verified_date"].str.match(_DATE), "")
    for c in ("verified_url", "page_collected_at", "operation_confirmed_at"):
        d[c] = ""
    d["resource_kind"] = d.apply(_kind, axis=1)
    d["note"] = ""
    return d


def read_supplement(path=SUPPLEMENT) -> pd.DataFrame:
    """보강 자원 (sources.py 가 만든 것). 없으면 빈 표."""
    if not path.exists():
        return pd.DataFrame()
    d = pd.read_csv(path, dtype=str, keep_default_na=False)
    if Path(path) == SUPPLEMENT:
        from bigcontest.resources.senior_centers import rows

        centers = rows(list(d.columns))
        if len(centers):
            d = pd.concat([d, centers], ignore_index=True)
    return d


def build(write: bool = True, corrections: pd.DataFrame | None = None,
          supplement: pd.DataFrame | None = None) -> pd.DataFrame:
    """원래 원장(상태 칸) + 보강 자원 → 정정 적용. 원래 745건의 기존 칸·값·순서는 그대로 맨 앞."""
    src = read_source()
    corr = load_corrections() if corrections is None else corrections
    base = derive(src)
    extra = read_supplement() if supplement is None else supplement
    if len(extra):
        assert list(extra.columns) == list(base.columns), "보강 자원 칸이 원장 v2 와 다름 (sources.py 로 다시 만듦)"
        base = pd.concat([base, extra], ignore_index=True)
    d = apply_corrections(base, corr)
    assert d.iloc[: len(src)][src.columns].reset_index(drop=True).equals(src), "기존 칸이 바뀌면 안 됨 (정정은 새 칸에만)"
    if write:
        d.to_csv(V2, index=False, encoding="utf-8")
    return d


def load(path=V2) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def spatial(d: pd.DataFrame) -> pd.DataFrame:
    """거리 계산에 쓸 수 있는 행 (좌표가 있고 권역 안)."""
    return d[d["coordinate_status"].isin(SPATIAL)]


def validate(d: pd.DataFrame) -> dict:
    """원장 검사. errors가 비어 있어야 통과."""
    errors, warnings = [], []
    miss = [c for c in REQUIRED + NEW_COLS if c not in d.columns]
    if miss:
        return {"errors": [f"필수 칸 없음: {miss}"], "warnings": [], "counts": {}}
    if d["resource_id"].eq("").any() or d["resource_id"].duplicated().any():
        errors.append("resource_id가 비었거나 중복")
    for c, allowed in ENUMS.items():
        bad = d.loc[~d[c].isin(allowed), "resource_id"].tolist()
        if bad:
            errors.append(f"{c} 허용 값이 아님: {bad[:5]}")
    for c in ["source_url", "verified_date"]:  # G-07: 출처·확인일
        bad = d.loc[d[c].str.strip() == "", "resource_id"].tolist()
        if bad:
            errors.append(f"{c} 없음 (G-07): {bad[:5]}")
    for c in ["city", "target_stage"]:
        bad = d.loc[d[c].str.strip() == "", "resource_id"].tolist()
        if bad:
            errors.append(f"{c} 없음 (G-07 지역·대상): {bad[:5]}")
    has_xy = (d["lat"] != "") & (d["lon"] != "")
    bad = d.loc[has_xy & d["coordinate_status"].isin(["미확인", "지오코딩 실패", "해당 없음"]), "resource_id"].tolist()
    bad += d.loc[~has_xy & d["coordinate_status"].isin(["확인(juso)", "탐색용"]), "resource_id"].tolist()
    if bad:
        errors.append(f"좌표와 coordinate_status가 맞지 않음: {bad[:5]}")
    nonspatial = d["delivery_channel"].isin(NONSPATIAL_CHANNELS) | d["resource_kind"].isin(NONSPATIAL_KINDS)
    bad = d.loc[(d["coordinate_status"] == "해당 없음") & ~nonspatial, "resource_id"].tolist()
    if bad:
        errors.append(f"방문형인데 좌표 '해당 없음' (좌표가 없으면 미확인): {bad[:5]}")
    for c in ("last_verified", "page_collected_at", "operation_confirmed_at"):
        bad = d.loc[(d[c] != "") & ~d[c].str.match(_DATE), "resource_id"].tolist()
        if bad:
            errors.append(f"{c}가 날짜(YYYY-MM-DD)가 아님: {bad[:5]}")
    bad = d.loc[(d["page_collected_at"] != "") & (d["verified_url"] == ""), "resource_id"].tolist()
    if bad:
        errors.append(f"페이지 확인일이 있는데 확인한 페이지 주소가 없음: {bad[:5]}")
    n_redo = int((d["last_verified"] == "").sum())
    if n_redo:
        warnings.append(f"확인일이 날짜가 아닌 행(재확인 필요) {n_redo}건")
    counts = {"total": len(d), "spatial": len(spatial(d)), "no_coord": int((~has_xy).sum())}
    counts.update({f"kind_{k}": int((d["resource_kind"] == k).sum()) for k in ENUMS["resource_kind"]})
    return {"errors": errors, "warnings": warnings, "counts": counts}


if __name__ == "__main__":
    d = build()
    v = validate(d)
    print(f"{V2.relative_to(ROOT)}: {v['counts']}")
    for e in v["errors"]:
        print("오류:", e)
    for w in v["warnings"]:
        print("주의:", w)
