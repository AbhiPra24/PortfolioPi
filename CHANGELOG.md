# Changelog

All notable changes to this project will be documented in this file.

- **Feature**: Added deep manual historical backfill script (`scripts/backfill_history.py`) to fetch 3 years of OHLCV data.
- **Fix**: Fixed dashboard refresh button via a shared `refresh_requests` polling table.
- **Feature**: Added nightly SQLite backup job (`core/scheduler.py`).
- **Feature**: Added 9-point structural action classification engine (Stage Analysis, Trend Template, Relative Strength).
- **Fix**: Fixed `_NIFTY50` ticker mapping (was requesting a nonexistent "NIFTY50" symbol instead of "NIFTY").
- **Fix**: Corrected `current_price` field to `current_market_price` in API mappings (matching the actual Breeze API response shape).
- **Fix**: Fixed holdings quantity double-counting (demat + portfolio holdings were being summed instead of deduplicated via max).
- **Fix**: Renamed `config.py` to `app_config.py` (was shadowing an internal import in `breeze_connect`).
