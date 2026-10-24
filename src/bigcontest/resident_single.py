"""행안부 주민등록 성·연령별 1인세대수 (행정동 × 월) 수집과 추출본 만들기. T1.5 집단별 S1 용.

출처: 행정안전부 주민등록 인구통계 https://jumin.mois.go.kr → 행정동 → '성/연령별 주민등록 1인세대수'
  (메뉴 sexdAge1Hshd.do?sttsGbn=admm, 'xlsx 파일 다운로드' → '전체읍면동현황'). 월말 기준, 5세 단위, 남·여,
  등록 구분 '전체'(거주자 + 거주불명자 + 재외국민). 공공데이터포털 15097972·15097974 와 같은 주민등록 자료다.
  1인세대의 연령·성은 그 세대주(= 혼자인 세대원)의 연령·성이다.

실행:
  uv run python -m bigcontest.resident_single            # 2025-07~12 받아 raw 에 저장 → 추출본과 비교 (다르면 --write 때만 씀)
  uv run python -m bigcontest.resident_single --offline  # raw 에 남은 xlsx 로 다시 만들어 비교만
결과: external/resident/주민등록_성연령별1인세대_2025-07_12.csv (git 추적, 강남·춘천 47개 동만)
      원문 xlsx 는 external/resident/raw/ (전국 파일, git 추적 안 함), 받은 기록은 external/resident/수집_기록_1인세대.csv
"""

from __future__ import annotations

import argparse
import hashlib
import io
import urllib.parse
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT

BASE = "https://jumin.mois.go.kr"
DOWN = BASE + "/sexdAge1HshdDown.do?searchYearMonth=month&xlsStats=3&downType=Xlsx"
OUT = ROOT / "external" / "resident"
RAW = OUT / "raw"
CSV = OUT / "주민등록_성연령별1인세대_2025-07_12.csv"
LOG = OUT / "수집_기록_1인세대.csv"
MONTHS = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
SGG = {"11680": ("1100000000", "1168000000"), "51110": ("5100000000", "5111000000")}  # 강남구, 춘천시
BANDS = [f"{a}~{a + 4}세" for a in range(0, 100, 5)] + ["100세이상"]
SEX = ["계", "남자", "여자"]


def raw_path(month: str) -> Path:
    return RAW / f"sexdAge1Hshd_{month.replace('-', '')}.xlsx"


def form(month: str) -> dict[str, str]:
    y, m = month.split("-")
    return {"sltOrgLvl1": "A", "sltOrgLvl2": "A", "sltUndefType": "", "searchYearStart": y, "searchMonthStart": m,
            "searchYearEnd": y, "searchMonthEnd": m, "sltArgTypes": "5", "sltArgTypeA": "0", "sltArgTypeB": "100",
            "sttsGbn": "admm", "sum": "sum", "gender": "gender", "category": "month"}


def fetch(month: str) -> Path:
    """한 달 전국 읍면동 xlsx 를 받아 raw 에 저장 (세션 쿠키가 필요해 화면을 먼저 연다)."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0")]
    op.open(BASE + "/sexdAge1Hshd.do?sttsGbn=admm", timeout=60).read()
    body = urllib.parse.urlencode(form(month)).encode()
    with op.open(DOWN, data=body, timeout=180) as r:
        data = r.read()
    if not data.startswith(b"PK"):
        raise ValueError(f"{month}: xlsx 가 아닌 응답 ({len(data)}바이트)")
    p = raw_path(month)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def parse(path: Path, month: str) -> pd.DataFrame:
    """전국 xlsx → 강남구·춘천시 행정동 행 (머리 4줄: 제목, 기준월, 계·남·여, 칸 이름)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d = pd.read_excel(path, header=None, dtype=str)
    want_month = f"{month[:4]}년{month[5:]}월"
    if want_month not in str(d.iloc[1, 2]).replace(" ", ""):
        raise ValueError(f"{path.name}: 기준월 칸 {d.iloc[1, 2]!r} 가 {month} 와 다름")
    head = [str(x).replace(" ", "") for x in d.iloc[3]]
    exp = ["행정기관코드", "행정기관"]
    for s in ["총", "남자", "여자"]:
        exp += [f"{s}세대수", f"{s}연령구간세대수"] + BANDS
    if head != exp:
        raise ValueError(f"{path.name}: 칸 구성이 예상과 다름: {head}")
    cols = ["행정기관코드", "행정기관"] + [f"{b}{s}" if b not in ("총", "연령구간합계") else f"{b}_{s}"
                                       for s in SEX for b in ["총", "연령구간합계"] + BANDS]
    body = d.iloc[4:].copy()
    body.columns = cols
    code = body["행정기관코드"].astype(str)
    body = body[code.str[:5].isin(SGG) & (code.str[5:] != "00000")].copy()
    name = body["행정기관"].str.split()
    out = pd.DataFrame({"행정기관코드": body["행정기관코드"].astype(str),
                        "기준연월": (pd.Period(month, "M").end_time.date()).isoformat(),
                        "시도명": name.str[0], "시군구명": name.str[1], "읍면동명": name.str[2:].str.join(" ")})
    for c in cols[2:]:
        v = pd.to_numeric(body[c].str.replace(",", "", regex=False), errors="raise")
        out[c] = v.astype(int)
    out = out.rename(columns={"총_계": "계", "총_남자": "남자", "총_여자": "여자"})
    out = out.drop(columns=[c for c in out.columns if c.startswith("연령구간합계_")])
    # 검산: 남 + 여 = 계, 5세 칸 합 = 성별 합
    assert (out["남자"] + out["여자"] == out["계"]).all(), "남+여 ≠ 계"
    for s in SEX:
        tot = out["계" if s == "계" else s]
        assert (out[[f"{b}{s}" for b in BANDS]].sum(axis=1) == tot).all(), f"{s} 연령 합 ≠ 총"
    return out.drop(columns=[f"{b}계" for b in BANDS]).reset_index(drop=True)


def build(months=MONTHS) -> pd.DataFrame:
    return pd.concat([parse(raw_path(m), m) for m in months], ignore_index=True)


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def same_as(df: pd.DataFrame, path: Path) -> bool:
    if not path.exists():
        return False
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf, dtype={"행정기관코드": str}).equals(pd.read_csv(path, dtype={"행정기관코드": str}))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="행안부 성·연령별 1인세대수 (강남·춘천 행정동)")
    ap.add_argument("--offline", action="store_true", help="raw 에 남은 xlsx 로만")
    ap.add_argument("--write", action="store_true", help="추출본이 다르거나 없으면 씀")
    a = ap.parse_args(argv)
    logs = []
    if not a.offline:
        for m in MONTHS:
            p = fetch(m)
            logs.append({"fetched_at": datetime.now().isoformat(timespec="seconds"), "month": m,
                         "source": "jumin.mois.go.kr sexdAge1HshdDown.do (행정동, 성/연령별 주민등록 1인세대수, 전체읍면동현황, 5세, 등록 구분 전체)",
                         "raw_file": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)})
    df = build()
    same = same_as(df, CSV)
    print(f"{CSV.name}: {len(df)}행, 기존 추출본과 {'같음' if same else '다름/없음'}")
    if logs:
        lg = pd.DataFrame(logs)
        lg["rows_47dong"] = lg["month"].map(df.groupby(df["기준연월"].str[:7]).size())
        lg["same_as_csv"] = "예" if same else ("아니오 → 새로 씀" if a.write else "아니오")
        lg.to_csv(LOG, mode="a", header=not LOG.exists(), index=False, encoding="utf-8")
    if not same and a.write:
        df.to_csv(CSV, index=False, encoding="utf-8-sig")
        print("씀:", CSV.relative_to(ROOT))
    return 0 if same or a.write else 1


if __name__ == "__main__":
    raise SystemExit(main())
