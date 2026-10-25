"""R-3d 기대효과 숫자 (rules-v2.0, 실제 2025 자료, 정기 감시와 같은 그래프).

기준일마다 두 도시 정기 감시를 돌려 후보 수(등급별)와 Level 3 칸의 규모를 센다.
- Level 3 칸 = 연휴 주의 칸 근거(get_holiday_watch) + 동절기 칸 근거 중 Level 3 (get_winter_cells). 칸 = 동 × 성 × 60대 이상
- 분모 = 같은 기준일 칸 축(cell_axes.axes_at)의 도시 전체 60대 이상 칸: 주민등록 인구, 1인세대(혼자인 세대주)
- '검토 대상 축소'는 도시 전체 60대 이상 칸 중 먼저 볼 칸의 비율이다. 업무시간 절감·고립 발견 실적이 아니다.
결과: reports/r3_규칙개정/기대효과_v2.csv
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from bigcontest.agent.graph import build_graph
from bigcontest.agent.schemas import ToolResult
from bigcontest.agent.state import initial_state
from bigcontest.agent.tools import signal as S
from bigcontest.agent.tools.cell_axes import axes_at
from bigcontest.agent.tools.common import city_of_evidence
from bigcontest.agent.tools.datasource import dev_snapshot_id, open_datasource
from bigcontest.config import ROOT

OUT = ROOT / "reports" / "r3_규칙개정"
DAYS = {"2025-09-26": "추석 7일 전", "2025-10-13": "추석 뒤", "2025-12-20": "동절기"}
MODE = "simulated_replay"
_ADM = re.compile(r"\((\d{8})\)")


def l3_cells(evidence: list[ToolResult], city: str) -> set[tuple[str, str]]:
    """(동, 성) Level 3 칸. 연휴 주의 칸은 모두 Level 3, 동절기 칸은 경고 첫 줄이 'Level 3'."""
    out = set()
    for e in evidence:
        if e.status != "OK" or city_of_evidence(e) != city:
            continue
        if e.metric == S.WATCH_METRIC or (e.metric == S.WINTER_CELL_METRIC and e.warnings[0].startswith("Level 3")):
            who = e.applied_scope.split(") ")[-1]  # '여성 60대 이상'
            out.add((_ADM.search(e.applied_scope).group(1), who[0]))
    return out


def main() -> pd.DataFrame:
    ds = open_datasource(dev_snapshot_id())
    rows = []
    for d, label in DAYS.items():
        st = build_graph().invoke(initial_state(f"impact-{d}", "monitoring", d, tools_backend="real"),
                                  {"configurable": {"thread_id": f"impact-{d}"}})
        evidence = [ToolResult.model_validate(e) for e in st["evidence"]]
        ax = axes_at(ds, date.fromisoformat(d), MODE).df
        old = ax[ax["age5"] == "60대+"]
        for city in ("강남", "춘천"):
            cands = [c for c in st["candidates"] if c["city"] == city]
            base = old[old["city"] == city]
            cells = l3_cells(evidence, city)
            pick = base[[(a, s) in cells for a, s in zip(base["adm_cd"], base["sex"])]]
            lv = pd.Series([c["candidate_level"] for c in cands]).value_counts()
            rows.append({
                "as_of": d, "기준일": label, "city": city,
                "후보": len(cands), "Level 3 후보": int(lv.get("Level 3", 0)), "Level 2 후보": int(lv.get("Level 2", 0)),
                "Level 1 후보": int(lv.get("Level 1", 0)), "판단 보류": int(lv.get("판단 보류", 0)),
                "검토 예산 K": int(st.get("selection_budget") or 0),
                "동 수": base["adm_cd"].nunique(), "Level 3 동": pick["adm_cd"].nunique(),
                "60대 이상 칸": len(base), "Level 3 칸": len(pick),
                "60대 이상 인구": int(base["pop"].sum()), "Level 3 칸 인구": int(pick["pop"].sum()),
                "60대 이상 1인세대": int(base["one_person"].sum()), "Level 3 칸 1인세대": int(pick["one_person"].sum()),
            })
    df = pd.DataFrame(rows)
    df["칸 비율"] = (df["Level 3 칸"] / df["60대 이상 칸"]).round(4)
    df["인구 비율"] = (df["Level 3 칸 인구"] / df["60대 이상 인구"]).round(4)
    df["1인세대 비율"] = (df["Level 3 칸 1인세대"] / df["60대 이상 1인세대"]).round(4)
    df["1인세대 비율 / 인구 비율"] = (df["1인세대 비율"] / df["인구 비율"]).round(2)
    df.to_csv(OUT / "기대효과_v2.csv", index=False, encoding="utf-8-sig")
    return df


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(main().to_string(index=False))
