"""첫 화면 GET /home (api/services/home.py): 동 색 = 그 동 예정된 점검 후보의 가장 높은 등급, 이탈 경보의 먼저 볼 동은
따로 표시, 요약 수는 후보 등급별. 가짜 백엔드로 경로 모양을 보고, build_home 은 후보를 직접 넣어 본다."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.services.home import HomeResponse, build_home  # noqa: E402
from bigcontest.agent.schemas import Candidate  # noqa: E402


def _c(cid, adm=None, level="Level 2", ctype="scheduled_access_review", first=(), counter="해당 없음"):
    return Candidate(candidate_id=cid, candidate_type=ctype, city="춘천", adm_cd=adm, check_first_adm=list(first),
                     is_shared_context=bool(first), analysis_cohort="60대 이상", service_eligibility_group="고령층",
                     trigger="R1 추석 연휴 D-7", input_status="충족", counter_result=counter,
                     counter_reason="시험" if counter == "기각" else None, candidate_level=level,
                     response_type="담당자 추가 분석")


def test_home_route_shape_fake_backend():
    c = TestClient(create_app())
    r = c.get("/home", params={"as_of": "2025-09-26", "city": "춘천"})
    assert r.status_code == 200
    h = HomeResponse.model_validate(r.json())
    assert h.city == "춘천" and len(h.areas) == 25
    assert sum(h.summary.counts.values()) == len(h.candidates)
    assert c.get("/home", params={"as_of": "2025-09-26", "city": "서울"}).status_code == 400


def test_build_home_area_levels_and_deviation_dongs():
    cands = [
        _c("C-1", "32010640", "Level 3"), _c("C-2", "32010640", "Level 2"), _c("C-3", "32010650", "Level 1"),
        _c("C-4", None, "Level 2", "unexpected_activity_deviation", first=["32010660"], counter="통과"),
        _c("C-5", None, "Level 1", "unexpected_activity_deviation", first=["32010670"], counter="기각"),
    ]
    h = build_home(date(2025, 9, 26), "춘천", cands, [], "dev-test", "rules-v2.0", 2, with_resources=False)
    area = {a.adm_cd: a for a in h.areas}
    assert area["32010640"].level == "Level 3" and area["32010640"].candidate_ids == ["C-1", "C-2"]
    assert area["32010650"].level == "Level 1"
    assert area["32010660"].level is None and area["32010660"].deviation_first  # 반증 통과한 이탈 경보만
    assert not area["32010670"].deviation_first  # 기각된 경보는 먼저 볼 동으로 표시하지 않음
    assert h.summary.counts == {"Level 3": 1, "Level 2": 2, "Level 1": 2, "판단 보류": 0}
    assert h.summary.actions == ["담당자 추가 분석"]
    assert h.candidates[0].adm_nm and all(c.resources == [] for c in h.candidates)
