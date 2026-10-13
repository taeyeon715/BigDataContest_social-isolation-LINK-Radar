"""스키마 테스트: 에이전트 설계 §5.1·§2 와 필드 이름이 같은지, API 명세 예시가 모델을 통과하는지."""

import json
import re
from datetime import date
from typing import get_args

import pytest
from pydantic import ValidationError

from bigcontest.agent import schemas
from bigcontest.agent.schemas import (
    Candidate,
    GuardResult,
    NormalizedRequest,
    RunState,
    ToolResult,
)
from bigcontest.config import ROOT
from bigcontest.contracts import grain_contract

DOCS = ROOT / "docs"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _first_col_names(table: str) -> set[str]:
    names = set()
    for line in table.splitlines():
        if line.startswith("| `"):
            first = line.split("|")[1]
            names |= set(re.findall(r"`([a-z_]+)`", first))
    return names


def test_tool_result_fields_match_agent_design():
    text = (DOCS / "에이전트_설계.md").read_text(encoding="utf-8")
    spec = _first_col_names(_section(text, "### 5.1", "### 5.2"))
    fields = set(ToolResult.model_fields)
    assert spec - fields == set(), "§5.1 에 있는데 스키마에 없는 필드"
    assert fields - spec == set(), "스키마에 있는데 §5.1 에 없는 필드"


def test_run_state_fields_match_agent_design():
    text = (DOCS / "에이전트_설계.md").read_text(encoding="utf-8")
    spec = _first_col_names(_section(text, "## 2. 상태", "## 3. 노드"))
    assert spec == set(RunState.__annotations__)


def test_whitelists_match_grain_contract():
    gc = grain_contract()
    assert list(get_args(schemas.City)) == gc["cities"]
    assert list(get_args(schemas.Sido)) == gc["sido"]
    assert set(get_args(schemas.Dimension)) == set(gc["dimensions"])
    assert set(get_args(schemas.Metric)) == set(gc["metrics"])
    assert str(schemas.DATA_START) == gc["period"]["start"] and str(schemas.DATA_END) == gc["period"]["end"]
    for s in gc["sources"].values():
        assert set(s["dims"]) <= set(gc["dimensions"])


def _examples() -> list[tuple[str, dict]]:
    text = (DOCS / "API_명세.md").read_text(encoding="utf-8")
    found = re.findall(r"<!-- schema: (\w+) -->\s*```json\n(.*?)```", text, flags=re.S)
    return [(name, json.loads(body)) for name, body in found]


EXAMPLES = _examples()


@pytest.mark.parametrize("name, body", EXAMPLES, ids=[f"{i}-{n}" for i, (n, _) in enumerate(EXAMPLES)])
def test_api_examples_validate(name, body):
    model = getattr(schemas, name)
    model.model_validate(body)


def test_api_doc_covers_all_endpoints():
    text = (DOCS / "API_명세.md").read_text(encoding="utf-8")
    table = _section(text, "## 2. 엔드포인트 목록", "## 3.")
    rows = [line for line in table.splitlines() if line.startswith("| GET") or line.startswith("| POST")]
    paths = {re.search(r"`(/[^`?]*)", r).group(1) for r in rows}
    need = {
        "/health", "/snapshots/active", "/overview", "/map", "/candidates", "/runs", "/runs/{id}",
        "/runs/{id}/evidence", "/runs/{id}/review", "/runs/{id}/approve", "/qa", "/resources", "/data/capabilities",
    }
    assert need <= paths
    # 표의 응답 모델마다 예시가 하나 이상
    models = {re.findall(r"`(\w+)`\s*\|\s*$", r)[0] for r in rows}
    assert models <= {n for n, _ in EXAMPLES}
    for m in models | {n for n, _ in EXAMPLES}:
        assert hasattr(schemas, m), m


def test_capabilities_example_matches_contract():
    from bigcontest.contracts.grain import get_data_capabilities

    real = {s.source: s for s in get_data_capabilities().sources}
    body = next(b for n, b in EXAMPLES if n == "CapabilitiesResponse")
    for s in body["sources"]:
        assert real[s["source"]].model_dump(mode="json") == s


# ------------------------------------------------------------ 모델 규칙


def _ev(**kw):
    base = dict(evidence_id="EV-1", metric="m", value=1.0, analysis_mode="retrospective", as_of=date(2025, 10, 1), status="OK")
    base.update(kw)
    return base


def test_tool_result_rules():
    with pytest.raises(ValidationError):
        ToolResult(**_ev(status="ERROR", value=None))  # 오류 코드 없음
    ToolResult(**_ev(status="ERROR", value=None, error_code="TOOL_TIMEOUT"))
    ToolResult(**_ev(status="EMPTY", value=None))
    with pytest.raises(ValidationError):
        ToolResult(**_ev(value=None))  # OK 인데 값도 상태도 없음
    ToolResult(**_ev(value=None, value_state="suppressed"))
    with pytest.raises(ValidationError):
        ToolResult(**_ev(customer_residence="강원"))  # 가맹점 지역 없이 거주지만
    with pytest.raises(ValidationError):
        ToolResult(**_ev(merchant_region="춘천시", customer_residence="춘천"))  # 거주지는 시도만
    with pytest.raises(ValidationError):
        ToolResult(**_ev(availability_basis="reported"))  # 실제 공개일 없이 reported
    with pytest.raises(ValidationError):
        ToolResult(**_ev(unknown_field=1))


def test_request_whitelist():
    with pytest.raises(ValidationError):
        NormalizedRequest(city="서울", metric="카드결제")
    with pytest.raises(ValidationError):
        NormalizedRequest(city="강남", metric="결제액")
    with pytest.raises(ValidationError):
        NormalizedRequest(city="강남", metric="카드결제", period_from=date(2025, 6, 1))
    with pytest.raises(ValidationError):
        NormalizedRequest(city="강남", metric="카드결제", dimensions=["hour"])
    r = NormalizedRequest(city="강남", metric="체류인구", age_min=60, time_of_day="18_23")
    assert set(r.dimensions) == {"age", "time_of_day"}


def test_candidate_rules():
    base = dict(
        candidate_id="C-1",
        candidate_type="unexpected_activity_deviation",
        city="강남",
        analysis_cohort="80대+",
        input_status="충족",
        counter_result="기각",
        counter_reason="추석 연휴 기대값으로 설명됨",
        candidate_level="Level 1",
    )
    Candidate(**base)
    with pytest.raises(ValidationError):
        Candidate(**{**base, "candidate_level": "Level 2"})  # 기각은 Level 1
    with pytest.raises(ValidationError):
        Candidate(**{**base, "candidate_type": "scheduled_access_review"})  # 예정된 점검은 반증 없음
    with pytest.raises(ValidationError):
        Candidate(**{**base, "check_first_adm": ["11230510"]})  # 동 목록은 공유 맥락 표시 필요
    with pytest.raises(ValidationError):
        Candidate(**{**base, "selected_for_review": True, "queue_rank": 5, "selection_budget": 3})


def test_guard_result_rules():
    GuardResult(guard="guard_report", check="사실", verdict="PASS")
    with pytest.raises(ValidationError):
        GuardResult(guard="guard_report", check="사실", verdict="REVISE")  # 사유 없음
    with pytest.raises(ValidationError):
        GuardResult(guard="guard_report", check="표현", verdict="REVISE", rule_ids=["G-15"], reasons=["x"])
