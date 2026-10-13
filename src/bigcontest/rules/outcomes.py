"""현장 확인 결과 집계.

승인된 조사 실행마다 담당자가 동·집단 단위 건수만 적는다 (확인 가구 수, 연락됨, 부재, 신규 연계, 위기 발견).
개인 정보·메모는 받지 않는다.

작은 칸 규칙 (G-08 과 같은 10): 실행 하나의 건수는 그 실행 상세 화면에서만 그대로 보인다.
모아 보기(집계)에서는 합계가 10 미만인 칸을 숫자 대신 "10 미만"(None + small_cells)으로 내보내고,
그 칸이 들어간 비율도 내지 않는다.

이 집계는 다음 규칙 평가(트리거·사건별로 현장에서 실제로 확인된 비율 보기)에 입력으로 쓸 수 있다.
등급 계산에는 쓰지 않는다 (등급은 rules.yaml 과 근거로만).
"""

from __future__ import annotations

from typing import Iterable

SMALL_CELL = 10
COUNT_FIELDS = ("checked_households", "contacted", "absent", "new_links", "crises_found")
COUNT_KO = {"checked_households": "확인 가구", "contacted": "연락됨", "absent": "부재", "new_links": "신규 연계",
            "crises_found": "위기 발견"}
# 비율: (분자, 분모)
RATES = {"contact_rate": ("contacted", "checked_households"), "link_rate": ("new_links", "checked_households"),
         "crisis_rate": ("crises_found", "checked_households")}


def trigger_code_of(candidate: dict) -> str:
    """후보 → 트리거 코드 (사건 ID 의 둘째 칸: R1·R4·R6·W·DEV …). 사건 ID 가 없으면 트리거 문장 첫 낱말."""
    ev = candidate.get("event_id") or ""
    parts = ev.split(":")
    if len(parts) >= 3:
        return parts[1]
    if candidate.get("candidate_type") == "unexpected_activity_deviation":
        return "DEV"
    first = (candidate.get("trigger") or "").split(";")[0].strip().split(" ")[0]
    return first.split("-")[0] or "-"


def check_counts(c: dict) -> str | None:
    """건수 사이 관계 검사. 문제가 있으면 쉬운 문장."""
    total = c["checked_households"]
    if c["contacted"] + c["absent"] > total:
        return "연락됨 + 부재가 확인 가구 수보다 많습니다."
    if c["new_links"] > total or c["crises_found"] > total:
        return "신규 연계·위기 발견 수는 확인 가구 수보다 많을 수 없습니다."
    return None


def latest(records: Iterable[dict]) -> list[dict]:
    """(실행, 후보)마다 가장 나중에 저장된 기록만 (고쳐 적으면 새 기록이 앞 기록을 대신함).

    순서는 저장소가 붙인 저장 순서 seq 로만 정한다. created_at 은 표시용이라 같은 시각이 될 수 있고,
    outcome_id 의 무작위 끝자리로 순서를 정하면 고친 값이 이전 값으로 되돌아갈 수 있다. seq 가 없으면 오류."""
    out: dict[tuple, dict] = {}
    rows = list(records)
    if any(r.get("seq") is None for r in rows):
        raise ValueError("현장 결과 기록에 저장 순서(seq)가 없음")
    for r in sorted(rows, key=lambda r: int(r["seq"])):
        out[(r["run_id"], r["candidate_id"])] = r
    return list(out.values())


def summarize(records: Iterable[dict], by: str = "trigger", small_cell: int = SMALL_CELL) -> list[dict]:
    """도시 × 트리거(by=trigger) 또는 도시 × 트리거 × 사건(by=event)별 합계. 10 미만 칸은 None + small_cells."""
    if by not in ("trigger", "event"):
        raise ValueError("by 는 trigger / event")
    groups: dict[tuple, dict] = {}
    for r in latest(records):
        key = (r["city"], r["trigger_code"], r.get("event_id") if by == "event" else None)
        g = groups.setdefault(key, {"city": key[0], "trigger_code": key[1], "event_id": key[2],
                                    "runs": set(), "places": 0, **{f: 0 for f in COUNT_FIELDS}})
        g["runs"].add(r["run_id"])
        g["places"] += 1
        for f in COUNT_FIELDS:
            g[f] += int(r[f])
    rows = []
    for key in sorted(groups, key=lambda k: (k[0], k[1], k[2] or "")):
        g = groups[key]
        row = {"city": g["city"], "trigger_code": g["trigger_code"], "event_id": g["event_id"],
               "runs": len(g["runs"]), "places": g["places"], "small_cells": []}
        for f in COUNT_FIELDS:
            small = g[f] < small_cell
            row[f] = None if small else g[f]
            if small:
                row["small_cells"].append(f)
        for name, (num, den) in RATES.items():
            row[name] = (round(g[num] / g[den], 3) if row[num] is not None and row[den] is not None and g[den] else None)
        rows.append(row)
    return rows
