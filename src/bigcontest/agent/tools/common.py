"""실제 도구 공통: 반환 봉투 만들기, 시간 계약(기준일에 쓸 수 있던 달·날), 근거 ID.

근거 ID 는 `EV-<기준일>-<도시>-<도구 코드>[-<대상>]` 이다 (같은 입력 + 같은 스냅샷 → 같은 ID).
예상 밖 이탈의 근거(DEV)와 반증(CF1~CF4)은 같은 신호 키로 끝나 후보마다 짝을 맞춘다 (`signal_key`).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from bigcontest.agent.schemas import NormalizedRequest, ToolResult
from bigcontest.contracts import grain_contract
from bigcontest.contracts.time import DataAvailability, availability, check_data_time

CITY_CODE = {"강남": "GN", "춘천": "CC"}
CITY_OF_CODE = {v: k for k, v in CITY_CODE.items()}
CITY_PREFIX = {"강남": "11230", "춘천": "32010"}
MERCHANT = {"강남": "강남구", "춘천": "춘천시"}
RESIDENCE = {"강남": "서울", "춘천": "강원"}
SEX_CODE = {"남성": "M", "여성": "F"}
SEX_KO = {"남": "남성", "여": "여성"}
# 등급은 rules-v1.0 (configs/rules.yaml). 기상특보 근사 같은 도구 안 규칙은 아직 임시 기준
RULE_VERSION = "agent-s5-v1 (등급 rules-v1.0, 도구 근사는 임시)"
TEMP_RULE_NOTE = "임시 기준: 특보 근사 임계값은 아직 확정되지 않음"


def age_code(age_g: str) -> str:
    """'80대+' → '80P', '20대' → '20'."""
    return age_g.replace("대", "").replace("+", "P")


def cohort_key(city: str, age_g: str, sex: str) -> str:
    return f"{CITY_CODE[city]}-{age_code(age_g)}{SEX_CODE.get(sex, sex)}"


_KEY_RE = re.compile(r"-(?:DEV|CF\d)(?:-(?P<key>[0-9A-Za-z\-]+))?$")


def signal_key(evidence_id: str) -> str | None:
    """DEV / CF1~CF4 근거 ID → 신호 키 (가짜 도구는 빈 문자열). 해당 없으면 None."""
    m = _KEY_RE.search(evidence_id)
    if not m:
        return None
    return m.group("key") or ""


def city_of_evidence(ev: ToolResult) -> str | None:
    m = re.match(r"^EV-\d{8}-(GN|CC)-", ev.evidence_id)
    if m:
        return CITY_OF_CODE[m.group(1)]
    m = re.search(r"\((\d{8})\)", ev.applied_scope or "")
    if m:
        return next((c for c, p in CITY_PREFIX.items() if m.group(1).startswith(p)), None)
    if ev.merchant_region:
        return {"강남구": "강남", "춘천시": "춘천"}[ev.merchant_region]
    for c in CITY_CODE:
        if (ev.applied_scope or "").startswith(c):
            return c
    return None


# ---------------------------------------------------------------- 시간 계약


def lag_days(source: str) -> int:
    return grain_contract()["sources"][source]["time"]["lag_days"]


def daily_cutoff(source: str, as_of: date, mode: str) -> date | None:
    """일 자료를 기준일에 어디까지 쓸 수 있나. simulated_replay: 기준일 − 가정 공급 지연,
    retrospective: 기준일까지(사후), strict_as_of: 실제 공개일을 모르므로 None."""
    if mode == "strict_as_of":
        return None
    if mode == "retrospective":
        return as_of
    return as_of - timedelta(days=lag_days(source))


def usable_months(source: str, as_of: date, mode: str) -> list[DataAvailability]:
    """월 자료 중 기준일·모드에서 근거로 쓸 수 있는 달 (오래된 것부터)."""
    out = []
    for m in range(7, 13):
        item = availability(source, base_month=f"2025{m:02d}")
        if check_data_time(item, as_of, mode).usable:
            out.append(item)
    return out


# ---------------------------------------------------------------- 봉투


def evidence_id(ctx, code: str, city: str | None = None) -> str:
    city = city or (ctx.request.city if ctx.request else None)
    prefix = f"{CITY_CODE[city]}-" if city else ""
    return f"EV-{ctx.as_of:%Y%m%d}-{prefix}{code}"


def envelope(ctx, code: str, source: str | None, city: str | None = None, **kw) -> ToolResult:
    """실제 도구 반환 봉투. data_version 은 원천 파일 해시, 공개일은 가정값이면 assumed."""
    from bigcontest.agent.tools.datasource import open_datasource
    from bigcontest.contracts.claims import fill_claims

    category = kw.pop("claim_category", None)  # 할 수 있는 말의 {업종} 값 (모르면 측정 단위에서 찾거나 뺌)
    base = dict(
        evidence_id=evidence_id(ctx, code, city),
        analysis_mode=ctx.analysis_mode,
        as_of=ctx.as_of,
        snapshot_id=ctx.snapshot_id,
        rule_version=RULE_VERSION,
        status="OK",
    )
    if source:
        base["data_version"] = open_datasource(ctx.snapshot_id).data_version(source)
    base.update(kw)
    if base.get("simulated_available_at") and not base.get("actual_available_at"):
        base.setdefault("availability_basis", "assumed")
    if base["status"] != "OK" and "value_state" not in kw:
        base["value_state"] = "missing"
    return fill_claims(ToolResult(**base), category)


def empty(ctx, code: str, metric: str, reason: str, city: str | None = None, source: str | None = None,
          **kw) -> ToolResult:
    return envelope(ctx, code, None, city, metric=metric, status="EMPTY", warnings=[reason],
                    **({"data_version": f"{source}@{ctx.snapshot_id}"} if source else {}), **kw)


def error(ctx, code: str, metric: str, error_code: str, reason: str, city: str | None = None) -> ToolResult:
    return envelope(ctx, code, None, city, metric=metric, status="ERROR", error_code=error_code, warnings=[reason])


def req_with_city(req: NormalizedRequest, city: str) -> NormalizedRequest:
    return req if req.city == city else NormalizedRequest(**{**req.model_dump(), "city": city})
