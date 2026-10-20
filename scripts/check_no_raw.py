"""레포에 올라가는 리포트 CSV에 원천 값 열이 없는지 검사한다.

카드(USE_CNT·TS_AT 등 결제건수·금액)와 통신(체류량·FLOW_POP) 원천 값은 재배포할 수 없다.
리포트 CSV에는 비율·오차율·z·순위·지수 같은 파생 지표만 남긴다.

실행: uv run python scripts/check_no_raw.py [CSV 경로 ...]
인자가 없으면 git이 추적하는 reports/**/*.csv 와 analysis/**/*.csv(태연 분석 결과·외부 공공데이터)를 모두 본다. 금지 열이 하나라도 있으면 종료 코드 1.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 검사하는 폴더: 리포트와 태연 분석(analysis/). analysis/**/csv/ 같은 대회 원본 변환본은 .gitignore 라 git 목록에 없음
SCAN_DIRS = ("reports", "analysis")

# 열 이름이 정확히 이것이면 금지 (대소문자·앞뒤 공백 무시)
FORBIDDEN_EXACT = {
    # 카드 원천·원천 규모의 값
    "y", "yhat", "y_hat", "y_pred", "y_true", "actual", "pred", "prediction",
    "use_cnt", "use_amt", "ts_at", "amount", "amt", "cnt",
    "mae",  # 결제건수 단위 MAE: rel_mae와 함께 있으면 평균 결제건수를 되살릴 수 있음
    "mean_y", "total",
    # 통신 원천 값
    "체류량", "flow_pop", "flow_pop_cnt", "유동인구",
}
# 열 이름에 이 글자가 들어 있으면 금지 (원천 필드 이름)
FORBIDDEN_SUBSTR = ("use_cnt", "use_amt", "ts_at", "flow_pop", "tmst_", "mae(건)", "결제건수")


def forbidden(col: str) -> bool:
    c = col.strip().lstrip("﻿").lower()
    return c in FORBIDDEN_EXACT or any(s in c for s in FORBIDDEN_SUBSTR)


def header(path: Path) -> list[str]:
    # 외부 공공데이터 중 일부(행정안전부 주민등록 등)는 cp949
    for enc in ("utf-8-sig", "cp949"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return next(csv.reader(f), [])
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/cp949", b"", 0, 1, f"헤더를 읽을 수 없음: {path}")


def tracked_csvs(root: Path = ROOT) -> list[Path]:
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *SCAN_DIRS],
                             cwd=root, capture_output=True, check=True).stdout.decode("utf-8")
        files = [root / p for p in out.split("\0") if p.lower().endswith(".csv")]
    except (OSError, subprocess.CalledProcessError):
        files = sorted(p for d in SCAN_DIRS for p in (root / d).rglob("*.csv"))
    return [p for p in files if p.exists()]


def check(paths) -> list[tuple[Path, list[str]]]:
    bad = []
    for p in paths:
        cols = [c for c in header(Path(p)) if forbidden(c)]
        if cols:
            bad.append((Path(p), cols))
    return bad


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] if argv else tracked_csvs()
    missing = [p for p in paths if not p.exists()]
    if missing:
        print("파일 없음: " + ", ".join(str(p) for p in missing))
        return 2
    bad = check(paths)
    for p, cols in bad:
        try:
            shown = p.resolve().relative_to(ROOT)
        except ValueError:
            shown = p
        print(f"원천 값 열 발견: {shown}: {', '.join(cols)}")
    if bad:
        print(f"실패: {len(bad)}개 파일에 원천 값 열이 있습니다. 비율·지수 같은 파생 지표로 바꾸세요.")
        return 1
    print(f"통과: CSV {len(paths)}개에 원천 값 열 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
