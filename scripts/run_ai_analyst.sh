#!/usr/bin/env bash
# ==============================================================================
# PortfolioPi AI Analyst & Screener Dispatcher (Local Mac or Remote RasPi)
#
# Runs any of the Ollama-powered portfolio audit scripts against:
#   - Local SQLite database & local Ollama
#   - Remote Raspberry Pi (via SSH: 192.168.1.31 / 'raspi')
#   - OmniRoute / Remote Ollama (:20128 or :11434)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMAND="${1:-state}"
TARGET="${2:-local}"

usage() {
    echo "Usage: $0 [command] [target] [extra_args...]"
    echo ""
    echo "Commands:"
    echo "  state       - Run complete portfolio health & stage audit (analyse_state.py)"
    echo "  signals     - Run momentum & Chandelier exit stop-loss audit (analyse_signals.py)"
    echo "  cashflow    - Run dividend yield & SIP pacing review (analyse_cashflow.py)"
    echo "  ask         - Open interactive natural language assistant (ask_portfolio.py)"
    echo "  scan        - Run Screener.in market scraper & price indexer (scan_screener.py)"
    echo ""
    echo "Targets:"
    echo "  local       - Run locally using local SQLite & local Ollama (default)"
    echo "  raspi       - Execute directly on remote Raspberry Pi via SSH"
    echo "  omniroute   - Run locally but direct inference to RasPi OmniRoute (http://192.168.1.31:20128)"
    echo "  raspi-ollama- Run locally but direct inference to RasPi Ollama (http://192.168.1.31:11434)"
    echo ""
    echo "Examples:"
    echo "  ./scripts/run_ai_analyst.sh state"
    echo "  ./scripts/run_ai_analyst.sh signals"
    echo "  ./scripts/run_ai_analyst.sh scan local --screen golden-crossover --live"
    echo "  ./scripts/run_ai_analyst.sh scan local --screen fii-buying --ai"
    echo "  ./scripts/run_ai_analyst.sh state raspi"
    exit 1
}

case "$TARGET" in
    local)
        OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;
    omniroute)
        OLLAMA_URL="http://192.168.1.31:20128"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;
    raspi-ollama)
        OLLAMA_URL="http://192.168.1.31:11434"
        DB_PATH="${SQLITE_DB_PATH:-data/portfoliopi.db}"
        ;;
    raspi)
        echo "[*] Forwarding execution to Raspberry Pi (ssh raspi)..."
        ssh raspi "cd /data/Work/PortfolioPi && git pull origin main >/dev/null 2>&1 && python3 scripts/run_ai_analyst.sh $COMMAND local"
        exit 0
        ;;
    *)
        usage
        ;;
esac

shift 2 2>/dev/null || true

case "$COMMAND" in
    state)
        python3 "$SCRIPT_DIR/analyse_state.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" "$@"
        ;;
    signals)
        python3 "$SCRIPT_DIR/analyse_signals.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" "$@"
        ;;
    cashflow)
        python3 "$SCRIPT_DIR/analyse_cashflow.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" "$@"
        ;;
    ask)
        python3 "$SCRIPT_DIR/ask_portfolio.py" --db "$DB_PATH" --ollama-url "$OLLAMA_URL" "$@"
        ;;
    scan)
        python3 "$SCRIPT_DIR/scan_screener.py" --ollama-url "$OLLAMA_URL" "$@"
        ;;
    *)
        usage
        ;;
esac
