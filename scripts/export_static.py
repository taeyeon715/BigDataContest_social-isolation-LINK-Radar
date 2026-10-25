"""정적 배포용 첫 화면 JSON (Vercel). 실제 에이전트(정기 감시 그래프)를 기준일마다 돌려 두 도시 결과를 저장한다.

    uv run python scripts/export_static.py            # 기본 기준일 전부 (약 20분)
    uv run python scripts/export_static.py 2025-09-26 # 몇 날짜만 (index.json 은 있는 파일로 다시 씀)

- 결과: web/public/data/home/<기준일>_<GN|CC>.json (api/services/home.py build_home 과 같은 모양), index.json (기준일 목록)
- 원자료는 싣지 않는다: 후보·등급·근거 문장·자원 이름 같은 집계 결과만 (대회 자료 재배포 금지, NOTICE.md)
- 기본 기준일: 2025-07-01 부터 7일마다 + 시연 기준일(연휴 7일 전 등). 화면의 '오늘 기준'은 작년 같은 날 이전의
  가장 가까운 기준일을 쓴다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "web" / "public" / "data" / "home"
CITIES = ("강남", "춘천")
CODE = {"강남": "GN", "춘천": "CC"}  # 파일 이름은 영문 코드 (배포 주소 인코딩 문제 피함)
EXTRA = ("2025-08-08", "2025-09-26", "2025-10-13", "2025-12-20")  # 광복절 7일 전, 추석 7일 전·뒤, 동절기


def default_dates() -> list[str]:
    d, end, out = date(2025, 7, 1), date(2025, 12, 31), set(EXTRA)
    while d <= end:
        out.add(d.isoformat())
        d += timedelta(days=7)
    return sorted(out)


def export_day(d: str) -> None:
    from api.services.dashboard_real import rules_version
    from api.services.home import build_home
    from bigcontest.agent.graph import build_graph
    from bigcontest.agent.schemas import Candidate, ToolResult
    from bigcontest.agent.state import initial_state
    from bigcontest.agent.tools.common import city_of_evidence

    st = build_graph().invoke(initial_state(f"static-{d}", "monitoring", d, tools_backend="real"),
                              {"configurable": {"thread_id": f"static-{d}"}})
    evidence = [ToolResult.model_validate(e) for e in st.get("evidence") or []]
    cands = [Candidate.model_validate(c) for c in st.get("candidates") or []]
    for city in CITIES:
        home = build_home(date.fromisoformat(d), city, [c for c in cands if c.city == city],
                          [e for e in evidence if (city_of_evidence(e) or city) == city], st["snapshot_id"],
                          rules_version(), int(st.get("selection_budget") or 0))
        (OUT / f"{d}_{CODE[city]}.json").write_text(home.model_dump_json(), encoding="utf-8")


def write_index() -> None:
    from api.services.dashboard_real import rules_version

    days = sorted({p.stem.split("_")[0] for p in OUT.glob("*_*.json")})
    snap = json.loads((OUT / f"{days[0]}_{CODE[CITIES[0]]}.json").read_text(encoding="utf-8"))["snapshot_id"] if days else None
    (OUT / "index.json").write_text(json.dumps({
        "dates": days, "cities": list(CITIES), "codes": CODE, "snapshot_id": snap, "rule_version": rules_version(),
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "note": "실제 2025 하반기 자료로 정기 감시를 돌린 집계 결과. 원자료는 포함하지 않음",
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    days = argv or default_dates()
    for i, d in enumerate(days, 1):
        export_day(d)
        print(f"[{i}/{len(days)}] {d}", flush=True)
    write_index()


if __name__ == "__main__":
    main(sys.argv[1:])
