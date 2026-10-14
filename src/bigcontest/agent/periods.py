"""질문 속 기간 말 → 기간 이름(label) → 기준일 기준 날짜 창. 규칙 하나로 정한다.

기간 이름 (LLM 스키마 enum 과 같은 값)
- 지난주: 기준일이 든 주(월~일)의 바로 앞 주 월~일
- 이번 주: 기준일이 든 주 월요일 ~ 기준일
- 지난달: 기준일 앞 달 1일 ~ 말일
- 이번 달: 기준일이 든 달 1일 ~ 기준일
- 최근 7일 / 최근 14일 / 최근 30일: 기준일 − (N−1)일 ~ 기준일
- 추석 전: 추석 연휴 첫날 전 7일. 추석 연휴: 연휴(공휴일·주말이 이어진 날). 추석 후: 연휴 끝 다음 날부터 7일
- 날짜를 적으면 그 날(2025-10-07, 10월 7일, 10/7), 두 날짜면 그 사이, 달만 적으면(10월) 그 달 1일~말일

창을 쓰는 법 (nodes.scope_request)
- 조회 경로: 이 창으로 자료를 본다. 창 끝이 기준일 뒤면 기준일까지로 자르고, 창 시작이 기준일 뒤면 보류
  (TIME_PERIOD_AFTER_AS_OF). 자료 기간(2025-07-01~12-31) 밖이면 보류 (TIME_PERIOD_OUT_OF_DATA)
- 조사 경로: 등급은 언제나 실행 기준일까지 자료로 정한다 (정기 감시와 같은 등급). 기간 말은 기록만 하고
  보고서 한계에 "질문 기간은 등급 계산에 쓰지 않음"을 적는다 ("추석 전에 점검" 은 기간이 아니라 할 일의 때)
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from functools import lru_cache

RELATIVE = ("지난주", "이번 주", "지난달", "이번 달", "최근 7일", "최근 14일", "최근 30일", "추석 전", "추석 연휴", "추석 후")
DATA_YEAR = 2025

_WORDS = (  # (정규식, 기간 이름). 앞에서부터 처음 맞는 것
    (r"지난\s*주|저번\s*주", "지난주"),
    (r"이번\s*주|금주", "이번 주"),
    (r"지난\s*달|저번\s*달|전월", "지난달"),
    (r"이번\s*달|이달|금월", "이번 달"),
    (r"최근\s*(?:일주일|1주일?|7일)", "최근 7일"),
    (r"최근\s*(?:2주일?|14일)", "최근 14일"),
    (r"최근\s*(?:한\s*달|1개월|30일)", "최근 30일"),
    (r"추석\s*(?:전|앞|직전|이전)", "추석 전"),
    (r"추석\s*(?:후|뒤|이후|직후|지나)|연휴\s*(?:뒤|후|이후)", "추석 후"),
    (r"추석\s*(?:연휴|때|기간|에|중)", "추석 연휴"),
)
_ISO = re.compile(r"(20\d\d)[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})")
_MD_KO = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MD_SLASH = re.compile(r"(?<![\d-])(\d{1,2})/(\d{1,2})(?![\d/])")
_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)")


def _d(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_period(text: str) -> str | None:
    """질문 → 기간 이름 (RELATIVE 중 하나, 또는 날짜 "2025-10-07" / "2025-10-01~2025-10-07" / 달 "2025-10")."""
    text = text or ""
    for pat, label in _WORDS:
        if re.search(pat, text):
            return label
    days: list[date] = []
    spans: list[tuple[int, int]] = []
    for rx, conv in ((_ISO, lambda m: _d(int(m[1]), int(m[2]), int(m[3]))),
                     (_MD_KO, lambda m: _d(DATA_YEAR, int(m[1]), int(m[2]))),
                     (_MD_SLASH, lambda m: _d(DATA_YEAR, int(m[1]), int(m[2])))):
        for m in rx.finditer(text):
            if any(m.start() < b and a < m.end() for a, b in spans):
                continue
            d = conv(m)
            if d:
                days.append(d)
                spans.append(m.span())
    if days:
        days = sorted(set(days))
        return str(days[0]) if len(days) == 1 else f"{days[0]}~{days[-1]}"
    m = _MONTH.search(text)
    if m and 1 <= int(m[1]) <= 12:
        return f"{DATA_YEAR}-{int(m[1]):02d}"
    return None


@lru_cache
def _holidays() -> dict[date, str]:
    from bigcontest.config import load_config

    return {date.fromisoformat(str(k)): v for k, v in (load_config().get("holidays") or {}).items()}


def chuseok_block() -> tuple[date, date] | None:
    """추석 연휴: 추석 날을 품고 공휴일·주말이 이어진 날들 (configs/project.yaml holidays)."""
    hol = _holidays()
    day = next((d for d, n in hol.items() if n == "추석"), None)
    if day is None:
        return None

    def off(d: date) -> bool:
        return d in hol or d.weekday() >= 5

    lo = hi = day
    while off(lo - timedelta(days=1)):
        lo -= timedelta(days=1)
    while off(hi + timedelta(days=1)):
        hi += timedelta(days=1)
    # 연휴 앞뒤에 붙은 주말만 있는 날은 빼고 공휴일까지로 (10/3 금 ~ 10/9 목)
    while lo not in hol:
        lo += timedelta(days=1)
    while hi not in hol:
        hi -= timedelta(days=1)
    return lo, hi


def _month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


def window(label: str, as_of: date) -> tuple[date, date] | None:
    """기간 이름 → (시작, 끝). 자르지 않은 날짜 (기준일·자료 기간 자르기는 쓰는 쪽에서)."""
    if label == "지난주":
        mon = as_of - timedelta(days=as_of.weekday() + 7)
        return mon, mon + timedelta(days=6)
    if label == "이번 주":
        return as_of - timedelta(days=as_of.weekday()), as_of
    if label == "지난달":
        first = as_of.replace(day=1) - timedelta(days=1)
        return first.replace(day=1), first
    if label == "이번 달":
        return as_of.replace(day=1), as_of
    if m := re.fullmatch(r"최근 (\d+)일", label):
        return as_of - timedelta(days=int(m[1]) - 1), as_of
    if label.startswith("추석"):
        blk = chuseok_block()
        if blk is None:
            return None
        lo, hi = blk
        if label == "추석 전":
            return lo - timedelta(days=7), lo - timedelta(days=1)
        if label == "추석 후":
            return hi + timedelta(days=1), hi + timedelta(days=7)
        return lo, hi
    if m := re.fullmatch(r"(\d{4}-\d\d-\d\d)(?:~(\d{4}-\d\d-\d\d))?", label):
        a = date.fromisoformat(m[1])
        return a, date.fromisoformat(m[2]) if m[2] else a
    if m := re.fullmatch(r"(\d{4})-(\d\d)", label):
        y, mo = int(m[1]), int(m[2])
        return date(y, mo, 1), _month_end(y, mo)
    return None
