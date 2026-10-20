"""outputs/ 결과 파일을 reports/t1_2축위험도/ 로 복사한다 (LINK-Radar 리포트 목록용).

노트북은 그대로 outputs/ 에 쓰고, 이 스크립트로 복사본을 맞춘다. tests/test_t1_t2.py 가 둘이 같은지 본다.
실행: uv run python analysis/taeyeon_2axis/sync_reports.py
"""

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "outputs"
DST = HERE.parents[1] / "reports" / "t1_2축위험도"
KINDS = {".csv", ".json", ".md"}


def files():
    return sorted(p for p in SRC.iterdir() if p.suffix in KINDS)


def main():
    DST.mkdir(parents=True, exist_ok=True)
    for p in files():
        shutil.copyfile(p, DST / p.name)
        print("복사:", p.name)


if __name__ == "__main__":
    main()
