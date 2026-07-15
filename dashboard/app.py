import os

import streamlit as st
from db import execute_db

st.set_page_config(page_title="PortfolioPi", layout="wide")

if "apisession" in st.query_params:
    new_token = st.query_params["apisession"]
    execute_db("DELETE FROM session_tokens")
    execute_db("INSERT INTO session_tokens (token) VALUES (%s)", (new_token,))
    st.success("Successfully updated ICICI Breeze API Token from redirect!")
    st.query_params.clear()

password = os.getenv("DASHBOARD_PASSWORD")
if not password:
    raise ValueError("DASHBOARD_PASSWORD environment variable is not set")

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("Login")
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
        return False
    return True

if check_password():
    st.title("PortfolioPi")

    pages = {
        "Menu": [
            st.Page("views/0_Portfolio.py", title="Portfolio"),
            st.Page("views/1_Watchlist_Signals.py", title="Watchlist & Signals"),
            st.Page("views/2_Historical_Charts.py", title="Historical Charts"),
            st.Page("views/3_Session_Status.py", title="Session Status"),
            st.Page("views/4_Action_Plan.py", title="Action Plan"),
            st.Page("views/5_Portfolio_Analyser.py", title="Portfolio Analyser"),
        ]
    }
    pg = st.navigation(pages)
    pg.run()
