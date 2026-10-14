"""호칭 없는 이름·영문 이름 탐지와 저장 전 가리기 (외부 점검 K-02, _review/8b0ae2e/audit_privacy.py 이식).

- "김철수에게 먼저 연락"과 그 변형은 가려지고([이름]), 질문에 들어가면 개인 지목으로 보류
- API DB 의 모든 표와 그래프 체크포인트 어디에도 원문 이름이 남지 않음 (질문·검토·자원 판정·승인·이의 글)
- 흔한 말·지명(이번·정도·김장철·신동면·남면 이장님 …)은 이름으로 보지 않음 (오탐 낮게)
- 규칙 기반이라 모든 이름을 찾는다고 보장하지 않음 (문서에 한계를 적음)
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import create_app  # noqa: E402
from api.auth import Auth  # noqa: E402
from bigcontest.agent import guard as G  # noqa: E402
from bigcontest.agent.pii import find_names, place_words  # noqa: E402

# 가상의 시험 이름
NAMED = [
    "김철수에게 먼저 연락", "민수에게 연락", "Kim Chulsoo에게 연락", "김철수한테 전화해 줘", "최민준께 안부 전화",
    "김철수가 혼자 사는데 확인 부탁", "김철수의 상태 확인", "김철수 먼저 연락", "연락할 사람 김철수", "박지민이 연락 안 됨",
    "강남 김철수 할머니", "박영희님 결제", "이영희 씨 확인", "김 씨 어르신", "남궁민수에게 연락", "Chulsoo Kim 방문",
    "KIM Chul-soo 연락", "John Smith에게 연락", "Mr. Smith 연락", "이순자 할머니 댁 방문",
]
RAW_NAMES = ("김철수", "민수", "Kim Chulsoo", "최민준", "박지민", "박영희", "이영희", "남궁민수", "Chulsoo Kim",
             "KIM Chul-soo", "John Smith", "Smith", "이순자")
NORMAL = [
    "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?", "강남 1인세대 비율 상위 동은?", "춘천 남면 어르신 겨울철 점검",
    "조사할 어르신 동 순위", "선생님, 춘천 고령층 체류인구는?", "남면 이장님께 먼저 연락", "이웃에게 안부 전화 부탁",
    "주민에게 연락해 확인", "정도가 심한 동 확인", "한번 더 확인해줘", "김장철 결제 변화 확인", "이번 주 방문 계획",
    "고령층의 결제 확인", "전화로 확인", "남성의 결제 변화 확인", "신동면 방문 일정", "동산면의 어르신 방문",
    "효자1동 주민들의 안부 확인", "박물관 방문객 변화", "강원도 방문", "기준일 전에 확인",
    "전화 안부 서비스로 연결할 수 있어?", "이 실행 담당자에게 한 줄로 알려 줘", "고립 위험 동 확인",
    "지난주 방문 결과 확인", "우선 순위대로 연락", "모두에게 연락", "여러분께 공유", "주무관님께 전화", "공무원에게 연락",
    "유형별로 확인", "장애인 가구 방문", "최근 결제 확인", "Level 3 후보 확인", "LINK Radar 결과 확인",
    "Safety Guard 확인", "정기 방문 확인", "신사동 경로당 방문", "석사동 어르신 확인", "소양동 방문", "원인 확인",
    "도시락 배달 확인", "노인맞춤돌봄 수행기관에 연락", "한파 쉼터 연락", "추석 연휴 전화 확인", "황금연휴 결제",
    "주민센터에 연락", "행정복지센터 방문", "구청에 전화", "민원 확인", "강남 중장년 남성 1인가구 비율",
]


@pytest.mark.parametrize("text", NAMED)
def test_names_detected_and_masked(text):
    masked, n = G.mask_pii(text)
    assert n >= 1 and "[이름]" in masked, text
    assert not any(raw in masked for raw in RAW_NAMES), masked
    assert G.individual_target(text), text  # 이름이 든 질문은 개인 지목
    assert G.individual_target(masked), masked  # 가린 뒤에도 ([이름] 표시)
    assert G.mask_pii(masked)[0] == masked  # 다시 가려도 같음


def test_normal_text_is_not_a_name():
    bad = {q: find_names(q) for q in NORMAL if find_names(q)}
    assert bad == {}
    for q in NORMAL:
        assert G.mask_pii(q)[0] == q, q


def test_place_names_are_excluded():
    words = place_words()
    assert {"신동면", "신동", "동산면", "남면", "효자1동", "효자", "강남", "춘천"} <= words
    for adm in ("신동면", "동산면", "동면", "남산면", "석사동", "소양동"):
        assert find_names(f"{adm}에게 연락할 곳 확인") == [], adm


def test_other_pii_still_masked():
    masked, _ = G.mask_pii("테헤란로 152 203호 010-1234-5678 person@example.invalid")
    assert "010-1234-5678" not in masked and "person@example.invalid" not in masked and "203호" not in masked


# ---------------------------------------------------------------- API·체크포인트 (점검 재현)


def _tables_with(path: Path, needles: tuple[str, ...]) -> list[str]:
    con = sqlite3.connect(path)
    try:
        found = []
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in con.execute(f'SELECT * FROM "{t}"'):
                blob = b" ".join(x if isinstance(x, bytes) else str(x).encode("utf-8") for x in row)
                text = " ".join(x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x) for x in row)
                if any(n.encode("utf-8") in blob or n in text or n.encode("unicode_escape").decode() in text
                       for n in needles):
                    found.append(t)
                    break
        return found
    finally:
        con.close()


def test_named_question_and_memos_never_stored(tmp_path):
    """실제 그래프(가짜 도구·가짜 LLM): 이름이 든 질문은 보류, 검토·승인·이의 글의 이름은 가린 글만 저장.
    API DB 모든 표와 체크포인트 DB 에 원문 이름이 없음."""
    logging.disable(logging.CRITICAL)
    db = tmp_path / "p.sqlite"
    app = create_app(db_path=db, backend="real", tools_backend="stub", llm="fake", delay_s=0, auth=Auth({}))
    memo = "김철수에게 먼저 연락. Kim Chulsoo한테도. 테헤란로 152 203호. 010-1234-5678 person@example.invalid"
    try:
        with TestClient(app) as c:
            q = "이번 추석 전에 춘천 어르신 어디부터 확인해야 해? 김철수에게 먼저 연락"
            r = c.post("/runs", json={"mode": "qa", "question": q, "as_of": "2025-09-26"})
            assert r.status_code == 202, r.text
            rid = r.json()["run_id"]
            app.state.runs.wait_idle()
            st = c.get(f"/runs/{rid}").json()
            assert st["status"] == "abstained" and st["route"] == "out_of_scope"
            assert (st.get("abstain_reason") or "").startswith("OUT_OF_SCOPE_INDIVIDUAL")
            saved = app.state.store.get_run(rid)["question"]
            assert "김철수" not in saved and "[이름]" in saved
            # 정상 질문 실행에 이름이 든 메모들
            ok = c.post("/runs", json={"mode": "qa", "question": "이번 추석 전에 춘천 어르신 어디부터 확인해야 해?",
                                       "as_of": "2025-09-26"}).json()["run_id"]
            app.state.runs.wait_idle()
            st = c.get(f"/runs/{ok}").json()
            assert st["status"] == "awaiting_approval"
            rv = c.post(f"/runs/{ok}/review", json={"reviewer": "audit", "memo": memo, "resource_judgements": [],
                                                    "checked_evidence_ids": [], "report_revision": st["report_revision"]})
            assert rv.status_code == 200, rv.text
            assert "김철수" not in app.state.store.list_reviews(ok)[0]["memo"]
            rev = c.get(f"/runs/{ok}").json()["report_revision"]
            mo = c.post(f"/runs/{ok}/approve", json={"decision": "more", "report_revision": rev, "approver": "x",
                                                     "idempotency_key": "m1", "memo": memo,
                                                     "focus": {"memo": "민수에게 연락해 보고 강수 확인"}})
            assert mo.status_code == 200, mo.text
            app.state.runs.wait_idle()
            rev = c.get(f"/runs/{ok}").json()["report_revision"]
            a = c.post(f"/runs/{ok}/approve", json={"decision": "held", "report_revision": rev, "approver": "x",
                                                    "idempotency_key": "h1", "memo": memo})
            assert a.status_code == 200, a.text
            obj = c.post("/objections", json={"target_type": "run", "run_id": ok, "city": "춘천",
                                              "reason_category": "기타", "text": memo, "idempotency_key": "o1"})
            assert obj.status_code in (200, 201), obj.text
            app.state.runs.wait_idle()
    finally:
        app.state.runs.close()
        app.state.store.close()
        logging.disable(logging.NOTSET)
    needles = ("김철수", "Kim Chulsoo", "민수에게", "010-1234-5678", "person@example.invalid", "테헤란로 152")
    assert _tables_with(db, needles) == []
    assert _tables_with(db.with_suffix(".agent.sqlite"), needles) == []
