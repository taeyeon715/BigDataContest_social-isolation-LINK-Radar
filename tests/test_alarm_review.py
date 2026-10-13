"""EDA-6 경보 보완 테스트: 세 값 논리, 보류 회계, 입력 계약, 강수, 연휴 뒤 회복, 기준일 카드."""

import itertools

import numpy as np
import pandas as pd
import pytest

from bigcontest.signals.alarm import (
    R_MISSING, R_WIN, Panel, as_of_guard, combine_any, holiday_review, null_accounting, null_summary, records, reindex_daily,
)
from fixtures import HOLIDAYS, card_daily_small

NAN = np.nan


# ------------------------------------------------------------ 둘 중 하나 (세 값 논리)
@pytest.mark.parametrize("a,b", list(itertools.product([1.0, 0.0, NAN], repeat=2)))
def test_combine_any_three_valued(a, b):
    got = combine_any(np.array([a]), np.array([b]))[0]
    if a == 1 or b == 1:
        assert got == 1
    elif a == 0 and b == 0:
        assert got == 0
    else:
        assert np.isnan(got)  # (0, 보류)·(보류, 0)·(보류, 보류)는 '경보 없음'이 아니라 보류


def test_constant_cohort_either_is_not_silent_zero():
    """한 코호트를 일정한 500으로 바꾸면 absolute는 MAD=0으로 보류, local은 0인 날이 생긴다 (반례)."""
    Y = card_daily_small()
    col = Y.columns[0]
    Y[col] = 500.0
    p = Panel(Y, HOLIDAYS)
    j = p.cols.index(col)
    a, loc = p.alarm_any(j, "absolute"), p.alarm_any(j, "local")
    either = p.alarm_any(j, "either")
    mixed = np.isnan(a) & (loc == 0)
    assert mixed.sum() > 0
    assert np.isnan(either[mixed]).all()
    # score_one도 같은 조합 함수를 쓴다
    fast = p.score_one(col, Y[col].to_numpy())
    np.testing.assert_array_equal(np.nan_to_num(fast["either"], nan=-1), np.nan_to_num(either, nan=-1))


# ------------------------------------------------------------ 보류 회계
def test_window_null_counted_separately():
    p = Panel(card_daily_small(), HOLIDAYS)
    na = null_accounting(p)
    cfg = p.cfg
    n_cols = len(p.cols)
    for path in ("absolute", "local"):
        c = na[path]
        assert c["z 없음 (경보도 보류)"] == cfg.min_train * n_cols
        # 학습 기간 뒤 첫 (min_valid − 1)일은 z가 있어도 창 안 유효 점수가 모자라 보류
        assert c["z 있음, 경보창 판정 불가"] == (cfg.min_valid - 1) * n_cols
        assert c["경보 보류 합계"] + c["경보 판정 가능"] == c["전체"]
        assert c["첫 경보 판정 가능일"] == p.dates[cfg.min_train + cfg.min_valid - 1].date()
    ns = null_summary(p)
    win = ns[(ns["단계"] == "경보창") & (ns["path"] == "local")]
    assert win["코호트·일"].sum() == (cfg.min_valid - 1) * n_cols


def test_state_exposes_alarm_null_reason():
    p = Panel(card_daily_small(), HOLIDAYS)
    st = p.state(p.cols[0], "local")
    t = p.cfg.min_train + 1
    assert st["reason"].iloc[t] == "" and not np.isnan(st["z"].iloc[t])
    assert np.isnan(st["alarm"].iloc[t])
    assert st["alarm_reason"].iloc[t] == R_WIN
    assert (st.loc[st["alarm"].notna(), "alarm_reason"] == "").all()


# ------------------------------------------------------------ 입력 계약
def test_panel_rejects_unsorted_dates():
    Y = card_daily_small()
    with pytest.raises(ValueError, match="정렬"):
        Panel(Y.iloc[::-1], HOLIDAYS)


def test_panel_rejects_duplicate_dates():
    Y = card_daily_small()
    Y2 = pd.concat([Y.iloc[:50], Y.iloc[49:]])
    with pytest.raises(ValueError, match="중복"):
        Panel(Y2, HOLIDAYS)


def test_panel_rejects_gaps():
    """이틀 간격 입력이 '7일 창'으로 14일을 보는 일이 없게 한다."""
    Y = card_daily_small()
    with pytest.raises(ValueError, match="하루 간격"):
        Panel(Y.iloc[::2], HOLIDAYS)


@pytest.mark.parametrize("bad", [np.inf, -np.inf, -1.0])
def test_panel_rejects_nonfinite_or_negative(bad):
    Y = card_daily_small()
    Y.iloc[40, 0] = bad
    with pytest.raises(ValueError, match="무한대|음수"):
        Panel(Y, HOLIDAYS)
    p = Panel(card_daily_small(), HOLIDAYS)
    y = p.Y.iloc[:, 0].to_numpy().copy()
    y[40] = bad
    with pytest.raises(ValueError):
        p.score_one(p.cols[0], y)


def test_missing_rows_are_not_zero_transactions():
    """빠진 날을 reindex_daily로 넣으면 NaN(미공급)이 되고, 그날은 0건이 아니라 '관측 없음'으로 보류된다."""
    Y = card_daily_small()
    gap = Y.index[60:63]
    Yg = reindex_daily(Y.drop(gap))
    assert Yg.loc[gap].isna().all().all()
    p = Panel(Yg, HOLIDAYS)
    st = p.state(p.cols[0], "absolute")
    assert (st.loc[gap, "reason"] == R_MISSING).all()
    assert st.loc[gap, "z"].isna().all()
    # 0으로 채우면 경보가 울린다 (공급 확인된 0건과 미공급은 다르다)
    p0 = Panel(Yg.fillna(0), HOLIDAYS)
    assert (p0.state(p0.cols[0], "absolute").loc[gap, "z"] < -2.5).all()
    assert not (st.loc[gap, "reason"] == "").any()


# ------------------------------------------------------------ 강수
def _rain_alarm_row(rain, Y):
    p = Panel(Y, HOLIDAYS)
    rec = records(p, rain=rain)
    col = Y.columns[0]
    return rec[(rec["city"] == "A") & (rec["cohort"] == col[1]) & (rec["alarm_date"] >= Y.index[90])].iloc[0]


def _target_drop():
    Y = card_daily_small()
    Y.iloc[90:97, 0] *= 0.6
    return Y


def test_rain_beta_not_estimable_is_null():
    """과거에 비 온 날이 모자라면 설명량 0이 아니라 NULL + 사유 + 표본 수."""
    Y = _target_drop()
    rain = pd.DataFrame({"A": 0.0, "B": 0.0}, index=Y.index)
    rain.iloc[[50, 91], 0] = 10.0  # 과거 비 온 날 1일뿐
    row = _rain_alarm_row(rain, Y)
    assert np.isnan(row["weather_explained_local"]) and np.isnan(row["after_weather_local"])
    assert row["rain_explained"] is None
    assert "추정 불가" in row["weather_status"]
    assert row["rain_n_wet"] == 1


def test_missing_weather_row_is_not_dry_day():
    """ASOS 관측 행이 없는 날은 무강수(0)가 아니다."""
    Y = _target_drop()
    idx = Y.index
    rain = pd.DataFrame({"A": 0.0, "B": 0.0}, index=idx)
    rain.iloc[list(range(30, 90, 6)), 0] = 10.0
    rain_missing = rain.drop(idx[91:93])  # 첫 경보 창(끝 = 인덱스 92) 안 이틀 관측 행 없음
    row = _rain_alarm_row(rain_missing, Y)
    assert row["rain_missing_in_window"] == 2
    assert row["rain_explained"] is None
    assert "누락" in row["weather_status"]
    full = _rain_alarm_row(rain, Y)
    assert full["rain_missing_in_window"] == 0
    assert full["rain_explained"] is not None and not full["rain_explained"]
    assert full["weather_status"] == ""


def test_no_weather_data_is_null():
    row = _rain_alarm_row(None, _target_drop())
    assert row["rain_explained"] is None and row["weather_status"] == "강수 자료 없음"


# ------------------------------------------------------------ 연휴 뒤 회복
H = 90  # 공휴일 인덱스


def _holiday_case(city_from: int, city_to: int | None, target_to: int | None = None, hol=(H,)):
    """A 도시 전체를 [city_from, city_to) 동안 40% 낮춤. target_to가 있으면 대상 코호트만 그때까지 계속 낮춤.
    hol: 공휴일 인덱스 (기본은 반례처럼 하루)."""
    Y = card_daily_small()
    city_cols = [c for c in Y.columns if c[0] == "A"]
    ci = [Y.columns.get_loc(c) for c in city_cols]
    Y.iloc[city_from:city_to, ci] *= 0.6
    if target_to is not None:
        Y.iloc[city_to:target_to, ci[0]] *= 0.6
    p = Panel(Y, [Y.index[h] for h in hol])
    return p, p.cols.index(city_cols[0])


def _first_abs_alarm(p, j, after=85):
    t = np.where(p.res["absolute"]["alarm"][:, j] == 1)[0]
    t = t[t >= after]
    assert len(t), "absolute 경보가 울려야 함"
    return int(t[0])


def test_city_wide_persistent_drop_is_not_holiday_recovery():
    """반례: 공휴일부터 도시 전체가 계속 40% 낮음 → '기각/회복'이 아니라 도시 공통 변화로 분류."""
    p, j = _holiday_case(H, None)
    rv = holiday_review(p, j, H + 2)
    assert rv["review"] != "기각"
    assert rv["review"] == "확인 필요" and rv["city_common_flag"]
    assert rv["recovery_abs"] == "평소보다 낮음 지속"
    assert rv["recovery_local"] == "도시 대비 추가 이탈 없음"
    assert rv["recovery_common"] == "도시 공통 하락 지속"
    assert "공휴일 원인의 증거가 아니" in rv["review_reason"]


HOL3 = (H - 1, H, H + 1)  # 사흘 연휴


def test_holiday_dip_then_real_recovery_is_rejectable():
    """사흘 연휴 동안 도시 전체가 낮았다가 연휴 뒤 평소 수준으로 돌아옴 → 기각 가능."""
    p, j = _holiday_case(H - 1, H + 2, hol=HOL3)
    t = _first_abs_alarm(p, j)
    rv = holiday_review(p, j, t)
    assert rv["review"] == "기각"
    assert rv["recovery_abs"] == "평소 수준 회복" and not rv["city_common_flag"]


def test_only_target_fails_to_recover_is_not_rejected():
    """같은 연휴인데 대상 코호트만 연휴 뒤에도 낮게 남음 → 기각하지 않음."""
    p, j = _holiday_case(H - 1, H + 2, hol=HOL3)
    t = _first_abs_alarm(p, j)
    p2, j2 = _holiday_case(H - 1, H + 2, target_to=len(p.dates), hol=HOL3)
    rv = holiday_review(p2, j2, t)
    assert rv["review"] not in ("기각", "확인 필요")
    assert rv["recovery_abs"] == "평소보다 낮음 지속" and rv["recovery_local"] == "도시 대비 이탈 지속"


def test_recovery_not_yet_observed_is_pending():
    p, j = _holiday_case(H - 1, H + 2, hol=HOL3)
    t = _first_abs_alarm(p, j)
    short = Panel(p.Y.iloc[: t + 2], p.holidays)  # 연휴 뒤 회복 확인일이 아직 없음
    rv = holiday_review(short, j, t)
    assert rv["review"] == "검토 중" and rv["recovery_abs"] == "회복 미관측"
    assert pd.isna(rv["review_decided_at"])


# ------------------------------------------------------------ 기준일 카드
def _card_case():
    """사흘 연휴에 도시 전체 하락 + 대상 코호트는 연휴 뒤까지 낮음, 비 자료 포함."""
    Y = card_daily_small()
    ci = [Y.columns.get_loc(c) for c in Y.columns if c[0] == "A"]
    Y.iloc[H - 1:H + 2, ci] *= 0.6
    Y.iloc[H + 2:H + 12, ci[1]] *= 0.6
    rain = pd.DataFrame({"A": 0.0, "B": 0.0}, index=Y.index)
    rain.iloc[list(range(20, 120, 5)), :] = 8.0
    return Y, rain, [Y.index[h] for h in HOL3]


@pytest.mark.parametrize("as_of_i", [H + 1, H + 3, H + 6])
def test_future_payments_and_weather_do_not_change_cards(as_of_i):
    Y, rain, hol = _card_case()
    as_of = Y.index[as_of_i]
    base = records(Panel(Y, hol), rain=rain, as_of=as_of)
    assert len(base)
    # 미래 결제·날씨 변경
    Y2, rain2 = Y.copy(), rain.copy()
    rng = np.random.default_rng(3)
    Y2.iloc[as_of_i + 1:] = (Y2.iloc[as_of_i + 1:] * rng.uniform(0.2, 2.0, Y2.iloc[as_of_i + 1:].shape)).round()
    rain2.iloc[as_of_i + 1:] = 30.0
    changed = records(Panel(Y2, hol), rain=rain2, as_of=as_of)
    # 미래 결제·날씨 삭제
    deleted = records(Panel(Y.iloc[: as_of_i + 1], hol), rain=rain.iloc[: as_of_i + 1], as_of=as_of)
    pd.testing.assert_frame_equal(base, changed)
    pd.testing.assert_frame_equal(base, deleted)
    assert (pd.to_datetime(base["episode_end"]) <= as_of).all()
    assert (pd.to_datetime(base["review_decided_at"]).dropna() <= as_of).all()
    assert (base["view"] == "기준일 카드").all()


def test_card_hides_review_decided_later():
    Y, rain, hol = _card_case()
    p = Panel(Y, hol)
    post = records(p, rain=rain)
    assert (post["view"] == "사후 목록").all()
    later = post[pd.to_datetime(post["review_decided_at"]) > pd.to_datetime(post["alarm_date"])]
    assert len(later), "사후 목록에 alarm_date 뒤에 결론 난 행이 있어야 함"
    row = later.iloc[0]
    card = records(p, rain=rain, as_of=row["alarm_date"])
    c = card[(card["city"] == row["city"]) & (card["cohort"] == row["cohort"]) & (card["alarm_date"] == row["alarm_date"])].iloc[0]
    assert c["review"] == "검토 중" and c["recovery_abs"] == "회복 미관측"
    assert pd.isna(c["review_decided_at"])
    assert c["review"] != row["review"]


def test_card_episode_end_and_path_until_as_of():
    Y, rain, hol = _card_case()
    p = Panel(Y, hol)
    post = records(p, rain=rain)
    long_ = post[pd.to_datetime(post["episode_end"]) > pd.to_datetime(post["alarm_date"])]
    assert len(long_)
    row = long_.iloc[0]
    card = records(p, rain=rain, as_of=row["alarm_date"])
    c = card[(card["city"] == row["city"]) & (card["cohort"] == row["cohort"]) & (card["alarm_date"] == row["alarm_date"])].iloc[0]
    assert c["episode_end"] == row["alarm_date"] and c["n_alarm_days"] == 1
    assert c["path"] == c["path_first"] == row["path_first"]


def test_time_fields_are_separate():
    Y, rain, hol = _card_case()
    post = records(Panel(Y, hol), rain=rain)
    r = post.iloc[0]
    assert r["trained_until"] < r["evidence_observed_to"] <= r["review_observed_to"]
    assert r["availability_basis"] == "unknown"


def test_as_of_guard_masks_late_conclusions():
    Y, rain, hol = _card_case()
    post = records(Panel(Y, hol), rain=rain)
    late = post[pd.to_datetime(post["review_decided_at"]) > pd.to_datetime(post["alarm_date"])]
    first = late.iloc[[0]].copy()
    first["episode_end"] = first["alarm_date"]
    g = as_of_guard(first, first["alarm_date"].iloc[0])
    assert g["review"].iloc[0] == "검토 중" and pd.isna(g["review_decided_at"].iloc[0])


def test_city_wide_drop_raises_city_alarm():
    """도시 전체 하락은 local로는 안 잡히지만 도시 경보(city_alarm)로 잡힌다."""
    Y = card_daily_small()
    ci = [Y.columns.get_loc(c) for c in Y.columns if c[0] == "A"]
    Y.iloc[90:97, ci] *= 0.6
    p = Panel(Y, HOLIDAYS)
    cs = p.city_signal["A"]
    assert (cs["city_alarm"].iloc[90:97] == 1).any()
    assert (cs["city_alarm"].iloc[60:85] == 0).all()
    assert (p.city_signal["B"]["city_alarm"].iloc[90:97] == 0).all()
