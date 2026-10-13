"""과거만 쓰는 시점 경보 (EDA-6).

입력: 날짜 × 코호트 일별 결제건수 표 (열 = (도시, 연령, 성) 같은 튜플, 첫 칸이 도시).

계산 순서 (날짜 t의 값은 t까지의 자료로만 정한다)
1. 기대값: t보다 앞선 같은 요일 값(최근 lookback일, 공휴일 제외)의 평균. e(t) = log(y+1) − 기대값
2. 도시 공통 변화 city_shock(t) = 같은 날 같은 도시 코호트들의 e 중앙값 (leave_target_out이면 대상 코호트 제외)
3. 두 경로
   - absolute: e(t) 자체 → 코호트가 평소보다 실제로 줄었나
   - local: e(t) − city_shock(t) → 도시 공통 변화를 빼고도 벗어났나
4. 점수 z(t) = (r(t) − 중앙값) / MAD. 중앙값·MAD는 t보다 앞선 날(공휴일 제외)로만 계산
   학습 기간이 짧거나, 과거 관측일이 부족하거나, MAD = 0이면 점수 대신 NaN + 보류 사유
5. 경보: 마지막 관측일 t의 창 [t−6, t]에서 z < −2.5인 날이 3일 이상. 창 안 점수가 5일 미만이면 보류
   (점수 보류 사유와 경보창 보류 사유는 따로 남긴다: z가 있어도 창 안 유효 점수가 모자라면 경보는 보류)
6. 두 경로 '둘 중 하나'는 세 값 논리: 하나라도 1이면 1, 둘 다 판정 가능하고 0이면 0, 나머지는 보류(NaN)
"""

import warnings
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

MAD_K = 1.4826
R_TRAIN, R_FEW, R_MAD0, R_NOEXP = "학습 기간 부족", "과거 관측일 부족", "MAD=0", "기대값 계산 불가"
R_WIN = "창 안 유효 점수 부족"  # 경보창 보류 사유 (z가 있어도 생길 수 있음)
R_MISSING = "관측 없음(미공급)"  # 입력 칸이 NaN: 공급 확인된 거래 0과 다름


@dataclass(frozen=True)
class AlarmConfig:
    lookback: int = 56  # 기대값·MAD에 쓰는 과거 일수
    min_train: int = 28  # 이보다 앞선 날은 평가하지 않음
    min_dow_obs: int = 2  # 기대값에 필요한 같은 요일 과거 관측 수
    min_past: int = 14  # 중앙값·MAD에 필요한 과거 관측일 수
    z_thr: float = -2.5
    win: int = 7
    k: int = 3
    min_valid: int = 5  # 창 안에 점수가 있어야 하는 날 수
    leave_target_out: bool = False
    dir_tol: float = 0.05  # 방향 판정: 평균 log 차이가 ±5% 안이면 '변화 없음'


def _nanmedian(a, axis):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis)


def _past_windows(x: np.ndarray, n_back: int) -> np.ndarray:
    """행 t = x[t−n_back : t] (t 자신은 포함하지 않음). 앞쪽은 NaN."""
    pad = np.concatenate([np.full(n_back, np.nan), x.astype(float)])
    return sliding_window_view(pad, n_back)[: len(x)]


def one_step_resid(y: np.ndarray, dow: np.ndarray, is_hol: np.ndarray, cfg: AlarmConfig) -> np.ndarray:
    """e(t) = log(y+1) − (t보다 앞선 같은 요일 평균). 같은 요일 과거 관측이 부족하면 NaN."""
    ly = np.log(np.asarray(y, float) + 1)
    out = np.full(len(ly), np.nan)
    n_same = cfg.lookback // 7
    for d in range(7):
        idx = np.where(dow == d)[0]
        v = np.where(is_hol[idx], np.nan, ly[idx])
        past = _past_windows(v, n_same)
        cnt = (~np.isnan(past)).sum(1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmean(past, 1)
        out[idx] = np.where(cnt >= cfg.min_dow_obs, ly[idx] - m, np.nan)
    return out


def robust_z(r: np.ndarray, is_hol: np.ndarray, cfg: AlarmConfig,
             missing: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """과거만 쓰는 robust z. 반환: z, 보류 사유(문자열 또는 ''), 중앙값, MAD.
    missing: 입력 관측이 없는 날(True). 그날은 '관측 없음(미공급)'으로 보류한다."""
    n = len(r)
    past = _past_windows(np.where(is_hol, np.nan, r), cfg.lookback)
    cnt = (~np.isnan(past)).sum(1)
    med = _nanmedian(past, 1)
    mad = _nanmedian(np.abs(past - med[:, None]), 1) * MAD_K
    reason = np.full(n, "", dtype=object)
    reason[np.isnan(r)] = R_NOEXP
    reason[cnt < cfg.min_past] = R_FEW
    reason[(cnt >= cfg.min_past) & (mad == 0)] = R_MAD0
    if missing is not None:
        reason[np.asarray(missing, bool)] = R_MISSING
    reason[np.arange(n) < cfg.min_train] = R_TRAIN
    ok = reason == ""
    z = np.full(n, np.nan)
    z[ok] = (r[ok] - med[ok]) / mad[ok]
    return z, reason, med, mad


def window_alarm(z: np.ndarray, cfg: AlarmConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """창 [t−win+1, t] 판정. 반환: alarm(1/0, 보류면 NaN), 낮은 날 수, 경보창 보류 사유('' 또는 R_WIN)."""
    pad = np.concatenate([np.full(cfg.win - 1, np.nan), z])
    W = sliding_window_view(pad, cfg.win)
    valid = (~np.isnan(W)).sum(1)
    low = (W < cfg.z_thr).sum(1)
    ok = valid >= cfg.min_valid
    alarm = np.where(ok, (low >= cfg.k).astype(float), np.nan)
    reason = np.where(ok, "", R_WIN).astype(object)
    return alarm, low, reason


def combine_any(a, b) -> np.ndarray:
    """두 경로 경보(1/0/NaN)의 '둘 중 하나' (세 값 논리).

    하나라도 1이면 1, 둘 다 판정 가능하고 0이면 0, 그 밖((0, 보류), (보류, 보류))은 보류(NaN).
    한 경로만 판정된 0을 완전한 '경보 없음'으로 저장하지 않는다.
    """
    a, b = np.asarray(a, float), np.asarray(b, float)
    out = np.full(np.broadcast_shapes(a.shape, b.shape), np.nan)
    out[(a == 0) & (b == 0)] = 0.0
    out[(a == 1) | (b == 1)] = 1.0
    return out


def city_shock(E: np.ndarray, cities: np.ndarray, leave_target_out: bool = False) -> np.ndarray:
    """날짜 × 코호트 행렬. 같은 도시 코호트 e의 중앙값(leave_target_out이면 자기 열 제외)."""
    S = np.full_like(E, np.nan)
    for c in np.unique(cities):
        cols = np.where(cities == c)[0]
        if not leave_target_out:
            S[:, cols] = _nanmedian(E[:, cols], 1)[:, None]
        else:
            for j in cols:
                others = [k for k in cols if k != j]
                S[:, j] = _nanmedian(E[:, others], 1) if others else np.nan
    return S


def direction(mean_log: float, tol: float = 0.05) -> str | None:
    """절대 변화 방향(schemas.Direction: 감소/증가/변화 없음). 도시보다 덜 늘어난 것은 '감소'가 아니다.
    값이 없으면 None (사유는 호출한 쪽이 보류 사유·경고로 남긴다)."""
    if mean_log is None or np.isnan(mean_log):
        return None
    if mean_log < -tol:
        return "감소"
    if mean_log > tol:
        return "증가"
    return "변화 없음"


def _value_problems(v: np.ndarray) -> list[str]:
    out = []
    obs = v[~np.isnan(v)]
    if np.isinf(obs).any():
        out.append(f"무한대 값 {int(np.isinf(obs).sum())}칸")
    fin = obs[np.isfinite(obs)]
    if (fin < 0).any():
        out.append(f"음수 값 {int((fin < 0).sum())}칸")
    return out


def check_panel_input(Y: pd.DataFrame) -> None:
    """Panel 입력 계약. 어기면 ValueError.

    - 날짜: 정렬, 중복 없음, 시각 없음, 하루 간격 연속 (행 수로 7일·56일을 세므로)
    - 값: 유한, 음수 아님. NaN은 '미공급(관측 없음)'으로 받아 보류 사유를 남긴다. 0으로 바꾸지 않는다.
    빠진 날짜가 있으면 reindex_daily()로 미공급(NaN) 행을 넣어서 부른다.
    """
    problems = []
    try:
        idx = pd.DatetimeIndex(Y.index)
    except (TypeError, ValueError):
        raise ValueError("Panel 입력 계약 위반: 날짜 인덱스가 아님") from None
    if idx.hasnans:
        problems.append("날짜 결측")
    if idx.has_duplicates:
        problems.append(f"날짜 중복 {int(idx.duplicated().sum())}개")
    if not idx.is_monotonic_increasing:
        problems.append("날짜가 오름차순으로 정렬되지 않음")
    if not idx.hasnans and (idx != idx.normalize()).any():
        problems.append("시각이 들어간 날짜")
    if len(idx) > 1 and idx.is_monotonic_increasing and not idx.has_duplicates:
        gaps = np.where(np.diff(idx.values) != np.timedelta64(1, "D"))[0]
        if len(gaps):
            g = gaps[0]
            problems.append(f"날짜가 하루 간격으로 이어지지 않음 {len(gaps)}곳 (예: {idx[g].date()} → {idx[g + 1].date()}). "
                            "빠진 날은 reindex_daily()로 미공급(NaN) 행을 넣을 것")
    if Y.columns.has_duplicates:
        problems.append("코호트 열 중복")
    try:
        v = Y.to_numpy(dtype=float)
    except (TypeError, ValueError):
        problems.append("숫자가 아닌 값")
    else:
        problems += _value_problems(v)
    if problems:
        raise ValueError("Panel 입력 계약 위반: " + "; ".join(problems))


def reindex_daily(Y: pd.DataFrame) -> pd.DataFrame:
    """날짜를 정렬하고 첫날~마지막 날을 하루 간격으로 채운다. 새로 생긴 날은 NaN(미공급)이지 0이 아니다.
    날짜 중복은 합치지 않고 오류로 둔다(어느 값이 맞는지 모름)."""
    if pd.DatetimeIndex(Y.index).has_duplicates:
        raise ValueError("날짜 중복: 합치는 규칙을 먼저 정할 것")
    Y = Y.sort_index()
    return Y.reindex(pd.date_range(Y.index[0], Y.index[-1], freq="D"))


def _check_column(y, n: int) -> np.ndarray:
    y = np.asarray(y, float)
    if y.shape != (n,):
        raise ValueError(f"열 길이 {y.shape}가 날짜 수 {n}와 다름")
    problems = _value_problems(y)
    if problems:
        raise ValueError("Panel 입력 계약 위반: " + "; ".join(problems))
    return y


class Panel:
    """한 시계열 묶음(실제 또는 주입)의 점수·경보.

    입력 Y: 날짜 × 코호트 결제건수. check_panel_input()을 통과해야 한다.
    NaN = 미공급(관측 없음). 공급 확인된 0건과 구분하며, 그날 점수는 '관측 없음(미공급)'으로 보류한다.
    (카드 daily()는 빠진 행을 0으로 채운다. 그 가정은 리포트 한계에 적는다.)
    """

    def __init__(self, Y: pd.DataFrame, holidays, cfg: AlarmConfig = AlarmConfig()):
        check_panel_input(Y)
        self.Y, self.cfg = Y.astype(float), cfg
        self.dates = pd.DatetimeIndex(Y.index)
        hol = pd.DatetimeIndex(sorted(holidays))
        self.holidays = hol
        self.missing = Y.isna().to_numpy()
        self.is_hol = self.dates.isin(hol)
        self.near_hol = np.array([np.abs((hol - d).days).min() <= 1 if len(hol) else False for d in self.dates])
        self.dow = self.dates.dayofweek.to_numpy()
        self.cols = list(Y.columns)
        self.cities = np.array([c[0] if isinstance(c, tuple) else c for c in self.cols])
        self.E = np.column_stack([one_step_resid(Y[c].to_numpy(), self.dow, self.is_hol, cfg) for c in self.cols])
        self._score()

    def _score(self):
        cfg = self.cfg
        self.S = city_shock(self.E, self.cities, cfg.leave_target_out)
        self.L = self.E - self.S
        res = {}
        for path, R in [("absolute", self.E), ("local", self.L)]:
            out = [robust_z(R[:, j], self.is_hol, cfg, self.missing[:, j]) for j in range(R.shape[1])]
            Z = np.column_stack([o[0] for o in out])
            res[path] = {
                "z": Z,
                "reason": np.column_stack([o[1] for o in out]),
                "mad": np.column_stack([o[3] for o in out]),
                "med": np.column_stack([o[2] for o in out]),
            }
            al = [window_alarm(Z[:, j], cfg) for j in range(Z.shape[1])]
            res[path]["alarm"] = np.column_stack([a[0] for a in al])
            res[path]["low"] = np.column_stack([a[1] for a in al])
            res[path]["alarm_reason"] = np.column_stack([a[2] for a in al])
        self.res = res
        # 도시 공통 변화 자체도 신호로 저장 (도시마다 하나). 같은 창 규칙으로 도시 경보(city_alarm)도 만든다
        self.city_signal = {}
        for c in np.unique(self.cities):
            s = _nanmedian(self.E[:, self.cities == c], 1)
            z, reason, _, _ = robust_z(s, self.is_hol, cfg)
            a, low, a_reason = window_alarm(z, cfg)
            self.city_signal[c] = pd.DataFrame({"city_shock": s, "city_shock_z": z, "reason": reason,
                                                "city_alarm": a, "city_alarm_reason": a_reason}, index=self.dates)

    def replace_column(self, col, y_new: np.ndarray) -> "Panel":
        """한 코호트만 바꾼 새 패널 (주입 실험용, 다른 열의 기대값은 다시 계산하지 않음)."""
        p = object.__new__(Panel)
        p.__dict__.update({k: v for k, v in self.__dict__.items() if k not in ("E", "res", "S", "L", "city_signal", "Y")})
        y_new = _check_column(y_new, len(self.dates))
        p.Y = self.Y.copy()
        p.Y[col] = y_new
        j = self.cols.index(col)
        p.missing = self.missing.copy()
        p.missing[:, j] = np.isnan(y_new)
        p.E = self.E.copy()
        p.E[:, j] = one_step_resid(np.asarray(y_new), self.dow, self.is_hol, self.cfg)
        p._score()
        return p

    def score_one(self, col, y_new: np.ndarray) -> dict:
        """한 코호트만 바꿨을 때 그 코호트의 경보만 다시 계산 (주입 실험을 빠르게). 결과는 replace_column과 같다."""
        cfg = self.cfg
        j = self.cols.index(col)
        y_new = _check_column(y_new, len(self.dates))
        e = one_step_resid(y_new, self.dow, self.is_hol, cfg)
        same = np.where(self.cities == self.cities[j])[0]
        others = [k for k in same if k != j]
        if cfg.leave_target_out:
            s = _nanmedian(self.E[:, others], 1)
        else:
            M = self.E[:, same].copy()
            M[:, list(same).index(j)] = e
            s = _nanmedian(M, 1)
        out = {}
        for path, r in [("absolute", e), ("local", e - s)]:
            z, reason, _, _ = robust_z(r, self.is_hol, cfg, np.isnan(y_new))
            out[path] = window_alarm(z, cfg)[0]
        out["either"] = combine_any(out["absolute"], out["local"])
        return out

    def until(self, as_of) -> "Panel":
        """as_of까지 잘린 패널 (기준일 조회용). 점수는 과거만 쓰므로 as_of까지의 값은 전체 패널과 같다.
        공휴일 달력은 미리 알 수 있으므로 그대로 넘긴다."""
        as_of = pd.Timestamp(as_of)
        if as_of < self.dates[0]:
            raise ValueError(f"as_of {as_of.date()}가 자료 시작일 {self.dates[0].date()}보다 앞섬")
        return Panel(self.Y.loc[self.dates <= as_of], self.holidays, self.cfg)

    def with_config(self, **kw) -> "Panel":
        return Panel(self.Y, self.holidays, replace(self.cfg, **kw))

    def state(self, col, path: str) -> pd.DataFrame:
        """코호트 하나의 날짜별 점수·경보·보류 사유.
        reason = 점수(z) 보류 사유, alarm_reason = 경보창 보류 사유 (z가 있어도 alarm이 NaN이면 채워짐)."""
        j = self.cols.index(col)
        r = self.res[path]
        return pd.DataFrame({
            "r": (self.E if path == "absolute" else self.L)[:, j],
            "z": r["z"][:, j], "reason": r["reason"][:, j], "alarm": r["alarm"][:, j],
            "alarm_reason": r["alarm_reason"][:, j], "low": r["low"][:, j],
        }, index=self.dates)

    def alarm_any(self, j: int, path: str) -> np.ndarray:
        """path = 'absolute' / 'local' / 'either'. 1/0, 보류는 NaN. either는 combine_any(세 값 논리)."""
        if path == "either":
            return combine_any(self.res["absolute"]["alarm"][:, j], self.res["local"]["alarm"][:, j])
        return self.res[path]["alarm"][:, j]


def _label(paths: set) -> str:
    return "both" if paths == {"absolute", "local"} else next(iter(paths))


VIEW_POSTHOC = "사후 목록"
VIEW_AS_OF = "기준일 카드"
AVAILABILITY_NOTE = "관측일 기준 재생: 카드·도시 공통요인의 실제 공급 지연 미반영"


def alarm_events(p: Panel, dedup_days: int = 7) -> list[dict]:
    """경보 발생만 (반증 검토 없음). 같은 코호트에서 dedup_days일 안에 다시 울리면(경로 무관) 한 묶음.
    묶음 사이 간격만 보므로 묶음 길이는 7일보다 길 수 있다.
    반환: j, col, t_first, t_last, path_first(첫 경보일 경로), path(묶음 안 경로 전체), n_alarm_days."""
    out = []
    for j, col in enumerate(p.cols):
        a_abs, a_loc = p.res["absolute"]["alarm"][:, j], p.res["local"]["alarm"][:, j]
        cur = None
        for t in range(len(p.dates)):
            fired = {k for k, a in (("absolute", a_abs[t]), ("local", a_loc[t])) if a == 1}
            if not fired:
                continue
            if cur is not None and t - cur["t_last"] <= dedup_days:
                cur["t_last"] = t
                cur["seen"] |= fired
                cur["n_alarm_days"] += 1
                continue
            cur = {"j": j, "col": col, "t_first": t, "t_last": t, "first": set(fired), "seen": set(fired), "n_alarm_days": 1}
            out.append(cur)
    for c in out:
        c["path_first"], c["path"] = _label(c.pop("first")), _label(c.pop("seen"))
    return out


def records(p: Panel, rain: pd.DataFrame | None = None, dedup_days: int = 7, as_of=None) -> pd.DataFrame:
    """경보 목록 (alarm_date = 첫 창의 마지막 관측일). 점수·경보(Panel) → 경보 묶음(alarm_events) → 반증 검토 순서.

    as_of=None: **사후 목록**. 패널 끝까지의 자료로 묶음 끝·경로 전체·회복 검토를 채운다.
      review_decided_at·recovery_observed_at이 alarm_date보다 늦은 행은 그날 화면에 보이던 상태가 아니다.
    as_of=날짜: **기준일 카드**. 패널을 as_of까지 잘라 다시 계산한다(기준일 뒤 결제·날씨는 쓰지 않음).
      회복을 아직 못 본 검토는 '검토 중 / 회복 미관측', episode_end·path는 as_of까지 본 값이다.

    - path_first: 첫 경보일의 경로. 방향·반증·강수 검토는 첫 창 기준이다
    - path: 묶음 안에서(기준일까지) 한 번이라도 울린 경로 전체 (absolute / local / both)
    - 시간 칸: trained_until(기대값·MAD에 쓴 마지막 날) / evidence_observed_to(경보 근거 마지막 관측일) /
      review_observed_to(반증 검토에 쓴 마지막 관측일) / availability_basis(공급일 확인 여부)
    경로별 중복 제거 건수는 episode_count()로 따로 센다.
    """
    view = VIEW_POSTHOC
    if as_of is not None:
        as_of = pd.Timestamp(as_of)
        p = p.until(as_of)
        if rain is not None:
            rain = rain.loc[pd.DatetimeIndex(rain.index) <= as_of]
        view = VIEW_AS_OF
    out = []
    for ev in alarm_events(p, dedup_days):
        j, t0, t1 = ev["j"], ev["t_first"], ev["t_last"]
        row = _record_row(p, j, ev["col"], t0, ev["path_first"])
        row.update(holiday_review(p, j, t0))
        row.update(weather_review(p, j, t0, rain))
        lo = max(0, t0 - p.cfg.win + 1)
        row.update({
            "episode_end": p.dates[t1], "n_alarm_days": ev["n_alarm_days"], "path_first": ev["path_first"], "path": ev["path"],
            "episode_near_holiday": bool(p.near_hol[lo: t1 + 1].any()),
        })
        row["review_scope"] = "첫 창" + (" (묶음이 뒤쪽 공휴일까지 이어짐)" if row["episode_near_holiday"] and not row["near_holiday"] else "")
        ends = [d for d in (row["review_decided_at"], row["recovery_observed_at"], p.dates[t0]) if not pd.isna(d)]
        row.update({
            "trained_until": (p.dates[t0] - pd.Timedelta(days=1)).date(),
            "evidence_observed_to": p.dates[t0].date(),
            "review_observed_to": max(ends).date(),
            "availability_basis": "unknown", "availability_note": AVAILABILITY_NOTE,
            "view": view, "as_of": p.dates[-1].date(),
        })
        out.append(row)
    df = pd.DataFrame(out)
    return as_of_guard(df, as_of) if as_of is not None else df


def as_of_guard(rec: pd.DataFrame, as_of) -> pd.DataFrame:
    """기준일 카드 안전장치: as_of 뒤에 정해진 결론·회복·묶음 끝이 보이지 않게 한다.
    records(as_of=...)는 잘린 패널로 계산하므로 보통 바뀌는 칸이 없다. 사후 목록에는 쓰지 않는다
    (사후 목록의 path는 기준일 뒤 경보까지 합친 값이라 이 함수로 되돌릴 수 없음)."""
    if rec.empty:
        return rec
    as_of = pd.Timestamp(as_of)
    rec = rec.copy()
    late = pd.to_datetime(rec["review_decided_at"]) > as_of
    rec.loc[late, ["review", "review_reason"]] = ["검토 중", f"공휴일 기대값·공통요인으로 설명되나 연휴 뒤 {NOT_SEEN}"]
    rec.loc[late, "review_decided_at"] = pd.NaT
    late_r = pd.to_datetime(rec["recovery_observed_at"]) > as_of
    rec.loc[late_r, ["recovery_abs", "recovery_local", "recovery_common"]] = NOT_SEEN
    rec.loc[late_r, ["recovery_abs_mean", "recovery_local_mean", "recovery_common_mean"]] = np.nan
    rec.loc[late_r, "recovery_observed_at"] = pd.NaT
    if (pd.to_datetime(rec["episode_end"]) > as_of).any() or (pd.to_datetime(rec["alarm_date"]) > as_of).any():
        raise ValueError("기준일 카드에 기준일 뒤 경보가 들어 있음: records(p, as_of=...)로 만들 것")
    return rec


def episode_count(alarm: np.ndarray, dedup_days: int = 7) -> int:
    """한 경로의 경보일 배열(1/0/NaN)에서 dedup_days일 안 재경보를 묶은 건수."""
    n, last = 0, None
    for t in np.where(alarm == 1)[0]:
        if last is None or t - last > dedup_days:
            n += 1
        last = t
    return n


def _record_row(p: Panel, j: int, col, t: int, path: str) -> dict:
    """첫 경보 창의 점수 근거 (alarm_date까지 자료만)."""
    cfg = p.cfg
    w = slice(max(0, t - cfg.win + 1), t + 1)
    e, s, l = p.E[w, j], p.S[w, j], p.L[w, j]
    mean_e, mean_s, mean_l = np.nanmean(e), np.nanmean(s), np.nanmean(l)
    city, *rest = col if isinstance(col, tuple) else (col,)
    # 방향은 경보를 만든 낮은 날들(z < 기준)의 절대 변화로 판정한다
    paths = ["absolute", "local"] if path == "both" else [path]
    flagged = np.zeros(len(e), bool)
    for k in paths:
        flagged |= np.nan_to_num(p.res[k]["z"][w, j], nan=0) < cfg.z_thr
    mean_flag = np.nanmean(e[flagged]) if flagged.any() else np.nan
    row = {
        "city": city, "cohort": " ".join(map(str, rest)),
        "alarm_date": p.dates[t], "window_start": p.dates[w][0], "window_end": p.dates[w][-1],
        "observed_from": p.dates[w][0].date(), "observed_to": p.dates[t].date(),
        "low_days_abs": int(p.res["absolute"]["low"][t, j]), "low_days_local": int(p.res["local"]["low"][t, j]),
        "mean_change_abs": np.expm1(mean_e), "city_shock_mean": np.expm1(mean_s), "mean_change_local": np.expm1(mean_l),
        "change_abs_low_days": np.expm1(mean_flag),
        "direction": direction(mean_flag, p.cfg.dir_tol),
        "direction_note": "" if flagged.any() else "방향 판정 불가: 창 안에 기준 아래 날이 없음",
        "near_holiday": bool(p.near_hol[w].any()),
    }
    return row


def _past_holiday_expect(p: Panel, j: int, start: int, R: np.ndarray) -> float:
    idx = np.where(p.is_hol[:start])[0]
    v = R[idx, j]
    v = v[~np.isnan(v)]
    return float(v.mean()) if len(v) else np.nan


REVIEWS = ("통과", "기각", "보류", "확인 필요", "검토 중")
REC_ABS = ("평소 수준 회복", "평소보다 낮음 지속")
REC_LOCAL = ("도시 대비 추가 이탈 없음", "도시 대비 이탈 지속")
REC_COMMON = ("도시 공통 회복", "도시 공통 하락 지속")
NOT_SEEN = "회복 미관측"


def _recovery(p: Panel, j: int, lo: int, t: int, recover_days: int, tol: float) -> dict:
    """연휴 뒤 회복을 세 가지로 나눠 본다.
    - recovery_abs: 대상 코호트가 평소 수준(absolute 잔차 ≥ tol)으로 돌아왔나
    - recovery_local: 도시 대비 추가 이탈이 없나 (도시 전체가 계속 낮아도 '추가 이탈 없음'일 수 있음)
    - recovery_common: 도시 공통요인이 돌아왔나
    연휴 끝(창 앞뒤 하루 안의 마지막 공휴일, 달력 기준) 다음 공휴일 아닌 날 recover_days일의 평균."""
    d_lo, d_t = p.dates[lo], p.dates[t]
    day = pd.Timedelta(days=1)
    hs = p.holidays[(p.holidays >= d_lo - day) & (p.holidays <= d_t + day)]
    last_h = hs.max() if len(hs) else d_t
    after = [i for i in np.where(p.dates > last_h)[0] if not p.is_hol[i]][:recover_days]
    out = {"recovery_abs": NOT_SEEN, "recovery_local": NOT_SEEN, "recovery_common": NOT_SEEN,
           "recovery_abs_mean": np.nan, "recovery_local_mean": np.nan, "recovery_common_mean": np.nan,
           "recovery_observed_at": pd.NaT}
    if len(after) < recover_days:
        return out
    for key, R, labels in (("abs", p.E, REC_ABS), ("local", p.L, REC_LOCAL), ("common", p.S, REC_COMMON)):
        m = float(np.nanmean(R[after, j]))
        out[f"recovery_{key}_mean"] = m
        out[f"recovery_{key}"] = labels[0] if m >= tol else labels[1]
    out["recovery_observed_at"] = p.dates[after[-1]]
    return out


def holiday_review(p: Panel, j: int, t: int, recover_days: int = 3, recover_tol: float = -0.05) -> dict:
    """공휴일 인접은 '반증 검토 시작' 조건일 뿐이다.

    대상 코호트의 공휴일 기대값(창 이전 공휴일의 local 잔차 평균), 도시 공통요인, 연휴 뒤 회복으로
    하락을 얼마나 설명하는지 계산한다.
    - 통과: 공휴일 기대값·공통요인을 빼도 경보 조건이 남음 (alarm_date에 결정)
    - 기각: 설명되고, 연휴 뒤 **평소 수준(absolute)으로 회복**했고 도시 대비 추가 이탈도 없음
    - 확인 필요(도시 공통 변화): 도시 대비 추가 이탈은 없지만 absolute는 계속 낮음. 다른 코호트도 함께
      낮아졌다는 설명일 뿐 공휴일이 원인이라는 증거가 아니므로 기각하지 않고 city_common_flag로 남긴다
    - 보류: 설명되나 대상 코호트가 도시 대비로도 회복하지 않음
    - 검토 중: 설명되나 연휴 뒤 회복을 아직 관측하지 못함 (review_decided_at 없음)
    회복 확인은 alarm_date 뒤 자료를 쓰므로 review_decided_at·recovery_observed_at을 따로 남긴다.
    정의된 '기대 연휴 경로'가 없으므로 회복은 평소 수준(absolute 잔차 ≥ recover_tol)으로만 본다.
    """
    cfg = p.cfg
    lo = max(0, t - cfg.win + 1)
    w = slice(lo, t + 1)
    if not p.near_hol[w].any():
        return {"review": "통과", "review_reason": "공휴일 인접 아님", "review_decided_at": p.dates[t],
                "drop_total": np.nan, "explained_common": np.nan, "explained_holiday": np.nan, "remaining": np.nan,
                "recovery_abs": "", "recovery_local": "", "recovery_common": "",
                "recovery_abs_mean": np.nan, "recovery_local_mean": np.nan, "recovery_common_mean": np.nan,
                "recovery_observed_at": pd.NaT, "city_common_flag": False}
    hol_exp = _past_holiday_expect(p, j, lo, p.L)
    adj = np.where(p.is_hol[w], np.nan_to_num(hol_exp), 0.0)
    e, s, l = p.E[w, j], p.S[w, j], p.L[w, j]
    drop = -np.nanmean(e)
    common = -np.nanmean(s)
    holiday = -np.nanmean(adj)
    remaining = -np.nanmean(l - adj)
    mad = p.res["local"]["mad"][w, j]
    z_adj = p.res["local"]["z"][w, j] - adj / mad
    still = np.nansum(z_adj < cfg.z_thr) >= cfg.k
    rec = _recovery(p, j, lo, t, recover_days, recover_tol)
    note = "" if not np.isnan(hol_exp) else " (과거 공휴일 관측 없음: 공휴일 기대값 0으로 둠)"
    city_flag = False
    decided = rec["recovery_observed_at"]
    if still:
        review, why, decided = "통과", "공휴일 기대값·공통요인을 빼도 이탈이 남음", p.dates[t]
    elif rec["recovery_abs"] == NOT_SEEN:
        review, why = "검토 중", f"공휴일 기대값·공통요인으로 설명되나 연휴 뒤 {NOT_SEEN}"
    elif rec["recovery_abs"] == REC_ABS[0] and rec["recovery_local"] == REC_LOCAL[0]:
        review, why = "기각", "공휴일 기대값·공통요인으로 설명되고 연휴 뒤 평소 수준으로 회복"
    elif rec["recovery_local"] == REC_LOCAL[0]:
        review, city_flag = "확인 필요", True
        why = ("도시 공통 변화로 분류: 도시 대비 추가 이탈은 없지만 연휴 뒤에도 평소보다 낮음. "
               "도시 공통 하락은 공휴일 원인의 증거가 아니므로 원인·접근성 확인 필요")
    else:
        review, why = "보류", f"공휴일 기대값·공통요인으로 설명되나 연휴 뒤 {rec['recovery_local']} ({rec['recovery_abs']})"
    return {"review": review, "review_reason": why + note, "review_decided_at": decided,
            "drop_total": drop, "explained_common": common, "explained_holiday": holiday, "remaining": remaining,
            **rec, "city_common_flag": city_flag}


def rain_beta(R: np.ndarray, rainy: np.ndarray, is_hol: np.ndarray, t0: int, lookback: int, min_n: int = 3,
              known: np.ndarray | None = None) -> tuple[float, int, int]:
    """t0보다 앞선 lookback일에서 '비 온 날 평균 − 안 온 날 평균'. 과거만 쓴다.
    반환 (beta, 비 온 날 수, 안 온 날 수). 어느 쪽이든 min_n일 미만이면 beta = NaN (추정 불가. 0이 아님).
    known: 강수 관측 행이 있는 날. 관측 행이 없는 날은 비 온 날·안 온 날 어느 쪽에도 넣지 않는다."""
    lo = max(0, t0 - lookback)
    r, w, h = R[lo:t0], rainy[lo:t0], is_hol[lo:t0]
    ok = ~np.isnan(r) & ~h
    if known is not None:
        ok &= known[lo:t0]
    a, b = r[ok & w], r[ok & ~w]
    if len(a) < min_n or len(b) < min_n:
        return np.nan, len(a), len(b)
    return float(a.mean() - b.mean()), len(a), len(b)


def weather_review(p: Panel, j: int, t: int, rain: pd.DataFrame | None, rain_mm: float = 1.0, min_n: int = 3) -> dict:
    """강수는 자동 기각하지 않는다. '날씨로 설명되는 부분'과 '보정 후 남는 이탈'을 기록하고,
    비를 빼면 경보 조건을 못 넘는 경보를 '강수로 설명되는 경보'로 따로 표시한다.

    - 강수 행이 없는 날(ASOS 관측 행 누락)은 무강수가 아니라 '모름'이다. 행은 있는데 강수량이 빈 날(무강수)은
      weather.py가 이미 0으로 둔다.
    - 비 효과(beta)를 추정할 과거가 모자라면 설명량을 0으로 두지 않고 NULL + 사유(weather_status) + 표본 수를 남긴다.
    - rain_explained: True(비를 빼면 경보 조건 미달) / False(빼도 남음, 또는 창에 경보 조건을 넘은 경로 없음) /
      None(판정 못 함). False도 '비가 설명하지 못함'의 증거로 쓰지 않는다(관측소 한 곳·과거 추정치 기준).
    """
    out = {"rain_days_in_window": np.nan, "rain_missing_in_window": np.nan, "rain_n_wet": np.nan, "rain_n_dry": np.nan,
           "weather_explained_abs": np.nan, "after_weather_abs": np.nan,
           "weather_explained_local": np.nan, "after_weather_local": np.nan, "rain_explained": None, "weather_status": ""}
    city = p.cities[j]
    if rain is None or city not in rain.columns:
        out["weather_status"] = "강수 자료 없음"
        return out
    cfg = p.cfg
    rv = rain[city].reindex(p.dates).to_numpy(float)  # NaN = 관측 행 없음
    known = ~np.isnan(rv)
    rainy = known & (rv >= rain_mm)
    lo = max(0, t - cfg.win + 1)
    w = slice(lo, t + 1)
    n_miss = int((~known[w]).sum())
    out["rain_missing_in_window"] = n_miss
    out["rain_days_in_window"] = int(rainy[w].sum())
    status, judged, n_wet, n_dry = [], [], [], []
    if n_miss:
        status.append(f"창 안 강수 관측 행 누락 {n_miss}일")
    for path, R in [("abs", p.E), ("local", p.L)]:
        key = "absolute" if path == "abs" else "local"
        beta, nw, nd = rain_beta(R[:, j], rainy, p.is_hol, lo, cfg.lookback, min_n, known)
        n_wet.append(nw)
        n_dry.append(nd)
        z = p.res[key]["z"][w, j]
        was = np.nansum(z < cfg.z_thr) >= cfg.k
        if np.isnan(beta):
            status.append(f"{key} 비 효과 추정 불가 (과거 비 온 날 {nw}일 · 안 온 날 {nd}일, 각 {min_n}일 이상 필요)")
            judged.append((was, None))
            continue
        if n_miss:
            judged.append((was, None))
            continue
        b = min(beta, 0.0)
        part = np.where(rainy[w], b, 0.0)
        drop = -np.nanmean(R[w, j])
        expl = -part.mean()
        out[f"weather_explained_{path}"] = expl
        out[f"after_weather_{path}"] = drop - expl
        z_adj = z - part / p.res[key]["mad"][w, j]
        judged.append((was, np.nansum(z_adj < cfg.z_thr) >= cfg.k))
    out["rain_n_wet"], out["rain_n_dry"] = min(n_wet), min(n_dry)
    fired = [now for was, now in judged if was]
    if not fired:
        out["rain_explained"] = False  # 창 안에서 경보 조건을 넘은 경로가 없음 (보정할 경보 없음)
    elif all(now is not None for now in fired):
        out["rain_explained"] = bool(not any(fired))
    out["weather_status"] = "; ".join(status)
    return out


def null_summary(p: Panel) -> pd.DataFrame:
    """보류 코호트·일 수를 단계·사유별로.
    단계 '점수' = z를 만들지 못한 사유, 단계 '경보창' = z는 있는데 경보 판정을 못 한 사유(창 안 유효 점수 부족 등)."""
    rows = []
    for path in ("absolute", "local"):
        res = p.res[path]
        r = res["reason"]
        vals, cnt = np.unique(r[r != ""], return_counts=True)
        for v, c in zip(vals, cnt):
            rows.append({"path": path, "단계": "점수", "사유": v, "코호트·일": int(c)})
        has_z = ~np.isnan(res["z"])
        ar = res["alarm_reason"][has_z & np.isnan(res["alarm"])]
        vals, cnt = np.unique(ar, return_counts=True)
        for v, c in zip(vals, cnt):
            rows.append({"path": path, "단계": "경보창", "사유": f"{v} (z는 있음)", "코호트·일": int(c)})
    return pd.DataFrame(rows)


def null_accounting(p: Panel) -> pd.DataFrame:
    """경로별 코호트·일 회계: z 없음 / z 있음·경보창 판정 불가 / 경보 보류 합계 / 경보 판정 가능, 첫 판정 가능일."""
    out = {}
    for path in ("absolute", "local"):
        z, a = p.res[path]["z"], p.res[path]["alarm"]
        a_nan = np.isnan(a)
        judged_days = np.where((~a_nan).any(1))[0]
        out[path] = {
            "z 없음 (경보도 보류)": int((np.isnan(z) & a_nan).sum()),
            "z 있음, 경보창 판정 불가": int((~np.isnan(z) & a_nan).sum()),
            "경보 보류 합계": int(a_nan.sum()),
            "경보 판정 가능": int((~a_nan).sum()),
            "z 없음, 경보 판정 가능": int((np.isnan(z) & ~a_nan).sum()),
            "전체": int(a.size),
            "첫 경보 판정 가능일": p.dates[judged_days[0]].date() if len(judged_days) else None,
        }
    return pd.DataFrame(out)


def _ar1(rng, n: int, sd: float, phi: float) -> np.ndarray:
    """정상(stationary) AR(1) 잡음. 주변 분산 sd², 이웃한 날 자기상관 phi."""
    phi = float(np.clip(phi, -0.95, 0.95))
    e = rng.normal(0, sd * np.sqrt(1 - phi**2), n)
    x = np.empty(n)
    x[0] = rng.normal(0, sd)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def _lag1(s: pd.Series) -> float:
    v = s.autocorr(1)
    return 0.0 if np.isnan(v) else float(v)


def rule_lag1(Y: pd.DataFrame, holidays) -> tuple[pd.Series, pd.Series]:
    """경보 규칙이 보는 잔차의 lag-1 자기상관. (코호트별 local 잔차, 도시별 city_shock). 공휴일은 뺀다."""
    idx = pd.DatetimeIndex(Y.index)
    is_hol = idx.isin(pd.DatetimeIndex(sorted(holidays)))
    E = pd.DataFrame(np.column_stack([one_step_resid(Y[c].to_numpy(), idx.dayofweek.to_numpy(), is_hol, AlarmConfig())
                                      for c in Y.columns]), index=idx, columns=Y.columns)
    E[is_hol] = np.nan
    cities = np.array([c[0] for c in Y.columns])
    shock = {c: E.loc[:, cities == c].median(axis=1) for c in np.unique(cities)}
    loc = pd.Series({col: _lag1(E[col] - shock[cities[j]]) for j, col in enumerate(Y.columns)})
    return loc, pd.Series({c: _lag1(s) for c, s in shock.items()})


def synthetic_normal(Y: pd.DataFrame, holidays, seed: int = 0, n_days: int | None = None, calibrate: bool = True) -> pd.DataFrame:
    """정상으로 만든 합성 시계열. 실제 자료의 수준·요일 효과·흔들림 크기·이웃한 날 자기상관만 빌려오고 이상은 넣지 않는다.

    log(y+1) = 코호트 요일 평균 + 도시 공통 잡음 + 코호트 잡음. 두 잡음은 AR(1)이다.
    - 흔들림 크기: 요일 효과·느린 추세를 뺀 잔차의 MAD
    - 자기상관: 경보 규칙이 실제로 보는 잔차(rule_lag1)와 맞춘다. 과거 평균을 빼면 자기상관이 줄어들므로
      calibrate=True이면 한 번 만들어 재 보고 모자란 만큼 AR 계수를 올려 다시 만든다.
    공휴일 효과는 없다(공휴일 날짜는 평일처럼 생성). attrs["lag1_real"], attrs["lag1_synthetic"]에 코호트별 값을 남긴다.
    """
    dates = pd.DatetimeIndex(Y.index) if n_days is None else pd.date_range(Y.index[0], periods=n_days)
    hol = pd.DatetimeIndex(sorted(holidays))
    L = np.log(Y.astype(float) + 1)
    L[pd.DatetimeIndex(Y.index).isin(hol)] = np.nan  # 공휴일은 빈칸으로 두고 날짜 간격은 유지
    dow_eff = L.groupby(L.index.dayofweek).mean()
    cities = np.array([c[0] for c in Y.columns])
    resid = L - dow_eff.reindex(L.index.dayofweek).to_numpy()
    resid = resid - resid.rolling(28, min_periods=7, center=True).median()  # 느린 추세 제거 후 흔들림
    sd_c, sd_i = {}, {}
    for c in np.unique(cities):
        m = resid.loc[:, cities == c].median(axis=1)
        sd_c[c] = float((m - m.median()).abs().median() * MAD_K)
    for j, col in enumerate(Y.columns):
        r = resid[col] - resid.loc[:, cities == cities[j]].median(axis=1)
        sd_i[col] = float((r - r.median()).abs().median() * MAD_K)
    tgt_i, tgt_c = rule_lag1(Y, holidays)

    def gen(phi_i, phi_c):
        rng = np.random.default_rng(seed)
        common = {c: _ar1(rng, len(dates), sd_c[c], phi_c[c]) for c in sd_c}
        out = {}
        for j, col in enumerate(Y.columns):
            mu = dow_eff[col].reindex(dates.dayofweek).to_numpy()
            ly = mu + common[cities[j]] + _ar1(rng, len(dates), sd_i[col], phi_i[col])
            out[col] = np.maximum(np.round(np.expm1(ly)), 0)
        return pd.DataFrame(out, index=dates)

    res = gen(tgt_i, tgt_c)
    if calibrate:
        got_i, got_c = rule_lag1(res, holidays)
        res = gen((tgt_i + (tgt_i - got_i)).clip(-0.9, 0.9), (tgt_c + (tgt_c - got_c)).clip(-0.9, 0.9))
    res.attrs["lag1_real"] = tgt_i.to_dict()
    res.attrs["lag1_synthetic"] = rule_lag1(res, holidays)[0].to_dict()
    return res
