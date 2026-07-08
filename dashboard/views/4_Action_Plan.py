import os
import sys
import pandas as pd
import streamlit as st
from db import query_db

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

st.header("Portfolio Action Plan")

st.markdown("Automated classification of your holdings and watchlist based on Stage Analysis, Relative Strength, Distribution Days, and Chandelier Exits.")

data = query_db("SELECT stock_code, action, rationale, timestamp FROM stock_actions ORDER BY action")

if data:
    df = pd.DataFrame(data, columns=['Stock Code', 'Action', 'Rationale', 'Updated At'])
    
    actions = sorted(df['Action'].unique())
    for action in actions:
        st.subheader(f"🏷️ {action}")
        action_df = df[df['Action'] == action]
        st.dataframe(action_df[['Stock Code', 'Rationale', 'Updated At']], use_container_width=True)
else:
    st.info("No action plan data available yet. Please wait for the daily data refresh pipeline to run.")
