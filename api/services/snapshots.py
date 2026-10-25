"""활성 스냅샷 (가짜). 진짜는 배치가 manifest를 바꿔 발행하고 API는 읽기만 한다."""

from __future__ import annotations

from bigcontest.agent.schemas import SnapshotInfo

from api.errors import ApiError
from api.services.fake_data import DEFAULT_ACTIVE_SNAPSHOT, FAKE_SNAPSHOTS


class SnapshotService:
    def __init__(self, active: str | None = DEFAULT_ACTIVE_SNAPSHOT):
        self._active = active

    @property
    def active_id(self) -> str | None:
        return self._active

    def active(self) -> SnapshotInfo:
        if self._active is None:
            raise ApiError(503, "NO_ACTIVE_SNAPSHOT", "발행된 분석 스냅샷이 없습니다.")
        return FAKE_SNAPSHOTS[self._active]

    def get(self, snapshot_id: str) -> SnapshotInfo:
        if snapshot_id not in FAKE_SNAPSHOTS:
            raise ApiError(404, "SNAPSHOT_NOT_FOUND", "없는 스냅샷입니다.")
        return FAKE_SNAPSHOTS[snapshot_id]

    def publish(self, snapshot: SnapshotInfo) -> None:
        """테스트용: 새 스냅샷 발행을 흉내 냄. 진행 중인 실행은 자기 snapshot_id를 계속 씀."""
        FAKE_SNAPSHOTS[snapshot.snapshot_id] = snapshot
        self._active = snapshot.snapshot_id

    def unpublish_all(self) -> None:
        """테스트용: 활성 스냅샷이 없는 상태."""
        self._active = None


class AgentSnapshotService(SnapshotService):
    """실제 그래프의 활성 스냅샷.

    - 도구 real: 지금 dev 스냅샷 (data/interim·external·ref 파일 해시, tools.datasource). 조회할 때마다 다시 계산
      (해시는 파일 시각·크기로 캐시). 파일이 바뀌면 새 실행부터 새 id. 이미 시작한 실행은 자기 id 를 계속 쓰고,
      그 사이 파일이 바뀌었으면 도구가 SNAPSHOT_MISMATCH 로 멈춘다 (조용히 새 자료를 읽지 않음)
    - 도구 stub: 가짜 도구용 고정 id (state.STUB_SNAPSHOT_ID)
    - publish: 테스트용으로 활성 id 를 바꿈 (진행 중 실행은 바뀌지 않는지 확인)
    """

    def __init__(self, tools_backend: str = "real"):
        super().__init__(None)
        self.tools_backend = tools_backend
        self._known: dict[str, SnapshotInfo] = {}
        self._override: str | None = None
        self._off = False

    def _current(self) -> str | None:
        if self._off:
            return None
        if self._override:
            return self._override
        from bigcontest.agent.state import default_snapshot_id

        sid = default_snapshot_id(self.tools_backend)
        return None if sid == "dev-unavailable" else sid

    @property
    def active_id(self) -> str | None:
        return self._current()

    def _info(self, sid: str) -> SnapshotInfo:
        if sid not in self._known:
            from datetime import datetime, timedelta, timezone

            from bigcontest.agent.schemas import DATA_END, DATA_START
            from bigcontest.agent.tools.common import rule_version
            from bigcontest.ingest import CLEANING_RULE_VERSION
            from bigcontest.runmeta import ledger_version

            self._known[sid] = SnapshotInfo(
                snapshot_id=sid, created_at=datetime.now(timezone(timedelta(hours=9))), data_from=DATA_START,
                data_to=DATA_END, cleaning_rule_version=CLEANING_RULE_VERSION, rule_version=rule_version(),
                ledger_version=ledger_version(), previous_snapshot_id=None)
        return self._known[sid]

    def active(self) -> SnapshotInfo:
        sid = self._current()
        if sid is None:
            raise ApiError(503, "NO_ACTIVE_SNAPSHOT", "발행된 분석 스냅샷이 없습니다.")
        return self._info(sid)

    def get(self, snapshot_id: str) -> SnapshotInfo:
        if snapshot_id not in self._known:
            raise ApiError(404, "SNAPSHOT_NOT_FOUND", "없는 스냅샷입니다.")
        return self._known[snapshot_id]

    def publish(self, snapshot: SnapshotInfo) -> None:
        self._known[snapshot.snapshot_id] = snapshot
        self._override = snapshot.snapshot_id

    def unpublish_all(self) -> None:
        self._off = True
