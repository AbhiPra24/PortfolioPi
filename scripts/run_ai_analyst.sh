#!/usr/bin/env bash
# ==============================================================================
# PortfolioPi AI Analyst & Screener Dispatcher (Local Mac or Remote RasPi)
#
# Runs any of the Ollama-powered portfolio audit scripts against:
#   - Local SQLite database & local Ollama (:11434) with agy fallback
#   - Remote Raspberry Pi (via SSH: 192.168.1.31 / 'raspi')
#   - Direct RasPi Ollama (:11434)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    local exit_code="${1:-1}"
    echo "Usage: $0 [command] [target] [extra_args...]"
    echo ""
    echo "Commands:"
    echo "  state       - Run complete portfolio health & stage audit (analyse_state.py)"
    echo "  signals     - Run momentum & Chandelier exit stop-loss audit (analyse_signals.py)"
    echo "  cashflow    - Run dividend yield & SIP pacing review (analyse_cashflow.py)"
    echo "  ask         - Open interactive natural language assistant (ask_portfolio.py)"
    echo "  scan        - Run Screener.in market scraper & price indexer (scan_screener.py)"
    echo "  rebalance   - Run daily portfolio rebalance engine (daily_portfolio_rebalancer.py)"
    echo ""
    echo "Targets:"
    echo "  local        - Run locally using local SQLite & local Ollama (:11434), falling back to agy (default)"
    echo "  raspi        - Execute directly on remote Raspberry Pi via SSH"
    echo "  raspi-ollama - Run locally but send inference to RasPi Ollama (http://192.168.1.31:11434)"
    echo ""
    echo "Examples:"
    echo "  ./scripts/run_ai_analyst.sh state"
    echo "  ./scripts/run_ai_analyst.sh signals"
    echo "  ./scripts/run_ai_analyst.sh rebalance"
    echo "  ./scripts/run_ai_analyst.sh scan local --screen golden-crossover --live"
    echo "  ./scripts/run_ai_analyst.sh scan local --screen fii-buying --ai"
    echo "  ./scripts/run_ai_analyst.sh state raspi"
    exit "$exit_code"
}

if [[ "$1" == "-h" || "$1" == "--help" || "$1" == "help" ]]; then
    usage 0
fi

COMMAND="state"
if [[ $# -ge 1 && ! "$1" =~ ^- ]]; then
    COMMAND="$1"
    shift
fi

TARGET="local"
if [[ $# -ge 1 && ! "$1" =~ ^- ]]; then
    TARGET="$1"
    shift
fi

case "$TARGET" in
    local)
        OLLAMA_URL="${OLLAMA_BASE_URL:-http://192.168.1.39:11434}"
        FALLBACK_URL="${OLLAMA_FALLBACK_URL:-http://127.0.0.1:11434}"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;
    raspi-ollama)
        OLLAMA_URL="http://127.0.0.1:11434"
        FALLBACK_URL="http://127.0.0.1:11434"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;
    mac-ollama)
        OLLAMA_URL="http://192.168.1.39:11434"
        FALLBACK_URL="http://127.0.0.1:11434"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;

    raspi)
        echo "[*] Forwarding execution to Raspberry Pi (ssh raspi)..."
        ssh raspi "cd /data/Work/PortfolioPi && git pull origin main >/dev/null 2>&1 && bash scripts/run_ai_analyst.sh $COMMAND local"
        exit 0
        ;;
    *)
        usage 1
        ;;
esac

case "$COMMAND" in
    state)
        python3 "$SCRIPT_DIR/analyse_state.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    signals)
        python3 "$SCRIPT_DIR/analyse_signals.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    cashflow)
        python3 "$SCRIPT_DIR/analyse_cashflow.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    ask)
        python3 "$SCRIPT_DIR/ask_portfolio.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    scan)
        python3 "$SCRIPT_DIR/scan_screener.py" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    rebalance)
        python3 "$SCRIPT_DIR/daily_portfolio_rebalancer.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" --fallback-url "$FALLBACK_URL" "$@"
        ;;
    *)
        usage 1
        ;;
esac

