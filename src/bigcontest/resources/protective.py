"""보호 자원 칸: 먼저 볼 동 옆에 그 동에 이미 있는 연결 자원(경로당, 더위·한파 쉼터, 돌봄 이웃)을 함께 보여 준다.

왜: 지역 낙인 연구는 "결핍만 있는 동"으로 보이게 하는 표시를 경계한다(Clelland & Hill 2019). 그래서 후보 동을 보여 줄 때
그 동의 경로당·쉼터·돌봄단을 같은 화면에 둔다. **위험 칸이 아니다. 등급·순위·자원 추천 순서에 쓰지 않는다.**

묶음은 configs/need_types.yaml `protective.groups` (원장 subtype 목록). 자료
- 원장 v2 (external/resources/복지자원_원장_v2.csv)
  - 강남 경로당: 서울시 경로당 현황(OA-15052, 2025-06 말) → 행정동 배정이 있어 동마다 셀 수 있음
  - 쉼터: 강남구 무더위·한파쉼터 목록 안내(구청 페이지, 도시 단위 1줄씩)
  - 돌봄 이웃: 명예사회복지공무원·우리동네돌봄단·통·이장 (도시 단위, 사람 자원)
- 춘천 경로당: 동별 목록을 찾지 못함. 강원 노인여가복지시설 현황(2025-12 말)의 춘천시 집계(읍·면·동 구분 수)만
  → external/resources/protective/경로당_시군집계.csv (원본 xlsx 는 raw/, git 추적 안 함)
- 춘천 쉼터: 미수집 (행안부 쉼터 API 는 재난안전데이터공유플랫폼 키가 따로 필요. 키 없음) → "미수집"으로 표시

다시 만들기: uv run python -m bigcontest.resources.protective [--raw <xlsx>] [--check]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from functools import lru_cache
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources.match import load_needs

DIR = ROOT / "external" / "resources" / "protective"
RAW = DIR / "raw" / "gwd_노인여가복지시설현황_2025.xlsx"
COUNTS = DIR / "경로당_시군집계.csv"
NAMES = ROOT / "ref" / "adm_cd_names.csv"
RAW_SHA256 = "f2996a1dfc2ada542d7b1827cf04ed4303f655d67082d6ff20e8b41cef61d238"
RAW_PAGE = "https://www.data.go.kr/data/15160212/fileData.do"
RAW_DOWNLOAD = "https://state.gwd.go.kr/egf/bp/common/front/259988/download"
COUNT_COLS = ["city", "sigungu", "facility", "total", "eup", "myeon", "dong", "members", "base_date", "source_page",
              "source_download", "raw_sha256", "collected_date"]
NOTE = "보호 자원은 위험 표시가 아님: 그 동에 이미 있는 연결 자원. 등급·순위에 쓰지 않음"
MAX_ITEMS = 5
TYPE_COL = {"읍": "eup", "면": "myeon", "동": "dong"}


def config() -> dict:
    """configs/need_types.yaml 의 protective 칸."""
    return load_needs()["protective"]


# ------------------------------------------------------------ 춘천 경로당 시군 집계 (원본 xlsx → csv)
def build_counts(raw: Path = RAW, collected: str = "2026-10-14") -> pd.DataFrame:
    """강원 노인여가복지시설 현황 xlsx '경로당' 시트 → 춘천시 한 줄. 원본 해시가 다르면 멈춘다."""
    digest = hashlib.sha256(Path(raw).read_bytes()).hexdigest()
    if digest != RAW_SHA256:
        raise SystemExit(f"원본 해시가 다름: {digest} (기대 {RAW_SHA256}). 새 판이면 RAW_SHA256 과 README 를 같이 고칠 것")
    x = pd.read_excel(raw, sheet_name="경로당", header=None)
    head = str(x.iloc[1, 0])
    if "25. 12" not in head:
        raise SystemExit(f"기준일 표기가 예상과 다름: {head!r}")
    row = x[x[0].astype(str).str.strip() == "춘천"].iloc[0]

    def n(v) -> int:
        return 0 if pd.isna(v) else int(v)

    return pd.DataFrame([{
        "city": "춘천", "sigungu": "춘천시", "facility": "경로당", "total": n(row[1]), "eup": n(row[2]),
        "myeon": n(row[3]), "dong": n(row[4]), "members": n(row[5]), "base_date": "2025-12-31",
        "source_page": RAW_PAGE, "source_download": RAW_DOWNLOAD, "raw_sha256": digest, "collected_date": collected,
    }], columns=COUNT_COLS)


@lru_cache(maxsize=1)
def city_counts() -> dict[str, dict]:
    """도시 → 경로당 시군 집계 한 줄 (dict). 파일이 없으면 빈 dict."""
    if not COUNTS.exists():
        return {}
    with open(COUNTS, encoding="utf-8") as f:
        return {r["city"]: r for r in csv.DictReader(f)}


@lru_cache(maxsize=1)
def _adm() -> dict[str, dict]:
    with open(NAMES, encoding="utf-8") as f:
        return {r["adm_cd"]: r for r in csv.DictReader(f)}


# ------------------------------------------------------------ 보호 자원 칸
def _note(note, city: str) -> str | None:
    """묶음 설명. 도시마다 출처가 다르면 {도시: 설명}으로 적는다."""
    return note.get(city) if isinstance(note, dict) else note


def _item(r: dict, scope: str) -> dict:
    return {"resource_id": r["resource_id"], "name": r["name"], "subtype": r["subtype"],
            "address": r.get("address") or None, "phone": r.get("phone") or None,
            "schedule": r.get("schedule") if r.get("schedule") not in ("", "미상") else None,
            "holiday_open": r.get("holiday_open") or "미상", "source_url": r.get("verified_url") or r.get("source_url"),
            "last_verified": r.get("last_verified") or None, "scope": scope}


def protective_for(led: pd.DataFrame, city: str, adm_cd: str | None = None) -> dict:
    """도시(+동)의 보호 자원 칸. 묶음마다: 그 동 안 자원 수와 앞 몇 개, 도시 단위 안내, 도시 집계, 수집 상태.

    - 동 안 자원: 원장 adm_cd 가 그 동인 시설 (경로당 등)
    - 도시 단위: 행정동이 없는 안내·사람 자원 (쉼터 목록 페이지, 돌봄단·통장)
    - 춘천 경로당: 원장에 없어 시군 집계의 같은 유형(읍·면·동) 수만 (동별 목록 미수집)
    - 아무것도 없고 pending 에 적힌 묶음은 '미수집'
    """
    cfg = config()
    adm = _adm().get(adm_cd or "", {})
    sub = led[led["city"] == city]
    groups = []
    for name, spec in cfg["groups"].items():
        rows = sub[sub["subtype"].isin(spec.get("subtypes", []))]
        if "operating_status" in rows:
            rows = rows[rows["operating_status"] != "중단"]
        in_dong = rows[rows["adm_cd"] == adm_cd] if adm_cd else rows.iloc[0:0]
        city_level = rows[rows["adm_cd"].fillna("") == ""]
        spatial = rows[rows["adm_cd"].fillna("") != ""]
        # 동 안 수는 행정동이 붙은 자원이 있는 묶음만 (도시 단위 안내뿐이면 '0곳'이 아니라 모름)
        g = {"group": name, "label": spec.get("label", name),
             "in_dong": int(len(in_dong)) if adm_cd and len(spatial) else None,
             "city_total": int(len(spatial)) if len(spatial) else None,
             "items": [_item(r, "동") for r in in_dong.head(MAX_ITEMS).to_dict("records")]
             + [_item(r, "도시") for r in city_level.to_dict("records")],
             "status": "수집", "note": _note(spec.get("note"), city)}
        cc = city_counts().get(city) if spec.get("city_counts") else None
        if len(spatial) == 0 and cc and cc.get("facility") == spec.get("city_counts"):
            g["city_total"] = int(cc["total"])
            g["status"] = "도시 집계만"
            kind = adm.get("adm_type")
            part = f", 그중 {kind} 지역 {cc[TYPE_COL[kind]]}곳" if kind in TYPE_COL else ""
            g["note"] = (f"{cc['sigungu']} {spec.get('label', name)} {cc['total']}곳{part} "
                         f"(강원 노인여가복지시설 현황 {cc['base_date']} 기준). 동별 목록은 공개 자료에서 찾지 못함")
            g["in_dong"] = None
        pend = (cfg.get("pending") or {}).get(city, {}).get(name)
        if pend and not g["items"] and g["city_total"] is None:
            g["status"] = "미수집"
            g["note"] = pend
        elif pend:
            g["note"] = "; ".join(x for x in [g["note"], pend] if x)
        groups.append(g)
    return {"city": city, "adm_cd": adm_cd, "adm_nm": adm.get("adm_nm"), "groups": groups, "note": cfg.get("note", NOTE)}


@lru_cache(maxsize=2)
def _read_ledger(path: str, mtime_ns: int, size: int) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def ledger_v2() -> pd.DataFrame:
    """원장 v2 (파일이 바뀌면 다시 읽음). 읽기 전용으로 쓸 것."""
    import os

    from bigcontest.resources.ledger import V2

    st = os.stat(V2)
    return _read_ledger(str(V2), st.st_mtime_ns, st.st_size)


def protective_view(city: str, adm_cd: str | None = None) -> dict:
    """API 용: 지금 원장 v2 로 보호 자원 칸."""
    return protective_for(ledger_v2(), city, adm_cd)


def main(argv=None):
    ap = argparse.ArgumentParser(description="보호 자원: 춘천 경로당 시군 집계를 원본 xlsx 에서 다시 만듦")
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--check", action="store_true", help="지금 파일과 비교만")
    a = ap.parse_args(argv)
    new = build_counts(a.raw)
    old = pd.read_csv(COUNTS, dtype=str, keep_default_na=False) if COUNTS.exists() else None
    same = old is not None and new.astype(str).drop(columns=["collected_date"]).equals(
        old.drop(columns=["collected_date"]))
    print(f"{COUNTS.relative_to(ROOT)} 와 같음: {same}")
    if a.check:
        return same
    if not same:
        new.to_csv(COUNTS, index=False, encoding="utf-8")
        print("썼음:", new.to_dict("records"))
    return same


if __name__ == "__main__":
    main()
