"""후보·등급 규칙. 규칙표는 configs/rules.yaml, 코드는 여기.

- levels: 축 사실(구조·접근성·트리거·이탈·반증·근거 신뢰도) → 등급 + rule_id (LLM 무관, 결정적)
- candidates: 구조 순위(도시 × 월 전체 모집단), 진입 규칙, event_id, 연휴 뒤 기대 경로, 검토 배정 순서, 대응 종류
- compare: 비교안 A~D (같은 날짜·도시·K에서 자료를 하나씩 더함)
- sensitivity: 임계값 ±, 지표 하나씩 빼기 → Top-K 겹침률·후보 수 범위
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from bigcontest.contracts import load_yaml

LEVEL_ORDER = {"Level 0": 0, "Level 1": 1, "Level 2": 2, "Level 3": 3}


@lru_cache
def _base() -> dict:
    return load_yaml("rules.yaml")


def load_rules(overrides: dict[str, Any] | None = None) -> dict:
    """규칙표. overrides 는 점 경로 → 값 (예: {"thresholds.structure.top_share": 0.2}). 민감도 분석용."""
    r = copy.deepcopy(_base())
    for path, value in (overrides or {}).items():
        node = r
        *head, last = path.split(".")
        for k in head:
            node = node[k]
        node[last] = value
    return r


def rules_version(rules: dict | None = None) -> str:
    return (rules or _base())["version"]


def legacy_thresholds(rules: dict | None = None) -> dict[str, float]:
    """예전 STUB_LEVEL_RULES 와 같은 열쇠 (다른 노드·실행 메타데이터가 씀)."""
    t = (rules or _base())["thresholds"]
    return {
        "structure_top_share": t["structure"]["top_share"],
        "structure_min": t["structure"]["abs_min"],
        "access_far_km": t["access"]["far_km"],
        "small_n": t["structure"]["small_n"],
        "deviation_min_drop": t["deviation"]["min_drop"],
        "holiday_path_days": t["holiday_path"]["long_path_days"],
    }
