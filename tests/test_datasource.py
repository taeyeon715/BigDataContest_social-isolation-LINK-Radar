"""DataSource 고정 파일 읽기·메타데이터 해시·카드 0건 가정 표시.

두 임시 root 에 서로 다른 작은 자료를 만들고 (실제 dev 자료의 일부를 잘라 씀), 각 DataSource 가 자기 파일만
읽는지 본다. 자료가 없으면 건너뜀.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from bigcontest.agent.tools.datasource import (
    DEV_FILES,
    DataSource,
    SnapshotMismatch,
    _card2_from,
    dev_available,
    dev_files,
    dev_snapshot_id,
    files_snapshot_id,
    register_source,
)
from bigcontest.config import ROOT

pytestmark = pytest.mark.skipif(not dev_available(), reason="dev 스냅샷 자료(data/interim 등) 없음")

REAL = dev_files(ROOT)
DROP = (pd.Timestamp("2025-10-01"), "강남", "80대+", None)  # B 에서 뺄 코호트-일 (성은 아래에서 채움)


def _lines(src: Path, dst: Path, n: int | None) -> None:
    text = src.read_text(encoding="utf-8").splitlines(keepends=True)
    dst.write_text("".join(text if n is None else text[: n + 1]), encoding="utf-8")


def make_root(root: Path, city: str, holidays: dict, n_rows: int, drop_one: bool = False) -> Path:
    for k, rel in DEV_FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
    c2 = pd.read_parquet(REAL["card2"])
    c2 = c2[c2["city"] == city]
    if drop_one:
        sex = sorted(c2["SEX_CCD"].unique())[0]
        c2 = c2[~((c2["date"] == DROP[0]) & c2["age"].isin(["80대", "90대"]) & (c2["SEX_CCD"] == sex))]
    c2.to_parquet(root / DEV_FILES["card2"])
    c1 = pd.read_parquet(REAL["card1"])
    c1[c1["city"] == city].head(n_rows * 50).to_parquet(root / DEV_FILES["card1"])
    for t in ("tel_age", "tel_time", "tel_wkdy"):
        pd.read_parquet(REAL[t]).head(n_rows).to_parquet(root / DEV_FILES[t])
    for k in ("resident_pop", "resident_hh", "seoul_living", "asos"):
        _lines(REAL[k], root / DEV_FILES[k], n_rows)
    led = pd.read_csv(REAL["resources"], dtype=str, keep_default_na=False).head(n_rows)
    led.to_csv(root / DEV_FILES["resources"], index=False, encoding="utf-8")
    for k in ("adm_names", "adm_code_map", "asos_heat", "welfare_recipients"):  # rules-v1.1 자료는 통째로
        _lines(REAL[k], root / DEV_FILES[k], None)
    cfg = yaml.safe_load(REAL["config"].read_text(encoding="utf-8"))
    cfg["holidays"] = holidays
    (root / DEV_FILES["config"]).write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return root


def open_root(root: Path) -> DataSource:
    files = dev_files(root)
    return DataSource(files_snapshot_id(files), root=root)


@pytest.fixture(scope="module")
def roots(tmp_path_factory):
    a = make_root(tmp_path_factory.mktemp("snapA"), "춘천", {"2025-08-15": "광복절"}, 40)
    b = make_root(tmp_path_factory.mktemp("snapB"), "강남", {"2025-10-03": "개천절", "2025-10-06": "추석"}, 60,
                  drop_one=True)
    return a, b


def test_two_roots_are_fully_separate(roots):
    a, b = (open_root(r) for r in roots)
    assert a.snapshot_id != b.snapshot_id
    assert set(a.hashes) == set(b.hashes) == set(DEV_FILES)
    assert {k for k in DEV_FILES if a.hashes[k] != b.hashes[k]} >= {
        "card2", "tel_age", "resident_hh", "seoul_living", "asos", "resources", "config"}
    assert a.holidays() == [pd.Timestamp("2025-08-15")]
    assert b.holiday_names() == {pd.Timestamp("2025-10-03"): "개천절", pd.Timestamp("2025-10-06"): "추석"}
    assert set(a.card2()["city"]) == {"춘천"} and set(b.card2()["city"]) == {"강남"}
    assert {c[0] for c in a.panel().cols} == {"춘천"} and {c[0] for c in b.panel().cols} == {"강남"}
    assert len(a.weather()) == 40 and len(b.weather()) == 60
    assert len(a.tel("tel_age")) == 40 and len(b.tel("tel_age")) == 60
    assert len(a.ledger()) == 40 and len(b.ledger()) == 60
    assert a.households()["adm_cd"].nunique() <= 40 and len(b.households()) == 60
    assert len(b.seoul_living()) == 60
    # 두 객체의 표가 서로 섞이지 않음 (같은 객체를 다시 읽어도 같은 값)
    assert len(a.card2()) != len(b.card2()) and a.holidays() != b.holidays()


def test_pinned_source_ignores_global_repo_paths(roots, monkeypatch, tmp_path):
    """전역 레포 경로(config.yaml·interim·external)를 다른 곳으로 바꿔도 지정 root 의 결과는 같다."""
    ds = open_root(roots[0])
    import bigcontest.card as card
    import bigcontest.config as config
    import bigcontest.external as ext
    import bigcontest.weather as weather

    def boom():
        raise AssertionError("전역 config 를 읽음")

    monkeypatch.setattr(config, "load_config", boom)
    monkeypatch.setattr(card, "load_config", boom)
    monkeypatch.setattr(ext, "EXT", tmp_path / "nowhere")
    monkeypatch.setattr(ext, "CODE_MAP", tmp_path / "nowhere.csv")
    monkeypatch.setattr(ext, "SL_FILE", tmp_path / "nowhere_sl.csv")
    monkeypatch.setattr(weather, "CSV", tmp_path / "nowhere_w.csv")
    assert ds.holidays() == [pd.Timestamp("2025-08-15")]
    assert set(ds.card2()["city"]) == {"춘천"}
    assert len(ds.weather()) == 40 and len(ds.households()) > 0 and len(ds.seoul_living()) > 0
    assert len(ds.ledger()) == 40 and len(ds.adm_names()) > 0


def test_real_root_loaders_match_repo_loaders():
    """지정 파일 로더가 기존 전역 로더와 같은 표를 만든다 (기본 root)."""
    from bigcontest.card import load_card2
    from bigcontest.config import load_config
    from bigcontest.external import resident_households, seoul_living
    from bigcontest.weather import load as load_weather

    ds = DataSource(dev_snapshot_id(), root=ROOT)
    pd.testing.assert_frame_equal(_card2_from(ds.files["card2"]), load_card2())
    pd.testing.assert_frame_equal(ds.households(), resident_households())
    pd.testing.assert_frame_equal(ds.seoul_living(), seoul_living())
    pd.testing.assert_frame_equal(ds.weather(), load_weather())
    assert ds.holidays() == [pd.Timestamp(k) for k in load_config()["holidays"]]


# ---------------------------------------------------------------- 메타데이터 해시


def test_metadata_uses_captured_hashes_and_reports_change(tmp_path):
    from bigcontest.runmeta import RunMetadataConflict, write_run_metadata

    root = make_root(tmp_path / "snap", "춘천", {"2025-08-15": "광복절"}, 20)
    ds = open_root(root)
    meta0 = ds.run_metadata("r-ok", "simulated_replay", date(2025, 10, 15))
    assert meta0.snapshot_check == "일치"
    assert meta0.raw_files == {ds.files[k].name: ds.hashes[k] for k in ds.files}
    before = dict(ds.hashes)
    led = ds.ledger()  # 캐시
    write_run_metadata(meta0, tmp_path / "runs")

    path = ds.files["resources"]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # 실행 중 파일 변경
    assert ds.ledger() is led  # 이미 읽은 표는 실행 시작 때 자료 그대로
    meta = ds.run_metadata("r-ok", "simulated_replay", date(2025, 10, 15))
    assert meta.raw_files[path.name] == before["resources"]  # 지금 파일을 다시 해시해 섞지 않음
    assert meta.ledger_version == f"ledger@sha256:{before['resources'][:12]}"
    assert meta.snapshot_check.startswith("불일치") and "재실행 필요" in meta.snapshot_check
    assert "resources" in meta.snapshot_check
    with pytest.raises(RunMetadataConflict):  # 같은 실행을 불일치 상태로 조용히 덮어쓰지 않음
        write_run_metadata(meta, tmp_path / "runs")
    with pytest.raises(SnapshotMismatch, match="재실행 필요"):
        ds.run_metadata("r-x", "simulated_replay", date(2025, 10, 15), on_change="raise")
    with pytest.raises(SnapshotMismatch, match="재실행 필요"):
        ds.verify()
    with pytest.raises(SnapshotMismatch):
        ds.weather()  # 처음 읽는 표는 바뀐 파일을 읽지 않음
    with pytest.raises(SnapshotMismatch, match="재실행 필요"):
        DataSource(ds.snapshot_id, root=root)  # 재시작 뒤 같은 id 재개 불가


# ---------------------------------------------------------------- 카드 0건 가정 표시


def test_card_missing_cohort_day_is_counted_and_warned(roots):
    from bigcontest.agent.schemas import NormalizedRequest
    from bigcontest.agent.tools import data as D
    from bigcontest.agent.tools.stubs import ToolContext

    b = open_root(roots[1])
    obs = b.card_observed()
    missing = [(d, c) for c in obs.columns for d in obs.index[~obs[c]]]
    assert len(missing) == 1 and missing[0][0] == DROP[0] and missing[0][1][:2] == ("강남", "80대+")
    col = missing[0][1]
    assert float(b.panel().Y.loc[DROP[0], col]) == 0.0  # daily() 는 0 으로 채움 (card.py 그대로)
    assert b.card_missing_days(col, "2025-09-25", "2025-10-05") == 1
    register_source(b.snapshot_id, b)
    as_of = date(2025, 10, 6)
    req = NormalizedRequest(city="강남", metric="카드결제", period_to=as_of, age_min=80, sex=col[2][0])
    ctx = ToolContext(scenario="normal", as_of=as_of, analysis_mode="simulated_replay", snapshot_id=b.snapshot_id,
                      request=req, backend="real")
    ser = [r for r in D.get_card_cohort_series(ctx) if r.metric == D.CARD_METRIC_SERIES and r.status == "OK"]
    assert ser and any("행 없음=0건 가정" in w for w in ser[0].warnings)
    assert any("0건으로 채운 날 1일" in w for w in ser[0].warnings)


def test_real_card_has_no_missing_cohort_days():
    """현재 dev 카드 패널(도시×연령×성)에는 원자료 행이 없는 코호트-일이 없음."""
    ds = DataSource(dev_snapshot_id(), root=ROOT)
    assert int((~ds.card_observed()).to_numpy().sum()) == 0
