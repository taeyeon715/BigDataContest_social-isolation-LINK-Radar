"""성능 개선(캐시·벡터화·늦은 import·색인)이 결과를 바꾸지 않는지. 원본 데이터(data/) 없이 돈다."""

import os
import subprocess
import sys
from dataclasses import asdict

import numpy as np
import pandas as pd
import pytest

from bigcontest.agent.tools import datasource as DS
from bigcontest.agent.tools.signal import group_mode
from bigcontest.config import ROOT
from bigcontest.resources import ledger as L
from bigcontest.resources import match as M

if str(ROOT) not in sys.path:  # api 패키지
    sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------ 동절기 격자 유형 (최빈값, 동점 규칙 고정)


def _lambda_mode(d, by, col):
    return d.groupby(by)[col].agg(lambda s: s.mode().iloc[0])


@pytest.mark.parametrize("dtype", [object, "str"])
def test_group_mode_ties_pick_smallest_value(dtype):
    """동점이면 정렬 순서가 앞인 값 (Series.mode 와 같음): 동 < 면 < 읍. 빈 값은 세지 않음."""
    d = pd.DataFrame({
        "g": ["a", "a", "a", "a", "b", "b", "c", "c", "c", "d", "d", "d"],
        "t": ["읍", "면", "읍", "면", "읍", "동", "면", None, None, "읍", "읍", "면"],
    })
    d["t"] = d["t"].astype(dtype)
    got = group_mode(d, "g", "t")
    assert got.to_dict() == {"a": "면", "b": "동", "c": "면", "d": "읍"}
    assert got.to_dict() == _lambda_mode(d, "g", "t").to_dict()
    assert list(got.index) == list(_lambda_mode(d, "g", "t").index)


def test_group_mode_matches_lambda_on_random_ties():
    rng = np.random.default_rng(7)
    d = pd.DataFrame({"g": rng.integers(0, 400, 3000), "t": rng.choice(["동", "읍", "면"], 3000)})
    d["t"] = d["t"].astype("str")
    a, b = group_mode(d, "g", "t"), _lambda_mode(d, "g", "t")
    n_tie = (d.groupby(["g", "t"]).size().unstack(fill_value=0)
             .apply(lambda r: (r == r.max()).sum() > 1, axis=1).sum())
    assert n_tie > 10  # 동점 격자가 실제로 있음
    assert a.index.equals(b.index) and (a == b).all()


# ------------------------------------------------------------ need_types.yaml 캐시


def test_load_needs_cached_and_reloaded_on_change(tmp_path):
    p = tmp_path / "needs.yaml"
    p.write_text("need_types: {a: {}}\ncause_types: {}\n", encoding="utf-8")
    first = M.load_needs(p)
    assert M.load_needs(p) is first  # 다시 파싱하지 않음
    p.write_text("need_types: {a: {}, b: {}}\ncause_types: {}\n", encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert list(M.load_needs(p)["need_types"]) == ["a", "b"]
    assert M.load_needs() == M.load_needs(M.NEEDS)


# ------------------------------------------------------------ 자원 매칭: 행 dict (records) 캐시


def _iterrows_reference(d, adm_cd, origin, group, cause_type, cfg):
    """예전 구현처럼 iterrows 로 읽은 행(Series)을 dict 로 바꿔 넘김: 같은 판단이어야 함."""
    recs = [dict(r) for _, r in d.iterrows()]
    return M.match_resources(d, adm_cd, group, None, cause_type, origin, None, cfg, records=recs)


def test_match_records_same_as_without():
    led = L.load(L.V2)
    cfg = M.load_needs()
    for city, adm in (("춘천", "32010350"), ("강남", "11230650")):
        sub = led[led["city"] == city]
        recs = M.ledger_records(sub)
        origin = (37.8, 127.7) if city == "춘천" else (37.5, 127.04)
        for grp, cause in ((None, None), ("고령층", "거점 원거리형+달력 공백형"), ("청년 남성", "한파·대설형")):
            a = [asdict(m) for m in M.match_resources(sub, adm, grp, None, cause, origin)]
            b = [asdict(m) for m in M.match_resources(sub, adm, grp, None, cause, origin, records=recs)]
            assert a == b
            if grp == "고령층":
                assert a == [asdict(m) for m in _iterrows_reference(sub, adm, origin, grp, cause, cfg)]
            s1 = M.select_resources(sub, adm, grp, None, "1인가구 밀집형", origin=origin)
            s2 = M.select_resources(sub, adm, grp, None, "1인가구 밀집형", origin=origin, records=recs)
            assert asdict(s1) == asdict(s2)
            assert M.access_gap(sub, adm, origin, "안부·관계", grp) == M.access_gap(sub, adm, origin, "안부·관계", grp,
                                                                                records=recs)
    with pytest.raises(ValueError):
        M.match_resources(sub, adm, records=recs[:-1])


def test_match_uses_row_position_not_label():
    """부분표의 index 가 0부터가 아니어도 (도시로 거른 원장) 필요 유형 표시가 행마다 맞음."""
    led = L.load(L.V2)
    sub = led[led["city"] == "춘천"]
    assert sub.index[0] != 0
    a = M.match_resources(sub, "32010350", "고령층", "안부·관계", origin=(37.8, 127.7))
    b = M.match_resources(sub.reset_index(drop=True), "32010350", "고령층", "안부·관계", origin=(37.8, 127.7))
    assert [asdict(m) for m in a] == [asdict(m) for m in b]


# ------------------------------------------------------------ 늦은 import


def test_models_package_does_not_import_statsmodels():
    code = ("import sys, bigcontest.models as m; assert 'statsmodels' not in sys.modules; "
            "assert 'lightgbm' not in sys.modules; from bigcontest.models import STLModel, LGBMModel; "
            "assert STLModel.__module__ == 'bigcontest.models.stl' and 'statsmodels' in sys.modules; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
    import bigcontest.models as m

    with pytest.raises(AttributeError):
        m.NoSuchModel  # noqa: B018


# ------------------------------------------------------------ 스냅샷 캐시 개수 제한


def test_open_datasource_keeps_recent_dev_snapshots_only(monkeypatch):
    class Fake:
        def __init__(self, sid):
            self.snapshot_id = sid

    monkeypatch.setattr(DS, "DataSource", Fake)
    monkeypatch.setattr(DS, "_OPENED", type(DS._OPENED)())
    monkeypatch.setattr(DS, "_SOURCES", {})
    DS.register_source("persona-x", Fake("persona-x"))
    a = DS.open_datasource("dev-a")
    DS.open_datasource("dev-b")
    assert DS.open_datasource("dev-a") is a  # 최근에 씀
    DS.open_datasource("dev-c")  # dev-b 가 밀려남
    assert list(DS._OPENED) == ["dev-a", "dev-c"]
    assert DS.open_datasource("dev-a") is a
    assert DS.open_datasource("persona-x").snapshot_id == "persona-x"  # 등록한 것은 지우지 않음
    with pytest.raises(DS.DataUnavailable):
        DS.open_datasource("other-1")


# ------------------------------------------------------------ API 원장 캐시 (원장 파일 해시가 키)


def test_dashboard_ledger_cache_follows_ledger_file(tmp_path, monkeypatch):
    from api.services import dashboard_real as D

    p = tmp_path / "ledger.csv"
    p.write_bytes(L.V2.read_bytes())
    monkeypatch.setattr(L, "V2", p)
    v1 = D._ledger_view()
    a = D.find_ledger_resources("춘천", None, None, "고령층")
    assert D._ledger_view() is v1
    assert [x.model_dump() for x in D.find_ledger_resources("춘천", None, None, "고령층")] == [x.model_dump() for x in a]
    m1 = D.map_resources("춘천")
    # 원장 파일이 바뀌면 (서버를 다시 켜지 않아도) 새 판을 읽음
    led = L.load(p)
    drop = a[0].resource_id
    led[led["resource_id"] != drop].to_csv(p, index=False, encoding="utf-8")
    v2 = D._ledger_view()
    assert v2 is not v1 and v2.version != v1.version
    b = D.find_ledger_resources("춘천", None, None, "고령층")
    assert drop not in {x.resource_id for x in b} and len(b) == len(a) - 1
    assert D.map_resources("춘천") is not m1
    # 돌려준 항목을 바꿔도 캐시는 그대로
    b[0].name = "바꿈"
    assert D.find_ledger_resources("춘천", None, None, "고령층")[0].name != "바꿈"


# ------------------------------------------------------------ SQLite 색인


def test_store_hot_queries_use_indexes(tmp_path):
    from api.store import Store

    s = Store(tmp_path / "s.sqlite")
    plans = {
        "monitoring": ("SELECT * FROM runs WHERE mode = 'monitoring' AND as_of = ? AND analysis_mode = ?"
                       " ORDER BY created_at DESC, run_id DESC", ("2025-09-26", "retrospective")),
        "list_runs": ("SELECT * FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?", (50,)),
        "candidate_runs": ("SELECT * FROM runs WHERE candidate_id IS NOT NULL ORDER BY created_at DESC, run_id DESC", ()),
        "last_follow_up": ("SELECT * FROM reviews WHERE run_id = ? AND follow_up_on IS NOT NULL"
                           " ORDER BY created_at DESC, rowid DESC LIMIT 1", ("r",)),
        "llm_calls": ("SELECT COUNT(*) AS n FROM llm_calls WHERE run_id = ? AND node != 'followup'", ("r",)),
        "approvals": ("SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at, rowid", ("r",)),
    }
    for name, (sql, args) in plans.items():
        detail = " / ".join(r[3] for r in s._conn.execute("EXPLAIN QUERY PLAN " + sql, args))
        assert "USING INDEX" in detail and "TEMP B-TREE" not in detail, (name, detail)
    s.close()
    Store(tmp_path / "s.sqlite").close()  # 이미 색인이 있는 파일을 다시 열어도 됨
