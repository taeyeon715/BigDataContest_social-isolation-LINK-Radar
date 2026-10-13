from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
HOLIDAYS = [pd.Timestamp("2025-08-15")]


def card_daily_small() -> pd.DataFrame:
    """날짜 × (도시, 코호트) 가짜 일별 결제건수."""
    d = pd.read_csv(HERE / "card_daily_small.csv", parse_dates=["date"])
    return d.pivot_table(index="date", columns=["city", "cohort"], values="USE_CNT").astype(float)
