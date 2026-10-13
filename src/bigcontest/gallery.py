"""reports/README.md (리포트 목록 + 그림 모음)를 만든다.

실행: uv run python -m bigcontest.gallery   (EDA 스크립트를 돌리면 자동으로 실행됨)
"""

import re

from bigcontest.config import ROOT

REPORTS = ROOT / "reports"
IMG = re.compile(r"!\[([^\]]*)\]\((figs/[^)]+)\)")


def _sections(text: str):
    """(절 제목, 그림 설명, 그림 경로) 목록."""
    out, section = [], ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        for cap, path in IMG.findall(line):
            out.append((section, cap, path))
    return out


def build():
    folders = sorted(d for d in REPORTS.iterdir() if d.is_dir() and (d / "README.md").exists())
    lines = [
        "# 분석 리포트",
        "",
        "각 폴더의 `README.md`가 리포트 본문입니다. 아래 그림을 누르면 그 리포트로 이동합니다.",
        "",
        "이 파일은 리포트 스크립트(`notebooks/`)를 실행하면 자동으로 다시 만들어집니다(`src/bigcontest/gallery.py`). 이름 앞머리: `eda*` 탐색, `m*` 모델, `r*` 규칙, `w*` 자원, `e*` 평가.",
        "",
        "## 목록",
        "",
        "| 리포트 | 그림 수 |",
        "|---|---:|",
    ]
    parsed = {}
    for d in folders:
        text = (d / "README.md").read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        figs = _sections(text)
        parsed[d.name] = (title, figs)
        lines.append(f"| [{title}]({d.name}/README.md) | {len(figs)} |")
    lines += ["", "## 그림 모음"]
    for name, (title, figs) in parsed.items():
        if not figs:
            continue
        lines += ["", f"### [{title}]({name}/README.md)", ""]
        lines += ["| | |", "|---|---|"]
        cells = [
            f'<a href="{name}/README.md"><img src="{name}/{path}" width="420"></a><br>{cap} <sub>({sec})</sub>'
            for sec, cap, path in figs
        ]
        for i in range(0, len(cells), 2):
            pair = cells[i:i + 2] + [""] * (2 - len(cells[i:i + 2]))
            lines.append("| " + " | ".join(pair) + " |")
    (REPORTS / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
