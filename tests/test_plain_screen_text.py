"""화면에 가는 서버 문장에 내부 표시가 없다: 규칙 판(rules-v1.1)·설정 파일(rules.yaml)·설정 열쇠(context_only 등).
규칙 판·ID 는 데이터 칸(rule_id·rule_version)과 등급 이유 끝 [rules-v1.1 …] 꼬리표에만 남는다 (화면이 꼬리표를 뺌)."""

import ast
import re
from pathlib import Path

from bigcontest.agent.tools import signal as S
from bigcontest.rules import rules_version
from bigcontest.rules.levels import Axes, StructFact, decide, rule_label

ROOT = Path(__file__).resolve().parents[1]
INTERNAL = re.compile(r"rules-v\d|\b\w+\.yaml\b|\bcontext_only\b|\bapply_to_structure\b|\btarget_population\b"
                      r"|\bcity_guidance\b|\bholiday_path\b|\bcard_context\b|\bguard_rules\b")
TAG = re.compile(r"\[rules-[^\]]*\]")  # 등급 이유 꼬리표 (데이터, 화면이 뺌)
# 근거 출처 칸(rule_derived_from, 근거 표 "자세히")의 모델 설정 판은 기록이라 둠
KEEP = ("configs/models.yaml",)
HANGUL = re.compile(r"[가-힣]")

# 사람에게 보이는 문장을 만드는 파일
SCREEN_FILES = [
    "src/bigcontest/rules/levels.py",
    "src/bigcontest/agent/tools/data.py",
    "src/bigcontest/agent/tools/signal.py",
    "src/bigcontest/agent/tools/report.py",
    "src/bigcontest/agent/nodes.py",
    "src/bigcontest/agent/followup.py",  # 이어 묻기 "적용 규칙 …" 줄은 LLM 입력이라 그대로, 화면이 뺌
    "api/routes/dashboard.py",
    "api/services/dashboard_real.py",
]


def _strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n.lineno, n.value


def test_screen_sentences_have_no_internal_tokens():
    bad = []
    for f in SCREEN_FILES:
        for line, s in _strings(ROOT / f):
            if HANGUL.search(s) and not any(k in s for k in KEEP) and INTERNAL.search(TAG.sub("", s)):
                bad.append(f"{f}:{line}: {s[:80]}")
    assert not bad, "\n".join(bad)


def test_level_reason_text_is_plain_and_tag_keeps_version():
    miss = StructFact("11110000", 0.4, 2, 22, "강남 22개 동", False, target_share=0.1, target_median=0.12,
                      target_label="40대·50대 남성", rate_top=True)
    a = Axes(candidate_type="scheduled_access_review", input_status="충족", trigger="R1 추석 연휴 D-7", structure=miss)
    d = decide(a)
    assert "구조 상위로 보지 않음" in d.reason and not INTERNAL.search(d.reason)
    g = decide(Axes(candidate_type="scheduled_access_review", input_status="충족", trigger="R1 추석 연휴 D-7",
                    city_guidance=True))
    assert g.rule_id == "L2-CITY-GUIDANCE" and "도시 단위로 안내" in g.reason and not INTERNAL.search(g.reason)
    # 판·규칙 ID 는 꼬리표에 그대로 (데이터·판단 기록용)
    assert rule_label(g) == f"[{rules_version()} L2-CITY-GUIDANCE+CAP-SHARED-CONTEXT]"


def test_context_only_note_is_plain_korean():
    src = (ROOT / "src/bigcontest/agent/tools/data.py").read_text(encoding="utf-8")
    assert "전입 맥락 표시만 (참고용 맥락, 등급에 쓰지 않음)" in src
    assert not INTERNAL.search(S.HEAT_RULE_NOTE) and not INTERNAL.search(S.VISITOR_NOTE)
