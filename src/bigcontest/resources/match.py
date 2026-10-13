"""자원 매칭: 필요 유형에 맞고 실제 이용할 수 있는 자원을 이유와 함께 고른다.

순서 (각 단계에서 빠지면 그 이유를 남김)
  0. 출처·확인일 (G-07): 출처 URL·확인일·지역·대상이 없으면 제외
  1. 관할: 같은 도시인가, 관할 행정동 자원이면 그 동인가
  2. 대상 연령·자격: 해당 안 됨 → 제외, 자격 미상·개인별 기준 → "확인 필요"
  3. 필요 유형: configs/need_types.yaml
  4. 운영 상태: 중단 → 제외, 미상 → 표시만. 달력 공백형은 휴일 미운영 → 제외
  5. 거리 / 전화형: 좌표가 있으면 직선거리, 전화형·제도·사업·사람 자원은 거리와 무관(오류 아님),
     좌표 없는 물리 거점은 거리 계산에서 빼고 "위치 확인 필요"로 남김,
     권역 외(관할 밖) 물리 거점은 거리 계산에서 빼고 최대 "확인 필요"까지만 (추천 금지)
순위 (find_resources)
  - 고령층 + 안부·관계: elderly_first (노인맞춤돌봄서비스 → 행정복지센터 → 노인맞춤돌봄 수행기관 → 경로당·사람 자원
    → 노인 돌봄·안전 확인 → (거점 원거리형이면 위기 전화가 아닌 전화형) → 찾아가는 서비스 → 노인복지관) 먼저
  - 거점 원거리형: 전화형 자원을 필요 유형·거리 한도와 상관없이 남기고, 위기 전화가 아닌 전화형 → 찾아가는 서비스 순으로 앞에
  - 그다음 추천 → 확인 필요 → (달력 공백형이면 휴일 운영 '예' → 그 밖) → 거리
추천 고르기 (select_resources): 제외 규칙(확인일·출처 없음, 관할 밖 물리 거점)을 먼저 적용하고 상위 K.
  필요 유형이 여럿이면 먼저 유형마다 가장 앞 자원 하나씩 (예: 연휴 = 휴일 약국 + 안부 전화 + 생필품 창구),
  그다음 순서대로 채운다. 순서는 그대로 (고른 자원을 원래 순위 순으로 둠).
  빈자리는 다음 자원으로 채우고, 모자라면 모자란 대로. 건너뛴 자원은 "확인할 자원"으로 사유와 함께 남긴다.
  위기 전화(crisis_lines)는 순위에 넣지 않고 "위급 시 안내" 묶음으로 따로 둔다.
"확인 필요"의 이유는 checks 에 코드로 따로 남긴다 (자격 미상 / 휴일 운영 미상 / 위치 미확인 / 운영 상태 미상 …).
원인 유형은 "+"로 여럿을 이을 수 있다 (예: 거점 원거리형+한파·대설형): 필요 유형은 합치고 표시는 하나라도 켜지면 켬.
결정: 추천 / 확인 필요 / 제외. 판단은 담당자가 하며 이 결과는 후보 목록이다.
"""

import re
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
import yaml

from bigcontest.config import ROOT
from bigcontest.resources.ledger import CITY_PREFIX, ELIGIBILITY_RULES, NONSPATIAL_KINDS, SPATIAL

NEEDS = ROOT / "configs" / "need_types.yaml"
PHONE = {"전화", "온라인"}

# "확인 필요" 이유 코드 (서로 섞지 않음)
CHECK_ELIG = "자격 미상"
CHECK_HOLIDAY = "휴일 운영 미상"
CHECK_LOCATION = "위치 미확인"
CHECK_OPERATION = "운영 상태 미상"
CHECK_OUT_OF_AREA = "관할 밖 거점"
CHECK_COORD = "좌표 탐색용"


def load_needs(path=NEEDS) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def city_of(adm_cd: str) -> str:
    for city, prefix in CITY_PREFIX.items():
        if str(adm_cd).startswith(prefix):
            return city
    raise ValueError(f"강남·춘천 행정동 코드가 아님: {adm_cd}")


def need_mask(d: pd.DataFrame, need_type: str, cfg: dict) -> pd.Series:
    spec = cfg["need_types"][need_type]
    return (
        d["type"].isin(spec.get("types", []))
        | d["subtype"].isin(spec.get("subtypes", []))
        | d["resource_id"].isin(spec.get("resource_ids", []))
    )


def group_eligibility(row, group: str) -> tuple[str, str]:
    """(해당됨 / 해당 안 됨 / 확인 필요, 이유). 자격 기준을 모르면 확인 필요."""
    if row["eligibility_rule_status"] != "확인됨" or row["eligibility"] not in ELIGIBILITY_RULES:
        return "확인 필요", f"자격 기준 미상 ({row['eligibility'] or '빈 값'})"
    verdicts, why = ELIGIBILITY_RULES[row["eligibility"]]
    return verdicts.get(group, "확인 필요"), why


def check_eligibility(d: pd.DataFrame, resource_id: str, group: str) -> dict:
    row = d.loc[d["resource_id"] == resource_id]
    if row.empty:
        return {"resource_id": resource_id, "group": group, "verdict": "확인 필요", "reason": "원장에 없는 자원"}
    v, why = group_eligibility(row.iloc[0], group)
    return {"resource_id": resource_id, "group": group, "verdict": v, "reason": why}


@dataclass
class Match:
    resource_id: str
    name: str
    decision: str                 # 추천 / 확인 필요 / 제외
    eligibility: str = ""         # 해당됨 / 해당 안 됨 / 확인 필요 (집단을 줬을 때)
    distance_km: float | None = None
    delivery_channel: str = ""
    coordinate_status: str = ""
    source_url: str = ""
    verified_date: str = ""
    reasons: list[str] = field(default_factory=list)  # 판단 이유
    flags: list[str] = field(default_factory=list)    # 주의 표시 (위치 확인 필요 등)
    checks: list[str] = field(default_factory=list)   # 확인할 것 코드 (CHECK_*), 이유마다 따로
    subtype: str = ""
    last_verified: str = ""                           # 날짜로 확인된 확인일 (없으면 빈 값 → G-07 제외)
    crisis: bool = False                              # 위기 전화 (위급 시 안내 묶음)
    resource_kind: str = ""                           # 시설 / 전화 상담 / 제도·사업 / 사람
    needs: list[str] = field(default_factory=list)    # 이 자원이 맞는 필요 유형 (요청한 것 중)
    holiday_open: str = ""                            # 예 / 아니오 / 미상


def _haversine(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return float(6371 * 2 * np.arcsin(np.sqrt(a)))


def split_cause(cause_type: str | None) -> list[str]:
    """'거점 원거리형+한파·대설형' → ['거점 원거리형', '한파·대설형']."""
    return [c.strip() for c in (cause_type or "").split("+") if c.strip()]


def _needs_for(need_type, cause_type, cfg) -> tuple[list[str] | None, dict]:
    """(필요 유형 목록, 합친 원인 설정). 원인이 여럿이면 필요 유형은 순서대로 합치고 표시는 하나라도 켜지면 켬."""
    parts = split_cause(cause_type)
    unknown = [c for c in parts if c not in cfg["cause_types"]]
    if unknown:
        raise KeyError(f"모르는 원인 유형: {unknown}")
    cause: dict = {}
    needs: list[str] = []
    for c in parts:
        spec = cfg["cause_types"][c]
        needs += [n for n in spec.get("needs", []) if n not in needs]
        cause.update({k: v for k, v in spec.items() if k != "needs" and v})
    if parts:
        cause["needs"] = needs
    if need_type:
        return [need_type], cause
    return (needs or None), cause


def is_crisis(row, cfg: dict) -> bool:
    """위기 전화 (자살예방·정신건강 위기상담). 첫 추천이 아니라 '위급 시 안내'로 따로 보인다."""
    spec = cfg.get("crisis_lines") or {}
    pat = spec.get("name_pattern")
    return row["resource_id"] in spec.get("resource_ids", []) or bool(pat and re.search(pat, str(row["name"])))


def elderly_rank(m: Match, cfg: dict, prefer_phone: bool = False) -> int | None:
    """고령층 안부·관계 첫 순위 (elderly_first) 안의 자리. 해당 없으면 None.
    far_phone 칸: 거점 원거리형일 때 위기 전화가 아닌 전화형 자리."""
    for i, spec in enumerate(cfg.get("elderly_first") or []):
        if spec.get("far_phone"):
            if prefer_phone and m.delivery_channel in PHONE and not m.crisis:
                return i
            continue
        if spec.get("name_pattern") and re.search(spec["name_pattern"], m.name):
            return i
        if m.subtype in spec.get("subtypes", []) or m.delivery_channel in spec.get("delivery_channels", []):
            return i
    return None


def match_resources(d: pd.DataFrame, adm_cd: str | None, group: str | None = None, need_type: str | None = None,
                    cause_type: str | None = None, origin: tuple[float, float] | None = None,
                    max_km: float | None = None, cfg: dict | None = None, city: str | None = None) -> list[Match]:
    """원장 모든 행에 대한 판단 (제외 포함). origin = (위도, 경도), 보통 행정동 중심점.
    adm_cd 없이 city 만 주면 도시 전체 목록 (관할 행정동 자원도 남기고 거리는 재지 않음, API GET /resources)."""
    cfg = cfg or load_needs()
    city = city_of(adm_cd) if adm_cd else city
    if city is None:
        raise ValueError("adm_cd 또는 city 가 필요함")
    needs, cause = _needs_for(need_type, cause_type, cfg)
    in_need = pd.Series(True, index=d.index)
    masks = {n: need_mask(d, n, cfg) for n in needs or []}
    if needs:
        in_need = pd.concat(list(masks.values()), axis=1).any(axis=1)
    out = []
    for i, r in d.iterrows():
        m = Match(r["resource_id"], r["name"], "추천", delivery_channel=r["delivery_channel"],
                  coordinate_status=r["coordinate_status"], source_url=r["source_url"], verified_date=r["verified_date"],
                  subtype=r["subtype"], last_verified=r.get("last_verified", ""), crisis=is_crisis(r, cfg),
                  resource_kind=r.get("resource_kind", ""), needs=[n for n in masks if masks[n][i]],
                  holiday_open=r["holiday_open"])
        out.append(m)
        phone = r["delivery_channel"] in PHONE
        nonspatial = m.resource_kind in NONSPATIAL_KINDS
        # 0. 출처·확인일 (G-07)
        if not all(str(r[c]).strip() for c in ["source_url", "verified_date", "city", "target_stage"]):
            m.decision, m.reasons = "제외", ["출처·확인일·지역·대상 중 빠진 것이 있음 (G-07)"]
            continue
        if not r["last_verified"]:
            m.flags.append(f"확인일 재확인 필요 ({r['verified_date']})")
        # 1. 관할
        if r["city"] != city:
            m.decision, m.reasons = "제외", ["다른 도시 자원"]
            continue
        if adm_cd and r["service_area"] == "관할 행정동" and r["adm_cd"] != adm_cd:
            m.decision, m.reasons = "제외", [f"관할 행정동이 아님 ({r['adm_nm']})"]
            continue
        # 2. 대상 연령·자격
        if group:
            v, why = group_eligibility(r, group)
            m.eligibility = v
            if v == "해당 안 됨":
                m.decision, m.reasons = "제외", [f"대상 아님: {why}"]
                continue
            if v == "확인 필요":
                m.decision = "확인 필요"
                m.checks.append(CHECK_ELIG)
            m.reasons.append(f"자격 {v}: {why}")
        # 3. 필요 유형
        if not in_need[i] and not (phone and cause.get("prefer_phone")):
            m.decision, m.reasons = "제외", ["필요 유형에 맞지 않음"]
            continue
        if needs and in_need[i]:
            m.reasons.append(f"필요 유형: {', '.join(m.needs)}")
        # 4. 운영 상태
        if r["operating_status"] == "중단":
            m.decision, m.reasons = "제외", ["운영 중단"]
            continue
        if r["operating_status"] == "확인 필요":
            m.flags.append("새 목록에서 빠짐: 운영 확인 필요")
            m.checks.append(CHECK_OPERATION)
        elif r["operating_status"] == "미상":
            m.flags.append("운영 상태 미확인")
            m.checks.append(CHECK_OPERATION)
        if cause.get("needs_holiday_open"):
            if r["holiday_open"] == "아니오":
                m.decision, m.reasons = "제외", ["휴일 미운영 (연휴 대응)"]
                continue
            if r["holiday_open"] == "미상":
                m.flags.append("휴일 운영 확인 필요")
                m.checks.append(CHECK_HOLIDAY)
        # 5. 거리 / 전화형 / 위치가 없는 자원 (제도·사업, 사람)
        if nonspatial and r["coordinate_status"] == "해당 없음":
            m.reasons.append(f"{m.resource_kind}: 위치 없음, 거리와 무관 (범위: {r['service_area']})")
        elif phone:
            m.reasons.append("전화형: 거리와 무관")
            if cause.get("prefer_phone"):
                m.reasons.append("거점 원거리형: 전화형 우선" if not m.crisis else "위기 전화: 위급 시 안내로 따로 둠")
        elif r["coordinate_status"] in SPATIAL:
            if origin is not None:
                m.distance_km = _haversine(origin[0], origin[1], float(r["lat"]), float(r["lon"]))
                if max_km is not None and m.distance_km > max_km:
                    m.decision, m.reasons = "제외", [f"거리 {m.distance_km:.1f}km > {max_km}km"]
                    continue
            if r["coordinate_status"] == "탐색용":
                m.flags.append("좌표 탐색용 (도로명주소 확인 전)")
                m.checks.append(CHECK_COORD)
        elif r["coordinate_status"] == "권역 외":
            # 관할 밖 물리 거점: 추천하지 않고 최대 "확인 필요" (이용 가능 여부를 담당자가 확인)
            m.decision = "확인 필요"
            m.reasons.append("권역 외 물리 거점: 관할 밖이라 추천하지 않음, 거리 계산 제외")
            m.flags.append("권역 외 (관할 밖 거점)")
            m.checks.append(CHECK_OUT_OF_AREA)
            if r["lat"] == "":
                m.flags.append("위치 확인 필요")
                m.checks.append(CHECK_LOCATION)
        else:
            m.flags.append("위치 확인 필요")
            m.checks.append(CHECK_LOCATION)
            m.reasons.append(f"좌표 {r['coordinate_status']}: 거리 계산 제외")
    return out


def find_resources(d: pd.DataFrame, adm_cd: str | None, group: str | None = None, need_type: str | None = None,
                   cause_type: str | None = None, origin: tuple[float, float] | None = None,
                   max_km: float | None = None, cfg: dict | None = None, city: str | None = None) -> list[Match]:
    """제외하지 않은 자원만, 순서대로 (모듈 설명의 '순위'). 위기 전화도 목록에는 남는다 (select_resources 가 따로 묶음)."""
    cfg = cfg or load_needs()
    needs, cause = _needs_for(need_type, cause_type, cfg)
    keep = [m for m in match_resources(d, adm_cd, group, need_type, cause_type, origin, max_km, cfg, city)
            if m.decision != "제외"]
    elderly = group == "고령층" and "안부·관계" in (needs or [])
    n_old = len(cfg.get("elderly_first") or [])

    def key(m: Match):
        phone = m.delivery_channel in PHONE
        tier = 0
        if cause.get("prefer_phone"):
            tier = 0 if (phone and not m.crisis) else (1 if m.delivery_channel == "찾아가는 서비스" else 2)
        if elderly:
            e = elderly_rank(m, cfg, bool(cause.get("prefer_phone")))
            tier = e if e is not None else n_old + tier
        nonspatial = m.resource_kind in NONSPATIAL_KINDS and m.coordinate_status == "해당 없음"
        bucket = 0 if m.distance_km is not None else (1 if phone or nonspatial else 2)  # 거리 있음 → 전화형·제도 → 위치 미확인
        holiday = 0 if (not cause.get("needs_holiday_open") or m.holiday_open == "예") else 1  # 연휴: 휴일 운영 확인된 곳 먼저
        return (tier, 0 if m.decision == "추천" else 1, holiday, bucket, m.distance_km or 0.0, m.resource_id)

    return sorted(keep, key=key)


def hard_exclusion(m: Match) -> list[str]:
    """추천 전에 반드시 빼는 이유 (G-07 과 같은 기준 + 관할 밖 물리 거점). 빈 목록이면 추천할 수 있음."""
    why = []
    if not str(m.source_url).strip():
        why.append("출처 URL 없음 (G-07)")
    if not m.last_verified:
        why.append(f"확인일이 날짜가 아님 ({m.verified_date or '빈 값'}, G-07)")
    if m.coordinate_status == "권역 외" and m.delivery_channel not in PHONE:
        why.append("관할 밖 물리 거점 (권역 외)")
    return why


@dataclass
class Selection:
    ranked: list[Match]                                       # 추천 후보 상위 K (제외 규칙을 먼저 적용)
    crisis: list[Match]                                       # 위급 시 안내 (순위 밖, 늘 아래에)
    excluded: list[tuple[Match, list[str]]]                   # 확인할 자원: 상위 K 를 채우다 건너뛴 자원과 이유
    n_candidates: int = 0                                     # 매칭을 통과한 자원 수 (제외 규칙 전)
    n_valid: int = 0                                          # 제외 규칙까지 통과한 자원 수 (위기 전화 빼고)
    n_excluded: int = 0                                       # 제외 규칙에 걸린 자원 수 (전체)


def select_resources(d: pd.DataFrame, adm_cd: str, group: str | None = None, need_type: str | None = None,
                     cause_type: str | None = None, origin: tuple[float, float] | None = None,
                     max_km: float | None = None, cfg: dict | None = None, limit: int = 5,
                     per_subtype: int | None = 2) -> Selection:
    """find_resources 순서에서 제외 규칙을 먼저 적용하고 상위 limit 를 뽑는다. 빈자리는 다음 자원으로 채우고
    모자라면 모자란 대로 둔다. 위기 전화는 순위 밖 '위급 시 안내'로.
    per_subtype: 같은 원장 분류(subtype)는 상위에 이만큼까지 (경로당 5곳·방문요양 5곳만 나오지 않게).
    분류 한도로 못 들어간 자원은 제외가 아니라 그다음 순서이며, 다른 자원이 모자라면 다시 채운다."""
    cfg = cfg or load_needs()
    needs, _ = _needs_for(need_type, cause_type, cfg)
    found = find_resources(d, adm_cd, group, need_type, cause_type, origin, max_km, cfg)
    crisis: list[Match] = []
    valid: list[tuple[int, Match]] = []                       # (found 안 자리, 자원)
    skipped: list[tuple[int, Match, list[str]]] = []          # 제외 규칙에 걸린 자원
    for pos, m in enumerate(found):
        why = hard_exclusion(m)
        if why:
            skipped.append((pos, m, why))
        elif m.crisis:
            crisis.append(m)
        else:
            valid.append((pos, m))
    cap = per_subtype if per_subtype is not None else limit
    picked: list[int] = []      # valid 안 자리
    count: dict[str, int] = {}

    def take(j: int):
        picked.append(j)
        count[valid[j][1].subtype] = count.get(valid[j][1].subtype, 0) + 1

    # 1) 필요 유형마다 그 유형의 가장 앞 자원 하나 (그 자원을 이미 골랐으면 건너뜀)
    for need in needs or []:
        if len(picked) >= limit:
            break
        for j, (_, m) in enumerate(valid):
            if need in m.needs and count.get(m.subtype, 0) < cap:
                if j not in picked:
                    take(j)
                break
    # 2) 순서대로 채움 (같은 분류는 cap 까지). 3) 모자라면 분류 한도로 미룬 자원으로
    held = []
    for j, (_, m) in enumerate(valid):
        if len(picked) >= limit:
            break
        if j in picked:
            continue
        if count.get(m.subtype, 0) >= cap:
            held.append(j)
            continue
        take(j)
    main = sorted(picked)
    main += held[: max(0, limit - len(main))]  # 다른 분류가 모자라면 미룬 자원으로 채움 (순서 유지)
    ranked = [valid[j][1] for j in main]
    # 확인할 자원: 상위 K 의 마지막 자원보다 앞에서 건너뛴 자원 (모자라면 모두) + 위기 전화의 제외 사유
    last = max((valid[j][0] for j in main), default=-1) if len(ranked) >= limit else len(found)
    excluded = [(m, why) for pos, m, why in skipped if pos < last or m.crisis]
    return Selection(ranked, crisis, excluded, len(found), len(valid), len(skipped))


def access_gap(d: pd.DataFrame, adm_cd: str, origin: tuple[float, float], need_type: str,
               group: str | None = None, cfg: dict | None = None) -> dict:
    """관측 거점까지 가장 가까운 직선거리 + 전화형 대안 + 위치 미확인 수. 실제 공급 부족이라는 뜻은 아님."""
    found = find_resources(d, adm_cd, group, need_type, origin=origin, cfg=cfg)
    dist = [m.distance_km for m in found if m.distance_km is not None]
    return {
        "adm_cd": adm_cd, "need_type": need_type, "group": group,
        "nearest_km": min(dist) if dist else None,
        "n_spatial": len(dist),
        "phone_alternatives": [m.resource_id for m in found if m.delivery_channel in PHONE],
        "n_location_unknown": sum("위치 확인 필요" in m.flags for m in found),
    }


def to_frame(matches: list[Match]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(m) for m in matches])
    if not df.empty:
        for c in ("reasons", "flags", "checks"):
            df[c] = df[c].map("; ".join)
    return df
