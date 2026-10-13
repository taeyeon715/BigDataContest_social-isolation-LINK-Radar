"""Welfare 도구 (에이전트 설계 §5): 자원 찾기, 관측 거점까지 직선거리, 자격 확인. resources/match.py 를 그대로 쓴다.

- 봉투(ToolResult)에는 건수 요약만, 자원별 상세는 `ResourceRecommendation` 모양의 dict 목록으로 따로 돌려준다.
- 원장 값 → 스키마 값 (원장이 기준): 해당됨 → 적합, 해당 안 됨 → 부적합, 확인 필요 → 확인 필요 /
  운영 중 → 운영, 중단 → 종료, 미상·확인 필요 → 확인 필요. 확인일은 날짜로 읽히는 것(last_verified)만 쓴다.
- eligibility 는 자격 판정만 옮긴다. 휴일 운영·위치·운영 상태 등 다른 "확인 필요" 이유는 warnings 에
  "확인 필요(이유)" 로 따로 남긴다 (자격 미상과 섞지 않음).
- 추천은 제외 규칙(확인일·출처 없음, 관할 밖 물리 거점)을 먼저 적용한 뒤 상위 K (match.select_resources).
  건너뛴 자원은 봉투 warnings 에 "확인할 자원(추천 제외)"로 사유와 함께, 모자라면 모자란 수를 그대로 적는다.
- 위기 전화(자살예방·정신건강 위기상담)는 순위 밖 "위급 시 안내" 묶음으로 목록 맨 아래에 둔다.
- 원장은 2026년 수집본이다. 모든 봉투에 원장 수집 시점을 적고(ledger_time_note), 공개 시점은 모름(availability_basis
  unknown, actual_available_at 없음)으로 둔다. 과거 기준일에 붙이면 "현재 대응안 예시"이며 당시 운영·위치는 모른다.
  observed_to 에 원장 수집일을 넣지 않는다: 기준일 뒤 날짜라 G-13(기준일 뒤 자료)에 걸리기 때문. 수집일은 warnings 로.
"""

from __future__ import annotations

import re
from datetime import date

from bigcontest.agent.schemas import ToolResult
from bigcontest.agent.tools.common import envelope, empty
from bigcontest.agent.tools.datasource import open_datasource
from bigcontest.contracts.claims import claim_lists
from bigcontest.resources import match as M

ACCESS_METRIC = "관측 거점까지 직선거리"
ACCESS_NEED = "안부·관계"  # 접근성 근거로 보는 필요 유형 (W-1 동별 접근거리와 같은 기준)
LEDGER_NOTE = "현재 대응안 예시: 2026년 수집 원장을 2025년 신호에 붙임"
ELIG = {"해당됨": "적합", "해당 안 됨": "부적합", "확인 필요": "확인 필요", "": "확인 필요"}
OPER = {"운영 중": "운영", "중단": "종료", "미상": "확인 필요", "확인 필요": "확인 필요"}
# 트리거 규칙 → 원인 유형 (configs/need_types.yaml cause_types). R4·R6 은 기상 하위 유형으로 (WEATHER_CAUSE)
CAUSE_OF_RULE = {"R1": "달력 공백형", "R2": "달력 공백형", "R3": "달력 공백형", "W": "계절 공동화형"}
WEATHER_CAUSE = {"폭염": "폭염형", "한파": "한파·대설형", "대설": "한파·대설형", "호우": "호우형",
                 "HEAT": "폭염형", "COLD": "한파·대설형", "SNOW": "한파·대설형", "RAIN": "호우형"}
WEATHER_UNKNOWN = "기상 대응 확인 필요"
FAR_CAUSE = "거점 원거리형"
DEFAULT_CAUSE = "1인가구 밀집형"
CRISIS_LABEL = "위급 시 안내"
GROUPS = ("고령층", "중장년 남성 1인가구", "청년 남성")
MAX_EXCLUDED_SHOWN = 8
# 원장 시점: 공개 시점 모름. 관측 기간도 넣지 않음 (수집일이 기준일 뒤라 G-13 에 걸림)
TIME_FIELDS = {"availability_basis": "unknown", "actual_available_at": None, "observed_from": None, "observed_to": None}


def _origin(ds, adm_cd: str) -> tuple[float, float] | None:
    r = ds.adm_index.get(adm_cd)
    try:
        return (float(r["lat"]), float(r["lon"])) if r else None
    except (TypeError, ValueError):
        return None


def _date(s: str) -> str | None:
    try:
        return date.fromisoformat(s).isoformat() if s else None
    except ValueError:
        return None


def ledger_collected(led) -> str:
    """원장 수집일 (collected_date 중 가장 늦은 날). 없으면 '미상'."""
    ds = [d for d in led["collected_date"] if _date(d)] if "collected_date" in led else []
    return max(ds) if ds else "미상"


def ledger_time_note(led, as_of: date) -> str:
    """원장 수집 시점 고지. 기준일이 수집 전이면 그해 당시 운영·위치는 모른다고 적는다."""
    col = ledger_collected(led)
    note = f"현재 원장(수집 {col}) 기준"
    if col == "미상" or as_of < date.fromisoformat(col):
        note += f" — {as_of.year}년 당시 운영·위치는 확인되지 않음"
    return note


def causes_of(trigger: str | None) -> list[str]:
    """트리거 문장('; '로 이음) → 원인 유형 목록 (순서 유지, 중복 없음).
    R4 → 폭염형. R6 → 하위 유형(호우·대설·한파)별, 모르면 '기상 대응 확인 필요' (폭염으로 단정하지 않음)."""
    out: list[str] = []
    for part in (trigger or "").split(";"):
        part = part.strip()
        if not part:
            continue
        rule = part.split(" ")[0].split("-")[0]
        if rule in ("R4", "R6"):
            kinds = [k for k in WEATHER_CAUSE if k in part]
            cause = WEATHER_CAUSE[kinds[0]] if kinds else ("폭염형" if rule == "R4" else WEATHER_UNKNOWN)
        else:
            cause = CAUSE_OF_RULE.get(rule)
        if cause and cause not in out:
            out.append(cause)
    return out


def cause_for(trigger: str | None, access_km: float | None, far_km: float) -> str:
    """후보 → 원인 유형. 관측 거점이 멀면 거점 원거리형(전화형 우선)을 앞에 두되 원래 원인을 '+'로 남긴다.
    예: '거점 원거리형+계절 공동화형+한파·대설형'. 트리거가 없으면 1인가구 밀집형."""
    causes = causes_of(trigger)
    if access_km is not None and access_km >= far_km:
        return "+".join([FAR_CAUSE] + causes)
    return "+".join(causes) or DEFAULT_CAUSE


def _cause_label(cause_type: str | None) -> str:
    parts = M.split_cause(cause_type)
    if not parts:
        return "원인 유형 -"
    if parts[0] == FAR_CAUSE and len(parts) > 1:
        return f"원인 유형 {' + '.join(parts[1:])} (관측 거점이 멀어 {FAR_CAUSE} 순서 적용)"
    return f"원인 유형 {' + '.join(parts)}"


def _check_warnings(m: M.Match, grp: str | None) -> list[str]:
    """'확인 필요' 이유를 코드별로 따로 (자격 / 휴일 운영 / 위치 / 운영 상태 / 좌표)."""
    out = [f"확인 필요({code})" for code in dict.fromkeys(m.checks)]
    if grp is None:
        out.append(f"확인 필요({M.CHECK_ELIG}): 대상 집단을 원장 집단으로 정하지 못함")
    return out


def _item(m: M.Match, r, grp: str | None, note: str, group_label: str) -> dict:
    warnings = [f"전달 방식: {m.delivery_channel}"] + list(m.flags) + list(m.reasons) + _check_warnings(m, grp)
    if group_label == CRISIS_LABEL:
        warnings.insert(0, f"묶음: {CRISIS_LABEL} (자살·정신건강 위기 때 안내, 첫 추천 아님)")
    if r.get("verified_url") and r["verified_url"] != m.source_url:
        warnings.append(f"확인한 공식 페이지: {r['verified_url']} (원장 출처: {m.source_url})")
    if r.get("page_collected_at"):
        warnings.append(f"공식 페이지 확인 {r['page_collected_at']} / 운영 안내 날짜 {r.get('operation_confirmed_at') or '없음'}")
    if r.get("note"):  # 원장 참고 (예: E-Gen 명절 당일 운영은 다시 확인, 수행기관 위수탁기간 종료)
        warnings.append(f"참고: {r['note']}")
    return {
        "resource_id": m.resource_id, "name": m.name, "resource_type": r["subtype"] or r["type"],
        "city": r["city"], "adm_cd": r["adm_cd"] or None, "target_group": r["target_stage"] or None,
        # 자격 판정만 옮김 (다른 '확인 필요' 이유는 warnings)
        "eligibility": ELIG.get(m.eligibility, "확인 필요") if grp else "확인 필요",
        "operating_status": OPER.get(r["operating_status"], "확인 필요"),
        "distance_km": round(m.distance_km, 2) if m.distance_km is not None else None,
        "phone_based": m.delivery_channel in M.PHONE, "source_url": r.get("verified_url") or m.source_url,
        "verified_date": _date(r["last_verified"]), "warnings": warnings + [LEDGER_NOTE, note],
        "group": group_label,  # 화면 묶음 (ResourceRecommendation 칸 아님, to_resources 에서 빠짐)
    }


def find_resources(ctx, adm_cd: str | None, group: str | None, need_type: str | None = None,
                   cause_type: str | None = None, limit: int = 5) -> tuple[ToolResult, list[dict]]:
    """필요 유형·자격 필터 → 제외 규칙 → 상위 limit (match.select_resources). (요약 봉투, 자원 dict 목록).
    목록 = 추천 후보(순위) + 맨 아래 '위급 시 안내'."""
    ds = open_datasource(ctx.snapshot_id)
    city = ctx.request.city
    grp = group if group in GROUPS else None
    code = f"RES-{adm_cd or 'CITY'}"
    if adm_cd is None:
        return empty(ctx, code, "복지 자원 후보", "행정동이 정해지지 않아 자원 매칭 안 함"), []
    led = ds.ledger()
    note = ledger_time_note(led, ctx.as_of)
    # 다른 도시 자원은 match 의 관할 단계에서 어차피 빠지므로 미리 거름 (match 는 행마다 돌아 느림). 결과는 스냅샷별 캐시
    sub = ds.cached(("ledger_city", city), lambda: led[led["city"] == city])
    sel = ds.cached(("select", adm_cd, grp, need_type, cause_type, limit),
                    lambda: M.select_resources(sub, adm_cd, grp, need_type, cause_type, origin=_origin(ds, adm_cd),
                                               limit=limit))
    rows = led.set_index("resource_id")
    ranked = [_item(m, rows.loc[m.resource_id], grp, note, "추천 후보") for m in sel.ranked]
    crisis = [_item(m, rows.loc[m.resource_id], grp, note, CRISIS_LABEL) for m in sel.crisis]
    items = ranked + crisis
    n_unknown = sum(i["operating_status"] == "확인 필요" for i in items)
    warn = [f"{_cause_label(cause_type)} / 필요 유형 {need_type or '-'}",
            f"운영 상태 확인 필요 {n_unknown}/{len(items)}건 (원장 운영 상태 미상)", LEDGER_NOTE, note,
            "제외 규칙(확인일·출처 없음, 관할 밖 물리 거점)을 먼저 적용한 뒤 상위 자원을 고름"]
    if len(ranked) < limit:
        warn.append(f"추천할 수 있는 자원 {len(ranked)}건 (요청 {limit}건): 조건에 맞는 자원이 부족함")
    if crisis:
        warn.append(f"{CRISIS_LABEL} {len(crisis)}건은 순위 밖, 목록 맨 아래 ("
                    + ", ".join(f"{m.resource_id} {m.name}" for m in sel.crisis) + ")")
    for m, why in sel.excluded[:MAX_EXCLUDED_SHOWN]:
        warn.append(f"확인할 자원(추천 제외): {m.resource_id} {m.name} — {'; '.join(why)}")
    if len(sel.excluded) > MAX_EXCLUDED_SHOWN:
        warn.append(f"확인할 자원(추천 제외) 외 {len(sel.excluded) - MAX_EXCLUDED_SHOWN}건")
    valid, forbidden = claim_lists("resources")
    env = envelope(
        ctx, code, "resources", metric="복지 자원 후보", value=len(items), unit="건",
        denominator=(f"원장 {len(led)}건 중 관할·자격·필요 유형 필터 통과 {sel.n_candidates}건, 제외 규칙 통과 "
                     f"{sel.n_valid}건의 상위 {len(ranked)}건 + {CRISIS_LABEL} {len(crisis)}건"),
        measured_grain="시설·서비스 1건", applied_scope=f"{city} {ds.adm_index.get(adm_cd, {}).get('adm_nm', '')} ({adm_cd})",
        service_eligibility_group=group, interpretation_status="직접 확인", valid_claims=valid,
        forbidden_claims=forbidden + ["서비스 공백", "실제 이용 가능 확정", "당시 운영 확인"],
        warnings=warn, value_state="observed", **TIME_FIELDS)
    return env, items


def get_access_gap(ctx, need_type: str = ACCESS_NEED) -> list[ToolResult]:
    """행정동 중심점 → 관측 거점(필요 유형) 직선거리 + 전화형 대안 수. 실제 공급 부족이라는 뜻은 아님.
    원장은 수집 시점 자료라 기준일 당시 거점이 있었는지는 모른다 (warnings 에 원장 수집 시점)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    grp = req.group if req.group in GROUPS else None
    led = ds.ledger()
    note = ledger_time_note(led, ctx.as_of)
    names = ds.adm_names()
    valid = ["관측된 거점까지 직선거리가 큼 / 작음"]
    forbidden = ["서비스 공백", "서비스 공급 부족", "이동시간", "당시 접근성 확인"]
    out = []
    for adm in names.loc[names["city"] == city, "adm_cd"]:
        origin = _origin(ds, adm)
        label = f"{city} {ds.adm_index[adm]['adm_nm']} ({adm})"
        if origin is None:
            out.append(empty(ctx, f"ACC-{adm}", ACCESS_METRIC, "행정동 중심점 없음", applied_scope=label))
            continue
        sub = ds.cached(("ledger_city", city), lambda: led[led["city"] == city])
        g = ds.cached(("access", adm, need_type, grp), lambda: M.access_gap(sub, adm, origin, need_type, grp))
        warn = [f"필요 유형 {need_type}, 전화형 대안 {len(g['phone_alternatives'])}건, 위치 미확인 {g['n_location_unknown']}건",
                "좌표 일부 탐색용(도로명주소 확인 전)", "직선거리일 뿐 이동시간·실제 이용 가능성은 아님", note]
        if g["nearest_km"] is None:
            out.append(envelope(ctx, f"ACC-{adm}", "resources", metric=ACCESS_METRIC, value=None, unit="km",
                                applied_scope=label, warnings=warn + ["거리를 잴 수 있는 관측 거점 없음"],
                                value_state="missing", measured_grain="행정동 중심점 → 관측 거점 (직선)", **TIME_FIELDS))
            continue
        out.append(envelope(
            ctx, f"ACC-{adm}", "resources", metric=ACCESS_METRIC, value=round(float(g["nearest_km"]), 2), unit="km",
            denominator=f"필요 유형 '{need_type}' 관측 거점 {g['n_spatial']}곳 중 가장 가까운 곳", n_obs=g["n_spatial"],
            measured_grain="행정동 중심점 → 관측 거점 (직선)", applied_scope=label, interpretation_status="계산 재현",
            valid_claims=valid, forbidden_claims=forbidden, warnings=warn, value_state="observed", **TIME_FIELDS))
    return out


def check_eligibility(ctx, resource_id: str, group: str | None) -> list[ToolResult]:
    """자원 하나의 집단 자격 (match.check_eligibility). 기준을 모르면 확인 필요(자격 미상)."""
    ds = open_datasource(ctx.snapshot_id)
    led = ds.ledger()
    grp = group if group in GROUPS else None
    if grp is None:
        res = {"verdict": "확인 필요", "reason": f"원장 집단이 아님: {group}"}
    else:
        res = M.check_eligibility(led, resource_id, grp)
    reason = res["reason"]
    if res["verdict"] == "확인 필요" and not re.match(r"확인 필요\(", reason):
        reason = f"확인 필요({M.CHECK_ELIG}): {reason}"
    return [envelope(ctx, f"ELG-{resource_id}", "resources", metric="자격 확인", value=ELIG.get(res["verdict"], "확인 필요"),
                     measured_grain="시설·서비스 1건", applied_scope=resource_id, service_eligibility_group=group,
                     interpretation_status="직접 확인" if res["verdict"] != "확인 필요" else "미확인",
                     warnings=[reason, ledger_time_note(led, ctx.as_of)], value_state="observed", **TIME_FIELDS)]
