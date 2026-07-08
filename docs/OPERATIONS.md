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

## Troubleshooting

- **Bot container shows unhealthy**: The Docker healthcheck ensures a data refresh pipeline has succeeded in the last 24 hours by checking `job_heartbeats`. Check the "Session Status" dashboard page or run `/status` in Telegram. If no jobs ran, your session token may be expired.
- **Session token expired**: Follow the Daily Session Refresh flow (Telegram `/refresh_session` command).
- **Dashboard shows stale data**: The dashboard relies on background jobs. If you click a "Refresh" button on the dashboard, it queues a request in `refresh_requests`. The bot container polls this table every 60 seconds. If data isn't updating, check the bot container logs (`docker logs portfoliopi-bot`) to confirm the polling job is running and processing the request.
