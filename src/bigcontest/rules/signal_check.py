"""신호 방향 확인표 (configs/signal_check.csv, notebooks/r3c_signal_check.py 가 만듦).

강남 시민생활 기준으로 성·연령마다 H·S 신호의 방향을 확인한 결과. '방향 미확인' 신호는 그 성·연령 칸에서
Level 3 근거로 세지 않는다 (rules_v2.yaml signal_check). 춘천은 같은 성·연령의 강남 결과를 쓴다.
"""

from __future__ import annotations

import csv
from functools import lru_cache

from bigcontest.config import ROOT

FILE = ROOT / "configs" / "signal_check.csv"
AXIS_KO = {"H": "행동 고립", "S": "1인 비율"}


@lru_cache
def table() -> dict[tuple[str, str, str], dict]:
    if not FILE.exists():
        return {}
    with open(FILE, encoding="utf-8") as f:
        return {(r["axis"], r["sex"], r["age5"]): r for r in csv.DictReader(f)}


def unverified(sex: str, age5: str) -> list[str]:
    """그 성·연령에서 방향이 확인되지 않은 신호 (H·S 중)."""
    return [a for a in AXIS_KO if (r := table().get((a, sex, age5))) and r["status"] != "확인"]


def note(axis: str, sex: str, age5: str, city: str) -> str | None:
    """화면에 펼쳐 보일 한 줄. 확인된 신호면 None."""
    r = table().get((axis, sex, age5))
    if not r or r["status"] == "확인":
        return None
    borrowed = " 춘천은 강남 결과를 빌려 씀." if city == "춘천" else ""
    return (f"{AXIS_KO[axis]}: 강남 {r['n_dong']}개 동 시민생활 '외출·소통 모두 적음'과 방향이 확인되지 않음 "
            f"(순위상관 {float(r['rho']):+.2f}, p {float(r['p']):.2f}).{borrowed}")
