"""부가 데이터 정의서 (docs/부가데이터_정의.csv) 누락 검사.

- 칸 13개 (04_데이터 4-2 형식): item_id, name, kind, definition, inputs, grain, type_range, missing_rule, code, version,
  used_by, can_say_cannot_say, status
- 도구가 만드는 지표 이름(ToolResult.metric)은 모두 정의서 name 에 있어야 한다 ({…} 자리는 틀로 맞춤).
  지표 이름은 tests/metric_scan.py 가 코드에서 정적으로 모은다. 새 지표를 만들면 정의서에 한 줄을 더할 것
- 값 상태(ValueState)·원장 상태 칸도 모두 정의서에 있어야 한다
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from bigcontest.agent.schemas import ValueState
from bigcontest.resources import ledger as L

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from metric_scan import normalize, tool_metrics  # noqa: E402

CSV = ROOT / "docs" / "부가데이터_정의.csv"
COLS = ["item_id", "name", "kind", "definition", "inputs", "grain", "type_range", "missing_rule", "code", "version",
        "used_by", "can_say_cannot_say", "status"]
KINDS = {"정리 칸", "지표", "플래그", "기대값", "코호트 정의", "규칙 값", "자원 칸", "값 상태", "값 규칙"}


def rows() -> list[dict]:
    with open(CSV, encoding="utf-8") as f:
        r = csv.DictReader(f)
        assert r.fieldnames == COLS
        return list(r)


def test_shape_and_ids():
    rs = rows()
    assert len(COLS) == 13
    ids = [r["item_id"] for r in rs]
    assert len(ids) == len(set(ids)), "item_id 중복"
    for r in rs:
        assert r["kind"] in KINDS, r
        for c in ("name", "definition", "code", "status"):
            assert r[c].strip(), (r["item_id"], c)


def test_every_tool_metric_is_defined():
    found, unresolved = tool_metrics()
    assert not unresolved, f"metric 값을 코드에서 못 읽음 (metric_scan 을 고치거나 상수로): {unresolved}"
    assert len(found) > 20
    names = {normalize(r["name"]) for r in rows()}
    missing = sorted(m for m in found if m not in names)
    assert not missing, f"정의서에 없는 지표 (docs/부가데이터_정의.csv 에 한 줄씩 더할 것): {missing}"


def test_value_states_and_ledger_columns_are_defined():
    names = {r["name"] for r in rows()}
    assert {v.value for v in ValueState} <= names
    assert set(L.NEW_COLS) <= names, set(L.NEW_COLS) - names
