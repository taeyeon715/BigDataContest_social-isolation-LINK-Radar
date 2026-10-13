"""EDA 리포트(markdown) 작성 도우미.

리포트 하나 = reports/<폴더>/ 하나
  README.md   리포트 본문 (GitHub에서 폴더를 열면 바로 보임)
  figs/       그림
  *.csv       결과 표
저장할 때 reports/README.md(목차 + 그림 모음)도 다시 만든다.
"""

from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.plot import save

REPORTS = ROOT / "reports"


def report_dir(folder: str) -> Path:
    d = REPORTS / folder
    d.mkdir(parents=True, exist_ok=True)
    return d


class Report:
    def __init__(self, folder: str, title: str):
        self.dir = report_dir(folder)  # 예: reports/eda3_카드기초
        self.fig_dir = self.dir / "figs"
        self.lines = [f"# {title}\n", "[← 전체 리포트 목록](../README.md)\n"]

    def h(self, text: str):
        self.lines.append(f"\n## {text}\n")

    def h3(self, text: str):
        self.lines.append(f"\n### {text}\n")

    def p(self, text: str = ""):
        self.lines.append(text)

    def fig(self, fig, file: str, caption: str):
        save(fig, self.fig_dir / file)
        self.lines.append(f"\n![{caption}](figs/{file})\n")

    def table(self, df: pd.DataFrame, fmt: dict | None = None, index: bool = True):
        fmt = fmt or {}
        cols = list(df.columns)
        head = ([df.index.name or ""] if index else []) + cols
        self.lines.append("")
        self.lines.append("| " + " | ".join(map(str, head)) + " |")
        self.lines.append("|" + "---|" * len(head))
        for i, r in df.iterrows():
            cells = [fmt.get(c, "{}").format(r[c]) for c in cols]
            self.lines.append("| " + " | ".join(([str(i)] if index else []) + cells) + " |")
        self.lines.append("")

    def save(self) -> Path:
        out = self.dir / "README.md"
        out.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        from bigcontest.gallery import build

        build()
        return out
