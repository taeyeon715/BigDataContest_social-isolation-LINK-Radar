"""⑥ 파이프라인 연결 (어댑터만, 기존 코드 수정 없음).

`SimDataSource` 는 `agent.tools.datasource.DataSource` 와 같은 메서드를 가진다.
- 카드·통신 표(card2·card1·card2_visitors·tel_*)는 LINK-Sim 합성 표
- 공개 자료(주민등록·날씨·공휴일·자원 원장·동 코드·수급자·시민생활)는 레포의 공개 파일 그대로
- snapshot_id = "sim-" + (공개 파일 해시 + 시뮬레이터 설정·보정 목표 해시 + 주입 변형) 앞 12자리
- data_version 은 합성 표에 "<원천>@sim-…/synthetic" 이 붙는다 (근거 봉투에서 합성임이 드러남)

`register(ds)` 로 등록하면 기존 도구·규칙·그래프가 `snapshot_id` 로 이 자료를 읽는다 (r1 페르소나 주입과 같은 방식).
원본(data/interim) 없이도 돈다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import pandas as pd

from bigcontest.agent.tools.datasource import DEV_FILES, DataSource, _hashes_of, register_source
from bigcontest.config import ROOT
from bigcontest.sim import aggregate as AG
from bigcontest.sim import params as P
from bigcontest.sim.calibrate import TARGETS_FILE
from bigcontest.sim.world import World, build_world

SYNTHETIC_SOURCES = ("card2", "card1", "tel_age", "tel_time", "tel_wkdy")
PUBLIC_SOURCES = {k: v for k, v in DEV_FILES.items() if k not in SYNTHETIC_SOURCES}


def _digest(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
    return h.hexdigest()


class SimDataSource(DataSource):
    """LINK-Sim 합성 자료를 DataSource 처럼 읽게 한다. 모든 결과는 합성(synthetic)이다."""

    data_source = "synthetic"

    def __init__(self, world: World | None = None, counts: np.ndarray | None = None, variant: str = "base",
                 holidays_override: dict | None = None, root: Path = ROOT, **world_kw):
        self.root = Path(root)
        self.files = {k: self.root / v for k, v in PUBLIC_SOURCES.items()}
        self.hashes = _hashes_of(self.files)
        self.world = world if world is not None else build_world(**world_kw)
        self._counts = counts
        self._holidays_override = holidays_override
        w = self.world
        sim_key = _digest(P.SIM_VERSION, w.seed, len(w.people), TARGETS_FILE.read_bytes(),
                          json.dumps(w.noise_params, sort_keys=True, default=str), variant,
                          b"" if counts is None else np.ascontiguousarray(counts).tobytes(),
                          json.dumps(holidays_override or {}, sort_keys=True, default=str),
                          *(f"{k}:{self.hashes[k]}" for k in sorted(self.hashes)))
        self.sim_hash = sim_key
        self.variant = variant
        self.snapshot_id = "sim-" + sim_key[:12]
        self._cache = {}
        self._lock = threading.RLock()

    # ------------------------------------------------ 버전 (합성 표는 합성 표시)
    def data_version(self, source: str) -> str:
        if source in SYNTHETIC_SOURCES:
            return f"{source}@{self.snapshot_id}/synthetic"
        return super().data_version(source)

    def counts(self) -> np.ndarray:
        return self._get("counts", lambda: self.world.counts() if self._counts is None else self._counts)

    # ------------------------------------------------ 공휴일 (스트레스 시험용 가상 공휴일을 더할 수 있음)
    def holidays(self) -> list[pd.Timestamp]:
        return self._get("holidays", lambda: sorted(self.holiday_names()))

    def holiday_names(self) -> dict[pd.Timestamp, str]:
        def read():
            base = {pd.Timestamp(k): v for k, v in self._config()["holidays"].items()}
            base.update({pd.Timestamp(k): v for k, v in (self._holidays_override or {}).items()})
            return base

        return self._get("holiday_names", read)

    # ------------------------------------------------ 합성 표
    def card2(self) -> pd.DataFrame:
        return self._get("card2", lambda: AG.card2_rows(self.world, self.counts()))

    def card1(self) -> pd.DataFrame:
        return self._get("card1", lambda: AG.card1_rows(self.world, self.counts()))

    def card2_visitors(self) -> pd.DataFrame:
        return self._get("card2_visitors", lambda: AG.visitors(self.world, self.counts()))

    def tel(self, name: str) -> pd.DataFrame:
        frames = self._get("tel_all", lambda: AG.tel_frames(self.world))
        return frames[name]


def register(ds: SimDataSource) -> str:
    register_source(ds.snapshot_id, ds)
    return ds.snapshot_id
