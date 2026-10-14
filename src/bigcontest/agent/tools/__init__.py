"""에이전트 도구 (에이전트 설계 §5).

- 실제 도구: data.py, signal.py, welfare.py, report.py (고정 스냅샷은 datasource.py)
- 가짜 도구: stubs.py (그래프 분기 테스트용)
둘 다 같은 반환 봉투(`ToolResult`)를 돌려주고, `call_tool` 이 ToolContext.backend(real / stub)로 고른다.
도구 이름은 아래 화이트리스트로만 부른다. LLM 이 고른 이름도 여기서 거른다.
`ctx.tool_log` 가 목록이면 도구 한 번마다 {tool, ms, status, n} 을 붙인다 (노드가 상태 `tool_timings` 로 모음).

시간 제한·오류 분류 (AG-05)
- 도구마다 제한 시간(configs/llm.yaml `tools.timeout_s`, 기본 `timeout_s_default`). 넘으면 기다리지 않고 TOOL_TIMEOUT 봉투.
  가짜 도구도 같은 길을 지난다 (느린 가짜 도구로 시험)
- 예외는 봉투로: TOOL_TIMEOUT·TOOL_IO 는 다시 시도할 수 있는 오류, 자료·계약 오류(DataSourceError 코드)와 TOOL_ERROR 는
  다시 해도 같은 결과 (`is_retriable`. Guard 처치 retry_failed_tool 이 씀)
- 화이트리스트 밖 이름·가짜 도구 시나리오 오류(StubToolError)는 코드 잘못이라 그대로 예외
- 실행 예산(budget.run_budget)이 켜져 있으면 도구 시간을 더한다 (조사 루프가 한도를 봄)
"""

import contextvars
import threading
import time
from functools import lru_cache

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


# ---------------------------------------------------------------- 시간 제한 · 오류 분류 (AG-05)


class ToolTimeout(TimeoutError):
    """도구가 제한 시간 안에 끝나지 않음."""


@lru_cache
def tool_limits() -> dict:
    from bigcontest.agent.llm import llm_config

    return dict(llm_config().get("tools") or {})


def timeout_for(name: str) -> float | None:
    """도구 제한 시간 (초). 설정이 없으면 None (제한 없음)."""
    lim = tool_limits()
    v = (lim.get("timeout_s") or {}).get(name, lim.get("timeout_s_default"))
    return float(v) if v else None


def error_code_of(exc: BaseException) -> str:
    """예외 → 오류 코드. TOOL_TIMEOUT·TOOL_IO 는 다시 시도할 수 있고, 자료·계약 오류와 TOOL_ERROR 는 아님."""
    from bigcontest.agent.tools.datasource import DataSourceError

    if isinstance(exc, ToolTimeout):
        return "TOOL_TIMEOUT"
    if isinstance(exc, DataSourceError):
        return exc.code
    if isinstance(exc, OSError):  # 파일·네트워크 입출력
        return "TOOL_IO"
    return "TOOL_ERROR"


def is_retriable(code: str | None) -> bool:
    """다시 시도하면 풀릴 수 있는 오류인가 (configs/llm.yaml tools.retriable_error_codes)."""
    return code in set(tool_limits().get("retriable_error_codes") or ("TOOL_TIMEOUT", "TOOL_IO"))


def _tool_error(name: str, ctx, exc: BaseException):
    """도구 예외 → status=ERROR 봉투 (내부 예외 문장은 넣지 않고 종류만). 다시 시도 가능 여부를 경고에 적음."""
    from bigcontest.agent.tools.common import error
    from bigcontest.agent.tools.datasource import DataSourceError

    code = error_code_of(exc)
    if code == "TOOL_TIMEOUT":
        reason = f"{name} 시간 초과 ({timeout_for(name) or 0:g}초)"
    else:
        reason = f"{name} 실패 ({type(exc).__name__})" + (f": {exc}" if isinstance(exc, DataSourceError) else "")
    reason += " · 다시 시도 가능" if is_retriable(code) else " · 다시 시도해도 같은 결과"
    eid, metric = f"ERR-{name.upper().replace('_', '')}", _METRIC_OF.get(name, name)
    if getattr(ctx, "backend", "stub") == "stub":
        from bigcontest.agent.tools import stubs

        return stubs._error(ctx, eid, metric, code, reason)
    return error(ctx, eid, metric, code, reason)


def run_limited(fn, timeout: float | None):
    """fn() 을 제한 시간 안에 실행. 넘으면 ToolTimeout (도구 스레드는 데몬으로 두고 더 기다리지 않음).
    예산 같은 맥락(contextvars)은 도구 스레드로 복사한다."""
    if not timeout:
        return fn()
    box: dict = {}
    cv = contextvars.copy_context()

    def target():
        try:
            box["out"] = cv.run(fn)
        except BaseException as e:  # noqa: BLE001 — 부른 쪽에서 다시 올림
            box["exc"] = e

    th = threading.Thread(target=target, daemon=True, name="linkradar-tool")
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise ToolTimeout(f"{timeout:g}초 넘음")
    if "exc" in box:
        raise box["exc"]
    return box["out"]


def _log_tool(ctx, name: str, t0: float, results) -> None:
    ms = round((time.perf_counter() - t0) * 1000, 1)
    from bigcontest.agent import budget as _budget

    b = _budget.current()
    if b is not None:
        b.add_tool_ms(ms)
    log = getattr(ctx, "tool_log", None)
    if log is None:
        return
    st = [getattr(r, "status", "OK") for r in results]
    status = "ERROR" if "ERROR" in st else "EMPTY" if st and all(s == "EMPTY" for s in st) else "OK"
    item = {"tool": name, "ms": ms, "status": status, "n": len(st)}
    codes = [getattr(r, "error_code", None) for r in results if getattr(r, "status", "OK") == "ERROR"]
    if codes:
        item["error_code"] = codes[0]
        item["retriable"] = is_retriable(codes[0])
    log.append(item)


def call_tool(name: str, ctx, **params):
    """도구 이름 → 반환 봉투 목록. ctx.backend 로 가짜/실제를 고른다. 화이트리스트 밖 이름은 KeyError.
    제한 시간을 넘거나 예외가 나면 ERROR 봉투 하나 (오류 코드로 다시 시도 가능 여부를 가름)."""
    t0 = time.perf_counter()
    out = _call_tool(name, ctx, **params)
    _log_tool(ctx, name, t0, out)
    return out


def _call_tool(name: str, ctx, **params):
    if name not in TOOL_NAMES:
        raise KeyError(f"화이트리스트 밖 도구: {name}")
    if getattr(ctx, "backend", "stub") == "stub":
        from bigcontest.agent.tools import stubs

        if ctx.scenario not in stubs.SCENARIOS or name not in stubs._TOOLS:
            return stubs.call_tool(name, ctx)  # 코드 잘못: StubToolError 그대로
        fn = lambda: stubs.call_tool(name, ctx)  # noqa: E731
    else:
        if name not in _REAL:
            raise KeyError(f"이 이름은 그래프 도구가 아님: {name}")
        import importlib

        mod, attr = _REAL[name]
        impl = getattr(importlib.import_module(f"bigcontest.agent.tools.{mod}"), attr)
        fn = lambda: impl(ctx, **params)  # noqa: E731
    try:
        return run_limited(fn, timeout_for(name))
    except Exception as e:  # noqa: BLE001 — 도구 오류는 봉투로 (에이전트 설계 §9)
        return [_tool_error(name, ctx, e)]


def find_resources(ctx, adm_cd, group, **kw):
    """자원 찾기 (요약 봉투, 자원 dict 목록). 시간 제한·오류 봉투는 call_tool 과 같다."""
    t0 = time.perf_counter()
    env, items = _find_resources(ctx, adm_cd, group, **kw)
    _log_tool(ctx, "find_resources", t0, [env])
    return env, items


def _find_resources(ctx, adm_cd, group, **kw):
    if getattr(ctx, "backend", "stub") == "stub":
        from bigcontest.agent.tools import stubs

        fn = lambda: stubs.find_resources(ctx, adm_cd, group)  # noqa: E731
    else:
        from bigcontest.agent.tools import welfare

        fn = lambda: welfare.find_resources(ctx, adm_cd, group, **kw)  # noqa: E731
    try:
        return run_limited(fn, timeout_for("find_resources"))
    except Exception as e:  # noqa: BLE001
        return _tool_error("find_resources", ctx, e), []
