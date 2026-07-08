import sqlite3
import streamlit as st
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "portfoliopi.db")

@st.cache_resource
def get_db_connection():
    # URI mode=ro requires uri=True
    uri = f"file:{DB_PATH}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)

def query_db(query, args=(), one=False):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, args)
        rv = cur.fetchall()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.OperationalError as e:
        st.error(f"DB Error: {e}")
        return None
