"""tests/fixtures/card_daily_small.csv 를 만든다 (원본 카드 자료 없이 도는 테스트용 가짜 시계열).

실행: uv run python tests/fixtures/make_card_daily_small.py
두 도시 × 코호트 6개 × 120일. 요일 효과 + 도시 공통 잡음 + 코호트 잡음, 이상은 넣지 않음.
"""

from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
dates = pd.date_range("2025-07-01", periods=120)
dow = np.array([0.0, 0.02, 0.0, 0.03, 0.08, 0.15, -0.2])
rows = []
for city in ["A", "B"]:
    common = rng.normal(0, 0.03, len(dates))
    for k in range(6):
        level = np.log(800 + 150 * k)
        ly = level + dow[dates.dayofweek] + common + rng.normal(0, 0.05, len(dates))
        for d, v in zip(dates, np.round(np.expm1(ly))):
            rows.append((d.date(), city, f"c{k}", int(v)))
out = pd.DataFrame(rows, columns=["date", "city", "cohort", "USE_CNT"])
out.to_csv(Path(__file__).with_name("card_daily_small.csv"), index=False)
