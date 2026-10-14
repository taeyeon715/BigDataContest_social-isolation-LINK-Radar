"""에이전트 도구 (에이전트 설계 §5).

- 실제 도구: data.py, signal.py, welfare.py, report.py (고정 스냅샷은 datasource.py)
- 가짜 도구: stubs.py (그래프 분기 테스트용)
둘 다 같은 반환 봉투(`ToolResult`)를 돌려주고, `call_tool` 이 ToolContext.backend(real / stub)로 고른다.
도구 이름은 아래 화이트리스트로만 부른다. LLM 이 고른 이름도 여기서 거른다.
`ctx.tool_log` 가 목록이면 도구 한 번마다 {tool, ms, status, n} 을 붙인다 (노드가 상태 `tool_timings` 로 모음).
"""

import time

# 에이전트 설계 §5 도구 표 (묶음별)
DATA_TOOLS = (
    "get_data_capabilities",
    "check_request_contract",
    "get_structure",
    "get_mobility_layer",
    "get_card_cohort_series",
    "get_card_time_band",
    "get_validation_reference",
)
SIGNAL_TOOLS = (
    "get_calendar_triggers",
    "calc_expected_activity",
    "calc_cohort_deviation",
    "calc_holiday_gap",
    "calc_winter_contraction",
    "calc_heat_response",
    "check_confounders",
    "classify_candidate_type",
    "assign_level",
)
WELFARE_TOOLS = ("find_resources", "get_access_gap", "check_eligibility")
REPORT_TOOLS = ("build_evidence_card", "generate_response_plan", "write_run_metadata")

TOOL_NAMES: tuple[str, ...] = DATA_TOOLS + SIGNAL_TOOLS + WELFARE_TOOLS + REPORT_TOOLS

# LLM 이 "추가 조사"로 고를 수 있는 도구 (등급 계산에 쓰지 않는 선택 맥락·탐색 도구)
EXTRA_TOOLS: tuple[str, ...] = (
    "get_mobility_layer",
    "get_validation_reference",
    "calc_holiday_gap",
    "calc_winter_contraction",
    "calc_heat_response",
)

# ---------------------------------------------------------------- 백엔드 고르기 (stub / real)

BACKENDS = ("real", "stub")

# 실제 도구 중 그래프가 이름으로 부르는 것 (find_resources·check_eligibility 는 인자가 따로라 별도 함수)
_REAL = {
    "get_data_capabilities": ("data", "get_data_capabilities"),
    "check_request_contract": ("data", "check_request_contract"),
    "get_structure": ("data", "get_structure"),
    "get_mobility_layer": ("data", "get_mobility_layer"),
    "get_card_cohort_series": ("data", "get_card_cohort_series"),
    "get_card_time_band": ("data", "get_card_time_band"),
    "get_validation_reference": ("data", "get_validation_reference"),
    "get_calendar_triggers": ("signal", "get_calendar_triggers"),
    "calc_expected_activity": ("signal", "calc_expected_activity"),
    "calc_cohort_deviation": ("signal", "calc_cohort_deviation"),
    "calc_holiday_gap": ("signal", "calc_holiday_gap"),
    "calc_winter_contraction": ("signal", "calc_winter_contraction"),
    "calc_heat_response": ("signal", "calc_heat_response"),
    "check_confounders": ("signal", "check_confounders"),
    "get_access_gap": ("welfare", "get_access_gap"),
}
_METRIC_OF = {"get_structure": "1인세대 비율", "get_access_gap": "관측 거점까지 직선거리",
              "check_confounders": "반증: 공휴일 기대값", "calc_cohort_deviation": "카드 결제건수 평소 대비 (이탈 경보)"}


def _tool_error(name: str, ctx, exc: Exception):
    """실제 도구 예외 → status=ERROR 봉투 (내부 예외 문장은 넣지 않고 종류만)."""
    from bigcontest.agent.tools.common import error
    from bigcontest.agent.tools.datasource import DataSourceError

    code = exc.code if isinstance(exc, DataSourceError) else "TOOL_ERROR"
    reason = f"{name} 실패 ({type(exc).__name__})" + (f": {exc}" if isinstance(exc, DataSourceError) else "")
    return error(ctx, f"ERR-{name.upper().replace('_', '')}", _METRIC_OF.get(name, name), code, reason)


def _log_tool(ctx, name: str, t0: float, results) -> None:
    log = getattr(ctx, "tool_log", None)
    if log is None:
        return
    st = [getattr(r, "status", "OK") for r in results]
    status = "ERROR" if "ERROR" in st else "EMPTY" if st and all(s == "EMPTY" for s in st) else "OK"
    log.append({"tool": name, "ms": round((time.perf_counter() - t0) * 1000, 1), "status": status, "n": len(st)})


def call_tool(name: str, ctx, **params):
    """도구 이름 → 반환 봉투 목록. ctx.backend 로 가짜/실제를 고른다. 화이트리스트 밖 이름은 KeyError."""
    t0 = time.perf_counter()
    out = _call_tool(name, ctx, **params)
    _log_tool(ctx, name, t0, out)
    return out


def _call_tool(name: str, ctx, **params):
    if name not in TOOL_NAMES:
        raise KeyError(f"화이트리스트 밖 도구: {name}")
    if getattr(ctx, "backend", "stub") == "stub":
        from bigcontest.agent.tools import stubs

        return stubs.call_tool(name, ctx)
    if name not in _REAL:
        raise KeyError(f"이 이름은 그래프 도구가 아님: {name}")
    import importlib

    mod, fn = _REAL[name]
    try:
        return getattr(importlib.import_module(f"bigcontest.agent.tools.{mod}"), fn)(ctx, **params)
    except Exception as e:  # noqa: BLE001 — 도구 오류는 봉투로 (에이전트 설계 §9)
        return [_tool_error(name, ctx, e)]


def find_resources(ctx, adm_cd, group, **kw):
    """자원 찾기 (요약 봉투, 자원 dict 목록)."""
    t0 = time.perf_counter()
    env, items = _find_resources(ctx, adm_cd, group, **kw)
    _log_tool(ctx, "find_resources", t0, [env])
    return env, items


def _find_resources(ctx, adm_cd, group, **kw):
    if getattr(ctx, "backend", "stub") == "stub":
        from bigcontest.agent.tools import stubs

        return stubs.find_resources(ctx, adm_cd, group)
    from bigcontest.agent.tools import welfare

    try:
        return welfare.find_resources(ctx, adm_cd, group, **kw)
    except Exception as e:  # noqa: BLE001
        return _tool_error("find_resources", ctx, e), []
