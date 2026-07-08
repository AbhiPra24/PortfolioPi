# PortfolioPi

A Streamlit dashboard, algorithmic screener, and Telegram bot for your ICICI Direct stock portfolio. **Read-only by design** — PortfolioPi strictly analyzes data and will never place, modify, or cancel orders.

## Architecture

```text
    +-------------------------------------------------+
    |                  Raspberry Pi                   |
    |                                                 |
    |  +-------------+      +----------------------+  |
    |  | Dashboard   |      | Bot                  |  |
    |  | (Streamlit) |      | (Python Telegram Bot)|  |
    |  | Port: 8653  |      | + APScheduler        |  |
    |  +------+------+      +----------+-----------+  |
    |         |                        |              |
    |         | (read-only)            | (read/write) |
    |  +------+------------------------+-----------+  |
    |  |               SQLite (WAL)                |  |
    |  +-----------------------+-------------------+  |
    |                          |                      |
    |                +---------+----------+           |
    |                |    core/breeze     |           |
    |                +---------+----------+           |
    +--------------------------|----------------------+
                               | (Internet)
                    +----------+-----------+
                    | ICICI Breeze Connect |
                    +----------------------+
```

## Setup & Deployment

1. **Clone the repo**
   ```bash
   git clone https://github.com/AbhiPra24/PortfolioPi.git
   cd PortfolioPi
   ```

2. **Environment Variables**
   Copy the example environment file and fill in your details:
   ```bash
   cp .env.example .env
   # Edit .env with your favorite editor
   ```
   **Important:** `TELEGRAM_OWNER_IDS` should be your numeric Telegram User ID. `DASHBOARD_PASSWORD` is required for dashboard access.

3. **Deploy with Docker**
   Start the services in detached mode:
   ```bash
   docker compose up --build -d
   ```
   *To persist on reboot on a Raspberry Pi, the containers are set to `restart: unless-stopped`. Ensure the Docker daemon is enabled in systemd (`sudo systemctl enable docker`).*

## First-Run Session Flow

ICICI Breeze requires a daily manual login to generate a session token:
1. Visit `https://api.icicidirect.com/apiuser/login?api_key=YOUR_API_KEY` in your browser.
2. Log in and copy the `apisession` token from the resulting URL.
3. Send this to the bot on Telegram: `/refresh_session <TOKEN>`

## Bot Commands

- `/start` - Welcome message and command list.
- `/portfolio` - View your current portfolio P&L summary.
- `/signals` - View the top algorithmic signals from your holdings and watchlist.
- `/watchlist add <TICKER>` - Add a stock to your watchlist.
- `/watchlist remove <TICKER>` - Remove a stock.
- `/watchlist list` - View current watchlist.
- `/price <TICKER>` - Get a live quote.
- `/funds` - View available funds/margin.
- `/status` - Check session token freshness and system health.
- `/refresh_session <TOKEN>` - Update the daily Breeze API session token.
