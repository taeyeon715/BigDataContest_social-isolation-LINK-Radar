"""스냅샷 발행 테스트. tmp_path 에서만 만든다."""

import json
import subprocess
import sys
from datetime import date

import duckdb
import pandas as pd
import pytest

from bigcontest.snapshot import (
    ACTIVE,
    DUCKDB_FILE,
    Snapshot,
    SnapshotError,
    active_id,
    list_snapshots,
    prune,
    publish,
)

PERIOD = dict(data_from=date(2025, 7, 1), data_to=date(2025, 12, 31))

READER = """
import sys, duckdb
con = duckdb.connect(sys.argv[1], read_only=True)
print(con.execute("select sum(n) from signal").fetchone()[0])
"""


def _builder(n: int):
    def build(folder):
        pd.DataFrame({"city": ["강남", "춘천"], "n": [n, n]}).to_parquet(folder / "candidates.parquet", index=False)
        with duckdb.connect(str(folder / DUCKDB_FILE)) as con:
            con.execute("create table signal as select * from (values (?), (?)) t(n)", [n, n])

    return build


def _read_other_process(path) -> str:
    out = subprocess.run([sys.executable, "-c", READER, str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_publish_and_pinned_read(tmp_path):
    m = publish(_builder(1), root=tmp_path, snapshot_id="snap-a", rule_version="rules-v0", **PERIOD)
    assert active_id(tmp_path) == "snap-a"
    assert m.row_counts["candidates.parquet"] == 2 and m.row_counts[f"{DUCKDB_FILE}:signal"] == 2
    assert m.previous_snapshot_id is None
    snap = Snapshot.open(root=tmp_path)
    assert snap.snapshot_id == "snap-a"
    assert snap.query("select sum(n) from signal")[0][0] == 2
    assert pd.read_parquet(snap.file("candidates.parquet"))["n"].sum() == 2


def test_reader_works_while_new_snapshot_is_built(tmp_path):
    publish(_builder(1), root=tmp_path, snapshot_id="snap-a", **PERIOD)
    pinned = Snapshot.open(root=tmp_path)  # 실행 시작 때 고정
    seen = {}

    def build_b(folder):
        _builder(5)(folder)
        # 새 스냅샷 DuckDB 에 쓰기 연결이 열린 상태에서
        with duckdb.connect(str(folder / DUCKDB_FILE)) as w:
            w.execute("insert into signal values (100)")
            # 다른 프로세스가 활성 스냅샷을 읽기 전용으로 열 수 있어야 함
            seen["other_process"] = _read_other_process(Snapshot.open(root=tmp_path).file(DUCKDB_FILE))
            seen["same_process"] = pinned.query("select sum(n) from signal")[0][0]
            seen["active_during_build"] = active_id(tmp_path)

    publish(build_b, root=tmp_path, snapshot_id="snap-b", **PERIOD)
    assert seen == {"other_process": "2", "same_process": 2, "active_during_build": "snap-a"}
    assert active_id(tmp_path) == "snap-b"
    # 고정한 읽기는 발행 뒤에도 이전 스냅샷 그대로 (이전 버전은 지우지 않음)
    assert pinned.query("select sum(n) from signal")[0][0] == 2
    assert Snapshot.open(root=tmp_path).query("select sum(n) from signal")[0][0] == 110
    assert json.loads((tmp_path / ACTIVE).read_text(encoding="utf-8"))["previous_snapshot_id"] == "snap-a"


@pytest.mark.parametrize("bad", ["raise", "empty", "validator"])
def test_failed_publish_keeps_previous(tmp_path, bad):
    publish(_builder(1), root=tmp_path, snapshot_id="snap-a", **PERIOD)

    def build(folder):
        if bad == "raise":
            _builder(2)(folder)
            raise RuntimeError("신호 계산 실패")
        if bad == "empty":
            pd.DataFrame({"n": []}).to_parquet(folder / "candidates.parquet", index=False)
        else:
            _builder(2)(folder)

    def validator(folder, manifest):
        if bad == "validator":
            raise SnapshotError("결합 동 수 불일치")

    with pytest.raises(SnapshotError):
        publish(build, root=tmp_path, snapshot_id="snap-b", validate=validator, **PERIOD)
    assert active_id(tmp_path) == "snap-a"
    assert list_snapshots(tmp_path) == ["snap-a"]
    assert {p.name for p in tmp_path.iterdir()} == {ACTIVE, "snap-a"}  # 만들던 폴더도 남지 않음
    assert Snapshot.open(root=tmp_path).query("select sum(n) from signal")[0][0] == 2


def test_snapshot_is_immutable_and_prune_protects(tmp_path):
    for i, sid in enumerate(["snap-a", "snap-b", "snap-c", "snap-d"]):
        publish(_builder(i + 1), root=tmp_path, snapshot_id=sid, **PERIOD)
    with pytest.raises(SnapshotError):
        publish(_builder(9), root=tmp_path, snapshot_id="snap-b", **PERIOD)
    removed = prune(tmp_path, keep=1, pinned={"snap-b"})
    assert removed == ["snap-a", "snap-c"]
    assert list_snapshots(tmp_path) == ["snap-b", "snap-d"]
    assert active_id(tmp_path) == "snap-d"
    with pytest.raises(SnapshotError):
        Snapshot.open("snap-a", root=tmp_path)


def test_no_snapshot_yet(tmp_path):
    assert active_id(tmp_path) is None
    with pytest.raises(SnapshotError):
        Snapshot.open(root=tmp_path)


# ------------------------------------------------------------ 실행 메타데이터


def test_run_metadata_idempotent(tmp_path):
    from bigcontest.runmeta import RunMetadataConflict, collect, read_run_metadata, write_run_metadata

    raw = tmp_path / "raw.csv"
    raw.write_text("a,b\n1,2\n", encoding="utf-8")
    kw = dict(
        run_id="run-001",
        snapshot_id="snap-a",
        analysis_mode="simulated_replay",
        as_of=date(2025, 9, 26),
        raw_files=[raw],
        thresholds={"drop_pct": 0.15, "K": 10},
        rule_version="rules-v0",
        ledger="ledger@sha256:test",
    )
    m1 = collect(**kw)
    assert m1.raw_files["raw.csv"] and m1.cleaning_rule_version and m1.contract_versions["grain"]
    saved, created = write_run_metadata(m1, tmp_path / "runs")
    assert created
    # 같은 실행을 다시 저장(재개·재시도) → 기록 1건, 처음 것 그대로
    again, created = write_run_metadata(collect(**kw), tmp_path / "runs")
    assert not created and again.created_at == m1.created_at
    assert len(list((tmp_path / "runs").iterdir())) == 1
    # 같은 run_id 에 다른 내용 → 충돌
    with pytest.raises(RunMetadataConflict):
        write_run_metadata(collect(**{**kw, "thresholds": {"drop_pct": 0.2}}), tmp_path / "runs")
    assert read_run_metadata("run-001", tmp_path / "runs").thresholds["drop_pct"] == 0.15
