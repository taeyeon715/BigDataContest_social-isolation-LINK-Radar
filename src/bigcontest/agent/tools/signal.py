"""Signal 도구 (에이전트 설계 §5): 달력 트리거, 이탈 경보(기준일 카드), 반증, 연휴 깊이·회복, 겨울 변화, 폭염 반응,
후보 종류 분류, 등급(configs/rules.yaml rules-v1.0).

- 이탈 경보·반증은 signals/alarm.py 의 **기준일 카드**(records(as_of=...))만 쓴다. 카드 공급 지연(가정 3일)을 빼고
  자른 날까지의 자료로 다시 계산하므로 기준일 뒤 회복·결론이 보이지 않는다.
- 도시 공통 하락은 기각하지 않고 "확인 필요"로 남긴다. 강수는 자동 기각하지 않는다.
- 기대 활동(`calc_expected_activity`): 현재 이탈의 기대값 = 경보가 실제로 쓰는 EDA-6 기준선(같은 요일 과거 8주
  평균, 공휴일 제외, 기준일 전 자료만), 다음 1~7일 예측 = 3A 채택 모델(configs/models.yaml, 지금 0b).
  학습 기간 부족(warm-up)·결측일 때만 빈 결과.
- 폭염(R4)·기상특보(R6) 트리거: 기준일에 발표된 예보 기록만 쓸 수 있다. 2025년 예보 기록이 없으므로 기본은
  "예보 자료 없음 → 판단 보류/확인 필요"(EMPTY). 기준일 뒤 ASOS 실측을 쓰는 것은 `allow_scenario=True` 일 때만,
  **상황 시연(미래 실측 사용)** 으로 표시한 가설 근거로 만든다. strict_as_of 에서는 만들지 않고, 성능·기준일 평가에서 뺀다.
- 등급(`assign_level`)은 configs/rules.yaml(rules-v1.0)과 bigcontest.rules 로 정한다. 접근성 근거(2026년 원장)는
  과거 공개 시점을 모르면 "현재 원장 기준 접근성"으로 표시하고, strict_as_of 에서는 등급 축으로 세지 않는다.
"""

from __future__ import annotations

import math
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from bigcontest.agent.schemas import Candidate, NormalizedRequest, ToolResult
from bigcontest.agent.tools.common import (
    MERCHANT,
    RESIDENCE,
    SEX_CODE,
    TEMP_RULE_NOTE,
    cohort_key,
    city_of_evidence,
    daily_cutoff,
    empty,
    envelope,
    lag_days,
    signal_key,
    usable_months,
)
from bigcontest.agent.tools.data import card_cohorts, card_zero_notes
from bigcontest.agent.tools.datasource import open_datasource
from bigcontest.contracts.claims import card_claim, claim_lists
from bigcontest.contracts.inputs import evaluate_inputs
from bigcontest.rules import legacy_thresholds, load_rules, rules_version
from bigcontest.rules import candidates as RC
from bigcontest.rules.levels import Axes, decide, full_rule_id, rule_checks, rule_label

DEVIATION_METRIC = "카드 결제건수 평소 대비 (이탈 경보)"
EXPECTED_METRIC = "카드 결제건수 기대값 (이탈 기준선, EDA-6)"
FORECAST_METRIC = "카드 결제건수 다음 1~7일 예측 (3A 채택 모델)"
EXPECTED_PENDING = "기대 활동 기준선 없음 (학습 기간 부족 또는 같은 요일 과거 관측 부족)"
WINTER_METRIC = "춘천 읍·면 겨울 변화"
HEAT_METRIC = "폭염일 생필품 결제 상대 차이 (탐색)"
TRIGGER_METRIC = "달력 트리거"
SCENARIO_FLAG = "상황 시연(미래 실측 사용)"
SCENARIO_NOTE = (f"{SCENARIO_FLAG}: 기준일 뒤 ASOS 실측을 예보 대신 씀. 실제 예보 재생이 아님. "
                 "strict_as_of 불가, 성능·기준일(as-of) 평가에서 제외")
NO_FORECAST_NOTE = ("R4 폭염·R6 기상특보: 기준일에 발표된 예보 자료 없음 → 판단 보류/확인 필요 "
                    "(2025년 예보 발표 기록 없음. 기준일 뒤 ASOS 실측은 예보 대신 쓰지 않음)")
REVIEW_PENDING_NOTE = "검토 중 / 회복 미관측"
CITY_COMMON_NOTE = "도시 공통 변화로 분류 / 확인 필요 (공휴일이 원인이라는 증거 아님)"
ALARM_RULE_FROM = "2025-07~12 전체로 정한 경보 규칙 (EDA-6 합성 주입 평가로 정한 임계값: 사후 참고)"
RULE_CALIBRATED_UNTIL = date(2025, 12, 31)  # 경보 임계값·3A 채택 결정에 쓴 자료의 마지막 날
ACCESS_CURRENT = "현재 원장 기준 접근성"

# 등급 규칙: configs/rules.yaml (rules-v1.0). 예전 이름 STUB_LEVEL_RULES 는 같은 열쇠의 임계값 dict 로 남김
# (다른 노드·실행 메타데이터가 access_far_km 등을 읽음)
LEVEL_RULES = load_rules()
STUB_LEVEL_RULES = legacy_thresholds(LEVEL_RULES)
# 기상특보 근사 (일자료라 특보 기준과 같지 않음. 임시)
WARN_RULES = {"호우": ("rain", ">=", 80.0), "대설": ("snow_new", ">=", 5.0), "한파": ("tmin", "<=", -12.0)}
HEAT_TMAX = 33.0
WINTER_MONTHS = (11, 12, 1, 2)
RECOVERY_DAYS = 10


# ---------------------------------------------------------------- 달력 트리거


def holiday_blocks(ds) -> list[tuple[date, date, str]]:
    """공휴일이 낀 3일 이상 연휴 (주말 포함). (시작, 끝, 이름)."""
    hol = ds.holiday_names()
    days = pd.date_range("2025-06-01", "2026-01-31")
    off = [(d.dayofweek >= 5) or (d in hol) for d in days]
    out, i = [], 0
    while i < len(days):
        if not off[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(days) and off[j + 1]:
            j += 1
        block = days[i: j + 1]
        names = [hol[d] for d in block if d in hol]
        if names and len(block) >= 3:
            name = next((n for n in names if "추석" in n), names[0]).replace("연휴", "").strip() or names[0]
            out.append((block[0].date(), block[-1].date(), name))
        i = j + 1
    return out


def _trigger(ctx, code, value, scope, start, end, grain="날짜 (공식 달력)", source=None, warnings=None,
             status="직접 확인", **kw) -> ToolResult:
    return envelope(ctx, f"TRG-{code}", source, metric=TRIGGER_METRIC, value=value, measured_grain=grain,
                    applied_scope=scope, observed_from=start, observed_to=end, interpretation_status=status,
                    warnings=warnings or [], value_state="observed", **kw)


def get_calendar_triggers(ctx) -> list[ToolResult]:
    """기준일의 예정된 점검 트리거: R1 연휴 D-7, R2 명절 당일·다음 날, R3 연휴 뒤 회복(춘천 고령층),
    R4 폭염 예보, R6 기상특보(호우·대설·한파), W 동절기(춘천 읍·면)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city, as_of = ctx.request, ctx.request.city, ctx.as_of
    out: list[ToolResult] = []
    for start, end, name in holiday_blocks(ds):
        if start - timedelta(days=7) <= as_of <= start - timedelta(days=1):
            out.append(_trigger(ctx, "R1", f"R1 {name} 연휴 D-{(start - as_of).days} ({start:%m/%d}~{end:%m/%d})",
                                city, start, end, simulated_available_at=start - timedelta(days=7)))
        elderly = req.group == "고령층" or (req.group is None and (req.age_min or 0) >= 60)
        if city == "춘천" and elderly and end < as_of <= end + timedelta(days=RECOVERY_DAYS):
            out.append(_trigger(ctx, "R3", f"R3 {name} 연휴 뒤 회복 확인 ({end:%m/%d} 뒤 {RECOVERY_DAYS}일)",
                                city, start, end, simulated_available_at=end + timedelta(days=1)))
    for d, n in ds.holiday_names().items():
        if n == "추석" and d.date() <= as_of <= d.date() + timedelta(days=1):
            out.append(_trigger(ctx, "R2", "R2 명절 당일·다음 날 (추석)", city, d.date(), d.date(),
                                simulated_available_at=d.date()))
    weather = weather_triggers(ctx, ds)
    if city == "춘천" and as_of.month in WINTER_MONTHS:
        mstart = date(as_of.year, as_of.month, 1)
        out.append(_trigger(ctx, "W", "W 동절기 (춘천 읍·면)", "춘천 읍·면", mstart, mstart,
                            "날짜 (계절 규칙)", warnings=["관광형 동(여름 방문객 비중 큼)은 따로 확인"],
                            simulated_available_at=mstart))
    ok_weather = [e for e in weather if e.status == "OK"]
    return (out + ok_weather or [empty(ctx, "TRG", TRIGGER_METRIC, "기준일에 해당하는 트리거 없음")]) + \
        [e for e in weather if e.status != "OK"]


def allow_scenario(ctx) -> bool:
    """상황 시연(미래 실측 사용)을 명시적으로 허용했나 (ToolContext.allow_scenario). 기본 False."""
    return bool(getattr(ctx, "allow_scenario", False))


def weather_triggers(ctx, ds) -> list[ToolResult]:
    """R4 폭염 예보·R6 기상특보.

    - 사전 판단에는 발표 시각이 기준일 이전인 예보 기록만 쓸 수 있다. 2025년 예보 기록이 없으므로 기본은
      EMPTY "예보 자료 없음 → 판단 보류/확인 필요". 기준일 뒤 ASOS 실측을 판단 입력으로 쓰지 않는다.
    - allow_scenario=True 이고 strict_as_of 가 아니면: 기준일·다음 날의 ASOS 실측으로 만든 **상황 시연** 근거
      (interpretation_status=가설, 경고에 상황 시연 표시). 실제 예보 재생·성능 평가가 아님.
    """
    as_of, city = ctx.as_of, ctx.request.city
    if ctx.analysis_mode == "strict_as_of" or not allow_scenario(ctx):
        why = NO_FORECAST_NOTE + (" · strict_as_of 에서는 상황 시연도 쓰지 않음"
                                  if ctx.analysis_mode == "strict_as_of" and allow_scenario(ctx) else "")
        return [empty(ctx, "TRG-WX", TRIGGER_METRIC, why)]
    w = ds.weather()
    w = w[w["city"] == city].set_index("date")
    targets = [pd.Timestamp(as_of), pd.Timestamp(as_of + timedelta(days=1))]
    rows = w.reindex(targets)
    grain = f"관측소 × 일 (서울 108 / 춘천 101) · {SCENARIO_FLAG}"
    out = []
    hot = rows[rows["tmax"] >= HEAT_TMAX]
    if len(hot):
        d = hot.index[0].date()
        out.append(_trigger(ctx, "R4-SCN", f"R4 폭염 [{SCENARIO_FLAG}] (최고 {HEAT_TMAX:g}℃ 이상, {d:%m/%d} 실측)",
                            city, d, d, grain, "asos", [SCENARIO_NOTE], "가설"))
    for kind, (col, op, thr) in WARN_RULES.items():
        hit = rows[rows[col] >= thr] if op == ">=" else rows[rows[col] <= thr]
        if len(hit):
            d = hit.index[0].date()
            out.append(_trigger(ctx, f"R6-{ {'호우': 'RAIN', '대설': 'SNOW', '한파': 'COLD'}[kind]}-SCN",
                                f"R6 {kind} 특보 근사 [{SCENARIO_FLAG}] ({d:%m/%d} 실측)", city, d, d, grain, "asos",
                                [SCENARIO_NOTE, f"특보 기준 근사: 일자료 {col} {op} {thr:g} ({TEMP_RULE_NOTE})"],
                                "가설"))
    return out


def is_scenario(ev: ToolResult) -> bool:
    """상황 시연(미래 실측 사용) 근거인가. 성능·기준일 평가에서 뺄 때 쓴다."""
    return any(w.startswith(SCENARIO_FLAG) for w in ev.warnings)


# ---------------------------------------------------------------- 기대 활동


def _adopted_forecaster(holidays):
    """configs/models.yaml 의 3A 채택 예측 모델. (모델, 이름, 설정). 연결 안 된 모델이면 (None, 이름, 설정)."""
    import yaml

    from bigcontest.config import ROOT
    from bigcontest.models.baseline import SameWeekdayMean

    with open(ROOT / "configs" / "models.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    name = str(cfg["decisions"]["adopted_forecast_model"])
    spec = cfg["models"].get(name, {})
    if spec.get("name") == "same_weekday_mean":
        return SameWeekdayMean(holidays, n_weeks=int(spec["n_weeks"]), min_obs=int(spec["min_obs"])), name, cfg
    return None, name, cfg


def calc_expected_activity(ctx) -> list[ToolResult]:
    """코호트마다 두 봉투.

    - EXP-<코호트>: 현재 이탈의 기대값 = 경보(EDA-6)가 실제로 쓰는 기준선. 자료 마지막 날(기준일 − 공급 지연)의
      같은 요일 과거 8주 평균(공휴일 제외, 그날보다 앞선 자료만). 이것이 `expected_activity` 입력이다.
    - FC-<코호트>: 다음 1~7일 예측 = 3A 채택 모델(지금 0b: 같은 요일 최근 4주 평균). 맥락 정보 (등급 입력 아님).
    학습 기간 부족·결측이면 EMPTY (그때만 이탈 후보가 부분 판정).
    """
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    cutoff = daily_cutoff("card2", ctx.as_of, ctx.analysis_mode)
    if cutoff is None:
        return [empty(ctx, "EXP", EXPECTED_METRIC,
                      "TIME_AVAILABILITY_UNKNOWN: 카드 실제 공개일을 몰라 strict_as_of 에서 쓰지 않음")]
    cohorts, res = card_cohorts(req)
    if not cohorts:
        return [empty(ctx, "EXP", EXPECTED_METRIC, res.message if res else "코호트 없음")]
    p = ds.panel()
    t = pd.Timestamp(cutoff)
    if t < p.dates[0] or t > p.dates[-1]:
        return [empty(ctx, "EXP", EXPECTED_METRIC, f"{cutoff}는 카드 자료 기간 밖")]
    i = int(np.where(p.dates == t)[0][0])
    n_same = p.cfg.lookback // 7
    same = [i - 7 * k for k in range(1, n_same + 1) if i - 7 * k >= 0]
    used = [k for k in same if not p.is_hol[k]]
    model, mname, mcfg = _adopted_forecaster(p.holidays)
    lag = lag_days("card2")
    valid, forbidden = claim_lists("card2", is_shared_context=True)
    out = []
    for age, sex in cohorts:
        col = (city, age, sex)
        key = f"{cohort_key(city, age, sex)[3:]}"
        if col not in p.cols:
            out.append(empty(ctx, f"EXP-{key}", EXPECTED_METRIC, f"패널에 없는 코호트 {age} {sex}"))
            continue
        j = p.cols.index(col)
        common = dict(
            measured_grain="도시×성×연령×일", applied_scope=f"{city} (동 목록은 공유 맥락)",
            merchant_region=MERCHANT[city], customer_residence=RESIDENCE[city], is_shared_context=True,
            analysis_cohort=f"{age} {sex}", service_eligibility_group=res.service_eligibility_group if res else None,
            valid_claims=valid, forbidden_claims=forbidden + ["고립 발생"], calibrated_until=RULE_CALIBRATED_UNTIL,
            claim_category="전체 업종")
        e = p.E[i, j]
        y = float(p.Y[col].iloc[i])
        miss = ds.card_missing_days(col, p.dates[used[-1]] if used else t, t - pd.Timedelta(days=1))
        if i < p.cfg.min_train or np.isnan(e) or not used:
            why = (f"학습 기간 부족 (경보 warm-up {p.cfg.min_train}일, {cutoff}까지 자료)" if i < p.cfg.min_train
                   else "같은 요일 과거 관측 부족 (공휴일 제외 2개 미만)")
            out.append(empty(ctx, f"EXP-{key}", EXPECTED_METRIC, f"{EXPECTED_PENDING}: {why}",
                             analysis_cohort=f"{age} {sex}"))
        else:
            exp = float(np.expm1(np.log1p(y) - e))
            out.append(envelope(
                ctx, f"EXP-{key}", "card2", metric=EXPECTED_METRIC, value=round(exp, 1), unit="건/일·연령별 배율값",
                denominator="같은 코호트 같은 요일 과거 8주 평균 (공휴일 제외, 그날보다 앞선 자료만; EDA-6 경보 기대값)",
                n_obs=len(used), observed_from=p.dates[used[-1]].date(), observed_to=p.dates[used[0]].date(),
                simulated_available_at=p.dates[used[0]].date() + timedelta(days=lag),
                trained_until=(t - pd.Timedelta(days=1)).date(), rule_derived_from=ALARM_RULE_FROM,
                interpretation_status="계산 재현", value_state="observed",
                warnings=[f"기대값 대상일 {cutoff} (카드 공급 지연 {lag}일 가정한 자료 마지막 날), 실제 {y:g}건",
                          "경보 기대값은 EDA-6 기준선 유지 (3A 결정: expected_value_for_alarm=EDA-6)",
                          f"경보 임계값은 {ALARM_RULE_FROM}"]
                + ([f"행 없음=0건 가정: 기준선 기간에 결제 행이 없는 날 {miss}일"] if miss else []),
                **common))
        # 다음 1~7일 예측 (3A 채택 모델)
        if model is None:
            out.append(empty(ctx, f"FC-{key}", FORECAST_METRIC, f"채택 모델 {mname} 은 도구에 아직 연결 전",
                             analysis_cohort=f"{age} {sex}"))
            continue
        hist = p.Y.loc[p.dates <= t, [col]]
        pred = model.forecast(hist)[col]
        vals = [None if np.isnan(v) else round(float(np.expm1(v)), 1) for v in pred.to_numpy()]
        if all(v is None for v in vals):
            out.append(empty(ctx, f"FC-{key}", FORECAST_METRIC, "같은 요일 과거 관측 부족으로 예측 불가",
                             analysis_cohort=f"{age} {sex}"))
            continue
        out.append(envelope(
            ctx, f"FC-{key}", "card2", metric=FORECAST_METRIC, value=vals, unit="건/일·연령별 배율값 (h=1~7)",
            denominator=f"모델 {mname}: 같은 요일 최근 {model.n_weeks}주 평균 (공휴일 제외)", n_obs=len(vals),
            observed_from=p.dates[max(0, i - 7 * model.n_weeks)].date(), observed_to=cutoff,
            simulated_available_at=cutoff + timedelta(days=lag), trained_until=cutoff,
            rule_derived_from=f"3A 모델 비교(2025-07~12 시간순 rolling-origin 평가)로 {mname} 채택 (사후 결정, "
                              f"configs/models.yaml {mcfg['version']})",
            interpretation_status="계산 재현", value_state="observed",
            warnings=[f"예측 대상일 {(t + pd.Timedelta(days=1)).date()}~{(t + pd.Timedelta(days=7)).date()} "
                      f"(예측 기준일 {cutoff} = 자료 마지막 날. 기준일 {ctx.as_of} 이전 날도 포함)",
                      "맥락 정보: 이탈 판정·등급 입력 아님 (경보 기대값은 EDA-6 기준선)"],
            **common))
    return out


# ---------------------------------------------------------------- 이탈 경보 (기준일 카드)


def _cohort_parts(cohort: str) -> tuple[str, str]:
    age, sex = cohort.split(" ")
    return age, sex


def active_alarms(ctx) -> tuple[pd.DataFrame, date | None, str | None]:
    """요청 코호트의 기준일 카드 경보 중 기준일에 아직 열려 있는 묶음. (표, 자른 날, 빈 사유)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    cutoff = daily_cutoff("card2", ctx.as_of, ctx.analysis_mode)
    if cutoff is None:
        return pd.DataFrame(), None, "TIME_AVAILABILITY_UNKNOWN: 카드 실제 공개일을 몰라 strict_as_of 에서 쓰지 않음"
    cohorts, res = card_cohorts(req)
    if not cohorts:
        return pd.DataFrame(), cutoff, (res.message if res else "코호트 없음")
    p = ds.panel()
    if pd.Timestamp(cutoff) < p.dates[0] + pd.Timedelta(days=p.cfg.min_train):
        return pd.DataFrame(), cutoff, f"경보 학습 기간 부족 ({cutoff}까지 자료)"
    card = ds.alarm_card(cutoff)
    if card.empty:
        return card, cutoff, "이탈 경보 없음"
    keep = {f"{a} {s}" for a, s in cohorts}
    open_from = pd.Timestamp(cutoff) - pd.Timedelta(days=p.cfg.win)
    m = (card["city"] == city) & card["cohort"].isin(keep) & (pd.to_datetime(card["episode_end"]) >= open_from)
    rows = card[m].sort_values(["alarm_date", "cohort"])
    return rows, cutoff, None if len(rows) else "기준일에 열려 있는 이탈 경보 없음"


def _key(row) -> str:
    age, sex = _cohort_parts(row["cohort"])
    return f"{cohort_key(row['city'], age, sex)}-{pd.Timestamp(row['alarm_date']):%Y%m%d}"


def _d(x) -> date | None:
    return None if pd.isna(x) else pd.Timestamp(x).date()


def calc_cohort_deviation(ctx) -> list[ToolResult]:
    """이탈 경보 (absolute·local 두 경로, EDA-6 규칙). 도시 × 코호트 단위 (동은 공유 맥락)."""
    rows, cutoff, why = active_alarms(ctx)
    if why:
        return [empty(ctx, "DEV", DEVIATION_METRIC, why)]
    ds = open_datasource(ctx.snapshot_id)
    city = ctx.request.city
    valid, forbidden = claim_lists("card2", is_shared_context=True)
    out = []
    for _, r in rows.iterrows():
        age, sex = _cohort_parts(r["cohort"])
        alarm = _d(r["alarm_date"])
        warn = [f"경보 최초일 {alarm} (경로 {r['path_first']}, 묶음 {r['path']})",
                f"카드 공급 지연 {lag_days('card2')}일 가정: {cutoff}까지 자료로 계산한 기준일 카드",
                f"경보 규칙(z<-2.5, 7일 중 3일)은 {ALARM_RULE_FROM}"]
        if not r["direction"]:
            warn.append(r["direction_note"] or "방향 판정 불가")
        warn += card_zero_notes(ds, (city, age, sex), pd.Timestamp(r["window_start"]) - pd.Timedelta(days=56), alarm)
        claim = card_claim(RESIDENCE[city], f"{age} {sex}", MERCHANT[city], "전체 업종", "낮음")
        out.append(envelope(
            ctx, f"DEV-{_key(r)}", "card2", metric=DEVIATION_METRIC,
            value=round(1 + float(r["mean_change_abs"]), 3), unit="평소 대비 비율",
            denominator="같은 코호트 같은 요일 과거 8주 평균 (공휴일 제외, 카드 데이터2)", n_obs=7,
            direction=r["direction"] or None, measured_grain="도시×성×연령×일",
            applied_scope=f"{city} (동 목록은 공유 맥락)", merchant_region=MERCHANT[city],
            customer_residence=RESIDENCE[city], is_shared_context=True, analysis_cohort=f"{age} {sex}",
            observed_from=_d(r["window_start"]), observed_to=alarm,
            simulated_available_at=alarm + timedelta(days=lag_days("card2")),
            trained_until=_d(r["trained_until"]), calibrated_until=RULE_CALIBRATED_UNTIL,
            rule_derived_from=ALARM_RULE_FROM, interpretation_status="계산 재현",
            valid_claims=[claim] + valid[1:], forbidden_claims=forbidden + ["고립 발생", f"{city} 거주"],
            warnings=warn, value_state="observed"))
    return out


# ---------------------------------------------------------------- 반증


def _num(x) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.3f}"


def check_confounders(ctx) -> list[ToolResult]:
    """반증: 공휴일 기대값·도시 공통요인(연휴 뒤 회복 세 가지), 강수(설명 부분 기록, 자동 기각 아님), 학사일정(미수집).
    경보마다 따로. 근거 ID 끝은 경보 근거(DEV)와 같은 신호 키."""
    rows, cutoff, why = active_alarms(ctx)
    if why:
        return []
    out = []
    for _, r in rows.iterrows():
        key = _key(r)
        alarm = _d(r["alarm_date"])
        decided = _d(r["review_decided_at"])
        seen_to = _d(r["recovery_observed_at"]) or alarm
        base = dict(observed_from=_d(r["window_start"]), observed_to=max(seen_to, alarm),
                    simulated_available_at=max(seen_to, alarm) + timedelta(days=lag_days("card2")),
                    measured_grain="도시×성×연령×일", applied_scope=f"{r['city']} (동 목록은 공유 맥락)",
                    analysis_cohort=r["cohort"], interpretation_status="계산 재현", value_state="observed")
        numbers = (f"창 평균 하락 {_num(r['drop_total'])} 중 도시 공통 {_num(r['explained_common'])}, "
                   f"공휴일 기대값 {_num(r['explained_holiday'])}, 남는 이탈 {_num(r['remaining'])} (log 차이)")
        rec = (f"연휴 뒤 회복: {r['recovery_abs'] or '-'} / {r['recovery_local'] or '-'} / {r['recovery_common'] or '-'}")
        review = r["review"]
        if review == "검토 중":
            v1 = v2 = "검토 중"
            n1 = n2 = REVIEW_PENDING_NOTE
            base["interpretation_status"] = "미확인"
        elif review == "기각":
            v1, n1 = "기각", r["review_reason"]
            v2, n2 = "통과", "도시 대비 추가 이탈 없음, 도시 공통도 회복 또는 기각 근거와 별개"
        elif review == "확인 필요":
            v1, n1 = "설명 일부", "공휴일 기대값·공통요인으로 대부분 설명되나 연휴 뒤에도 평소보다 낮음"
            v2, n2 = "공통 변화", CITY_COMMON_NOTE
            path = _holiday_path_review(ctx, r, cutoff)
            if path is not None:
                verdict, why_path = path
                if verdict == "기대 경로 안":
                    v2, n2 = "기대 경로 안", f"연휴 뒤 기대 경로 안 (rules.yaml holiday_path): {why_path}"
                else:
                    n2 = f"{CITY_COMMON_NOTE}; 연휴 뒤 기대 경로: {why_path}"
        elif review == "보류":
            v1, n1 = "보류", r["review_reason"]
            v2, n2 = "통과", "도시 대비로도 회복하지 않음"
        else:  # 통과
            v1, n1 = "통과", r["review_reason"]
            v2, n2 = "통과", "도시 공통요인을 빼도 경보 조건이 남거나 공휴일 인접 아님"
        dec = [f"검토 결정일 {decided}"] if decided else []
        out.append(envelope(ctx, f"CF1-{key}", "card2", metric="반증: 공휴일 기대값", value=v1,
                            warnings=[n1, numbers] + dec, **base))
        out.append(envelope(ctx, f"CF2-{key}", "card2", metric="반증: 도시 공통요인", value=v2,
                            warnings=[n2, rec] + dec, **base))
        # 강수: 기각하지 않음. 설명되는 부분과 남는 이탈을 기록
        re_ = r["rain_explained"]
        if re_ is True or re_ == True:  # noqa: E712 (pandas object)
            v3 = "설명 일부"
            n3 = (f"날씨로 설명되는 부분 {_num(r['weather_explained_abs'])}, 보정 후 남는 이탈 "
                  f"{_num(r['after_weather_abs'])} (absolute, log 차이). 비를 빼면 경보 조건 미달이지만 기각하지 않음")
        elif re_ is False or re_ == False:  # noqa: E712
            v3, n3 = "통과", "비를 빼도 경보 조건이 남거나 창에 비 영향 없음 (비가 설명하지 못한다는 증거는 아님)"
        else:
            v3, n3 = "판정 불가", r["weather_status"] or "강수 효과 추정 불가"
        out.append(envelope(ctx, f"CF3-{key}", "asos", metric="반증: 강수", value=v3,
                            warnings=[n3, f"창 안 비 온 날 {r['rain_days_in_window']}"],
                            **{**base, "measured_grain": "관측소 × 일 (서울 108 / 춘천 101)"}))
        out.append(empty(ctx, f"CF4-{key}", "반증: 학사일정", "학사일정 미수집. 반증에서 빠짐 (선택 맥락)"))
    return out


def _holiday_path_review(ctx, r, cutoff) -> tuple[str, str] | None:
    """'확인 필요(도시 공통)' 경보를 연휴 뒤 기대 경로로 다시 본다 (rules.candidates.post_path_review).
    경보 창에 걸친 연휴가 없으면 None."""
    ds = open_datasource(ctx.snapshot_id)
    alarm, w0 = _d(r["alarm_date"]), _d(r["window_start"])
    blocks = [b for b in holiday_blocks(ds) if b[0] - timedelta(days=1) <= alarm and b[1] + timedelta(days=1) >= w0]
    if not blocks:
        return None
    p = ds.panel()
    age, sex = _cohort_parts(r["cohort"])
    col = (r["city"], age, sex)
    if col not in p.cols:
        return None
    hol = {d.date() for d in p.holidays}
    return RC.post_path_review(p.E, p.L, p.dates, p.cols.index(col), blocks[-1], hol, cutoff, LEVEL_RULES)


# ---------------------------------------------------------------- 연휴 깊이·회복


def calc_holiday_gap(ctx) -> list[ToolResult]:
    """가장 최근에 끝난 연휴: 연휴 중·연휴 뒤(최대 10일, 공휴일 제외) 결제의 평소 대비 (코호트별)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    cutoff = daily_cutoff("card2", ctx.as_of, ctx.analysis_mode)
    if cutoff is None:
        return [empty(ctx, "HOL", "연휴 중 결제 평소 대비", "TIME_AVAILABILITY_UNKNOWN")]
    done = [b for b in holiday_blocks(ds) if b[1] < cutoff]
    if not done:
        return [empty(ctx, "HOL", "연휴 중 결제 평소 대비", f"{cutoff}까지 끝난 연휴 없음")]
    start, end, name = done[-1]
    p = ds.panel().until(pd.Timestamp(cutoff))
    in_block = np.where((p.dates >= pd.Timestamp(start)) & (p.dates <= pd.Timestamp(end)))[0]
    after = [i for i in np.where(p.dates > pd.Timestamp(end))[0] if not p.is_hol[i]][:RECOVERY_DAYS]
    cohorts, _ = card_cohorts(req)
    out = []
    for age, sex in cohorts:
        col = (city, age, sex)
        if col not in p.cols:
            continue
        j = p.cols.index(col)
        code = f"{cohort_key(city, age, sex)[3:]}"
        common = dict(unit="평소 대비 비율", denominator="같은 코호트 같은 요일 과거 8주 평균 (공휴일 제외)",
                      measured_grain="도시×성×연령×일", applied_scope=f"{city} (동 목록은 공유 맥락)",
                      merchant_region=MERCHANT[city], customer_residence=RESIDENCE[city], is_shared_context=True,
                      analysis_cohort=f"{age} {sex}", interpretation_status="계산 재현", value_state="observed")
        for tag, idx, metric in (("HOL", in_block, f"{name} 연휴 중 결제 평소 대비"),
                                 ("HREC", after, f"{name} 연휴 뒤 결제 평소 대비 (최대 {RECOVERY_DAYS}일)")):
            e = p.E[idx, j] if len(idx) else np.array([])
            e = e[~np.isnan(e)]
            if len(e) < 3:
                out.append(empty(ctx, f"{tag}-{code}", metric, "회복 미관측 (관측일 3일 미만)"))
                continue
            from bigcontest.signals.alarm import direction

            m = float(np.mean(e))
            out.append(envelope(ctx, f"{tag}-{code}", "card2", metric=metric, value=round(float(np.expm1(m)) + 1, 3),
                                n_obs=len(e), direction=direction(m), observed_from=p.dates[idx[0]].date(),
                                observed_to=p.dates[idx[-1]].date(),
                                simulated_available_at=p.dates[idx[-1]].date() + timedelta(days=lag_days("card2")),
                                warnings=["추석 1회 관측 (반복 미확인)"], **common))
    return out or [empty(ctx, "HOL", "연휴 중 결제 평소 대비", "코호트 없음")]


# ---------------------------------------------------------------- 겨울 변화 (춘천 읍·면)


def group_mode(d: pd.DataFrame, by: str, col: str) -> pd.Series:
    """묶음(by)마다 col 의 최빈값. `groupby(by)[col].agg(lambda s: s.mode().iloc[0])` 와 같은 값:
    동점이면 작은 값(정렬 순서 앞)을 고르고, 빈 값은 세지 않는다. 묶음마다 람다를 부르지 않아 빠르다."""
    n = d.groupby([by, col]).size().rename("_n").reset_index()
    n = n[n["_n"] > 0].sort_values([by, "_n", col], ascending=[True, False, True], kind="mergesort")
    return n.drop_duplicates(by).set_index(by)[col]


def calc_winter_contraction(ctx) -> list[ToolResult]:
    """춘천 읍·면 주민형 격자(운영상 대리 규칙)의 60+ 체류 여름(7·9월) → 겨울(11·12월) 변화 (EDA-5 I-5 방식).
    기준일에 공개된 달만 쓴다. 같은 격자 전 연령 변화와의 차이를 함께 적는다."""
    if ctx.request.city != "춘천":
        return [empty(ctx, "WIN", WINTER_METRIC, "춘천 읍·면만 해당")]
    ds = open_datasource(ctx.snapshot_id)
    months = [m.base_month for m in usable_months("tel_age", ctx.as_of, ctx.analysis_mode)]
    summer = [m for m in ("202507", "202509") if m in months]
    winter = [m for m in ("202511", "202512") if m in months]
    if not summer or not winter:
        return [empty(ctx, "WIN", WINTER_METRIC, "이번 겨울(11·12월) 통신 자료가 기준일에 아직 공개 전 (가정 공개일 기준)")]

    def compute():
        from bigcontest.telecom import AGE_COLS, WKDY_COLS, age_cols, grid_id

        age, tim, wk = (ds.tel(n) for n in ("tel_age", "tel_time", "tel_wkdy"))
        sel = []
        for d_ in (age, tim, wk):
            d_ = d_[(d_["city"] == "춘천") & d_["STD_YM"].isin(months)].copy()
            d_["g"] = grid_id(d_["X_COORD"], d_["Y_COORD"])
            sel.append(d_)
        cc, tt, ww = sel
        cc["s60"] = cc[age_cols("60GU")].sum(axis=1)
        cc["all"] = cc[AGE_COLS].sum(axis=1)
        tt["night"] = tt[[f"TMST_{h:02d}" for h in range(0, 6)]].sum(axis=1)
        tt["day"] = tt[[f"TMST_{h:02d}" for h in range(11, 16)]].sum(axis=1)
        ww["wd"] = ww[WKDY_COLS[:5]].mean(axis=1)
        ww["we"] = ww[WKDY_COLS[5:]].mean(axis=1)
        gt = tt.groupby("g")[["night", "day"]].sum()
        gw = ww.groupby("g")[["wd", "we"]].sum()
        gm = cc.groupby(["g", "STD_YM"])["s60"].sum().unstack().fillna(0)
        gall = cc.groupby(["g", "STD_YM"])["all"].sum().unstack().fillna(0)
        gtype = group_mode(cc, "g", "adm_type")
        info = pd.DataFrame({"nd": gt["night"] / gt["day"], "ww": gw["we"] / gw["wd"], "type": gtype,
                             "s60": gm.mean(axis=1)}).dropna()
        info = info[info["s60"] >= 2]
        cut = info.loc[info["type"] != "동", "nd"].quantile(0.5)
        info["core"] = (info["nd"] >= cut) & info["ww"].between(0.8, 1.25)
        res = {}
        for typ, m in (("읍·면", info["type"].isin(["읍", "면"])), ("동지역", info["type"].eq("동"))):
            idx = info.index[m & info["core"]]
            ch60 = gm.loc[idx, winter].mean(axis=1).sum() / gm.loc[idx, summer].mean(axis=1).sum() - 1
            chall = gall.loc[idx, winter].mean(axis=1).sum() / gall.loc[idx, summer].mean(axis=1).sum() - 1
            res[typ] = (int(len(idx)), float(ch60), float(chall))
        return res

    res = ds.cached(("winter", tuple(months)), compute)
    n, ch60, chall = res["읍·면"]
    _, dong60, _ = res["동지역"]
    item = usable_months("tel_age", ctx.as_of, ctx.analysis_mode)[-1]
    valid, forbidden = claim_lists("tel_age")
    return [envelope(
        ctx, "WIN", "tel_age", metric=WINTER_METRIC, value=round(1 + ch60, 3), unit="겨울/여름 비율·60+ 체류",
        denominator=f"같은 격자 여름({'·'.join(m[4:] for m in summer)}월) 평균 60+ 체류", n_obs=n,
        direction="감소" if ch60 < -0.05 else ("증가" if ch60 > 0.05 else "변화 없음"),
        measured_grain="250m 격자 × 월 (통신 성연령, 60+ 묶음)", applied_scope="춘천 읍·면 주민형 격자",
        analysis_cohort="60+", observed_from=date(2025, 7, 1), observed_to=item.observed_to,
        simulated_available_at=item.simulated_available_at, interpretation_status="계산 재현",
        valid_claims=valid, forbidden_claims=forbidden + ["재현된 계절 수축 (6개월 자료)"],
        warnings=[f"겨울 = {'·'.join(m[4:] for m in winter)}월 (기준일에 공개된 달만)",
                  f"같은 격자 전 연령 변화 {chall:+.3f}, 차이 {ch60 - chall:+.3f}; 동지역 주민형 60+ {dong60:+.3f}",
                  "주민형 격자는 운영상 대리 규칙(밤 체류·주말 비율). 주민 확인 아님",
                  "6개월 자료라 매년 반복되는 계절 변화인지 미확인 (재현된 계절 수축 아님)"],
        value_state="observed")]


# ---------------------------------------------------------------- 폭염 반응 (탐색)


def calc_heat_response(ctx) -> list[ToolResult]:
    """폭염일(ASOS 최고 33℃ 이상, 7~8월)의 대상 연령 생필품 결제를 40·50대와 비교 (탐색, EDA-5 I-6 방식)."""
    ds = open_datasource(ctx.snapshot_id)
    req, city = ctx.request, ctx.request.city
    cutoff = daily_cutoff("card2", ctx.as_of, ctx.analysis_mode)
    cohorts, _ = card_cohorts(req)
    ages = sorted({a for a, _ in cohorts})
    if cutoff is None or not ages:
        return [empty(ctx, "HEAT", HEAT_METRIC, "기준일에 쓸 수 있는 카드 자료 없음")]
    if set(ages) & {"40대", "50대"}:
        return [empty(ctx, "HEAT", HEAT_METRIC, "비교 기준(40·50대)과 같은 연령이라 계산하지 않음")]
    c2 = ds.card2()
    hol = set(ds.holidays())
    d = c2[(c2["city"] == city) & (c2["cat"] == "생필품") & (c2["date"] <= pd.Timestamp(cutoff))
           & (c2["date"] < pd.Timestamp("2025-09-01"))]
    d = d[~d["date"].isin(hol)]
    g = d.groupby(["date", "age_g"])["USE_CNT"].sum().reset_index()
    w = ds.weather()
    tmax = w[w["city"] == city].set_index("date")["tmax"]
    g["tmax"] = g["date"].map(tmax)
    g = g[g["tmax"].notna()]
    g["hot"] = g["tmax"] >= HEAT_TMAX
    g["dow"] = g["date"].dt.dayofweek
    if g["hot"].sum() == 0:
        return [empty(ctx, "HEAT", HEAT_METRIC, f"{cutoff}까지 폭염일 없음")]
    b = g[~g["hot"]].groupby(["age_g", "dow"])["USE_CNT"].mean().rename("b")
    f = g.join(b, on=["age_g", "dow"])
    f = f[f["b"] > 0]
    f["lr"] = np.log(f["USE_CNT"].clip(lower=0.5) / f["b"])
    h = f[f["hot"]].groupby("age_g")["lr"].mean()
    if not set(ages) <= set(h.index) or not {"40대", "50대"} <= set(h.index):
        return [empty(ctx, "HEAT", HEAT_METRIC, "폭염일 비교에 필요한 연령 관측 없음")]
    diff = float(np.exp(h[ages].mean()) / np.exp(h[["40대", "50대"]].mean()) - 1)
    n_hot = int(g.loc[g["hot"], "date"].nunique())
    return [envelope(
        ctx, "HEAT", "card2", metric=HEAT_METRIC, value=round(diff, 3),
        unit=f"상대 차이: {'·'.join(ages)} ÷ 40·50대 − 1", denominator="같은 요일 평소(폭염 아닌 날) 대비 비율",
        n_obs=n_hot, direction="감소" if diff < -0.05 else ("증가" if diff > 0.05 else "변화 없음"),
        measured_grain="도시×연령×일 (생필품 업종)", applied_scope=f"{city} (동 목록은 공유 맥락)",
        merchant_region=MERCHANT[city], customer_residence=RESIDENCE[city], is_shared_context=True,
        analysis_cohort="·".join(ages), observed_from=date(2025, 7, 1), observed_to=min(cutoff, date(2025, 8, 31)),
        simulated_available_at=min(cutoff, date(2025, 8, 31)) + timedelta(days=lag_days("card2")),
        interpretation_status="가설",
        warnings=["탐색: 폭염일이 휴가철과 겹침. 등급 입력 아님", "ASOS 관측소 한 곳 기준 (동별 날씨 아님)"],
        valid_claims=claim_lists("card2", True)[0], forbidden_claims=claim_lists("card2", True)[1],
        value_state="observed")]


# ---------------------------------------------------------------- 후보 종류 분류


# 근거 지표 이름 → candidate_inputs.yaml 의 근거 이름 (가짜 도구 이름도 같이)
INPUT_OF_METRIC = {
    TRIGGER_METRIC: "calendar_trigger",
    "대상 집단 계약": "target_group",
    "1인세대 비율": "structure_vulnerability",
    "관측 거점까지 직선거리": "access_gap",
    "생필품 결제 일별 시계열": "card_cohort_series",
    "카드 결제건수 일별 시계열": "card_cohort_series",
    "생필품 결제 기대값": "expected_activity",
    EXPECTED_METRIC: "expected_activity",
    "생필품 결제 평소 대비": "cohort_deviation",
    DEVIATION_METRIC: "cohort_deviation",
    "반증: 공휴일 기대값": "holiday_expectation",
    "반증: 도시 공통요인": "city_common_factor",
    "반증: 강수": "weather_asos",
    "반증: 학사일정": "academic_calendar",
    "시민생활 '소통 적음' 비중 (탐색적 참고)": "validation_reference",
    "시민생활 '외출·소통 모두 적음' 비율 (탐색적 참고)": "validation_reference",
    WINTER_METRIC: "winter_contraction",
}
REQUIRED_CONFOUNDERS = ("holiday_expectation", "city_common_factor", "weather_asos")
CITY_LEVEL_SCHEDULED = ("calendar_trigger", "target_group", "card_cohort_series", "validation_reference",
                        "mobility_layer", "resources", "winter_contraction")


def input_of(ev: ToolResult) -> str | None:
    if ev.metric in INPUT_OF_METRIC:
        return INPUT_OF_METRIC[ev.metric]
    if ev.metric.startswith("체류인구 중"):
        return "mobility_layer"
    return None


def adm_of(ev: ToolResult) -> str | None:
    m = re.search(r"\((\d{8})\)", ev.applied_scope or "")
    return m.group(1) if m else None


def is_eupmyeon(ev: ToolResult) -> bool:
    name = re.sub(r"\s*\(\d{8}\)$", "", ev.applied_scope or "").split(" ")[-1]
    return name.endswith(("읍", "면"))


def _struct_items(structs: list[ToolResult]) -> list[tuple[str, str | None, float, list[str]]]:
    return [(adm_of(e), city_of_evidence(e), float(e.value), e.warnings) for e in structs
            if isinstance(e.value, (int, float)) and adm_of(e)]


def structure_facts(structs: list[ToolResult], rules: dict | None = None):
    """구조 근거 → adm → StructFact. 순위는 근거의 '순위 모집단'(도시 × 월 전체)을 씀."""
    return RC.structure_facts(_struct_items(structs), rules or LEVEL_RULES)


def structure_tops(structs: list[ToolResult], rules: dict | None = None) -> dict[str, tuple[float, int, int]]:
    """도시 안 1인세대 비율 상위 동 → (값, 순위, 동 수). 상위 비율 + 절대 최소값 (rules.yaml thresholds.structure)."""
    return {a: (f.value, f.rank, f.n) for a, f in structure_facts(structs, rules).items() if f.top}


def _ok_by_input(evidence: list[ToolResult]) -> dict[str, list[ToolResult]]:
    out: dict[str, list[ToolResult]] = {}
    for e in evidence:
        key = input_of(e)
        if key and e.status == "OK":
            out.setdefault(key, []).append(e)
    return out


# 후보 ID 번호대 (도시별). 정기 감시를 도시마다 따로 돌려도 같은 기준일 ID 가 겹치지 않는다
ID_BASE = {"춘천": 0, "강남": 500}


def classify_candidate_type(evidence: list[ToolResult], req: NormalizedRequest, as_of: date,
                            contract_check: dict | None = None, cities: list[str] | None = None,
                            rules: dict | None = None) -> list[Candidate]:
    """근거 → 후보 (예정된 점검 / 예상 밖 이탈) + 필수 근거 충족 여부. 도시마다 따로.

    - 예정된 점검: 트리거 + 동마다 (구조 또는 접근성). W 동절기는 춘천 읍·면 동에만, 필수 근거에 겨울 변화 추가
    - 예상 밖 이탈: 경보마다 도시 × 코호트. 동 목록은 그 도시 구조 취약성 순위로 "먼저 확인할 동" (공유 맥락)
    """
    cc = contract_check or {}
    rules = rules or LEVEL_RULES
    out: list[Candidate] = []
    cities = cities or [req.city]

    def next_id() -> str:
        # 도시마다 번호대를 나눠 같은 기준일의 두 도시 후보 ID 가 겹치지 않게 (춘천 001~, 강남 501~)
        n = sum(c.city == city for c in out)
        return f"C-{as_of:%Y%m%d}-{ID_BASE.get(city, 0) + n + 1:03d}"

    for city in cities:
        ev_city = [e for e in evidence if (city_of_evidence(e) or req.city) == city]
        have = _ok_by_input(ev_city)
        city_level = {k for k in have if k in CITY_LEVEL_SCHEDULED}
        target = (have.get("target_group") or [None])[0]
        triggers = have.get("calendar_trigger", [])
        cohort = (target.analysis_cohort if target else None) or cc.get("analysis_cohort") or "미확인"
        group = (target.service_eligibility_group if target else None) or cc.get("service_eligibility_group")

        # 예정된 점검 (진입: 트리거 + 동마다 구조 상위 또는 접근성 근거, rules.yaml candidate_types)
        if triggers:
            by_adm: dict[str | None, list[ToolResult]] = {}
            names: dict[str | None, ToolResult] = {}
            for key in ("structure_vulnerability", "access_gap"):
                for e in have.get(key, []):
                    by_adm.setdefault(adm_of(e), []).append(e)
                    names.setdefault(adm_of(e), e)
            ctx_by_adm: dict[str | None, list[str]] = {}
            for e in have.get("validation_reference", []):
                ctx_by_adm.setdefault(adm_of(e), []).append(e.evidence_id)
            facts = structure_facts(have.get("structure_vulnerability", []), rules)
            acc = {adm_of(e): e for e in have.get("access_gap", [])}
            need_axis = rules["candidate_types"]["scheduled_access_review"].get("require_axis_for_entry", True)
            if not by_adm:
                by_adm = {None: []}  # 구조·접근성 근거가 없으면 도시 단위 한 건 (부분 판정)
            below: list[tuple[str | None, list[ToolResult], list[ToolResult]]] = []
            made = 0
            for adm, evs in by_adm.items():
                ok_trig = [t for t in triggers
                           if not str(t.value).startswith("W") or (adm is not None and is_eupmyeon(names[adm]))]
                if not ok_trig:
                    continue
                if adm is not None and need_axis:
                    f = facts.get(adm)
                    _, a_counts, _, _ = access_axis(acc.get(adm), rules)
                    if not ((f is not None and f.top) or a_counts):
                        below.append((adm, evs, ok_trig))  # 기준 미달: 후보로 만들지 않음
                        continue
                out.append(_scheduled(next_id(), city, adm, evs, ok_trig, city_level, target, cohort, group,
                                      ctx_by_adm.get(adm, []), have))
                made += 1
            if below and not made:
                # 기준을 넘는 동이 없으면 도시 단위 한 건만 (트리거는 봤다는 기록, Level 1)
                trig = below[0][2]
                cand = _scheduled(next_id(), city, None, [e for _, evs, _ in below for e in evs], trig, city_level,
                                  target, cohort, group, [], have)
                out.append(cand.model_copy(update={"evidence_ids": [t.evidence_id for t in trig]
                                                   + ([target.evidence_id] if target else [])}))

        # 예상 밖 이탈
        structs = have.get("structure_vulnerability", [])
        tops = structure_tops(structs, rules)
        ranked = sorted(tops, key=lambda a: tops[a][1])[:3]
        first = ranked or [adm_of(e) for e in sorted(structs, key=lambda e: -float(e.value or 0))[:2]]
        series_by_cohort = {e.analysis_cohort: e for e in have.get("card_cohort_series", [])}
        for dev in have.get("cohort_deviation", []):
            key = signal_key(dev.evidence_id)
            related = [e for e in ev_city if signal_key(e.evidence_id) == key]
            avail = {input_of(e) for e in related if e.status == "OK" and input_of(e)}
            series = series_by_cohort.get(dev.analysis_cohort) or (
                next(iter(series_by_cohort.values())) if key == "" and series_by_cohort else None)
            if series:
                avail.add("card_cohort_series")
            # 기대 활동은 같은 코호트 것만 (코호트가 없는 근거는 도시 공통으로 봄)
            mine = lambda e: e.analysis_cohort in (None, dev.analysis_cohort)  # noqa: E731
            if any(mine(e) for e in have.get("expected_activity", [])):
                avail.add("expected_activity")
            if structs:
                avail.add("structure_vulnerability")
            chk = evaluate_inputs("unexpected_activity_deviation", avail)
            ids = [e.evidence_id for e in related] + ([series.evidence_id] if series else [])
            ids += [e.evidence_id for e in ev_city
                    if (input_of(e) == "expected_activity" or e.metric == FORECAST_METRIC) and mine(e)]
            out.append(Candidate(
                candidate_id=next_id(), candidate_type="unexpected_activity_deviation", city=city,
                check_first_adm=[a for a in first if a], analysis_cohort=dev.analysis_cohort or "미확인",
                service_eligibility_group=dev.service_eligibility_group or group, input_status=chk.status,
                missing_inputs=chk.missing_required, counter_result="보류", evidence_ids=list(dict.fromkeys(ids)),
                is_shared_context=True,
                event_id=f"{city}:DEV:{dev.analysis_cohort or '미확인'}:{dev.observed_to:%Y%m%d}" if dev.observed_to else None,
                event_opened_at=dev.simulated_available_at or dev.actual_available_at))
    return out


def _scheduled(cid, city, adm, evs, ok_trig, city_level, target, cohort, group, ctx_ids, have) -> Candidate:
    """예정된 점검 후보 하나 (event_id: 우선 트리거의 사건, 생성일: 트리거 공개일)."""
    winter = any(str(t.value).startswith("W") for t in ok_trig)
    ctype = "춘천 동절기 점검" if winter else "scheduled_access_review"
    avail = set(city_level) | {input_of(e) for e in evs}
    chk = evaluate_inputs(ctype, avail)
    ids = [t.evidence_id for t in ok_trig] + ([target.evidence_id] if target else [])
    ids += [e.evidence_id for e in evs] + ctx_ids
    if winter:
        ids += [e.evidence_id for e in have.get("winter_contraction", [])]
    codes = [RC.trigger_code(str(t.value)) or "R?" for t in ok_trig]
    main = ok_trig[RC.primary_trigger(codes)]
    code = RC.trigger_code(str(main.value)) or "R?"
    start = main.observed_from or main.as_of
    opened = main.simulated_available_at or main.actual_available_at
    if code == "W":
        y = start.year if start.month >= 11 else start.year - 1
        opened = date(y, 11, 1)
    return Candidate(
        candidate_id=cid, candidate_type="scheduled_access_review", city=city, adm_cd=adm,
        analysis_cohort=cohort, service_eligibility_group=group,
        trigger="; ".join(str(t.value) for t in ok_trig), input_status=chk.status,
        missing_inputs=chk.missing_required, counter_result="해당 없음", evidence_ids=ids,
        event_id=RC.event_id(city, code, start), event_opened_at=opened)


def judge_counter(c: Candidate, conf: dict[str, ToolResult]) -> tuple[str, str]:
    """이탈 후보 하나의 반증 결과 (기각 / 보류 / 통과) + 사유. 도시 공통 하락은 기각하지 않음."""
    if c.input_status == "보류":
        return "보류", f"필수 근거 결측으로 반증 판단 보류: {', '.join(c.missing_inputs)}"
    missing = [k for k in REQUIRED_CONFOUNDERS if k not in conf or conf[k].status != "OK"]
    if missing:
        codes = sorted({conf[k].error_code for k in missing if k in conf and conf[k].error_code})
        return "보류", f"반증 자료 결측: {', '.join(missing)}" + (f" ({', '.join(codes)})" if codes else "")
    oks = [e for e in conf.values() if e.status == "OK"]

    def note(e: ToolResult) -> str:
        return next((w for w in e.warnings if not w.startswith(("가정한 공개 시점", "검토 결정일", "가짜 도구"))), "")

    if any(e.value == "검토 중" for e in oks):
        return "보류", f"{REVIEW_PENDING_NOTE} (검토 결정일이 기준일 뒤라 결론을 쓰지 않음)"
    rejected = [e for e in oks if e.value == "기각"]
    if rejected:
        return "기각", "; ".join(f"{e.metric}: {note(e)}" for e in rejected)
    path = [e for e in oks if e.value == "기대 경로 안"]
    if path:
        return "기각", "; ".join(f"{e.metric}: {note(e)}" for e in path)
    if any(e.value == "공통 변화" for e in oks):
        return "보류", CITY_COMMON_NOTE
    held = [e for e in oks if e.value == "보류"]
    if held:
        return "보류", "; ".join(f"{e.metric}: {note(e)}" for e in held)
    reason = "반증 통과 (공휴일 기대값·도시 공통요인으로 설명되지 않음)"
    partial = [e for e in oks if e.value in ("설명 일부", "판정 불가")]
    if partial:
        reason += "; " + "; ".join(f"{e.metric} {e.value}: {note(e)}" for e in partial)
    return "통과", reason


def confounders_for(c: Candidate, evidence: list[ToolResult]) -> dict[str, ToolResult]:
    ids = set(c.evidence_ids)
    out: dict[str, ToolResult] = {}
    for e in evidence:
        key = input_of(e)
        if key in (*REQUIRED_CONFOUNDERS, "academic_calendar") and e.evidence_id in ids:
            out[key] = e
    return out


# ---------------------------------------------------------------- 등급 (rules-v1.0)


def access_known_at_as_of(e: ToolResult) -> bool:
    """접근성 근거가 기준일에 확인된 자료인가. 아니면 '현재 원장 기준 참고'.

    공개 시점(실제 또는 가정)이 있고 기준일 이전이며, 관측(원장 수집) 기간도 기준일 이전이어야 함.
    availability_basis=unknown, 원장 수집일이 기준일 뒤, 경고에 '현재 원장'이 있으면 False.
    """
    if e.availability_basis == "unknown":
        return False
    avail = e.actual_available_at or e.simulated_available_at
    if avail is None or avail > e.as_of:
        return False
    if (e.observed_to and e.observed_to > e.as_of) or (e.observed_from and e.observed_from > e.as_of):
        return False
    return not any("현재 원장" in w for w in e.warnings)


def access_axis(e: ToolResult | None, rules: dict | None = None) -> tuple[float | None, bool, bool, bool]:
    """접근성 축: (거리 km, 등급 축으로 셈, 현재 원장 표시, 참고만). 거리 기준(rules.yaml access.far_km) 이상이고
    기준일에 확인된 자료이거나(strict 아님) 일 때만 셈."""
    rules = rules or LEVEL_RULES
    if e is None or e.status != "OK" or not isinstance(e.value, (int, float)):
        return None, False, False, False
    a = float(e.value)
    far = a >= rules["thresholds"]["access"]["far_km"]
    known = access_known_at_as_of(e)
    strict = e.analysis_mode == "strict_as_of"
    allow_unknown = rules["thresholds"]["access"].get("count_unknown_availability_in_strict", False)
    counts = far and (known or not strict or allow_unknown)
    return a, counts, counts and not known, far and not counts


def evidence_confidence(c: Candidate, used: list[ToolResult], current_access: bool,
                        rules: dict | None = None) -> tuple[str, list[str]]:
    """근거 신뢰도 (rules.yaml confidence): 낮음 / 보통 / 높음 + 이유."""
    rules = rules or LEVEL_RULES
    min_hh = rules["thresholds"]["structure"]["min_households"]
    low: list[str] = []
    if c.input_status == "부분 판정":
        low.append("필수 근거 일부 없음")
    for e in used:
        if input_of(e) == "structure_vulnerability":
            if e.value_state not in (None, "observed"):
                low.append(f"구조 지표 값 상태 {e.value_state}")
            if e.n_obs is not None and e.n_obs < min_hh:
                low.append(f"구조 지표 분모 {e.n_obs}세대 (< {min_hh})")
        if input_of(e) == "weather_asos" and e.value == "판정 불가":
            low.append("강수 반증 판정 불가")
    if low:
        return "낮음", low
    mid = []
    if any(e.availability_basis != "reported" for e in used):
        mid.append("공개 시점 가정")
    if current_access:
        mid.append(ACCESS_CURRENT)
    if any(input_of(e) == "winter_contraction" for e in used):
        mid.append("계절 변화 한 해 관측")
    return ("보통", mid) if mid else ("높음", [])


def _drop_of(ev: ToolResult | None) -> float | None:
    if ev is None or not isinstance(ev.value, (int, float)):
        return None
    return round(1.0 - float(ev.value), 4)


def assign_level(candidates: list[Candidate], evidence: list[ToolResult], rules: dict | None = None,
                 as_of: date | None = None) -> list[Candidate]:
    """후보마다 등급 (configs/rules.yaml, bigcontest.rules.levels.decide). 기각 → Level 1, 보류 → 판단 보류,
    부분 판정·근거 신뢰도 낮음은 Level 3 불가. 모든 후보에 rule_id·rule_version·근거 신뢰도·대응 종류를 남긴다.

    구조 순위는 근거의 '순위 모집단'(도시 × 월 전체). 접근성 축: 기준일에 확인된 접근성만 확정 축.
    과거 공개 시점을 모르는 현재 원장 거리는 simulated_replay·retrospective 에서는 세되 "현재 원장 기준 접근성"으로
    표시하고, strict_as_of 에서는 세지 않는다 (참고로만 표시)."""
    if rules is None or "thresholds" not in rules:
        rules = LEVEL_RULES  # 예전 호출(STUB_LEVEL_RULES dict)도 받음
    ver = rules_version(rules)
    structs: list[ToolResult] = []
    access_ev: dict[str | None, ToolResult] = {}
    by_id = {e.evidence_id: e for e in evidence}
    for e in evidence:
        if e.status != "OK" or not isinstance(e.value, (int, float)):
            continue
        if input_of(e) == "structure_vulnerability":
            structs.append(e)
        elif input_of(e) == "access_gap":
            access_ev[adm_of(e)] = e
    facts = structure_facts(structs, rules)
    out = []
    for c in candidates:
        used = [by_id[i] for i in c.evidence_ids if i in by_id]
        ax = Axes(candidate_type=c.candidate_type, input_status=c.input_status, missing_inputs=list(c.missing_inputs),
                  counter_result=c.counter_result, counter_reason=c.counter_reason, trigger=c.trigger)
        current = False
        if c.candidate_type == "scheduled_access_review":
            ax.structure = facts.get(c.adm_cd) if c.adm_cd else None
            km, counts, current, ref = access_axis(access_ev.get(c.adm_cd), rules)
            ax.access_km, ax.access_counts, ax.access_current, ax.access_ref_only = km, counts, current, ref
        else:
            tops = [facts[a] for a in c.check_first_adm if a in facts and facts[a].top]
            ax.structure = tops[0] if tops else None
            ax.second_adm_axis = any(access_axis(access_ev.get(f.adm_cd), rules)[1] for f in tops)
            dev = next((e for e in used if input_of(e) == "cohort_deviation"), None)
            ax.deviation_drop = _drop_of(dev)
        ax.confidence, ax.confidence_why = evidence_confidence(c, used, current, rules)
        d = decide(ax, rules)
        level = d.level
        out.append(Candidate(**{
            **c.model_dump(), "candidate_level": level, "level_reason": f"{d.reason} {rule_label(d, rules)}",
            "rule_id": full_rule_id(d), "rule_version": ver, "rule_checks": rule_checks(ax, rules), "decided_at": as_of or c.decided_at,
            "evidence_confidence": ax.confidence,
            "response_type": RC.response_type(level, c.service_eligibility_group, rules)}))
    return out


def assign_queue(candidates: list[Candidate], evidence: list[ToolResult], k: int,
                 rules: dict | None = None) -> list[Candidate]:
    """검토 예산 K 로 순서·배정 (rules.yaml selection). 등급은 바꾸지 않는다."""
    vals = {adm_of(e): float(e.value) for e in evidence
            if e.status == "OK" and input_of(e) == "structure_vulnerability" and isinstance(e.value, (int, float))}
    return RC.assign_queue(candidates, vals, k, rules or LEVEL_RULES)
