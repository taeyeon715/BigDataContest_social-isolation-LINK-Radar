"""리포트 CSV 원천 열 검사 (scripts/check_no_raw.py)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_no_raw", ROOT / "scripts" / "check_no_raw.py")
cnr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cnr)


def _csv(tmp_path, name, head):
    p = tmp_path / name
    p.write_text("﻿" + head + "\n1,2,3\n", encoding="utf-8")
    return p


def test_forbidden_names():
    for c in ["y", "yhat", "USE_CNT", "TS_AT", "use_cnt", "amount", "체류량", "flow_pop", "mae", " Y ",
              "MAN_FLOW_POP_CNT_60GU", "TMST_00", "h=1 MAE(건)"]:
        assert cnr.forbidden(c), c
    for c in ["rel_mae", "coverage", "city_shock", "city_shock_z", "탐지율", "체류 규모(도시 평균=100)", "60대+",
              "mean_change_abs", "n_target", "대조 음성", "7→12월", "yday_flag"]:
        assert not cnr.forbidden(c), c


def test_check_flags_bad_file(tmp_path):
    good = _csv(tmp_path, "good.csv", "city,rel_mae,coverage")
    bad = _csv(tmp_path, "bad.csv", "city,y,yhat")
    out = cnr.check([good, bad])
    assert len(out) == 1 and out[0][0] == bad and out[0][1] == ["y", "yhat"]
    assert cnr.main([str(good)]) == 0
    assert cnr.main([str(good), str(bad)]) == 1


def test_tracked_reports_have_no_raw_columns():
    paths = cnr.tracked_csvs()
    assert cnr.check(paths) == []


def test_missing_file_fails(tmp_path):
    assert cnr.main([str(tmp_path / "없음.csv")]) == 2
