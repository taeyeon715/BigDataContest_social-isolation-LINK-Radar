"""외부 원본 파일 → 레포의 추출본 (주민등록 47개 동, 서울 시민생활 강남).

실행: uv run python -m bigcontest.extract_external --resident <폴더> --seoul <폴더> [--write]
  기본은 레포 파일과 비교만 한다 (같으면 "같음"). --write 일 때만 덮어쓴다.

원본 (레포에 없음. 제출 묶음 02_외부데이터 의 원본/ 폴더에 있음)
- 주민등록 (행정안전부, 공공데이터포털 15097972·15097974 '과거 데이터'), 월말 전국 파일 6개씩
    mois_15097972_YYYYMMDD.csv  행정동 성·1세 연령별 주민등록 인구 (cp949)
    mois_15097974_YYYYMMDD.csv  행정동 세대원수별 세대수 (cp949)
  → 행정기관코드 앞 5자리 11680(강남구)·51110(춘천시)만, 끝에 '기준'(YYYYMMDD) 칸을 붙여 월 순서로 이어 붙임
- 서울 시민생활 (서울 열린데이터광장 OA-22266·OA-22267), 월별 xlsx 6개씩
    2025.{월}월_10개 관심집단 수.xlsx, 2025.{월}월_29개 통신정보.xlsx
  → 행정동코드가 1123(강남구)으로 시작하는 행만, 끝에 'month' 칸. 10개 관심집단은 'adm_cd7'(행정동코드 글자) 칸도 붙임
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT

MONTH_ENDS = ["20250731", "20250831", "20250930", "20251031", "20251130", "20251231"]
RESIDENT = {
    "15097972": ROOT / "external" / "resident" / "주민등록_성연령별인구_2025-07_12.csv",
    "15097974": ROOT / "external" / "resident" / "주민등록_세대원수별세대수_2025-07_12.csv",
}
SGG = ["11680", "51110"]  # 행안부 10자리 코드 앞 5자리: 강남구, 춘천시
SEOUL = {
    "10개 관심집단 수": ROOT / "external" / "seoul_living" / "시민생활_10개관심집단_강남_2025-07_12.csv",
    "29개 통신정보": ROOT / "external" / "seoul_living" / "시민생활_29개통신정보_강남_2025-07_12.csv",
}


def resident(src: Path, dataset: str) -> pd.DataFrame:
    frames = []
    for d in MONTH_ENDS:
        x = pd.read_csv(Path(src) / f"mois_{dataset}_{d}.csv", encoding="cp949", dtype={"행정기관코드": str})
        x = x[x["행정기관코드"].str[:5].isin(SGG)].copy()
        x["기준"] = d
        frames.append(x)
    return pd.concat(frames, ignore_index=True)


def seoul_living(src: Path, kind: str) -> pd.DataFrame:
    frames = []
    for m in range(7, 13):
        x = pd.read_excel(Path(src) / f"2025.{m}월_{kind}.xlsx")
        x = x[x["행정동코드"].astype(str).str.startswith("1123")].copy()
        x["month"] = m
        frames.append(x)
    out = pd.concat(frames)
    if kind == "10개 관심집단 수":
        out["adm_cd7"] = out["행정동코드"].astype(str)
    return out


def same_as(df: pd.DataFrame, path: Path, **read) -> bool:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf, **read).equals(pd.read_csv(path, **read))


def main(argv=None):
    ap = argparse.ArgumentParser(description="외부 원본 → 레포 추출본")
    ap.add_argument("--resident", type=Path, help="주민등록 월말 전국 파일 12개가 있는 폴더")
    ap.add_argument("--seoul", type=Path, help="시민생활 월별 xlsx 12개가 있는 폴더")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    jobs = []
    if a.resident:
        jobs += [(resident(a.resident, k), p, {"dtype": {"행정기관코드": str}}) for k, p in RESIDENT.items()]
    if a.seoul:
        jobs += [(seoul_living(a.seoul, k), p, {}) for k, p in SEOUL.items()]
    for df, path, read in jobs:
        same = same_as(df, path, **read)
        print(f"{path.relative_to(ROOT).as_posix()}: {len(df)}행, 레포 파일과 같음: {same}")
        if a.write:
            df.to_csv(path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
