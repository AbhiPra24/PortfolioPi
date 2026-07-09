import pandas as pd
import plotly.express as px
import streamlit as st
from db import query_db

import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from algo.portfolio_analytics import compute_concentration, compute_beta, rollup_stage_health

st.header("Portfolio Analyser")

data = query_db("SELECT stock_code, quantity, average_price, current_price FROM holdings_snapshot")
if not data:
    st.info("No holdings data yet.")
else:
    df = pd.DataFrame(data, columns=['Stock Code', 'Quantity', 'Avg Price', 'Current Price'])
    df['Current Value'] = df['Quantity'] * df['Current Price']

    st.subheader("Concentration Risk")
    conc = compute_concentration(df, top_n=5)
    st.metric("Top 5 holdings % of portfolio", f"{conc['top_n_pct']:.1f}%")
    st.dataframe(pd.DataFrame(conc['top_holdings']), use_container_width=True)

    st.subheader("Stage Health (Weinstein)")
    stage_rows = query_db("SELECT stage FROM stage_history WHERE date = (SELECT MAX(date) FROM stage_history)")
    if stage_rows:
        counts = rollup_stage_health(stage_rows)
        st.plotly_chart(px.bar(x=[f"Stage {k}" for k in counts], y=list(counts.values())), use_container_width=True)
    else:
        st.info("No stage data yet.")

    st.subheader("Correlation / Beta vs Nifty 50")
    nifty = query_db("SELECT date, close FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date")
    if nifty:
        nifty_df = pd.DataFrame(nifty, columns=['date', 'close']).set_index('date')['close']
        betas = []
        for stock in df['Stock Code']:
            rows = query_db("SELECT date, close FROM ohlcv_cache WHERE stock_code = ? ORDER BY date", (stock,))
            if rows and len(rows) > 30:
                stock_series = pd.DataFrame(rows, columns=['date', 'close']).set_index('date')['close']
                aligned = pd.concat([stock_series, nifty_df], axis=1, join='inner')
                if len(aligned) > 30:
                    beta = compute_beta(aligned.iloc[:, 0], aligned.iloc[:, 1])
                    betas.append({"Stock": stock, "Beta vs Nifty": round(beta, 2)})
        if betas:
            st.dataframe(pd.DataFrame(betas), use_container_width=True)
    else:
        st.info("Nifty OHLCV not cached yet.")
