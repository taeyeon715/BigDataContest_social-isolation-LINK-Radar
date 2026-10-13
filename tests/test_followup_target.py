"""회귀 시험: 이어 묻기가 질문과 다른 대상을 대신 답하지 않는지.

질문에 적힌 후보 ID·동·성별·연령·집단·등급은 엄격한 조건. 카드에 맞는 후보가 없으면 NO_TARGET
("이 카드에 맞는 대상이 없습니다") + 새 조사 안내. 대상 조건이 없는 일반 질문만 검토 배정 순.
고령층 조사 카드, 가짜 도구, LLM 없음.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from bigcontest.agent.followup import answer_followup, pick_candidates  # noqa: E402
from bigcontest.agent.schemas import Candidate, EvidenceCard  # noqa: E402

Q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?"


@pytest.fixture(scope="module")
def card(tmp_path_factory):
    """고령층 조사 카드 (춘천 남면 Level 3, 동면 Level 2, 공유 맥락 Level 2)."""
    app = create_app(db_path=tmp_path_factory.mktemp("fu") / "s.sqlite", backend="real", tools_backend="stub",
                     llm="none", delay_s=0)
    with TestClient(app) as c:
        rid = c.post("/runs", json={"mode": "qa", "question": Q, "as_of": "2025-09-26"}).json()["run_id"]
        values = app.state.runs.agent.status(rid)["values"]
        yield c, rid, values
    app.state.runs.close()
    app.state.store.close()


def _no_target(out):
    assert out["status"] == "abstained", out.get("answer")
    assert out["abstain_reason"].startswith("NO_TARGET: 이 카드에 맞는 대상이 없습니다")
    assert out["answer"] is None and out["needed_data"] and "새 조사" in out["needed_data"][0]


@pytest.mark.parametrize("question", [
    "동산면은 왜 Level 3야?",          # 카드에 없는 동
    "청년 여성은 왜 Level 3야?",       # 카드에 없는 집단·성별
    "C-20250926-999의 이유를 설명해줘",  # 없는 후보 ID
    "남면은 왜 Level 1이야?",           # 동은 있지만 등급이 다름 (충돌 조건을 버리지 않음)
    "남면 40대는 왜 Level 3야?",        # 동은 있지만 연령이 다름
])
def test_review_repro_questions_get_no_target(card, question):
    _, _, values = card
    _no_target(answer_followup(values, question, "none"))


def test_named_dong_and_group_pick_exactly_that_candidate(card):
    _, _, values = card
    namyeon = [c["candidate_id"] for c in values["candidates"] if c["adm_cd"] == "32010350"]
    out = answer_followup(values, "남면 고령층은 왜 Level 3야?", "none")
    assert out["status"] == "answered"
    named = [line for line in out["answer"].split("\n") if line.startswith("후보 ")]
    assert named and all(namyeon[0] in line for line in named)  # 남면 후보만 설명
    others = [c["candidate_id"] for c in values["candidates"] if c["candidate_id"] not in namyeon]
    assert not any(o in out["answer"] for o in others)


def test_general_question_still_falls_back_to_assignment_order(card):
    _, _, values = card
    out = answer_followup(values, "왜 이렇게 나왔어?", "none")
    assert out["status"] == "answered" and out["answer"].startswith("후보 ")


def test_no_target_through_api(card):
    c, rid, _ = card
    r = c.post(f"/runs/{rid}/followup", json={"question": "동산면은 왜 Level 3야?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "abstained" and body["abstain_reason"].startswith("NO_TARGET")


# ---------------------------------------------------------------- 단위: 성별·연령도 엄격


def _cand(cid, level, adm=None, cohort="80대+", group="65세 이상"):
    kind = "scheduled_access_review" if adm else "unexpected_activity_deviation"
    return Candidate(candidate_id=cid, candidate_type=kind, city="강남", adm_cd=adm, check_first_adm=[],
                     is_shared_context=False, analysis_cohort=cohort, service_eligibility_group=group,
                     input_status="충족", counter_result="해당 없음" if adm else "통과", candidate_level=level)


CARD = EvidenceCard(card_id="CARD-1", title="t", snapshot_id="s", analysis_mode="simulated_replay",
                    as_of=date(2025, 9, 26), evidence_revision=1)
CANDS = [
    _cand("C-20250926-501", "Level 2", adm="11230510"),  # 신사동 고령층 (성별 없음 = 남녀 모두)
    _cand("C-20250926-502", "Level 3", cohort="40대 남성", group="40~64세 남성"),
    _cand("C-20250926-504", "Level 1", cohort="60대 여성", group=None),
]


def ids(cs):
    return [c.candidate_id for c in cs]


def test_sex_and_age_are_strict():
    assert ids(pick_candidates("중장년 여성은?", CANDS, CARD)) == []          # 중장년 후보는 남성만
    assert ids(pick_candidates("40대 남성은?", CANDS, CARD)) == ["C-20250926-502"]
    assert ids(pick_candidates("60대 여성은?", CANDS, CARD)) == ["C-20250926-504"]
    assert ids(pick_candidates("70대는?", CANDS, CARD)) == []                 # 80대+·40대·60대 뿐
    # 집단 이름이 없는 후보(60대 여성)는 나이 범위·성별로: 고령층(65세 이상)의 카드 코호트 60대와 겹침
    assert ids(pick_candidates("고령층 여성은?", CANDS, CARD)) == ["C-20250926-501", "C-20250926-504"]
    assert ids(pick_candidates("고령층 남성은?", CANDS, CARD)) == ["C-20250926-501"]
    assert ids(pick_candidates("C-20250926-502 는 Level 1이야?", CANDS, CARD)) == []  # ID 와 등급이 충돌
