"""폭염 특보 단계 (R4). 기상청 여름철 체감온도 식 + 특보 기준 (configs/rules.yaml thresholds.heat).

체감온도 (기상청, 2022년 여름철부터 쓰는 식. 기온 Ta ℃, 상대습도 RH %):
    Tw = Ta·atan(0.151977·(RH + 8.313659)^0.5) + atan(Ta + RH) − atan(RH − 1.67633)
         + 0.00391838·RH^1.5·atan(0.023101·RH) − 4.686035          (습구온도, Stull 2011)
    체감온도 = −0.2442 + 0.55399·Tw + 0.45535·Ta − 0.0022·Tw² + 0.00278·Tw·Ta + 3.0

일 최고 체감온도는 기상청이 매시 값으로 낸다. 우리는 ASOS **일자료**만 있어 (일 최고기온, 일 최소 상대습도)로
근사한다. 최고기온이 나는 오후에 습도가 가장 낮은 경우가 많아서다. 일 평균 습도로 낸 값은 위쪽 근사(더 높게 나옴)로
함께 남긴다 (external/weather/kma_asos_heat_2025-07_12.csv).

특보 단계 (2026 기준, 보도 확인):
- 폭염주의보: 일 최고 체감온도 33℃ 이상이 2일 이상 이어질 것으로 예상
- 폭염경보: 일 최고 체감온도 35℃ 이상이 2일 이상 이어질 것으로 예상
- 폭염중대경보(2026-06 신설): 체감온도 38℃ 이상 또는 기온 39℃ 이상이 하루라도 예상
과거 재생에는 예보 기록이 없어, 기준일·다음 날 실측을 "예상" 대신 쓰는 상황 시연(signal.weather_triggers)에서만 단계를 낸다.
"""

from __future__ import annotations

import math

STAGES = ("폭염주의보", "폭염경보", "폭염중대경보")


def wet_bulb(ta: float, rh: float) -> float:
    """Stull(2011) 습구온도 (℃)."""
    return (ta * math.atan(0.151977 * (rh + 8.313659) ** 0.5) + math.atan(ta + rh) - math.atan(rh - 1.67633)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def apparent_temp(ta: float | None, rh: float | None) -> float | None:
    """기상청 여름철 체감온도 (℃). 값이 없으면 None."""
    if ta is None or rh is None or (isinstance(ta, float) and math.isnan(ta)) or (isinstance(rh, float) and math.isnan(rh)):
        return None
    tw = wet_bulb(float(ta), float(rh))
    return -0.2442 + 0.55399 * tw + 0.45535 * ta - 0.0022 * tw ** 2 + 0.00278 * tw * ta + 3.0


def heat_stage(days: list[tuple[float | None, float | None]], rules: dict | None = None) -> str | None:
    """연속한 날들 [(일 최고 체감온도, 일 최고기온)] (첫째 = 기준일) → 특보 단계 이름 또는 None.

    중대경보는 하루라도, 경보·주의보는 min_days(2)일 연속일 때. 높은 단계부터 본다."""
    from bigcontest.rules import load_rules

    h = (rules or load_rules())["thresholds"]["heat"]
    n = h["min_days"]
    at = [a for a, _ in days]
    ta = [t for _, t in days]
    if any((a is not None and a >= h["severe_apparent"]) or (t is not None and t >= h["severe_temp"])
           for a, t in zip(at, ta)):
        return "폭염중대경보"
    for name, thr in (("폭염경보", h["warning"]), ("폭염주의보", h["advisory"])):
        run = 0
        for a in at:
            run = run + 1 if a is not None and a >= thr else 0
            if run >= n:
                return name
    return None
