"""외부 공공데이터 읽기 (external/ 폴더).

- 숫자로 바꾸지 못한 값은 0으로 채우지 않고 결측으로 두며, 사유와 행 수를 로그에 남긴다.
- 행정동 코드는 ref/adm_code_map.csv 한 곳에서만 연결한다. 앞자리를 잘라 맞추지 않는다.
"""

import logging
import re
import warnings

import numpy as np
import pandas as pd

from bigcontest.agent.schemas import ValueState
from bigcontest.config import ROOT

EXT = ROOT / "external"
CODE_MAP = ROOT / "ref" / "adm_code_map.csv"
log = logging.getLogger(__name__)

# 값 상태: 원본 0, 비공개(*), 원본 결측, 품질 의심(결측 처리)은 서로 다른 상태다. 기준은 schemas.ValueState
STATE_LABEL = {
    ValueState.OBSERVED.value: "값", ValueState.ZERO.value: "원본0", ValueState.SUPPRESSED.value: "비공개(*)",
    ValueState.MISSING.value: "결측", ValueState.QUALITY_SUSPECT.value: "품질의심",
}
SUPPRESSED_MARKS = {"*", "＊", "-*", "-＊"}  # 원본의 비공개 표기


def to_number(s: pd.Series, name: str = "") -> tuple[pd.Series, pd.Series]:
    """숫자로 변환. 실패한 값은 NA로 두고 사유를 따로 돌려준다(0으로 채우지 않음)."""
    raw = s.astype("string").str.strip().str.replace(",", "", regex=False)
    num = pd.to_numeric(raw, errors="coerce").astype(float)
    reason = pd.Series(pd.NA, index=s.index, dtype="string")
    reason[raw.isna()] = "원본 결측"
    sup = raw.isin(SUPPRESSED_MARKS).fillna(False)
    reason[sup] = "비공개(*)"
    reason[num.isna() & raw.notna() & ~sup] = "숫자 변환 실패"
    n_fail = int(reason.notna().sum())
    if n_fail:
        log.warning("%s: 숫자로 바꾸지 못한 값 %d개 (결측으로 둠) %s", name, n_fail, reason.value_counts().to_dict())
    return num, reason


def code_map(source_system: str, map_path=None) -> pd.DataFrame:
    m = pd.read_csv(map_path or CODE_MAP, dtype=str)
    return m[m["source_system"] == source_system].reset_index(drop=True)


def join_check(src_codes: pd.Series, source_system: str, pop: pd.Series | None = None, map_path=None) -> dict:
    """원천 코드 → adm_cd 결합 검사. 키 중복, 미매칭, 일대다, 양방향 일대일, 결합 전후 인구 합계."""
    m = code_map(source_system, map_path)
    keys = pd.Series(src_codes.unique())
    out = {
        "source_system": source_system,
        "n_source_codes": len(keys),
        "map_key_duplicates": int(m["source_code"].duplicated().sum()),
        "unmatched": sorted(set(keys) - set(m["source_code"])),
        "one_to_many": sorted(m.loc[m["source_code"].duplicated(keep=False), "source_code"].unique()),
        "many_to_one": sorted(m.loc[m["target_adm_cd"].duplicated(keep=False), "target_adm_cd"].unique()),
    }
    used = m[m["source_code"].isin(keys)]
    out["bijection"] = bool(not out["unmatched"] and not out["one_to_many"] and not out["many_to_one"]
                            and used["target_adm_cd"].nunique() == len(keys))
    if pop is not None:
        joined = src_codes.map(dict(zip(m["source_code"], m["target_adm_cd"])))
        out["pop_before"] = float(pop.sum())
        out["pop_after"] = float(pop[joined.notna()].sum())
    return out


def map_codes(src_codes: pd.Series, source_system: str, pop: pd.Series | None = None, map_path=None) -> pd.Series:
    """대응표로 adm_cd를 붙인다. 결합 검사를 통과하지 못하면 멈춘다. map_path: 대응표 파일(없으면 레포 기본)."""
    chk = join_check(src_codes, source_system, pop, map_path)
    bad = chk["map_key_duplicates"] or chk["unmatched"] or chk["one_to_many"] or chk["many_to_one"]
    if bad or (pop is not None and abs(chk["pop_before"] - chk["pop_after"]) > 1e-6):
        raise ValueError(f"행정동 코드 결합 검사 실패: {chk}")
    m = code_map(source_system, map_path)
    return src_codes.map(dict(zip(m["source_code"], m["target_adm_cd"])))


def resident_population(path=None, map_path=None) -> pd.DataFrame:
    """주민등록 성·연령별 인구 → 행정동 × 월 × 성 × 1세 연령 (long). 변환 실패 값은 NA + pop_na_reason.
    path·map_path: 고정 스냅샷 파일(없으면 레포 기본)."""
    d = pd.read_csv(path or EXT / "resident" / "주민등록_성연령별인구_2025-07_12.csv", dtype=str)
    d["adm_cd"] = map_codes(d["행정기관코드"], "mois_adm_cd2", map_path=map_path)
    d["month"] = pd.to_datetime(d["기준연월"]).dt.month
    cols = [c for c in d.columns if re.match(r"^\d+세(이상 )?(남자|여자)$", c)]
    long = d.melt(id_vars=["adm_cd", "month"], value_vars=cols, var_name="k", value_name="pop")
    long["age"] = long["k"].str.extract(r"^(\d+)").astype(int)
    long["sex"] = np.where(long["k"].str.endswith("남자"), "남", "여")
    long["pop"], long["pop_na_reason"] = to_number(long["pop"], "주민등록 인구")
    return long[["adm_cd", "month", "sex", "age", "pop", "pop_na_reason"]]


def resident_households(path=None, map_path=None) -> pd.DataFrame:
    """주민등록 세대원수별 세대수 → 행정동 × 월 (1인세대 비율 포함). path·map_path: 고정 스냅샷 파일(없으면 레포 기본)."""
    d = pd.read_csv(path or EXT / "resident" / "주민등록_세대원수별세대수_2025-07_12.csv", dtype=str)
    d["adm_cd"] = map_codes(d["행정기관코드"], "mois_adm_cd2", map_path=map_path)
    d["month"] = pd.to_datetime(d["기준연월"]).dt.month
    for c in ["전체세대수", "1인세대"]:
        d[c], _ = to_number(d[c], f"주민등록 {c}")
    d["one_person_rate"] = d["1인세대"] / d["전체세대수"]
    return d[["adm_cd", "month", "전체세대수", "1인세대", "one_person_rate"]]


RECIPIENTS_FILE = EXT / "welfare" / "서울_기초생활수급자_동별_2024.csv"


def seoul_recipients(path=None, names_path=None, gu: str = "강남구", city: str = "강남") -> pd.DataFrame:
    """서울시 국민기초생활보장 수급자(동별) 원본 CSV → 강남 행정동 × 연도 (총 수급 가구·인원).

    원본은 머리 4줄(시점·구분·단위·성별) 뒤에 '구, 동, 값…'. 동 이름으로 ref/adm_cd_names.csv 와 잇는다
    (같은 이름이 없으면 버리지 않고 adm_cd 결측). '-'(자료없음)은 결측, 0으로 채우지 않는다."""
    raw = pd.read_csv(path or RECIPIENTS_FILE, header=None, dtype=str, encoding="utf-8-sig")
    year, kind, unit, sub = (raw.iloc[i].tolist() for i in range(4))
    body = raw.iloc[4:]
    body = body[(body[0] == gu) & ~body[1].isin(["소계", "기타"])]
    cols = {"households": None, "persons": None}
    for j in range(2, raw.shape[1]):
        if kind[j] == "총 수급자" and sub[j] == "소계":
            if str(unit[j]).startswith("가구"):
                cols["households"] = j
            elif str(unit[j]).startswith("인원"):
                cols["persons"] = j
    names = pd.read_csv(names_path or ROOT / "ref" / "adm_cd_names.csv", dtype=str)
    names = names[names["city"] == city].set_index("adm_nm")["adm_cd"]
    out = pd.DataFrame({"adm_nm": body[1].to_numpy(), "year": int(year[cols["persons"]])})
    for k, j in cols.items():
        out[k], _ = to_number(body[j].replace("-", None).reset_index(drop=True), f"수급자 {k}")
    out["adm_cd"] = out["adm_nm"].map(names)
    return out[["adm_cd", "adm_nm", "year", "households", "persons"]]


SL_FILE = EXT / "seoul_living" / "시민생활_10개관심집단_강남_2025-07_12.csv"
SL_COLS = {
    "총인구": "pop",
    "1인가구수": "single",
    "외출-커뮤니케이션이 모두 적은 집단(전체)": "iso_all",
    "외출이 매우 적은 집단(전체)": "out_low_all",
    "커뮤니케이션이 적은 집단": "comm_low_single",
}
SL_INDICATORS = [
    "커뮤니케이션이 적은 집단", "평일 외출이 적은 집단", "휴일 외출이 적은 집단", "출근소요시간 및 근무시간이 많은 집단",
    "외출이 매우 적은 집단(전체)", "외출이 매우 많은 집단", "동영상서비스 이용이 많은 집단", "생활서비스 이용이 많은 집단",
    "재정상태에 대한 관심집단", "외출-커뮤니케이션이 모두 적은 집단(전체)",
]


def _seoul_living_raw(path=None) -> pd.DataFrame:
    return pd.read_csv(path or SL_FILE, dtype=str)


def seoul_living_quality(raw: pd.DataFrame | None = None, min_rows: int = 12) -> pd.DataFrame:
    """동별·지표별 0값 비율 자동 점검.

    품질 의심 = 그 동에서 6개월 모든 행이 0인데, 강남 전체에서 그 지표의 0 비율은 낮은(20% 미만) 경우.
    원인은 알 수 없으므로 '품질 의심'으로만 표시한다.
    """
    d = (raw if raw is not None else _seoul_living_raw()).copy()
    rows = []
    for c in SL_INDICATORS:
        v, _ = to_number(d[c], c)
        zero = v.eq(0)
        city_zero = float(zero.mean())
        for (adm7, nm), z in zero.groupby([d["adm_cd7"], d["행정동명"]]):
            share = float(z.mean())
            rows.append({"adm_cd7": adm7, "원본 동명": nm, "지표": c, "행 수": len(z), "0값 비율": share,
                         "강남 전체 0값 비율": city_zero,
                         "품질 의심": bool(share == 1.0 and len(z) >= min_rows and city_zero < 0.2)})
    return pd.DataFrame(rows)


def seoul_living(quality_na: bool = True, path=None, map_path=None) -> pd.DataFrame:
    """서울 시민생활 10개 관심집단 (강남) → 행정동 × 월 × 성 × 5세 연령.

    '(전체)' 집단은 전체 인구 기준, 나머지 집단은 1인가구 기준.
    quality_na=True이면 품질 의심 칸(예: 개포3동 7개 지표)을 NA로 바꾼다. 각 값의 상태는 <열>_state에
    ValueState 값(observed/zero/suppressed/missing/quality_suspect)으로 남긴다. 표시용 한국어는 STATE_LABEL.
    path·map_path: 고정 스냅샷 파일(없으면 레포 기본).
    """
    d = _seoul_living_raw(path)
    d["adm_cd"] = map_codes(d["adm_cd7"], "seoul_living_adm7", pop=pd.to_numeric(d["총인구"]), map_path=map_path)
    d["month"] = d["month"].astype(int)
    d["sex"] = d["성별"].astype(int).map({1: "남", 2: "여"})
    d["age"] = d["연령대"].astype(int)
    q = seoul_living_quality(d)
    suspect = set(map(tuple, q.loc[q["품질 의심"], ["adm_cd7", "지표"]].to_numpy()))
    keep = []
    for src, dst in SL_COLS.items():
        v, reason = to_number(d[src], src)
        state = pd.Series(ValueState.OBSERVED.value, index=d.index, dtype="string")
        state[v.eq(0).fillna(False)] = ValueState.ZERO.value
        state[reason.eq("비공개(*)").fillna(False)] = ValueState.SUPPRESSED.value
        state[reason.isin(["원본 결측", "숫자 변환 실패"]).fillna(False)] = ValueState.MISSING.value
        bad = d["adm_cd7"].map(lambda a, s=src: (a, s) in suspect).astype(bool)
        state[bad] = ValueState.QUALITY_SUSPECT.value
        if quality_na:
            v = v.mask(bad)
        d[dst], d[f"{dst}_state"] = v, state
        keep += [dst, f"{dst}_state"]
    return d[["adm_cd", "month", "sex", "age"] + keep]


def std_rate(df: pd.DataFrame, num: str, den: str, by: str = "adm_cd", strata=("sex", "age"), warn: bool = True) -> pd.DataFrame:
    """직접 표준화 비율. 가중치는 전체 df의 strata별 분모 구성.

    가중치 커버리지(그 단위에 실제로 값이 있는 칸의 가중치 합)를 함께 돌려주고, 100%가 아니면 경고한다.
    분자가 NA인 칸(품질 의심 등)은 그 단위에서 빠진 칸으로 본다. 빠진 칸이 있으면 남은 가중치로 다시 나눈다.
    """
    strata = list(strata)
    sub = df.dropna(subset=[num, den])
    w = df.groupby(strata)[den].sum()
    w = w / w.sum()
    g = sub.groupby([by] + strata)[[num, den]].sum()
    g = g[g[den] > 0]
    rate = (g[num] / g[den]).unstack(strata).reindex(columns=w.index)
    cover = rate.notna().mul(w, axis=1).sum(axis=1)
    out = pd.DataFrame({"rate": rate.mul(w, axis=1).sum(axis=1, min_count=1) / cover.replace(0, np.nan), "coverage": cover})
    low = out[out["coverage"] < 1 - 1e-9]
    if warn and len(low):
        warnings.warn(f"표준화 가중치 커버리지 100% 미만: {low['coverage'].round(3).to_dict()}", stacklevel=2)
    return out


def resources() -> pd.DataFrame:
    return pd.read_csv(EXT / "resources" / "복지자원_원장.csv", dtype={"adm_cd": str})


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))
