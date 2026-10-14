"""공휴일 목록의 공식 출처를 남기고, configs/project.yaml 의 목록과 맞는지 확인한다.

출처: 한국천문연구원 천문우주지식정보 「2025년 월력요항」 페이지
  https://astro.kasi.re.kr/kor/life/post/calendarData?year=2025
  (월력요항은 우주항공청이 천문법에 따라 관보에 게재하는 달력 기준 자료. 이 페이지가 그 표를 그대로 싣는다)
  - 공공데이터포털 '한국천문연구원_특일 정보'(15012690) API 는 지금 키로 403 (활용 신청 안 됨). 신청되면 그 응답으로 바꿀 수 있다.
  - 월력요항은 전해에 나온다. 그 뒤에 정한 임시공휴일은 들어 있지 않다. 2025-07~12 사이 임시공휴일은 따로 확인하지 못했다
    (관보 확인 필요). 2025년의 임시공휴일(1월 27일)·선거일(6월 3일)은 분석 기간 밖이다.

실행:
  uv run python -m bigcontest.holidays            # 페이지를 받아 raw 에 저장하고 비교
  uv run python -m bigcontest.holidays --offline  # raw 에 남은 페이지로 비교만
결과: external/holidays/공휴일_2025-07_12.csv (git 추적), 원문 페이지는 external/holidays/raw/ (git 추적 안 함)
"""

from __future__ import annotations

import argparse
import html
import re
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from bigcontest.config import ROOT

URL = "https://astro.kasi.re.kr/kor/life/post/calendarData?year=2025"
OUT = ROOT / "external" / "holidays"
RAW = OUT / "raw" / "kasi_calendarData_2025.html"
CSV = OUT / "공휴일_2025-07_12.csv"
PROJECT = ROOT / "configs" / "project.yaml"
YEAR = 2025
NAMES = ["광복절", "개천절", "추석", "대체공휴일(추석)", "한글날", "기독탄신일"]  # 2025-07~12 에 드는 공휴일 이름


def fetch(path: Path = RAW) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        path.write_bytes(r.read())
    return path


def _text(path: Path) -> str:
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def parse(path: Path = RAW) -> pd.DataFrame:
    """월력요항 '국경일과 공휴일' 표 → 날짜·이름 (2025-07~12). 기간은 하루씩 펼친다."""
    s = _text(path)
    rows = []
    for name in NAMES:
        m = re.search(re.escape(name) + r"\s*\*?\s*(\d+)월\s*(\d+)일(?:\s*~\s*(\d+)월\s*(\d+)일)?", s)
        if not m:
            raise ValueError(f"페이지에서 찾지 못함: {name}")
        a = pd.Timestamp(YEAR, int(m.group(1)), int(m.group(2)))
        b = pd.Timestamp(YEAR, int(m.group(3)), int(m.group(4))) if m.group(3) else a
        for d in pd.date_range(a, b):
            rows.append({"date": d.strftime("%Y-%m-%d"), "name": name})
    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    out["source"] = "한국천문연구원 천문우주지식정보 2025년 월력요항"
    out["source_url"] = URL
    return out


def project_holidays(path: Path = PROJECT) -> list[str]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return sorted(str(k) for k in cfg["holidays"])


def check(table: pd.DataFrame) -> dict:
    official = set(table["date"])
    mine = set(project_holidays())
    return {"same": official == mine, "only_official": sorted(official - mine), "only_config": sorted(mine - official)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="공휴일 공식 출처 확인")
    ap.add_argument("--offline", action="store_true", help="raw 에 남은 페이지로")
    a = ap.parse_args(argv)
    if not a.offline:
        fetch()
    t = parse()
    t["checked_at"] = datetime.now().strftime("%Y-%m-%d")
    r = check(t)
    print(t[["date", "name"]].to_string(index=False))
    print("configs/project.yaml 과 같음:", r)
    if r["same"]:
        prev = pd.read_csv(CSV, dtype=str) if CSV.exists() else None
        if prev is None or not prev[["date", "name"]].equals(t[["date", "name"]]):
            t.to_csv(CSV, index=False, encoding="utf-8")


if __name__ == "__main__":
    main()
