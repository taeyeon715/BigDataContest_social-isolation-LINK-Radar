"""값 상태: 원본 0 / 비공개(`*`) / 결측 / 품질 의심을 다른 상태로 보존한다 (설계 §2.1).

숫자로 바꾸지 못한 값을 0으로 채우지 않는다.
"""

import math

from bigcontest.agent.schemas import ValueState

SUPPRESSED_MARKS = {"*", "＊", "-*"}
MISSING_MARKS = {"", "-", "nan", "NaN", "None", "null", "NULL"}


def parse_value(raw: object, quality_suspect: bool = False) -> tuple[float | None, ValueState]:
    """원본 칸 하나 → (숫자 또는 None, 값 상태)."""
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None, ValueState.MISSING
    text = str(raw).strip().replace(",", "")
    if text in SUPPRESSED_MARKS:
        return None, ValueState.SUPPRESSED
    if text in MISSING_MARKS:
        return None, ValueState.MISSING
    try:
        v = float(text)
    except ValueError:
        return None, ValueState.MISSING
    if math.isnan(v):
        return None, ValueState.MISSING
    if quality_suspect:
        return v, ValueState.QUALITY_SUSPECT
    return v, (ValueState.ZERO if v == 0 else ValueState.OBSERVED)


def usable_for_stats(state: ValueState) -> bool:
    """통계 계산에 넣을 수 있는 상태. 품질 의심·비공개·결측은 빼고 따로 센다."""
    return state in (ValueState.OBSERVED, ValueState.ZERO)
