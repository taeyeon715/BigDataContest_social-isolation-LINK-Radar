"""README용 연휴 그래프. 공개 리포트 표의 반올림값만 읽어 그린다.

실행: uv run python scripts/readme_assets.py
분석을 다시 계산하거나 원자료를 읽지 않는다.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reports/eda5_인사이트재현/README.md"
OUT = ROOT / "docs/images/readme/holiday_clinic.png"


def main():
    table = SOURCE.read_text(encoding="utf-8").split("| 연령 | 깊이: 병원·의원 강남", 1)[1].split("\n\n", 1)[0]
    rows = [line.split("|")[1:-1] for line in table.splitlines() if line.startswith("| ")]
    rows = [[cell.strip() for cell in row] for row in rows]
    ages = [row[0].replace("80대+", "80대 이상") for row in rows]
    assert ages == ["20대", "30대", "40대", "50대", "60대", "70대", "80대 이상"]
    gn = [float(row[1].removesuffix("%")) for row in rows]
    cc = [float(row[2].removesuffix("%")) for row in rows]
    assert gn[-1] == 17 and cc[-1] == 10

    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        family = font_manager.FontProperties(fname=str(font)).get_name()
    else:
        family = next((name for name in ("Noto Sans CJK KR", "Noto Sans CJK JP", "NanumGothic", "AppleGothic")
                       if any(item.name == name for item in font_manager.fontManager.ttflist)), None)
        if family is None:
            raise RuntimeError("한글 글꼴(Malgun Gothic, Noto Sans CJK, NanumGothic, AppleGothic 중 하나)이 필요합니다.")
    plt.rcParams.update({"font.family": family, "axes.unicode_minus": False, "font.size": 12})
    fig, ax = plt.subplots(figsize=(10, 4.7), dpi=180)
    fig.subplots_adjust(left=0.09, right=0.97, top=0.75, bottom=0.24)
    fig.text(0.09, 0.94, "추석 연휴 의료 결제는 고령층에서 더 낮았습니다", fontsize=19, weight="bold", color="#302d25")
    fig.text(0.09, 0.86, "2025.10.03~10.09 · 병원·의원 · 평소 같은 요일 대비 결제 비율", fontsize=12, color="#5f5b50")
    ax.plot(ages, gn, marker="o", markersize=7, linewidth=2.5, color="#315d78", label="강남 가맹점 · 서울 거주 고객")
    ax.plot(ages, cc, marker="s", markersize=6, linewidth=2.5, linestyle="--", color="#a15b32", label="춘천 가맹점 · 강원 거주 고객")
    ax.set_ylim(0, 40)
    ax.set_yticks([0, 10, 20, 30, 40], ["0%", "10%", "20%", "30%", "40%"])
    ax.grid(axis="y", color="#e5e1d7", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color("#b6afa0")
    ax.tick_params(axis="both", length=0, pad=9)
    ax.legend(frameon=False, loc="upper right", fontsize=10)
    for values, color, offset in ((gn, "#315d78", 2), (cc, "#a15b32", -4)):
        ax.annotate(f"{values[-1]:.0f}%", (6, values[-1]), xytext=(8, offset), textcoords="offset points", color=color, weight="bold")
    fig.text(0.09, 0.10, "평소: 9/1~9/28·10/20~11/9의 비공휴일. 출처: EDA-5 3절 표의 반올림값.", fontsize=10, color="#5f5b50")
    fig.text(0.09, 0.04, "카드 결제의 집계 비교이며, 고립 여부·실제 진료 단절 인원을 뜻하지 않습니다.", fontsize=10, color="#5f5b50")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor="white")
    plt.close(fig)
    print(f"saved {OUT.relative_to(ROOT)} (7 age groups, report table)")


if __name__ == "__main__":
    main()
