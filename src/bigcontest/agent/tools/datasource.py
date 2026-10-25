"""도구가 자료를 읽는 한 곳. 모든 실제 도구는 여기서 고정한 `snapshot_id` 로 읽는다.

- 지금은 "dev" 스냅샷 하나: data/interim(정제 parquet) + external + ref + configs/project.yaml(공휴일)
  + rules-v2.0 칸 판정 입력(성·연령별 1인세대, 강남 성별 수급자, 춘천 기초연금 연보).
  snapshot_id = "dev-" + (입력 파일 해시들의 해시) 앞 12자리. 파일이 하나라도 바뀌면 id 가 바뀐다.
- **지정 파일만 읽음**: 모든 표(카드·공휴일·날씨·주민등록·시민생활·통신·원장·동 이름)는 이 객체의
  `self.files[원천]` 에서만 읽는다. 전역 ROOT·설정 파일 경로를 다시 찾지 않는다. root 나 files 를 바꾸면
  그 파일들만 읽는다.
- **해시는 만들 때 한 번**: `self.hashes` 는 객체를 만들 때 계산한 값이다. 실행 메타데이터도 이 값으로
  적는다 (지금 파일을 다시 해시해 섞지 않음).
- 파일이 바뀐 경우 (실제 검사 범위)
  - 객체를 만들 때: 지금 파일의 id 가 고정한 id 와 다르면 `SnapshotMismatch`.
  - 표를 **처음** 읽을 때: 파일이 만들 때와 다르면 `SnapshotMismatch` (조용히 새 자료를 읽지 않음).
  - 이미 읽어 둔 표: 캐시 값(만들 때 자료)을 그대로 돌려준다. 여기서는 파일 변경을 검사하지 않는다.
  - 메타데이터를 쓸 때(`run_metadata`): 다시 확인해 바뀌었으면 `snapshot_check` 에 "불일치 … 재실행 필요"를
    적거나(기본) `on_change="raise"` 면 `SnapshotMismatch`. 해시 칸은 언제나 만들 때 값.
  - 재개·발행 전에는 `verify()` 로 확인한다. 프로세스를 다시 시작해 같은 id 를 열면 객체를 새로 만들므로,
    파일이 바뀌었으면 `SnapshotMismatch` (재실행 필요).
- 나중에 발행 스냅샷(snapshot.py, data/snapshots/<id>/)을 읽게 할 때는 `DataSource(sid, files=…)` 로 그 파일을
  지정하거나, 같은 메서드를 가진 클래스를 만들어 `register_source` 로 등록하면 된다. 도구 코드는 바꾸지 않는다.
- 읽은 표는 스냅샷별로 한 번만 읽어 둔다 (프로세스 안 캐시).
- 카드 daily() 는 결제 행이 없는 (날짜, 코호트)를 0건으로 채운다. `card_observed()` 는 원자료 행이 있던 칸(True)을
  따로 남겨, 도구가 "행 없음=0건 가정" 일수를 근거 경고에 적게 한다 (card.py 는 바꾸지 않음).
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from bigcontest.config import ROOT

# 원천 이름 → 파일 (원천 이름은 grain_contract.yaml 의 sources 와 같게)
DEV_FILES: dict[str, str] = {
    "card2": "data/interim/card2.parquet",
    "card1": "data/interim/card1.parquet",
    "tel_age": "data/interim/tel_age.parquet",
    "tel_time": "data/interim/tel_time.parquet",
    "tel_wkdy": "data/interim/tel_wkdy.parquet",
    "resident_pop": "external/resident/주민등록_성연령별인구_2025-07_12.csv",
    "resident_hh": "external/resident/주민등록_세대원수별세대수_2025-07_12.csv",
    "resident_single": "external/resident/주민등록_성연령별1인세대_2025-07_12.csv",  # rules-v2.0 S축 (칸 1인세대)
    "seoul_living": "external/seoul_living/시민생활_10개관심집단_강남_2025-07_12.csv",
    "asos": "external/weather/kma_asos_daily_2025-07_12.csv",
    "asos_heat": "external/weather/kma_asos_heat_2025-07_12.csv",  # 일 최고 체감온도 근사 (R4 특보 단계)
    "welfare_recipients": "external/welfare/서울_기초생활수급자_동별_2024.csv",  # 서울 동별 수급자 (표시만)
    # rules-v2.0 V축 (등급 입력): 강남 동 × 성 수급자, 춘천 읍면동 기초연금 수급률
    "welfare_recipients_sex": "analysis/taeyeon_2axis/external/seoul_welfare/서울_국민기초생활보장수급자_동별_강남구_2024.csv",
    "chuncheon_pension": "analysis/taeyeon_2axis/external/chuncheon/춘천통계연보2025_11_보건및사회보장_20241231.xlsx",
    "resources": "external/resources/복지자원_원장_v2.csv",
    "adm_names": "ref/adm_cd_names.csv",
    "adm_code_map": "ref/adm_code_map.csv",
    "config": "configs/project.yaml",
}
DERIVED = {"card2", "card1", "tel_age", "tel_time", "tel_wkdy"}  # 정제 규칙을 거친 파일


class DataSourceError(RuntimeError):
    code = "DATA_ERROR"


class DataUnavailable(DataSourceError):
    code = "DATA_UNAVAILABLE"


class SnapshotMismatch(DataSourceError):
    code = "SNAPSHOT_MISMATCH"


_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_LOCK = threading.Lock()


def _sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _HASH_CACHE:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def file_hash(path: Path) -> str:
    """파일 sha256 (경로·수정 시각·크기가 같으면 다시 읽지 않음)."""
    return _sha256(Path(path))


def dev_files(root: Path = ROOT) -> dict[str, Path]:
    return {k: Path(root) / v for k, v in DEV_FILES.items()}


def _hashes_of(files: dict[str, Path]) -> dict[str, str]:
    missing = [k for k, p in files.items() if not p.exists()]
    if missing:
        raise DataUnavailable(f"스냅샷 파일 없음: {', '.join(missing)}")
    return {k: _sha256(p) for k, p in files.items()}


def _snapshot_id_of(hashes: dict[str, str]) -> str:
    joined = "\n".join(f"{k}:{hashes[k]}" for k in sorted(hashes))
    return "dev-" + hashlib.sha256(joined.encode()).hexdigest()[:12]


def dev_hashes(root: Path = ROOT) -> dict[str, str]:
    return _hashes_of(dev_files(root))


def dev_snapshot_id(root: Path = ROOT) -> str:
    """지금 파일로 만든 dev 스냅샷 id. 파일이 없으면 DataUnavailable."""
    return _snapshot_id_of(dev_hashes(root))


def files_snapshot_id(files: dict[str, Path]) -> str:
    """지정한 파일 묶음의 스냅샷 id (DataSource(files=…) 와 같은 계산)."""
    return _snapshot_id_of(_hashes_of({k: Path(v) for k, v in files.items()}))


def dev_available(root: Path = ROOT) -> bool:
    return all(p.exists() for p in dev_files(root).values())


def _card2_from(path: Path) -> pd.DataFrame:
    """card.load_card2(local_only=True) 와 같은 거르기를 지정 파일에 적용
    (본사등록 업종 제외, 같은 시도 거주, 10대 제외, 80·90대 → 80대+)."""
    from bigcontest.card import AGE_MAP

    # 읽으면서 거름 (180만 행 × 모든 칸을 올린 뒤 거르지 않음 → 순간 메모리 약 −300MB).
    # 거르는 칸 셋만 따로 읽어 원래 행 번호(index)를 그대로 붙인다 → 예전과 행·순서·dtype·index 까지 같음
    keys = pd.read_parquet(path, columns=["hq", "local", "age"])
    pos = np.flatnonzero((~keys["hq"] & keys["local"] & keys["age"].isin(AGE_MAP)).to_numpy())
    del keys
    d = pd.read_parquet(path, filters=[("hq", "==", False), ("local", "==", True), ("age", "in", list(AGE_MAP))])
    if len(d) != len(pos):  # 거르는 규칙이 어긋나면 조용히 넘어가지 않음
        raise DataSourceError(f"card2 거르기 결과가 다름 ({len(d)} != {len(pos)})")
    d.index = pd.Index(pos, dtype="int64")
    d["age_g"] = d["age"].map(AGE_MAP)
    return d


def _card1_from(path: Path) -> pd.DataFrame:
    """card.load_card1 과 같은 거르기를 지정 파일에 적용 (법인·본사등록·LPG가스 제외, 10대 제외, 80·90대 → 80대+)."""
    from bigcontest.card import AGE_MAP

    d = pd.read_parquet(path, columns=["date", "city", "TIME_GB", "SEX_CCD", "age", "USE_CNT", "hq", "corp",
                                       "MCT_RY_CD"])
    d = d[~d["hq"] & ~d["corp"] & (d["MCT_RY_CD"] != "LPG가스")]
    d = d[d["age"].isin(AGE_MAP)].copy()
    d["age_g"] = d["age"].map(AGE_MAP)
    return d[["date", "city", "TIME_GB", "SEX_CCD", "age_g", "USE_CNT"]]


class DataSource:
    """고정 스냅샷 읽기. 표는 처음 쓸 때 읽고 이 객체에 둔다.

    files: 원천 이름 → 파일 (없으면 root 아래 DEV_FILES). 모든 표는 이 파일에서만 읽는다.
    """

    def __init__(self, snapshot_id: str, root: Path = ROOT, files: dict[str, Path] | None = None):
        self.root = Path(root)
        self.files = {k: Path(v) for k, v in (files or dev_files(self.root)).items()}
        self.hashes = _hashes_of(self.files)  # 만들 때 한 번. 메타데이터도 이 값
        sid = _snapshot_id_of(self.hashes)
        if sid != snapshot_id:
            raise SnapshotMismatch(f"고정한 스냅샷 {snapshot_id} 와 지금 파일({sid})이 다름. 재실행 필요 (새 실행으로 다시 분석)")
        self.snapshot_id = snapshot_id
        self._cache: dict[Any, Any] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------ 버전·일관성
    def data_version(self, source: str) -> str:
        """근거 봉투의 data_version: <원천>@sha256:<앞 12자리>[/정제 규칙]."""
        from bigcontest.ingest import CLEANING_RULE_VERSION

        key = source if source in self.hashes else None
        if key is None:
            return f"{source}@{self.snapshot_id}"
        v = f"{source}@sha256:{self.hashes[key][:12]}"
        return v + (f"/{CLEANING_RULE_VERSION}" if source in DERIVED else "")

    def changed_files(self) -> list[str]:
        """만들 때 해시와 지금 파일이 다른 원천 (없어진 파일 포함). 빈 목록이면 일치."""
        return [k for k, p in self.files.items() if not p.exists() or _sha256(p) != self.hashes[k]]

    def verify(self) -> None:
        """지금 파일이 고정한 스냅샷 그대로인지. 다르면 SnapshotMismatch (재개·발행 전에 부름)."""
        changed = self.changed_files()
        if changed:
            raise SnapshotMismatch(f"{self.snapshot_id}: 입력 파일이 바뀜 ({', '.join(changed)}). 재실행 필요")

    def raw_file_hashes(self) -> dict[str, str]:
        """메타데이터용 원본 해시 (파일 이름 → sha256). 만들 때 캡처한 값."""
        return {self.files[k].name: self.hashes[k] for k in sorted(self.files, key=lambda k: self.files[k].name)}

    def run_metadata(self, run_id: str, analysis_mode: str, as_of, thresholds: dict | None = None,
                     rule_version: str | None = None, on_change: str = "mark"):
        """runmeta.RunMetadata. 원본 해시 = **이 객체를 만들 때 캡처한** 해시 (지금 파일을 다시 해시해 섞지 않음).

        지금 파일이 바뀌었으면 on_change="mark"(기본): snapshot_check 에 불일치·재실행 필요를 적음,
        on_change="raise": SnapshotMismatch.
        """
        from bigcontest.runmeta import SNAPSHOT_OK, collect, snapshot_changed_note

        changed = self.changed_files()
        if changed and on_change == "raise":
            self.verify()
        return collect(run_id, self.snapshot_id, analysis_mode, as_of, raw_hashes=self.raw_file_hashes(),
                       thresholds=thresholds, rule_version=rule_version,
                       ledger=f"ledger@sha256:{self.hashes['resources'][:12]}", root=self.root,
                       snapshot_check=snapshot_changed_note(changed) if changed else SNAPSHOT_OK)

    def _get(self, key, fn):
        with self._lock:
            if key not in self._cache:
                # 처음 읽을 때 파일이 고정한 스냅샷 그대로인지 다시 확인 (해시는 mtime·크기로 캐시돼 싸다)
                changed = self.changed_files()
                if changed:
                    raise SnapshotMismatch(f"{self.snapshot_id}: 실행 중에 입력 파일이 바뀜 ({', '.join(changed)}). "
                                           "재실행 필요 (새 실행으로 다시 분석)")
                self._cache[key] = fn()
            return self._cache[key]

    # ------------------------------------------------ 표 (모두 self.files 에서만)
    def _config(self) -> dict:
        def read():
            with open(self.files["config"], encoding="utf-8") as f:
                return yaml.safe_load(f)

        return self._get("config", read)

    def holidays(self) -> list[pd.Timestamp]:
        return self._get("holidays", lambda: [pd.Timestamp(k) for k in self._config()["holidays"]])

    def holiday_names(self) -> dict[pd.Timestamp, str]:
        return self._get("holiday_names", lambda: {pd.Timestamp(k): v for k, v in self._config()["holidays"].items()})

    def card2(self) -> pd.DataFrame:
        return self._get("card2", lambda: _card2_from(self.files["card2"]))

    def card1(self) -> pd.DataFrame:
        """카드 데이터1 (보조, 시간대 4구간). 고객 거주지 칸이 없음. 데이터2와 합산하지 않음."""
        return self._get("card1", lambda: _card1_from(self.files["card1"]))

    def panel(self):
        """경보 패널 (EDA-6 과 같은 입력): 카드 데이터2, 같은 시도 거주, 본사등록 제외, 도시 × 연령 × 성."""
        from bigcontest.card import daily
        from bigcontest.signals.alarm import Panel

        def build():
            b = daily(self.card2(), ["city", "age_g", "SEX_CCD"])
            wide = b.pivot_table(index="date", columns=["city", "age_g", "SEX_CCD"], values="USE_CNT").astype(float)
            from bigcontest.rules.version import alarm_config

            return Panel(wide, self.holidays(), alarm_config())  # 규칙 판에 맞는 경보 기준 (rules.version)

        return self._get("panel", build)

    def card_observed(self) -> pd.DataFrame:
        """패널과 같은 모양의 bool 표: 원자료 행이 있던 (날짜, 코호트) = True.
        False 칸은 daily() 가 0건으로 채운 칸 (거래 0 인지 미공급인지 구분 못 함)."""

        def build():
            p = self.panel()
            seen = self.card2().groupby(["date", "city", "age_g", "SEX_CCD"]).size()
            seen = seen.unstack(["city", "age_g", "SEX_CCD"])
            cols = pd.MultiIndex.from_tuples(p.cols, names=["city", "age_g", "SEX_CCD"])
            return seen.reindex(index=p.dates, columns=cols).notna()

        return self._get("card_observed", build)

    def card_missing_days(self, col, start=None, end=None) -> int:
        """코호트 하나에서 [start, end] 안의 '행 없음 → 0건 가정' 일수."""
        m = self.card_observed()
        if col not in m.columns:
            return 0
        s = m[col]
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        if end is not None:
            s = s[s.index <= pd.Timestamp(end)]
        return int((~s).sum())

    def card2_visitors(self) -> pd.DataFrame:
        """카드 데이터2 가맹점 도시 × 일 × 거주 구분(local) 결제건수 (본사등록 업종 제외, 모든 연령).
        외지 손님 비중(같은 시도 밖 거주 고객) 계산용. 원천 행은 들고 있지 않고 합계만."""

        def build():
            d = pd.read_parquet(self.files["card2"], columns=["date", "city", "local", "USE_CNT"],
                                filters=[("hq", "==", False)])
            return d.groupby(["date", "city", "local"], observed=True)["USE_CNT"].sum().unstack("local").fillna(0.0)

        return self._get("card2_visitors", build)

    def card2_categories(self) -> pd.DataFrame:
        """카드 데이터2 (같은 시도 거주, 패널과 같은 거르기) 도시 × 연령 × 성 × 일 × 업종(MCT_RY_CD) 결제건수.
        업종 다양성(결제량 보정) 계산용."""

        def build():
            d = self.card2()
            return d.groupby(["city", "age_g", "SEX_CCD", "date", "MCT_RY_CD"], observed=True)["USE_CNT"].sum()

        return self._get("card2_categories", build)

    def heat(self) -> pd.DataFrame:
        """일 최고 체감온도 근사 (external/weather/kma_asos_heat_*.csv). 파일이 없는 스냅샷이면 빈 표."""
        if "asos_heat" not in self.files:
            return pd.DataFrame(columns=["city", "date", "tmax", "at_max", "at_max_hi"])
        return self._get("heat", lambda: pd.read_csv(self.files["asos_heat"], parse_dates=["date"]))

    def recipients(self) -> pd.DataFrame:
        """서울 동별 기초생활수급자 (강남만, adm_cd). 파일이 없는 스냅샷이면 빈 표."""
        from bigcontest.external import seoul_recipients

        if "welfare_recipients" not in self.files:
            return pd.DataFrame(columns=["adm_cd", "year", "households", "persons"])
        return self._get("recipients", lambda: seoul_recipients(self.files["welfare_recipients"], self.files["adm_names"]))

    def resident_pop(self) -> pd.DataFrame:
        """주민등록 성·연령별 인구 (행정동 × 월 × 성 × 1세 연령)."""
        from bigcontest.external import resident_population

        return self._get("resident_pop",
                         lambda: resident_population(self.files["resident_pop"], self.files["adm_code_map"]))

    def resident_single(self) -> pd.DataFrame:
        """주민등록 성·연령별 1인세대 (행정동 × 월 × 성 × 5세 구간). rules-v2.0 S축."""
        from bigcontest.external import resident_single_by_age

        return self._get("resident_single",
                         lambda: resident_single_by_age(self.files["resident_single"], self.files["adm_code_map"]))

    def v_inputs(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """rules-v2.0 V축 원자료: (강남 동 × 성 수급자, 춘천 읍면동 기초연금). vulnerability.v_axis(inputs=…)."""
        from bigcontest.vulnerability import chuncheon_basic_pension, gangnam_recipients

        return self._get("v_inputs", lambda: (
            gangnam_recipients(self.files["welfare_recipients_sex"], self.files["adm_names"]),
            chuncheon_basic_pension(self.files["chuncheon_pension"], self.files["adm_names"])))

    def weather(self) -> pd.DataFrame:
        return self._get("weather", lambda: pd.read_csv(self.files["asos"], parse_dates=["date"]))

    def rain(self) -> pd.DataFrame:
        return self._get("rain", lambda: self.weather().pivot_table(index="date", columns="city", values="rain"))

    def alarm_card(self, cutoff) -> pd.DataFrame:
        """기준일 카드: cutoff 까지의 결제·날씨만으로 다시 계산한 경보 목록 (alarm.records(as_of=...))."""
        from bigcontest.signals.alarm import records

        cutoff = pd.Timestamp(cutoff)
        return self._get(("alarm_card", cutoff), lambda: records(self.panel(), rain=self.rain(), as_of=cutoff))

    def households(self) -> pd.DataFrame:
        from bigcontest.external import resident_households

        return self._get("households",
                         lambda: resident_households(self.files["resident_hh"], self.files["adm_code_map"]))

    def seoul_living(self) -> pd.DataFrame:
        from bigcontest.external import seoul_living

        return self._get("seoul_living",
                         lambda: seoul_living(path=self.files["seoul_living"], map_path=self.files["adm_code_map"]))

    def tel(self, name: str) -> pd.DataFrame:
        return self._get(("tel", name), lambda: pd.read_parquet(self.files[name]))

    def ledger(self) -> pd.DataFrame:
        from bigcontest.resources.ledger import load

        return self._get("ledger", lambda: load(self.files["resources"]))

    def adm_names(self) -> pd.DataFrame:
        return self._get("adm_names", lambda: pd.read_csv(self.files["adm_names"], dtype=str))

    @cached_property
    def adm_index(self) -> dict[str, dict]:
        return {r["adm_cd"]: r for r in self.adm_names().to_dict("records")}

    def cached(self, key, fn):
        """도구가 계산한 중간 결과 (같은 스냅샷이면 같은 결과)."""
        return self._get(("tool", key), fn)


_SOURCES: dict[str, DataSource] = {}   # register_source 로 등록한 것 (지우지 않음)
_OPENED: OrderedDict[str, DataSource] = OrderedDict()  # open_datasource 가 연 dev 스냅샷 (최근 것만)
MAX_OPEN_SOURCES = 2  # dev 스냅샷 하나가 표를 다 읽으면 약 1GB. 최근에 쓴 것만 남김


def register_source(snapshot_id: str, source) -> None:
    """발행 스냅샷 같은 다른 읽기 구현을 등록 (DataSource 와 같은 메서드)."""
    with _LOCK:
        _OPENED.pop(snapshot_id, None)
        _SOURCES[snapshot_id] = source


def open_datasource(snapshot_id: str) -> DataSource:
    """고정한 snapshot_id 의 자료. dev 스냅샷이 아니면(발행 스냅샷 읽기는 아직 없음) DataUnavailable.
    dev 스냅샷은 최근에 쓴 MAX_OPEN_SOURCES 개만 들고 있는다. 밀려난 스냅샷을 다시 열면 새로 만든다
    (그때 파일이 바뀌었으면 SnapshotMismatch → 재실행 필요. 조용히 새 자료를 읽지 않음)."""
    with _LOCK:
        if snapshot_id in _SOURCES:
            return _SOURCES[snapshot_id]
        if snapshot_id in _OPENED:
            _OPENED.move_to_end(snapshot_id)
            return _OPENED[snapshot_id]
        if not snapshot_id.startswith("dev-"):
            raise DataUnavailable(f"읽을 수 없는 스냅샷: {snapshot_id} (지금은 dev 스냅샷만 연결됨)")
        ds = DataSource(snapshot_id)
        _OPENED[snapshot_id] = ds
        while len(_OPENED) > MAX_OPEN_SOURCES:
            _OPENED.popitem(last=False)
        return ds
