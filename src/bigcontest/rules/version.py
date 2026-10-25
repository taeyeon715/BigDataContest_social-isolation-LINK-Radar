"""어느 규칙 판으로 돌리나 (에이전트 기본 rules-v2.0).

v1.1 결과를 다시 만드는 노트북(R-1·R-2·T1.5·S-1·A-1)은 맨 위에서 `LINKRADAR_RULES=v1.1` 을 정해
예전 카드 이탈 기준(z −2.5, 7일 중 3일, 최소 하락 10%)을 그대로 쓴다. 그 밖은 rules_v2.yaml deviation
(LINK-Sim 으로 고른 z −2.0, 7일 중 4일, 최소 하락 5%).
"""

from __future__ import annotations

import os

ENV = "LINKRADAR_RULES"


def active() -> str:
    return "v1.1" if os.environ.get(ENV, "v2.0") == "v1.1" else "v2.0"


def level_version() -> str:
    """등급 규칙 판 이름 (rules-v2.0, LINKRADAR_RULES=v1.1 이면 rules-v1.1)."""
    if active() == "v1.1":
        from bigcontest.rules import rules_version

        return rules_version()
    from bigcontest.rules.levels_v2 import load_rules_v2

    return load_rules_v2()["version"]


def alarm_config():
    """카드 이탈 경보 설정 (signals.alarm.AlarmConfig)."""
    from bigcontest.signals.alarm import AlarmConfig

    if active() == "v1.1":
        return AlarmConfig()
    from bigcontest.rules.levels_v2 import load_rules_v2

    d = load_rules_v2()["deviation"]
    return AlarmConfig(z_thr=float(d["z_thr"]), k=int(d["k"]))


def deviation_min_drop(v11: float) -> float:
    """이탈 크기 최소값. v1.1 이면 rules.yaml 값(v11) 그대로."""
    if active() == "v1.1":
        return v11
    from bigcontest.rules.levels_v2 import load_rules_v2

    return float(load_rules_v2()["deviation"]["min_drop"])
