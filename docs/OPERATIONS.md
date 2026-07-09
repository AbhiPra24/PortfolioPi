# Operations Runbook

## Daily Session Refresh
The ICICI Breeze API token expires daily, exact time undocumented by ICICI. You must authenticate to generate a new token.
See the [README](../README.md#daily-session-flow) for the step-by-step Daily Session Flow (browser login -> Telegram `/refresh_session` command).

## Manual Historical Backfills
To deeply backfill history (e.g. 3 years) for all tickers currently in your holdings and watchlist, run the manual script:

```bash
docker exec portfoliopi-bot python -m scripts.backfill_history
```

This will fetch 1095 calendar days in 90-day chunks, respecting rate limits.

**Verification**:
To ensure the backfill succeeded without silent API truncation, check the actual depth landed:
```bash
docker exec portfoliopi-bot sqlite3 data/portfoliopi.db "SELECT stock_code, COUNT(*) as days_cached, MIN(date) as earliest, MAX(date) as latest FROM ohlcv_cache GROUP BY stock_code ORDER BY days_cached ASC;"
```
If most tickers show ~700+ days cached (roughly 3 years minus non-trading days), it worked.

## Restoring from Backups
A nightly backup of the SQLite database is taken at 11:30 PM IST. Backups are stored in `data/backups/` and retained for 7 days.
The backups are standard SQLite files created via `VACUUM INTO`.

To restore:
1. Stop the containers: `docker compose down`
2. Backup the current broken DB: `mv data/portfoliopi.db data/portfoliopi.db.broken`
3. Copy the desired backup: `cp data/backups/portfoliopi_YYYYMMDD.db data/portfoliopi.db`
4. Restart the containers: `docker compose up -d`

## Data Ingestion & Refresh Architecture

### Two-Pipeline Model
PortfolioPi splits its data operations into two pipelines to balance accuracy and uptime:
1. **Breeze Ground-Truth Sync (`run_breeze_sync`)**:
   - **Trigger**: Daily cron (8:00 AM IST) and manual "Refresh Now" dashboard requests.
   - **Uptime dependence**: Requires a fresh Breeze session token (updated daily).
   - **Scope**: Fetches Demat/Portfolio holdings, updates `quantity` and `average_price`, and performs an incremental daily OHLCV backfill from Breeze.
2. **Market Data Refresh (`run_market_data_refresh`)**:
   - **Trigger**: Every 60 minutes, 24/7.
   - **Uptime dependence**: Unauthenticated yfinance API. Runs even if Breeze session token is expired or missing.
   - **Scope**: Fetches recent quotes and daily candles from `yfinance`, updates `current_price` in `holdings_snapshot`, appends new daily OHLCV rows to `ohlcv_cache`, runs technical indicator generation, and computes buy/sell verdicts.

### Watchlist Auto-Backfill
When a new ticker is added to the watchlist (via dashboard or Telegram):
1. A backfill request is queued in the `backfill_requests` table.
2. The background scheduler checks this queue every 20 seconds.
3. It fetches 3 years (1,095 days) of daily OHLCV candles via `yfinance` to seed the cache immediately.
4. Screener signals and Stage verdicts become active immediately instead of waiting for a daily sync cycle.

## Troubleshooting

- **Bot container shows unhealthy**: The Docker healthcheck ensures a data refresh pipeline has succeeded in the last 24 hours by checking `job_heartbeats`. Check the "Session Status" dashboard page or run `/status` in Telegram. If no jobs ran, your session token may be expired.
- **Session token expired**: Follow the Daily Session Refresh flow (Telegram `/refresh_session` command).
- **Dashboard shows stale data**: The dashboard relies on background jobs. If you click a "Refresh" button on the dashboard, it queues a request in `refresh_requests`. The bot container polls this table every 60 seconds. If data isn't updating, check the bot container logs (`docker logs portfoliopi-bot`) to confirm the polling job is running and processing the request.
