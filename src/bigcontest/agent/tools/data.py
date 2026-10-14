"""Data 도구 (에이전트 설계 §5): 지원 범위, 요청 계약, 구조 지표, 통신 레이어, 카드 코호트 시계열, 시민생활 참고.

모두 `datasource` 의 고정 스냅샷만 읽고, 기준일·분석 모드에서 쓸 수 있던 달·날만 쓴다 (contracts.time).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bigcontest.agent.schemas import ToolResult
from bigcontest.agent.tools.common import (
    MERCHANT,
    RESIDENCE,
    SEX_CODE,
    SEX_KO,
    age_code,
    daily_cutoff,
    empty,
    envelope,
    error,
    lag_days,
    usable_months,
)
from bigcontest.agent.tools.datasource import open_datasource
from bigcontest.contracts import cohorts_config
from bigcontest.contracts.claims import claim_lists
from bigcontest.contracts.cohort import resolve_cohort
from bigcontest.contracts.grain import check_request_contract as _contract
from bigcontest.contracts.grain import get_data_capabilities as _capabilities

SEX_CODE_OF = SEX_CODE  # 남성 → M, 여성 → F
CARD_METRIC_SERIES = "카드 결제건수 일별 시계열"
CARD_METRIC_LEVEL = "카드 결제건수 최근 7일 평소 대비"
CARD_DENOM = "카드 데이터2 결제건수 (같은 시도 거주 고객, 본사등록 업종 제외)"
CARD_ZERO_NOTE = "행 없음=0건 가정: 카드 결제 행이 없는 날은 0건으로 채움 (미공급과 구분 못 함)"


def card_zero_notes(ds, col, start, end) -> list[str]:
    """카드 근거 경고: 0건 가정 + 이 기간에 실제로 행이 없던(0으로 채운) 날 수 (있을 때만)."""
    n = ds.card_missing_days(col, start, end)
    return [CARD_ZERO_NOTE] + ([f"이 기간에 결제 행이 없어 0건으로 채운 날 {n}일 (미공급일 수 있음)"] if n else [])
STRUCTURE_METRIC = "1인세대 비율"
SL_METRIC = "시민생활 '외출·소통 모두 적음' 비율 (탐색적 참고)"
SL_NOTE = "시민생활은 탐색적 검증 참고. 등급 입력 아님 (관측 기간 미확인, 같은 공급자 자료라 독립 검증 아님)"
TEL_AGE_COL = {"10대": "10G", "20대": "20G", "30대": "30G", "40대": "40G", "50대": "50G", "60+": "60GU"}


def _adm_label(ds, adm_cd: str) -> str:
    r = ds.adm_index.get(adm_cd)
    city = r["city"] if r else ""
    return f"{city} {r['adm_nm']} ({adm_cd})" if r else f"({adm_cd})"


def _city_adms(ds, city: str, adm_cd: str | None = None) -> list[str]:
    names = ds.adm_names()
    adms = names.loc[names["city"] == city, "adm_cd"].tolist()
    return [a for a in adms if adm_cd is None or a == adm_cd]


def _group_sex(req) -> str | None:
    if req.sex:
        return req.sex
    if req.group:
        g = cohorts_config()["groups"].get(req.group)
        return g["service"]["sex"] if g else None
    return None


# ---------------------------------------------------------------- 지원 범위·계약


def get_data_capabilities(ctx) -> list[ToolResult]:
    cap = _capabilities()
    return [
        envelope(ctx, "CAP", None, metric="데이터 지원 범위", value=len(cap.sources), unit="원천",
                 measured_grain="원천", applied_scope="강남·춘천", interpretation_status="직접 확인",
                 valid_claims=[f"{s.source}: {', '.join(s.dimensions)} ({s.measured_grain})" for s in cap.sources],
                 forbidden_claims=[f"{'·'.join(c['dims'])} ({', '.join(c['metrics'])}): {c['reason']}"
                                   for c in cap.forbidden_combos],
                 data_version=f"{cap.grain_version}", value_state="observed")
    ]


def check_request_contract(ctx) -> list[ToolResult]:
    """대상 집단·단위 계약 (contracts.grain)."""
    if ctx.request is None:
        return [empty(ctx, "TGT", "대상 집단 계약", "정규화한 요청이 없음")]
    res = _contract(ctx.request)
    if res.status != "OK":
        return [error(ctx, "TGT", "대상 집단 계약", res.reason_codes[0], res.issues[0].message)]
    return [envelope(ctx, "TGT", None, metric="대상 집단 계약", value=res.analysis_cohort or "전체",
                     measured_grain=res.measured_grain, applied_scope=ctx.request.city,
                     analysis_cohort=res.analysis_cohort, service_eligibility_group=res.service_eligibility_group,
                     interpretation_status="직접 확인", warnings=list(res.warnings),
                     data_version=f"contract:{res.source}")]


# ---------------------------------------------------------------- 구조 지표


def get_structure(ctx, adm_cd: str | None = None) -> list[ToolResult]:
    """주민등록 1인세대 비율 (행정동 × 월). 기준일에 공개된 가장 최근 달."""
    ds = open_datasource(ctx.snapshot_id)
    city = ctx.request.city
    months = usable_months("resident_hh", ctx.as_of, ctx.analysis_mode)
    if not months:
        return [empty(ctx, "STR", STRUCTURE_METRIC, f"기준일 {ctx.as_of}({ctx.analysis_mode})에 쓸 수 있는 주민등록 세대 자료 없음")]
    item = months[-1]
    h = ds.households()
    h = h[h["month"] == int(item.base_month[4:])].set_index("adm_cd")
    valid, forbidden = claim_lists("resident_hh")
    # 순위·분모는 조회 필터와 무관하게 도시 × 기준월 행정동 전체에서 정한다 (표시만 필터)
    from bigcontest.rules.candidates import rank_population, rank_warning

    full = {a: float(h.loc[a, "one_person_rate"]) for a in _city_adms(ds, city)
            if a in h.index and pd.notna(h.loc[a, "one_person_rate"])}
    ranks = rank_population(full)
    population = f"{city} {len(full)}개 동 전체, {item.base_month[:4]}-{item.base_month[4:]} 주민등록 (조회 필터와 무관)"
    out = []
    for adm in _city_adms(ds, city, adm_cd or (ctx.request.adm_cd if ctx.request else None)):
        if adm not in h.index:
            out.append(empty(ctx, f"STR-{adm}", STRUCTURE_METRIC, f"{adm} 세대 자료 없음", source="resident_hh",
                             applied_scope=_adm_label(ds, adm)))
            continue
        r = h.loc[adm]
        v = r["one_person_rate"]
        ok = pd.notna(v)
        out.append(envelope(
            ctx, f"STR-{adm}", "resident_hh", metric=STRUCTURE_METRIC,
            value=round(float(v), 4) if ok else None, unit="비율", denominator="전체 세대수 (주민등록)",
            n_obs=int(r["전체세대수"]) if pd.notna(r["전체세대수"]) else None,
            measured_grain="행정동 × 월", applied_scope=_adm_label(ds, adm),
            observed_from=item.observed_from, observed_to=item.observed_to,
            simulated_available_at=item.simulated_available_at, interpretation_status="직접 확인",
            valid_claims=valid, forbidden_claims=forbidden, value_state="observed" if ok else "missing",
            warnings=[rank_warning(population, *ranks[adm])] if adm in ranks else [],
        ))
    shown = [a for a in _city_adms(ds, city, adm_cd or (ctx.request.adm_cd if ctx.request else None)) if a in h.index]
    out += structure_context(ctx, ds, city, item, h, shown)
    return out


# ---------------------------------------------------------------- 구조 맥락 (rules-v1.1)

TARGET_POP_METRIC = "대상 성·연령 인구 비중"
RECIPIENT_METRIC = "기초생활수급자 비율 (구조 별도 칸, 표시만)"
NET_METRIC = "1인세대 월 순증률 (전입 맥락, 표시만)"
RECIPIENT_PUBLISHED = pd.Timestamp("2025-12-01").date()  # 서울시 기본통계 자료갱신일 (2024년 값)
NO_RECIPIENT_CITY = "춘천은 동별 기초생활수급자 공개 자료 없음 (시군구 통계만). 보류 사유가 아니라 자료 한계"
TARGET_WARN = "대상 인구 모집단:"


def target_population_spec(group: str | None, rules: dict | None = None) -> dict | None:
    """rules.yaml thresholds.structure.target_population 에서 이 집단의 성·연령 (없으면 None)."""
    from bigcontest.rules import load_rules

    if not group:
        return None
    tp = (rules or load_rules())["thresholds"]["structure"].get("target_population") or {}
    from bigcontest.rules.candidates import group_of_label

    name = group_of_label(group) or group
    spec = (tp.get("groups") or {}).get(name)
    return {**spec, "quantile": tp.get("quantile", 0.5), "group": name} if spec else None


def structure_context(ctx, ds, city: str, item, h: pd.DataFrame, adms: list[str]) -> list[ToolResult]:
    """구조 지표 옆의 맥락 근거 (rules-v1.1).

    - POP-<adm>: 남성 집단의 대상 성·연령 인구 비중 + 도시 중앙값 (구조 상위 조건, rules.yaml target_population)
    - REC-<adm>: 기초생활수급자 비율 (강남만, 표시·원인 유형용. 등급 입력 아님)
    - NET-<adm>: 1인세대 월 순증률 (전입 맥락, 표시만)
    """
    out: list[ToolResult] = []
    req = ctx.request
    month = int(item.base_month[4:])
    label = {a: _adm_label(ds, a) for a in adms}
    common = dict(observed_from=item.observed_from, observed_to=item.observed_to,
                  simulated_available_at=item.simulated_available_at, interpretation_status="직접 확인")

    spec = target_population_spec(req.group if req else None)
    if spec is not None:
        rp = ds.resident_pop()
        rp = rp[rp["month"] == month]
        tot = rp.groupby("adm_cd")["pop"].sum()
        sel = rp[(rp["sex"] == spec["sex"]) & rp["age"].between(spec["age_min"], spec["age_max"])]
        share = (sel.groupby("adm_cd")["pop"].sum() / tot).dropna()
        city_all = share[share.index.isin(_city_adms(ds, city))]
        med = float(city_all.quantile(spec["quantile"])) if len(city_all) else None
        who = f"{spec['age_min']}~{spec['age_max']}세 {'남성' if spec['sex'] == '남' else '여성'}"
        for adm in adms:
            if adm not in share.index or med is None:
                continue
            v = float(share[adm])
            out.append(envelope(
                ctx, f"POP-{adm}", "resident_pop", metric=TARGET_POP_METRIC, value=round(v, 4), unit="비율",
                denominator="총인구 (주민등록, 같은 동·같은 달)", n_obs=int(tot.get(adm, 0)),
                measured_grain="행정동 × 월 × 성 × 1세 연령", applied_scope=label[adm], analysis_cohort=who,
                valid_claims=["동별 인구 구성"], forbidden_claims=["1인가구 수", "독거 중장년 남성 수 (1인세대와 곱하지 않음)"],
                value_state="observed",
                warnings=[f"{TARGET_WARN} {city} {len(city_all)}개 동 {who} 비중 중앙값 {med:.4f} "
                          f"({'이상' if v >= med else '미만'}) · 집단 {spec['group']}",
                          "구조 상위 조건: 1인세대 비율 상위 + 대상 인구 비중이 도시 중앙값 이상"],
                **common))

    if city == "강남":
        rec = ds.recipients()
        before = ctx.as_of < RECIPIENT_PUBLISHED
        if ctx.analysis_mode == "strict_as_of" and before:
            out.append(empty(ctx, "REC", RECIPIENT_METRIC, f"2024년 수급자 통계는 {RECIPIENT_PUBLISHED} 공개라 "
                             f"strict_as_of 기준일 {ctx.as_of}에는 쓰지 않음"))
        elif len(rec):
            rp = ds.resident_pop()
            m0 = int(rp["month"].min())
            pop = rp[rp["month"] == m0].groupby("adm_cd")["pop"].sum()
            rec = rec.dropna(subset=["adm_cd", "persons"]).set_index("adm_cd")
            rate = (rec["persons"] / pop.reindex(rec.index)).dropna()
            ranks = {a: i + 1 for i, a in enumerate(sorted(rate.index, key=lambda a: (-rate[a], a)))}
            n = len(rate)
            for adm in adms:
                if adm not in rate.index:
                    continue
                out.append(envelope(
                    ctx, f"REC-{adm}", "welfare_recipients", metric=RECIPIENT_METRIC, value=round(float(rate[adm]), 4),
                    unit="비율", denominator=f"총인구 (주민등록 2025-{m0:02d})", n_obs=int(pop.get(adm, 0)),
                    measured_grain="행정동 × 연(2024년 말)", applied_scope=label[adm],
                    availability_basis="unknown", interpretation_status="직접 확인", value_state="observed",
                    valid_claims=["동별 기초생활수급자 비율 (구조 맥락)"],
                    forbidden_claims=["개인·가구의 수급 여부", "수급자 = 고립", "등급 근거"],
                    warnings=[f"도시 안 {ranks[adm]}/{n}위 (강남 {n}개 동 전체)",
                              f"분자 2024년 수급자 인원(서울시 기본통계, 공개 {RECIPIENT_PUBLISHED}), 분모 2025-{m0:02d} 주민등록 인구: 연도가 다름",
                              "구조 별도 칸: 1인세대 비율과 합치지 않고 등급에 쓰지 않음 (원인 유형 '경제 취약형' 표시용)"]
                    + ([f"기준일 {ctx.as_of}은 공개({RECIPIENT_PUBLISHED}) 전: 지금 공개된 값으로 보여 줌 (당시에는 없던 값)"]
                       if before else [])))
    else:
        out.append(empty(ctx, "REC", RECIPIENT_METRIC, NO_RECIPIENT_CITY))

    prev = month - 1
    hp = ds.households()
    hp = hp[hp["month"] == prev].set_index("adm_cd") if prev >= 7 else None
    if hp is not None and len(hp):
        net = {}
        for adm in adms:
            if adm in h.index and adm in hp.index and pd.notna(h.loc[adm, "1인세대"]) and hp.loc[adm, "1인세대"] > 0:
                net[adm] = float(h.loc[adm, "1인세대"] / hp.loc[adm, "1인세대"] - 1)
        full = {}
        for a in _city_adms(ds, city):
            if a in h.index and a in hp.index and pd.notna(h.loc[a, "1인세대"]) and hp.loc[a, "1인세대"] > 0:
                full[a] = float(h.loc[a, "1인세대"] / hp.loc[a, "1인세대"] - 1)
        ranks = {a: i + 1 for i, a in enumerate(sorted(full, key=lambda a: (-full[a], a)))}
        for adm, v in net.items():
            out.append(envelope(
                ctx, f"NET-{adm}", "resident_hh", metric=NET_METRIC, value=round(v, 4), unit="비율",
                denominator=f"전월(2025-{prev:02d}) 1인세대 수", n_obs=int(hp.loc[adm, "1인세대"]),
                measured_grain="행정동 × 월", applied_scope=label[adm], value_state="observed",
                valid_claims=["1인세대 수의 월 변화 (등록 기준)"],
                forbidden_claims=["전입자 수 (순증은 전입·전출·분가·사망이 섞임)", "고립 위험 증가"],
                warnings=[f"도시 안 순증률 {ranks[adm]}/{len(full)}위",
                          "전입 맥락 표시만 (참고용 맥락, 등급에 쓰지 않음). 이사 뒤 관계망 약화는 근거 방향이 섞여 위험 요인으로 쓰지 않음"],
                **common))
    return out


# ---------------------------------------------------------------- 통신 레이어


def get_mobility_layer(ctx, by_adm: bool = False) -> list[ToolResult]:
    """통신 성연령: 생활권 체류인구 중 요청 코호트 비중 (구성 레이어, 위험 지표 아님)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    months = usable_months("tel_age", ctx.as_of, ctx.analysis_mode)
    if not months:
        return [empty(ctx, "MOB", "체류인구 구성", f"기준일 {ctx.as_of}에 공개된 통신 자료 없음")]
    item = months[-1]
    res = resolve_cohort("tel_age", req.age_min, req.age_max, req.sex, req.group) if (
        req.group or req.age_min is not None or req.age_max is not None) else None
    if res is not None and not res.supported:
        return [empty(ctx, "MOB", "체류인구 구성", res.message or "통신 연령 구간으로 나타낼 수 없음")]
    bands = res.bands if res else ["60+"]
    sexes = {"남": ["MAN"], "여": ["WMAN"]}.get(_group_sex(req) or "", ["MAN", "WMAN"])
    cols = [f"{s}_FLOW_POP_CNT_{TEL_AGE_COL[b]}" for s in sexes for b in bands]
    all_cols = [f"{s}_FLOW_POP_CNT_{a}" for s in ("MAN", "WMAN") for a in TEL_AGE_COL.values()]
    t = ds.tel("tel_age")
    t = t[(t["city"] == city) & (t["STD_YM"] == item.base_month)]
    label = "·".join(bands) + ("" if len(sexes) == 2 else (" 남성" if sexes == ["MAN"] else " 여성"))
    valid, forbidden = claim_lists("tel_age")
    common = dict(unit="비율", denominator="생활권 체류인구 전체 (SK 통신 성연령, 같은 달)",
                  measured_grain="50m 칸 × 월 × 성 × 연령(60+ 묶음)", observed_from=item.observed_from,
                  observed_to=item.observed_to, simulated_available_at=item.simulated_available_at,
                  analysis_cohort=label, interpretation_status="계산 재현", valid_claims=valid,
                  forbidden_claims=forbidden + ["위험 지표 (고령 비중 = 위험 가설은 폐기)"],
                  warnings=["체류·활동 집계라 주민 수가 아님. 구성 레이어로만 씀"])
    groups = [(None, t)] + ([(a, g) for a, g in t.groupby("adm_cd")] if by_adm else [])
    out = []
    for adm, g in groups:
        tot = float(g[all_cols].sum().sum())
        if tot <= 0:
            continue
        share = float(g[cols].sum().sum()) / tot  # n_obs = 분모 크기(체류인구 합, G-08 작은 칸 기준과 비교)
        code = f"MOB-{adm}" if adm else "MOB"
        scope = _adm_label(ds, adm) if adm else city
        out.append(envelope(ctx, code, "tel_age", metric=f"체류인구 중 {label} 비중", value=round(share, 4),
                            n_obs=int(round(tot)), applied_scope=scope, value_state="observed", **common))
    return out or [empty(ctx, "MOB", "체류인구 구성", "통신 자료에 값이 없음")]


# ---------------------------------------------------------------- 카드 코호트 시계열


def card_cohorts(req) -> tuple[list[tuple[str, str]], object]:
    """요청 → 카드 패널 열 [(연령, 성)] 과 연령 계약 결과."""
    res = resolve_cohort("card2", req.age_min, req.age_max, req.sex, req.group) if (
        req.group or req.age_min is not None or req.age_max is not None) else None
    if res is not None and not res.supported:
        return [], res
    ages = res.bands if res else ["20대", "30대", "40대", "50대", "60대", "70대", "80대+"]
    sex = _group_sex(req)
    sexes = [SEX_KO[sex]] if sex else ["남성", "여성"]
    return [(a, s) for a in ages for s in sexes], res


def get_card_cohort_series(ctx, days: int = 14) -> list[ToolResult]:
    """카드 데이터2 도시 × 연령 × 성 일별 결제건수 (공급 지연을 뺀 날까지)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    cutoff = daily_cutoff("card2", ctx.as_of, ctx.analysis_mode)
    if cutoff is None:
        return [empty(ctx, "SER", CARD_METRIC_SERIES, "TIME_AVAILABILITY_UNKNOWN: 카드 실제 공개일을 몰라 strict_as_of 에서 쓰지 않음")]
    cohorts, res = card_cohorts(req)
    if not cohorts:
        return [error(ctx, "SER", CARD_METRIC_SERIES, "COHORT_NOT_SUPPORTED", res.message or "카드 연령 구간 없음")]
    p = ds.panel()
    cut = pd.Timestamp(cutoff)
    if cut < p.dates[0]:
        return [empty(ctx, "SER", CARD_METRIC_SERIES, f"기준일에 쓸 수 있는 카드 자료 없음 ({cutoff} 이전)")]
    if req.period_label:  # 질문 기간 창 (nodes.scope_request 가 기준일까지로 자름). 공급 지연 뒤 날까지만
        cut = min(cut, pd.Timestamp(req.period_to))
        idx = np.where((p.dates <= cut) & (p.dates >= pd.Timestamp(req.period_from)))[0]
        if len(idx) == 0:
            return [empty(ctx, "SER", CARD_METRIC_SERIES,
                          f"질문 기간({req.period_label})에 기준일까지 쓸 수 있는 카드 자료 없음 (공급 지연, {cutoff}까지)")]
    else:
        idx = np.where(p.dates <= cut)[0]
    win = idx[-days:]
    win7 = idx[-7:]
    valid, forbidden = claim_lists("card2", is_shared_context=True)
    out = []
    for age, sex in cohorts:
        col = (city, age, sex)
        if col not in p.cols:
            out.append(empty(ctx, f"SER-{age_code(age)}{sex[0]}", CARD_METRIC_SERIES, f"패널에 없는 코호트 {age} {sex}"))
            continue
        j = p.cols.index(col)
        y = p.Y[col].to_numpy()[win]
        common = dict(
            denominator=CARD_DENOM, measured_grain="도시×성×연령×일", applied_scope=f"{city} (동 목록은 공유 맥락)",
            merchant_region=MERCHANT[city], customer_residence=RESIDENCE[city], is_shared_context=True,
            analysis_cohort=f"{age} {sex}", service_eligibility_group=res.service_eligibility_group if res else None,
            observed_to=p.dates[win[-1]].date(), interpretation_status="계산 재현",
            valid_claims=valid, forbidden_claims=forbidden + ["고립 발생"], value_state="observed", claim_category="전체 업종",
        )
        sim = p.dates[win[-1]].date() + pd.Timedelta(days=lag_days("card2"))
        key = f"{age_code(age)}{'M' if sex == '남성' else 'F'}"
        out.append(envelope(
            ctx, f"SER-{key}", "card2", metric=CARD_METRIC_SERIES, value=[float(v) for v in y],
            unit="건/일·연령별 배율값", n_obs=len(win), observed_from=p.dates[win[0]].date(),
            simulated_available_at=sim, warnings=card_zero_notes(ds, col, p.dates[win[0]], p.dates[win[-1]]),
            **common))
        # 최근 7일: 같은 요일 평소(공휴일 제외, 과거만) 대비
        e = p.E[win7, j]
        e = e[~np.isnan(e) & ~p.is_hol[win7]]
        if len(e) >= 4:
            from bigcontest.signals.alarm import direction

            m = float(np.mean(e))
            out.append(envelope(
                ctx, f"LVL-{key}", "card2", metric=CARD_METRIC_LEVEL, value=round(float(np.expm1(m)) + 1, 3),
                unit="평소 대비 비율", n_obs=len(e), direction=direction(m),
                observed_from=p.dates[win7[0]].date(), simulated_available_at=sim,
                trained_until=(p.dates[win7[0]] - pd.Timedelta(days=1)).date(),
                warnings=["평소 = 같은 요일 과거 8주 평균 (공휴일 제외, 기준일 전 자료만)"]
                + card_zero_notes(ds, col, p.dates[win7[0]] - pd.Timedelta(days=56), p.dates[win7[-1]]),
                **{**common, "denominator": "같은 코호트 같은 요일 과거 8주 평균 (카드 데이터2)"}))
        div = _diversity(ctx, ds, p, city, age, sex, win7, key, sim, common)
        if div is not None:
            out.append(div)
    return out


DIVERSITY_METRIC = "카드 업종 다양성 (결제량 보정, 평소 대비)"


def _diversity(ctx, ds, p, city, age, sex, win7, key, sim, common):
    """rules-v1.1 맥락: 하루 결제 N건당 기대 업종 수(희박화 근사)의 최근 7일 평균 ÷ 같은 요일 과거 8주 평균(공휴일 제외).
    결제량이 줄면 업종 수도 기계적으로 줄어드는 효과를 같은 건수로 맞춰 뺀다. 등급 입력 아님."""
    from bigcontest.rules import load_rules
    from bigcontest.rules.context import daily_diversity

    draws = load_rules()["thresholds"]["card_context"]["diversity_draws"]
    d = ds.cached(("diversity", city, age, sex, draws),
                  lambda: daily_diversity(ds.card2_categories(), city, age, sex, draws))
    if d.empty:
        return None
    d = d.reindex(p.dates)
    recent = d.iloc[win7].dropna()
    base_idx = sorted({i - 7 * k for i in win7 for k in range(1, 9) if i - 7 * k >= 0 and not p.is_hol[i - 7 * k]})
    base = d.iloc[base_idx].dropna()
    if len(recent) < 4 or len(base) < 14:
        return None
    ratio = float(recent.mean() / base.mean())
    return envelope(
        ctx, f"DIV-{key}", "card2", metric=DIVERSITY_METRIC, value=round(ratio, 3), unit="평소 대비 비율",
        n_obs=len(recent), direction="감소" if ratio < 0.97 else ("증가" if ratio > 1.03 else "변화 없음"),
        observed_from=p.dates[win7[0]].date(), simulated_available_at=sim,
        warnings=[f"최근 7일 결제 {draws}건당 기대 업종 수 {recent.mean():.1f}개 vs 평소 {base.mean():.1f}개 "
                  f"(같은 요일 과거 8주, 공휴일 제외)",
                  "결제량 보정: 하루 결제를 같은 건수로 맞춘 기대 업종 수 (복원 추출 근사). 업종은 카드 가맹점 업종 78종",
                  "맥락만 (참고용 맥락, 등급에 쓰지 않음): '생활 폭' 대리. 결제 총량과 따로 움직일 때만 정보가 됨"],
        **{**common, "denominator": "같은 코호트 같은 요일 과거 8주 평균 (카드 데이터2)"})


# ---------------------------------------------------------------- 카드 데이터1 시간대 (보조)

CARD1_METRIC = "카드 결제 시간대 비중 (카드 데이터1 보조)"
TIME_BAND_KO = {"00_05": "새벽 0~5시", "06_11": "오전 6~11시", "12_17": "낮 12~17시", "18_23": "저녁·밤 18~23시"}
CARD1_WARNINGS = [
    "카드 데이터1(보조): 고객 거주지 칸이 없어 결제한 사람이 이 도시 주민인지 알 수 없음",
    "가맹점 기준 결제 시간대일 뿐 체류·외출·고립을 뜻하지 않음 (밤 체류는 통신 자료로도 연령과 함께 볼 수 없음)",
    "결제건수는 연령별 배율값이라 같은 연령 안의 시간대 비중만 비교 (연령끼리 건수 비교 안 함)",
    "데이터2와 합산하지 않음. 법인·본사등록 업종·LPG가스(자동결제) 제외",
]


def get_card_time_band(ctx, days: int = 28) -> list[ToolResult]:
    """카드 데이터1: 도시 × 연령 결제 중 요청 시간대 비중 (최근 창 vs 바로 앞 같은 길이 창).

    질문에 기간 말이 있으면(request.period_label) 그 창(공급 지연을 뺀 날까지), 없으면 최근 days 일.
    연령마다 따로 (배율이 연령별이라 연령을 합치지 않음). 성별을 적으면 그 성별만."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    band = req.time_of_day
    if band not in TIME_BAND_KO:
        return [error(ctx, "TOD", CARD1_METRIC, "DIMENSION_NOT_AVAILABLE", "시간대가 없는 요청")]
    cutoff = daily_cutoff("card1", ctx.as_of, ctx.analysis_mode)
    if cutoff is None:
        return [empty(ctx, "TOD", CARD1_METRIC, "TIME_AVAILABILITY_UNKNOWN: 카드 실제 공개일을 몰라 strict_as_of 에서 쓰지 않음")]
    cohorts, res = card_cohorts(req)
    if not cohorts:
        return [error(ctx, "TOD", CARD1_METRIC, "COHORT_NOT_SUPPORTED", res.message or "카드 연령 구간 없음")]
    end = min(cutoff, req.period_to)
    start = req.period_from if req.period_label else end - pd.Timedelta(days=days - 1).to_pytimedelta()
    if start > end:
        return [empty(ctx, "TOD", CARD1_METRIC, f"기준일에 쓸 수 있는 카드 자료가 질문 기간에 없음 (공급 지연, {cutoff}까지)")]
    n_days = (end - start).days + 1
    prev_end = start - pd.Timedelta(days=1).to_pytimedelta()
    prev_start = prev_end - pd.Timedelta(days=n_days - 1).to_pytimedelta()
    d = ds.card1()
    d = d[d["city"] == city]
    sexes = sorted({s for _, s in cohorts})
    if len(sexes) == 1:
        d = d[d["SEX_CCD"] == sexes[0]]
    valid, forbidden = claim_lists("card1", is_shared_context=True)
    out = []
    for age in dict.fromkeys(a for a, _ in cohorts):
        g = d[d["age_g"] == age]

        def share(lo, hi):
            w = g[(g["date"] >= pd.Timestamp(lo)) & (g["date"] <= pd.Timestamp(hi))]
            tot = float(w["USE_CNT"].sum())
            return (float(w.loc[w["TIME_GB"] == band, "USE_CNT"].sum()) / tot if tot else None), tot

        cur, tot = share(start, end)
        label = f"{age}{' ' + sexes[0] if len(sexes) == 1 else ''}"
        code = f"TOD-{age_code(age)}{SEX_CODE_OF.get(sexes[0], '') if len(sexes) == 1 else ''}-{band.replace('_', '')}"
        if cur is None:
            out.append(empty(ctx, code, CARD1_METRIC, f"이 기간에 {label} 결제 행이 없음", source="card1"))
            continue
        prev, ptot = share(prev_start, prev_end) if prev_start >= pd.Timestamp("2025-07-01").date() else (None, 0)
        direction = None
        notes = list(CARD1_WARNINGS)
        if prev is not None:
            diff = cur - prev
            direction = "변화 없음" if abs(diff) < 0.01 else ("증가" if diff > 0 else "감소")
            notes.append(f"비교: 바로 앞 {n_days}일({prev_start}~{prev_end}) 비중 {prev:.3f} (차이 1%p 미만은 변화 없음)")
        else:
            notes.append("비교할 앞 기간 자료가 자료 기간(2025-07-01~) 밖이라 방향 없음")
        out.append(envelope(
            ctx, code, "card1", metric=f"카드 결제 중 {TIME_BAND_KO[band]} 비중 (카드 데이터1 보조)", value=round(cur, 3),
            unit="같은 연령 결제 중 비율",
            denominator=f"{city} 가맹점 {label} 카드 데이터1 결제건수 (모든 시간대)", n_obs=n_days,
            direction=direction, measured_grain="가맹점 시군구 × 일 × 시간대(4구간) × 성 × 연령 (카드 데이터1)",
            applied_scope=f"{city} 가맹점 (거주지 모름)", merchant_region=MERCHANT[city], is_shared_context=True,
            analysis_cohort=label, service_eligibility_group=res.service_eligibility_group if res else None,
            observed_from=start, observed_to=end, simulated_available_at=end + pd.Timedelta(days=lag_days("card1")).to_pytimedelta(),
            interpretation_status="계산 재현", valid_claims=valid, forbidden_claims=forbidden + ["거주자 단정", "체류·외출 단정"],
            value_state="observed", claim_category="전체 업종", warnings=notes))
    return out


# ---------------------------------------------------------------- 시민생활 (탐색적 참고)


def _sl_rows(sl: pd.DataFrame, req) -> pd.DataFrame:
    if req.group:
        g = cohorts_config()["groups"].get(req.group)
        lo, hi, sex = g["service"]["age_min"], g["service"]["age_max"], g["service"]["sex"]
    else:
        lo, hi, sex = req.age_min, req.age_max, req.sex
    m = pd.Series(True, index=sl.index)
    if lo is not None:
        m &= sl["age"] + 4 >= lo  # 5세 구간이 요청 연령과 겹치면 포함
    if hi is not None:
        m &= sl["age"] <= hi
    if sex:
        m &= sl["sex"] == sex
    return sl[m]


def get_validation_reference(ctx) -> list[ToolResult]:
    """서울 시민생활 '외출·커뮤니케이션 모두 적은 집단(전체)' 비율 (강남만, 탐색적 참고)."""
    req, city = ctx.request, ctx.request.city
    if city != "강남":
        return [empty(ctx, "VAL", SL_METRIC, "시민생활 자료는 강남만 있음")]
    ds = open_datasource(ctx.snapshot_id)
    months = usable_months("seoul_living", ctx.as_of, ctx.analysis_mode)
    if not months:
        return [empty(ctx, "VAL", SL_METRIC, f"기준일 {ctx.as_of}에 공개(가정)된 시민생활 파일 없음")]
    item = months[-1]
    sl = ds.seoul_living()
    sl = _sl_rows(sl[sl["month"] == int(item.base_month[4:])], req)
    valid, forbidden = claim_lists("seoul_living")
    out = []
    for adm in _city_adms(ds, city):
        g = sl[sl["adm_cd"] == adm]
        ok = g.dropna(subset=["iso_all", "pop"])
        bad = g["iso_all_state"].eq("quality_suspect").any()
        val = float(ok["iso_all"].sum() / ok["pop"].sum()) if len(ok) and ok["pop"].sum() > 0 else None
        state = "quality_suspect" if bad else ("observed" if val is not None else "missing")
        warn = [SL_NOTE, f"파일 기준월 {item.base_month}, 관측 기간 {item.observed_from:%Y-%m}~{item.observed_to:%Y-%m}로 가정(미확인)"]
        if bad:
            warn.append("품질 의심: 이 동의 일부 지표가 6개월 모두 0 (개포3동). 결측 처리")
            val = None
        out.append(envelope(
            ctx, f"VAL-{adm}", "seoul_living", metric=SL_METRIC, value=round(val, 4) if val is not None else None,
            unit="비율", denominator="총인구 추정 (시민생활 '(전체)' 집단, 같은 성·연령)",
            n_obs=int(round(float(ok["pop"].sum()))),  # 분모 크기 (G-08)
            measured_grain="행정동 × 성 × 5세 연령 × 파일 기준월", applied_scope=_adm_label(ds, adm),
            observed_from=item.observed_from, observed_to=item.observed_to,
            simulated_available_at=item.simulated_available_at, interpretation_status="계산 재현",
            valid_claims=valid, forbidden_claims=forbidden, warnings=warn, value_state=state))
    return out
