"""춘천시 공식 경로당 목록을 고정 원본·검토된 좌표표에서 원장으로 만든다. 네트워크 호출 없음.

목록 등재·주소 일치는 현재 운영·휴일 개방·개별 이용 자격 확인과 다르다.
원본에 '주소다름'이 있는 시설은 좌표를 사용하지 않고 확인 필요로 남긴다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT

DIR = ROOT / "external/resources/chuncheon_centers"
LIST = DIR / "시설목록.csv"
COORDS = DIR / "주소좌표.csv"
SOURCE = "https://www.chuncheon.go.kr/new-welfare/life/senior/senior-center/"


def rows(columns: list[str], path: Path = LIST, coords_path: Path = COORDS) -> pd.DataFrame:
    """기존 원장과 같은 칸. 원문 주소·행정동을 보존하고 좌표는 정확히 일치한 검색어만 사용."""
    if not path.exists():
        return pd.DataFrame(columns=columns)
    from bigcontest.resources import ledger as L

    facilities = pd.read_csv(path, dtype=str, keep_default_na=False)
    if facilities["source_record_id"].duplicated().any():
        raise ValueError("경로당 원천 ID 중복")
    coords = pd.read_csv(coords_path, dtype=str, keep_default_na=False) if coords_path.exists() else pd.DataFrame()
    by_keyword = {r["keyword"]: r for r in coords.to_dict("records")}
    names = pd.read_csv(ROOT / "ref/adm_cd_names.csv", dtype=str).query("city == '춘천'").set_index("adm_nm")
    base_cols = list(L.read_source().columns)
    output = []
    for f in facilities.to_dict("records"):
        if f["adm_nm"] not in names.index:
            raise ValueError("경로당 행정동이 기준 코드에 없음")
        c = by_keyword.get(f["keyword"], {})
        reliable = c.get("status") == "일치" and c.get("lat") and c.get("lon") and not f["address_warning"]
        rid = "CCSC-" + hashlib.sha256(f["source_record_id"].encode()).hexdigest()[:12]
        r = dict.fromkeys(base_cols, "")
        r.update(rid=rid, name=f["name"], type="관계형성·여가", subtype="경로당", hub="True",
                 target_stage="노년", eligibility="만 65세 이상(회원제)", city="춘천", address=f["address"],
                 adm_cd=names.loc[f["adm_nm"], "adm_cd"], adm_nm=f["adm_nm"], dong_method="공식 목록 행정동",
                 lat=c["lat"] if reliable else "", lon=c["lon"] if reliable else "",
                 coord_source="도로명주소(juso) 건물 출입구" if reliable else "none",
                 phone=f["phone"], source_url=SOURCE, verified_date=f["collected_at"], collected_date=f["collected_at"],
                 src="chuncheon_senior_centers_2026_05")
        row = L.derive(pd.DataFrame([r], columns=base_cols)).iloc[0].to_dict()
        row.update(resource_id=rid, source_record_id=f["source_record_id"], verified_url=SOURCE,
                   page_collected_at=f["collected_at"], operating_status="미상", holiday_open="미상",
                   note="공식 목록 등재(2026-05). 현재 운영·휴일 개방·회원 가입·마을별 이용 범위는 개별 확인 필요."
                        + (" 원문 주소에 정정 주의 표시: 좌표 사용 보류." if f["address_warning"] else ""))
        output.append(row)
    return pd.DataFrame(output).reindex(columns=columns, fill_value="")
