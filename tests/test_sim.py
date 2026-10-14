"""LINK-Sim (합성 자료): 결정적 시드, 집계 단위, 보정 허용 범위, 파이프라인 어댑터. 원본(data/interim) 없이 돈다."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bigcontest.sim import inject as I
from bigcontest.sim import params as P
from bigcontest.sim.adapter import PUBLIC_SOURCES, SYNTHETIC_SOURCES, SimDataSource, register
from bigcontest.sim.calibrate import fit_effects, load_targets, rule_stats
from bigcontest.sim.world import COHORTS, build_world
from bigcontest.telecom import AGE_COLS, KEY, TIME_COLS, WKDY_COLS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def world():
    return build_world()


@pytest.fixture(scope="module")
def sim(world):
    ds = SimDataSource(world)
    register(ds)
    return ds


def test_personas_valid():
    P.check_personas()
    assert len(P.PERSONAS) == 7
    assert {p.name for p in P.PERSONAS.values()} >= {"고립 진행형", "연휴 단절형", "계절 수축형", "건강 악화형", "정상 변동형",
                                                    "은둔 청년", "이사 직후형"}
    assert not P.PERSONAS["S6"].detectable


def test_deterministic_seed(world):
    w2 = build_world(noise_params=world.noise_params)
    assert w2.people.equals(world.people)
    assert np.array_equal(w2.states, world.states)
    assert np.array_equal(w2.counts(), world.counts())
    w3 = build_world(seed=1, calibrate=False)
    assert not np.array_equal(w3.states[:100], world.states[:100]) or len(w3.people) != len(world.people)


def test_ipf_matches_margins(world):
    info = world.pop_info["ipf"]
    assert info["max_rel_error_cell"] < 1e-6
    cells = world.pop_info["cells"]
    hh = world.pop_info["hh1"]
    ipf_one = cells[cells["alone"]].groupby("adm_cd")["ipf_n"].sum()
    assert np.allclose(ipf_one.reindex(hh.index), hh, rtol=1e-6)
    # 축소 표본: 도시 인구 합은 1% 안
    assert abs(world.people.shape[0] * P.SCALE / info["pop_10plus"] - 1) < 0.01
    assert set(world.people.columns) >= {"person_id", "adm_cd", "sex", "band", "alone", "persona"}
    assert "age" not in world.people.columns  # 1세 나이는 만들지 않음


def test_card_grain(sim):
    c2 = sim.card2()
    assert set(["date", "city", "age_g", "SEX_CCD", "MCT_RY_CD", "cat", "USE_CNT", "local", "hq"]) <= set(c2.columns)
    assert not c2.duplicated(["date", "city", "age_g", "SEX_CCD", "MCT_RY_CD"]).any()
    assert c2["date"].nunique() == 184 and c2["date"].min() == pd.Timestamp("2025-07-01")
    assert set(c2["cat"]) <= set(P.CATS) and (c2["USE_CNT"] > 0).all()
    assert (c2["data_source"] == "synthetic").all()
    p = sim.panel()
    assert p.Y.shape == (184, 28) and list(p.cols) == COHORTS
    c1 = sim.card1()
    assert set(c1["TIME_GB"]) == set(P.TIME_BANDS)
    v = sim.card2_visitors()
    assert list(v.columns) == [False, True] and len(v) == 184 * 2


def test_tel_grain(sim):
    for name, cols in (("tel_age", AGE_COLS), ("tel_time", TIME_COLS), ("tel_wkdy", WKDY_COLS)):
        t = sim.tel(name)
        assert list(t.columns) == [*KEY, *cols, "city", "adm_cd", "oa13", "adm_type"], name
        assert sorted(t["STD_YM"].unique()) == [f"2025{m:02d}" for m in range(7, 13)]
        assert t.groupby("city")["adm_cd"].nunique().to_dict() == {"강남": 22, "춘천": 25}
        assert (t["BLOCK_CD"].str[:8] == t["adm_cd"]).all() and (t["BLOCK_CD"].str[8] == "9").all()  # 가상 칸 표시
        assert not t.duplicated(["STD_YM", "BLOCK_CD"]).any()


def test_calibration_within_tolerance(world):
    tg = load_targets()
    Y = world.panel_frame()
    rs = rule_stats(Y, world.cal.holidays)
    eff = fit_effects(Y, world.cal)
    for c in COHORTS:
        k = "|".join(c)
        assert 0.8 <= rs["cohort"][k]["local_mad"] / tg["card"][k]["local_mad"] <= 1.25, k
        assert abs(rs["cohort"][k]["local_lag1"] - tg["card"][k]["local_lag1"]) <= 0.15, k  # 리포트 기준 0.1 (3개 코호트 미달 보고)
        assert np.corrcoef(eff[c]["dow"], tg["card"][k]["dow"])[0, 1] >= 0.9, k
        assert abs(eff[c]["effects"]["hol"] - tg["card"][k]["effects"]["hol"]) <= 0.05, k
    for c in P.CITIES:
        assert 0.8 <= rs["city"][c]["shock_mad"] / tg["city_noise"][c]["shock_mad"] <= 1.25


def test_targets_have_no_raw_values():
    tg = load_targets()
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                keys.add(str(k).lower())
                walk(v)

    walk(tg)
    assert not keys & {"use_cnt", "ts_at", "y", "amount", "cnt", "체류량", "flow_pop"}
    for v in tg["card"].values():  # 비율·로그 효과·흔들림만 (절대 건수 없음)
        assert abs(sum(v["cat_share"].values()) - 1) < 1e-3
        assert all(abs(x) < 2 for x in v["dow"] + v["month"])


def test_adapter_reads_no_provided_files(sim):
    assert not any("interim" in str(p) for p in sim.files.values())
    assert set(SYNTHETIC_SOURCES).isdisjoint(sim.files)
    assert set(sim.files) == set(PUBLIC_SOURCES)
    assert sim.snapshot_id.startswith("sim-")
    assert sim.data_version("card2").endswith("/synthetic")


def test_pipeline_runs_on_synthetic(sim):
    from bigcontest.rules.compare import collect, ctx_for, run_variant

    d = date(2025, 12, 20)
    c = ctx_for(sim.snapshot_id, "춘천", d, "고령층")
    ev = collect(c)
    card_ev = [e for e in ev if (e.data_version or "").startswith("card2@")]
    assert card_ev and all("synthetic" in e.data_version for e in card_ev)
    cands = run_variant(ev, "D", c.request, d, 10).candidates
    assert cands and all(x.candidate_level for x in cands)


def test_injection_monotone_and_labels(world):
    c = ("춘천", "70대", "여성")
    t0 = 100
    inj = I.prepare(world, c, "S1", t0, 0.5)
    drops = [1 - I.expected_ratio(world, inj, f)[t0 + 27] for f in (0.0, 0.05, 0.2, 0.5)]
    assert drops[0] == 0 and all(a < b for a, b in zip(drops, drops[1:]))
    assert (I.expected_ratio(world, inj, 0.2)[:t0] == 1).all()  # 시작 전에는 변화 없음
    s6 = I.prepare(world, c, "S6", t0, 0.3)
    r = I.expected_ratio(world, s6, 0.3)
    assert np.ptp(r) < 0.03 and np.abs(np.diff(r)).max() < 0.01  # 은둔 청년: 처음부터 낮아 '변화'(계단)가 없음
    s7 = I.prepare(world, c, "S7", t0, 0.1)
    assert I.expected_ratio(world, s7, 0.1)[t0 + 5] > 1  # 전입은 증가


def test_labeled_dataset_format(world):
    spec = importlib.util.spec_from_file_location("check_no_raw", ROOT / "scripts" / "check_no_raw.py")
    cnr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cnr)
    panel, scen = I.labeled_dataset(world, n_scenarios=4, seed=0)
    assert len(panel) == 4 * 184 * 28 and len(scen) == 4
    need = {"scenario_id", "date", "city", "age_g", "SEX_CCD", "sim_pay_index", "is_target", "inj_persona", "inj_fraction",
            "onset_date", "inj_w_frac", "exp_drop", "label_deviation", "bg_w_frac", "data_source"}
    assert need <= set(panel.columns)
    assert not [c for c in panel.columns if cnr.forbidden(c)]
    assert panel.groupby("scenario_id")["is_target"].sum().eq(184).all()
    assert (panel.loc[~panel["is_target"], "label_deviation"] == False).all()  # noqa: E712
    w = I.wide(panel, 0)
    assert w.shape == (184, 28)
    p2, _ = I.labeled_dataset(world, n_scenarios=4, seed=0)
    pd.testing.assert_frame_equal(panel, p2)


def test_offline_api_marks_synthetic():
    from fastapi.testclient import TestClient

    from bigcontest.sim.serve import build_app

    app, sid = build_app(":memory:")
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200 and r.headers["x-data-source"] == "synthetic"
        assert r.json()["active_snapshot_id"] == sid
