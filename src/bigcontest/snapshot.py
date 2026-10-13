"""분석 스냅샷 발행과 고정 읽기 (아키텍처 §4.1).

DuckDB 는 쓰기 중인 파일을 다른 프로세스가 읽기 전용으로도 못 연다. 그래서
1. 배치는 새 폴더(data/snapshots/.building-<id>/)에 Parquet·DuckDB 를 모두 만들고
2. 검증(파일·행 수·manifest)을 통과하면 폴더 이름을 <id>/ 로 바꾼 뒤
3. data/snapshots/active.json 만 원자적으로 바꾼다 (임시 파일 → os.replace).
   (발행이 한 번에 하나일 때 기준. 잠금이 없어 동시에 발행하면 previous_snapshot_id가 틀릴 수 있다.
    Windows에서 다른 프로세스가 active.json을 열고 있으면 os.replace가 실패할 수 있다.)
실패하면 만들던 폴더만 지우고 active.json 은 그대로 둔다. 이전 버전은 바로 지우지 않는다.

읽는 쪽(API·에이전트)은 실행을 시작할 때 Snapshot.open() 으로 snapshot_id 를 고정하고,
승인·재개까지 같은 폴더를 읽는다.

snapshot_id 는 폴더 이름이 되므로 경로에 쓰기 전에 모양을 검사한다 (`check_snapshot_id`).
`Snapshot.open(..., verify=True)` 는 manifest 의 sha256 과 파일을 다시 비교한다 (실수 방지용, 변조 방지 아님).
"""

import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
from pydantic import BaseModel, Field

from bigcontest.config import ROOT
from bigcontest.ingest import CLEANING_RULE_VERSION

SNAPSHOT_ROOT = ROOT / "data" / "snapshots"
ACTIVE = "active.json"
MANIFEST = "manifest.json"
DUCKDB_FILE = "analysis.duckdb"
BUILDING_PREFIX = ".building-"


class SnapshotError(RuntimeError):
    pass


# 발행 id(snap-YYYYMMDD-HHMMSS-xxxx), dev id(dev-<해시>), 테스트·가짜 id 모두 이 모양 안. 점·슬래시는 못 씀.
SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def check_snapshot_id(snapshot_id: str) -> str:
    """경로에 쓰기 전에 snapshot_id 모양 검사 (`../` 같은 값 차단)."""
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise SnapshotError(f"스냅샷 ID 형식이 맞지 않음: {snapshot_id!r}")
    return snapshot_id


class Manifest(BaseModel):
    snapshot_id: str
    created_at: datetime
    data_from: date
    data_to: date
    cleaning_rule_version: str
    rule_version: str | None = None
    ledger_version: str | None = None
    previous_snapshot_id: str | None = None
    files: dict[str, str] = Field(default_factory=dict)  # 상대 경로 → sha256
    row_counts: dict[str, int] = Field(default_factory=dict)  # 파일 또는 duckdb 표 → 행 수


def new_snapshot_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"snap-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan(folder: Path) -> tuple[dict[str, str], dict[str, int]]:
    files, rows = {}, {}
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.name == MANIFEST or p.suffix == ".wal":
            continue
        rel = p.relative_to(folder).as_posix()
        files[rel] = _sha256(p)
        if p.suffix == ".parquet":
            rows[rel] = pq.read_metadata(p).num_rows
        elif p.suffix == ".duckdb":
            with duckdb.connect(str(p), read_only=True) as con:
                for (t,) in con.execute("select table_name from information_schema.tables").fetchall():
                    rows[f"{rel}:{t}"] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
    return files, rows


def default_validate(folder: Path, manifest: Manifest) -> None:
    """기본 검증: 파일이 하나 이상, 모든 표의 행이 1개 이상, WAL 이 남지 않음(쓰기 연결이 닫힘)."""
    if not manifest.files:
        raise SnapshotError("스냅샷에 파일이 없음")
    empty = [k for k, n in manifest.row_counts.items() if n == 0]
    if empty:
        raise SnapshotError(f"빈 표: {empty}")
    if list(folder.rglob("*.wal")):
        raise SnapshotError("DuckDB 쓰기 연결이 닫히지 않음 (.wal 남음)")


def active_id(root: Path = SNAPSHOT_ROOT) -> str | None:
    p = Path(root) / ACTIVE
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))["snapshot_id"]


def read_manifest(snapshot_id: str, root: Path = SNAPSHOT_ROOT) -> Manifest:
    p = Path(root) / check_snapshot_id(snapshot_id) / MANIFEST
    if not p.exists():
        raise SnapshotError(f"없는 스냅샷: {snapshot_id}")
    return Manifest.model_validate_json(p.read_text(encoding="utf-8"))


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def publish(
    build: Callable[[Path], None],
    *,
    data_from: date,
    data_to: date,
    root: Path = SNAPSHOT_ROOT,
    snapshot_id: str | None = None,
    rule_version: str | None = None,
    ledger_version: str | None = None,
    cleaning_rule_version: str = CLEANING_RULE_VERSION,
    validate: Callable[[Path, Manifest], None] | None = None,
) -> Manifest:
    """build(폴더)로 새 스냅샷을 만들고 검증한 뒤 활성 스냅샷으로 바꾼다.

    build 는 받은 폴더 안에만 쓰고, DuckDB 연결은 닫고 끝내야 한다.
    실패하면 SnapshotError, 이전 활성 스냅샷은 그대로.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sid = check_snapshot_id(snapshot_id or new_snapshot_id())
    final = root / sid
    if final.exists():
        raise SnapshotError(f"이미 있는 스냅샷: {sid} (스냅샷은 덮어쓰지 않음)")
    work = root / f"{BUILDING_PREFIX}{sid}"
    work.mkdir()
    previous = active_id(root)
    try:
        build(work)
        files, rows = _scan(work)
        manifest = Manifest(
            snapshot_id=sid,
            created_at=datetime.now(),
            data_from=data_from,
            data_to=data_to,
            cleaning_rule_version=cleaning_rule_version,
            rule_version=rule_version,
            ledger_version=ledger_version,
            previous_snapshot_id=previous,
            files=files,
            row_counts=rows,
        )
        default_validate(work, manifest)
        if validate:
            validate(work, manifest)
        (work / MANIFEST).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.rename(work, final)
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        if isinstance(e, SnapshotError):
            raise
        raise SnapshotError(f"스냅샷 만들기 실패: {e}") from e
    _write_atomic(
        root / ACTIVE,
        json.dumps(
            {"snapshot_id": sid, "published_at": datetime.now().isoformat(), "previous_snapshot_id": previous},
            ensure_ascii=False,
            indent=2,
        ),
    )
    return manifest


class Snapshot:
    """고정한 스냅샷 하나를 읽는다. 열 때 snapshot_id 를 정하면 그 뒤 발행과 무관하다."""

    def __init__(self, snapshot_id: str, root: Path = SNAPSHOT_ROOT):
        self.root = Path(root)
        self.snapshot_id = check_snapshot_id(snapshot_id)
        self.path = self.root / snapshot_id
        self.manifest = read_manifest(snapshot_id, self.root)

    @classmethod
    def open(cls, snapshot_id: str | None = None, root: Path = SNAPSHOT_ROOT, verify: bool = False) -> "Snapshot":
        sid = snapshot_id or active_id(root)
        if sid is None:
            raise SnapshotError("발행된 스냅샷이 없음")
        snap = cls(sid, root)
        if verify:
            snap.verify()
        return snap

    def verify(self) -> None:
        """manifest 의 sha256 과 지금 파일을 비교. 다르거나 없으면 SnapshotError."""
        bad = []
        for rel, digest in self.manifest.files.items():
            p = self.path / rel
            if not p.is_file() or _sha256(p) != digest:
                bad.append(rel)
        if bad:
            raise SnapshotError(f"{self.snapshot_id}: manifest 와 다른 파일 {bad}")

    def file(self, name: str) -> Path:
        if name not in self.manifest.files:
            raise SnapshotError(f"{self.snapshot_id} 에 없는 파일: {name}")
        return self.path / name

    def connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.file(DUCKDB_FILE)), read_only=True)

    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        with self.connect() as con:
            return con.execute(sql, params or []).fetchall()


def list_snapshots(root: Path = SNAPSHOT_ROOT) -> list[str]:
    root = Path(root)
    if not root.exists():
        return []
    ids = [p.name for p in root.iterdir() if p.is_dir() and (p / MANIFEST).exists()]
    return sorted(ids, key=lambda s: (read_manifest(s, root).created_at, s))  # 오래된 것부터


def prune(root: Path = SNAPSHOT_ROOT, keep: int = 3, pinned: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """오래된 스냅샷 정리. 활성·고정(실행 중·승인 대기) 스냅샷과 최근 keep 개는 지우지 않는다."""
    ids = list_snapshots(root)
    protect = set(pinned) | set(ids[-keep:] if keep else [])
    act = active_id(root)
    if act:
        protect.add(act)
    removed = []
    for sid in ids:
        if sid not in protect:
            shutil.rmtree(Path(root) / check_snapshot_id(sid))
            removed.append(sid)
    return removed
