# PortfolioPi
A Streamlit dashboard, algo screener, and Telegram bot for ICICI Direct stock portfolio (read-only).

## Architecture
- Two Docker containers: `bot` and `dashboard`.
- Read-only Breeze API integration.
- SQLite database (WAL mode) shared between containers (read-only for dashboard).

## Daily Session Refresh
Breeze API requires a manual session token refresh daily:
1. Log in via browser: `https://api.icicidirect.com/apiuser/login?api_key=...`
2. Copy the resulting token.
3. Send `/refresh_session <token>` to the Telegram bot.
