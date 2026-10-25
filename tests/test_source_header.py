"""회귀 시험: 모든 경로의 X-Data-Source 표지가 백엔드와 맞는지.

real 백엔드에서는 설정 파일로 만드는 /data/capabilities(config)를 빼고 모두 real (현장 결과 모아 보기 포함).
fake 백엔드에서는 config 를 빼고 모두 fake. 성공·오류 응답 모두 표지가 붙는다.
새 경로를 더하면 EXPECTED 에도 더해야 이 시험이 통과한다 (표지 분기를 빠뜨리지 않게).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402

# 경로 → real 백엔드에서의 표지 (fake 백엔드는 config 말고 모두 fake)
EXPECTED = {
    ("GET", "/health"): "real",
    ("GET", "/me"): "real",
    ("GET", "/snapshots/active"): "real",
    ("GET", "/data/capabilities"): "config",
    ("GET", "/home"): "real",
    ("GET", "/overview"): "real",
    ("GET", "/map"): "real",
    ("GET", "/candidates"): "real",
    ("GET", "/resources"): "real",
    ("GET", "/resources/protective"): "real",
    ("POST", "/runs"): "real",
    ("GET", "/runs"): "real",
    ("GET", "/followups"): "real",
    ("GET", "/runs/{run_id}"): "real",
    ("GET", "/runs/{run_id}/evidence"): "real",
    ("GET", "/runs/{run_id}/report"): "real",
    ("GET", "/runs/{run_id}/resources"): "real",
    ("POST", "/runs/{run_id}/followup"): "real",
    ("GET", "/runs/{run_id}/history"): "real",
    ("POST", "/runs/{run_id}/review"): "real",
    ("POST", "/runs/{run_id}/approve"): "real",
    ("POST", "/runs/{run_id}/outcomes"): "real",
    ("GET", "/runs/{run_id}/outcomes"): "real",
    ("GET", "/outcomes/summary"): "real",
    ("POST", "/qa"): "real",
    ("POST", "/objections"): "real",
    ("GET", "/objections"): "real",
    ("POST", "/objections/{objection_id}/status"): "real",
}


def _routes(app):
    """앱의 API 경로 (OpenAPI 문서 기준: 포함한 라우터 안 경로까지)."""
    return {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops}


@pytest.mark.parametrize("backend", ["fake", "real"])
def test_every_route_has_the_right_source_header(tmp_path, backend):
    kw = {"backend": "real", "tools_backend": "stub", "llm": "none"} if backend == "real" else {}
    app = create_app(db_path=tmp_path / "s.sqlite", delay_s=0, **kw)
    with TestClient(app) as c:
        assert _routes(app) == set(EXPECTED), "새 경로는 EXPECTED 와 app.MarkSource 에 더하세요"
        for (method, path), want in EXPECTED.items():
            if backend == "fake" and want == "real":
                want = "fake"
            url = re.sub(r"\{[^}]+\}", "run-x", path)
            r = c.get(url) if method == "GET" else c.post(url, json={})
            assert r.headers.get("X-Data-Source") == want, (backend, method, path, r.status_code)
    if backend == "real":
        app.state.runs.close()
    app.state.store.close()


def test_real_outcome_summary_reads_real_store(tmp_path):
    """재현(audit_integrity.py): real 의 현장 결과 모아 보기는 저장된 실제 결과를 집계하므로 real."""
    app = create_app(db_path=tmp_path / "s.sqlite", backend="real", tools_backend="stub", llm="none", delay_s=0)
    with TestClient(app) as c:
        r = c.get("/outcomes/summary")
        assert r.status_code == 200 and r.headers["X-Data-Source"] == "real"
    app.state.runs.close()
    app.state.store.close()
