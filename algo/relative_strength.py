import sqlite3
import pandas as pd
from app_config import settings

def compute_relative_strength(stock_df: pd.DataFrame, nifty_df: pd.DataFrame):
    if len(stock_df) < 252 or len(nifty_df) < 252:
        return 0.0

    stock_df = stock_df.sort_values('date').reset_index(drop=True)
    nifty_df = nifty_df.sort_values('date').reset_index(drop=True)

    # Align dates
    df = pd.merge(stock_df[['date', 'close']], nifty_df[['date', 'close']], on='date', suffixes=('_stock', '_idx'))
    if len(df) < 252:
        return 0.0
    
    current_stock = df['close_stock'].iloc[-1]
    current_idx = df['close_idx'].iloc[-1]

    def get_ret(series, period):
        if period >= len(series):
            return 0.0
        return (series.iloc[-1] - series.iloc[-1 - period]) / series.iloc[-1 - period]

    stock_63 = get_ret(df['close_stock'], 63)
    idx_63 = get_ret(df['close_idx'], 63)
    stock_126 = get_ret(df['close_stock'], 126)
    idx_126 = get_ret(df['close_idx'], 126)
    stock_189 = get_ret(df['close_stock'], 189)
    idx_189 = get_ret(df['close_idx'], 189)
    stock_252 = get_ret(df['close_stock'], 252)
    idx_252 = get_ret(df['close_idx'], 252)

    rs_score = 0.4 * (stock_63 - idx_63) + 0.2 * (stock_126 - idx_126) + 0.2 * (stock_189 - idx_189) + 0.2 * (stock_252 - idx_252)
    return rs_score
