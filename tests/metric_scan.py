"""도구가 만드는 지표 이름(ToolResult.metric)을 코드에서 정적으로 모은다 (부가 데이터 정의서 누락 검사용).

- 대상: src/bigcontest/agent/tools/*.py 의 envelope / empty / error / _ev / _error / _empty / _tool_error 호출
- metric 값이 글자 그대로면 그 글자, 모듈 상수면 그 값, f-string 이면 {} 자리를 '{}'로 바꾼 틀
- 지역 변수면 같은 함수 안에서 그 이름에 넣은 값(글자·상수·f-string·조건식)과,
  for 문이 튜플 묶음(글자 튜플 목록, 모듈 dict.values())을 풀어 넣은 값을 찾는다
- 끝내 못 찾은 것은 unresolved 로 돌려준다 (시험이 실패하게 해서 사람이 정의서를 고치게)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "src" / "bigcontest" / "agent" / "tools"
# 함수 이름 → metric 위치 인자 번호 (키워드 metric= 도 봄)
CALLS = {"envelope": None, "_ev": None, "empty": 2, "error": 2, "_empty": 2, "_error": 2}


def template(node: ast.JoinedStr) -> str:
    out = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            out.append(str(v.value))
        else:
            out.append("{}")
    return "".join(out)


def normalize(name: str) -> str:
    """정의서 이름과 코드 틀을 같은 모양으로: {아무거나} → {}."""
    return re.sub(r"\{[^{}]*\}", "{}", name).strip()


class _Scan:
    def __init__(self, path: Path):
        self.path = path
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.consts: dict[str, object] = {}
        for n in self.tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                self.consts[n.targets[0].id] = n.value
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                self.consts[n.target.id] = n.value
        self.found: set[str] = set()
        self.unresolved: list[str] = []

    def values(self, node, func: ast.AST | None, depth: int = 0) -> list[str] | None:
        if depth > 6 or node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.JoinedStr):
            return [template(node)]
        if isinstance(node, ast.IfExp):
            a, b = self.values(node.body, func, depth + 1), self.values(node.orelse, func, depth + 1)
            return (a or []) + (b or []) if (a or b) else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"                 and isinstance(node.func.value, ast.Name) and isinstance(self.consts.get(node.func.value.id), ast.Dict):
            # 모듈 dict.get(이름, 이름): dict 값 전부 (기본값은 도구 이름이 그대로 지표가 되는 오류 봉투)
            return [v for x in self.consts[node.func.value.id].values for v in (self.values(x, None, depth + 1) or [])]
        if isinstance(node, ast.Name):
            local = self._local(node.id, func, depth) if func is not None else None
            if local:
                return local
            if node.id in self.consts:
                return self.values(self.consts[node.id], None, depth + 1)
        return None

    def _tuple_items(self, it, func, depth) -> list[ast.AST] | None:
        """for 문이 도는 대상 → 튜플 원소 목록 (글자 튜플 목록, 모듈 dict.values())."""
        if isinstance(it, (ast.Tuple, ast.List)):
            return list(it.elts)
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and it.func.attr == "values" \
                and isinstance(it.func.value, ast.Name) and isinstance(self.consts.get(it.func.value.id), ast.Dict):
            return list(self.consts[it.func.value.id].values)
        return None

    def _local(self, name: str, func, depth) -> list[str] | None:
        out: list[str] = []
        for n in ast.walk(func):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        out += self.values(n.value, None if n.value is None else func, depth + 1) or []
                    elif isinstance(t, ast.Tuple) and isinstance(n.value, ast.Subscript):
                        pass
            elif isinstance(n, ast.For) or isinstance(n, ast.comprehension):
                tgt = n.target
                if isinstance(tgt, ast.Tuple):
                    idx = next((i for i, e in enumerate(tgt.elts) if isinstance(e, ast.Name) and e.id == name), None)
                    if idx is None:
                        continue
                    items = self._tuple_items(n.iter, func, depth)
                    for item in items or []:
                        if isinstance(item, ast.Tuple) and idx < len(item.elts):
                            out += self.values(item.elts[idx], func, depth + 1) or []
        # 튜플 풀기: code, metric = _CONFOUNDERS[key]
        for n in ast.walk(func):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Tuple):
                tgt = n.targets[0]
                idx = next((i for i, e in enumerate(tgt.elts) if isinstance(e, ast.Name) and e.id == name), None)
                if idx is None:
                    continue
                v = n.value
                if isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name) \
                        and isinstance(self.consts.get(v.value.id), ast.Dict):
                    for item in self.consts[v.value.id].values:
                        if isinstance(item, ast.Tuple) and idx < len(item.elts):
                            out += self.values(item.elts[idx], None, depth + 1) or []
        return out or None

    def run(self) -> None:
        funcs = [n for n in ast.walk(self.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        owner: dict[int, ast.AST] = {}
        for f in funcs:
            for n in ast.walk(f):
                owner.setdefault(id(n), f)
        for f in funcs:
            for n in ast.walk(f):
                if not isinstance(n, ast.Call) or owner.get(id(n)) is not f:
                    continue
                fn = n.func.id if isinstance(n.func, ast.Name) else (n.func.attr if isinstance(n.func, ast.Attribute) else "")
                if fn not in CALLS:
                    continue
                node = next((k.value for k in n.keywords if k.arg == "metric"), None)
                pos = CALLS[fn]
                if node is None and pos is not None and len(n.args) > pos:
                    node = n.args[pos]
                if node is None:
                    continue
                params = {a.arg for a in f.args.args + f.args.kwonlyargs}
                if isinstance(node, ast.Name) and node.id in params:
                    continue  # 감싸는 함수 (empty·error·_ev): 부르는 쪽에서 셈
                vals = self.values(node, f)
                if vals:
                    self.found.update(normalize(v) for v in vals)
                else:
                    self.unresolved.append(f"{self.path.name}:{n.lineno} {ast.unparse(node)}")


def tool_metrics() -> tuple[set[str], list[str]]:
    found: set[str] = set()
    unresolved: list[str] = []
    for p in sorted(TOOLS.glob("*.py")):
        s = _Scan(p)
        s.run()
        found |= s.found
        unresolved += s.unresolved
    return found, unresolved


if __name__ == "__main__":
    f, u = tool_metrics()
    for x in sorted(f):
        print(x)
    print("UNRESOLVED", u)
