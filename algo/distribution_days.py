"""O'Neil distribution-day counting for institutional selling pressure."""

import pandas as pd


def count_distribution_days(df: pd.DataFrame) -> dict:
    if len(df) < 26:
        return {"dist_days": 0, "acc_days": 0}

    df = df.sort_values('date').reset_index(drop=True)
    recent_25 = df.tail(26).copy()
    recent_25['prev_close'] = recent_25['close'].shift(1)
    recent_25['prev_vol'] = recent_25['volume'].shift(1)

    recent_25 = recent_25.dropna()

    dist_days = len(recent_25[(recent_25['close'] < recent_25['prev_close']) & (recent_25['volume'] > recent_25['prev_vol'])])
    acc_days = len(recent_25[(recent_25['close'] > recent_25['prev_close']) & (recent_25['volume'] > recent_25['prev_vol'])])

    return {"dist_days": dist_days, "acc_days": acc_days}
