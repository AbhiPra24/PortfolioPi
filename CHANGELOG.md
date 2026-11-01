# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased] - 2026-11-01
### Architecture & Infrastructure
- **Sunset Supabase in favor of Local SQLite**:
  - Removed remote Postgres dependencies (`asyncpg`, `psycopg2`).
  - Switched bot, scheduler, and algo modules to asynchronous SQLite via `aiosqlite` in WAL mode (`data/portfoliopi.db`).
  - Switched Streamlit dashboard database connections to standard Python `sqlite3`.
  - Migrated and verified 68,913 historical records across all 17 tables onto Raspberry Pi host storage.
- **Ollama AI Analyst Integration**:
  - Integrated local Ollama server running on Apple Silicon Mac mini (`192.168.1.39:11434`) running `qwen2.5:7b`.
  - Created `core/ai_analyst.py` incorporating prompt templates for Weinstein Stages, Minervini Trend Templates, Relative Strength vs. Nifty 50, and Chandelier Trailing Stops.
  - Implemented Opportunity Rotation Matrix (`generate_opportunity_rotation_analysis`) to identify dragging positions and recommend rotations into Stage 2 breakout leaders.
  - Upgraded `/analyse <TICKER>` Telegram command to enrich algorithmic signals with AI commentary and risk evaluation.
  - Added non-blocking timeout and automatic fallback to algorithmic text if the AI inference host is offline.
- **Container & Deployment**:
  - Rebuilt and verified `portfoliopi-bot` Docker container on Raspberry Pi.
  - Updated healthcheck to verify SQLite WAL mode integrity (`PRAGMA quick_check`).
  - Strengthened `.gitignore` to prevent any local database or backup files (`*.db`, `backups/`, `data/*.db`) from ever entering version control.

- **Feature**: Added deep manual historical backfill script (`scripts/backfill_history.py`) to fetch 3 years of OHLCV data.
- **Fix**: Fixed dashboard refresh button via a shared `refresh_requests` polling table.
- **Feature**: Added nightly SQLite backup job (`core/scheduler.py`).
- **Feature**: Added 9-point structural action classification engine (Stage Analysis, Trend Template, Relative Strength).
- **Fix**: Fixed `_NIFTY50` ticker mapping (was requesting a nonexistent "NIFTY50" symbol instead of "NIFTY").
- **Fix**: Corrected `current_price` field to `current_market_price` in API mappings (matching the actual Breeze API response shape).
- **Fix**: Fixed holdings quantity double-counting (demat + portfolio holdings were being summed instead of deduplicated via max).
- **Fix**: Renamed `config.py` to `app_config.py` (was shadowing an internal import in `breeze_connect`).
