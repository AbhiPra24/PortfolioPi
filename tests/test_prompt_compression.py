import re
from unittest.mock import AsyncMock, patch
import pytest

from core.ai_analyst import (
    build_compressed_telemetry,
    build_compressed_prompt,
    generate_ai_analysis,
    generate_batch_ai_analysis,
)


def test_build_compressed_telemetry_exact():
    # Example from spec: BHEL|2|7/7|84|-0.5%|+52.2%|2|LT|EXIT_STOP
    # Stop: (248.75 - 250.0) / 250.0 * 100 = -0.5%
    metrics = {
        "price": 248.75,
        "stage": 2,
        "tt_score": 7,
        "rs_score": 84,
        "stop_loss": 250.0,
        "dist_days": 2,
        "action": "SELL / STOP SIP",
    }
    portfolio_ctx = {
        "pnl_pct": 52.2,
        "tax_term": "LT",
        "is_holding": True,
    }
    line = build_compressed_telemetry("BHEL", metrics, portfolio_ctx)
    assert line == "[AUDIT] BHEL|2|7/7|84|-0.5%|+52.2%|2|LT|EXIT_STOP"


def test_build_compressed_telemetry_defaults_and_deltas():
    # Test positive stop margin, short term tax default, accumulate action
    # cmp: 110, stop: 100 -> +10.0%
    metrics = {
        "current_price": 110.0,
        "stage": 2,
        "minervini_score": "6/7",
        "rs": 75.4,
        "stop": 100.0,
        "dist_days": 1,
        "action": "BUY MORE / CONTINUE SIP",
    }
    portfolio_ctx = {
        "pnl_pct": 5.0,
        "tax_category": "STCG",
    }
    line = build_compressed_telemetry("TCS", metrics, portfolio_ctx)
    assert line == "[AUDIT] TCS|2|6/7|75|+10.0%|+5.0%|1|ST|ACCUMULATE"


def test_prompt_token_savings_greater_than_60_percent():
    # Compare old verbose prompt size vs new compressed prompt size
    ticker = "BHEL"
    metrics = {
        "price": 248.75,
        "stage": 2,
        "tt_score": 7,
        "rs_score": 84,
        "vol_ratio": 1.4,
        "dist_days": 2,
        "stop_loss": 250.0,
        "rsi14": 42.1,
        "macd": 1.25,
        "action": "SELL / STOP SIP",
    }
    ctx = {
        "is_holding": True,
        "pnl_pct": 52.2,
        "tax_term": "LT",
    }

    # Canonical verbose prompt previously used in PortfolioPi
    verbose_prompt = f"""You are the Chief Quantitative Strategist for PortfolioPi.
Your mandate is to MAXIMIZE long-term portfolio growth while strictly protecting capital.

[STOCK TELEMETRY]
Symbol: {ticker}
Current Price: ₹{metrics.get('price')}
Weinstein Stage: Stage {metrics.get('stage')}
Minervini Trend Checklist: {metrics.get('tt_score')}/7 criteria passing
Relative Strength vs Nifty 50: {metrics.get('rs_score')}
Volume Ratio: {metrics.get('vol_ratio')}x (20-day average)
Institutional Distribution Days: {metrics.get('dist_days')}
Chandelier Trailing Stop: ₹{metrics.get('stop_loss')}
RSI(14): {metrics.get('rsi14')} | MACD: {metrics.get('macd')}
Algorithmic Verdict: {metrics.get('action')}

[PORTFOLIO CONTEXT]
Position: {'HELD' if ctx.get('is_holding') else 'WATCHLIST ONLY'}
Unrealized PnL: {ctx.get('pnl_pct'):+.1f}%

[INSTRUCTIONS]
Provide a crisp, professional 3-bullet assessment for Telegram:
• <b>Setup & Trend Structure</b>: Minervini criteria, moving average alignment, benchmark outperformance.
• <b>Capital Risk & Trailing Stop</b>: Downside distance to Chandelier Exit and distribution pressure.
• <b>High-Conviction Directive</b>: Exact execution directive (ACCUMULATE on dips, HOLD, TRIM, or CUT LOSS) to maximize compounding.
Keep formatting clean using Telegram HTML tags (<b>, <i>, <code>). Do not invent numbers not provided above.
"""

    compressed_line = build_compressed_telemetry(ticker, metrics, ctx)
    compressed_prompt = build_compressed_prompt([compressed_line])

    verbose_len = len(verbose_prompt)
    compressed_len = len(compressed_prompt)

    # Character and word/token approximation reductions
    verbose_words = len(verbose_prompt.split())
    compressed_words = len(compressed_prompt.split())

    token_savings_pct = (1.0 - (compressed_words / verbose_words)) * 100.0
    char_savings_pct = (1.0 - (compressed_len / verbose_len)) * 100.0

    print(f"\nVerbose Words: {verbose_words} | Compressed Words: {compressed_words} | Word Savings: {token_savings_pct:.1f}%")
    print(f"Verbose Chars: {verbose_len} | Compressed Chars: {compressed_len} | Char Savings: {char_savings_pct:.1f}%")

    # Assert > 60% reduction
    assert token_savings_pct > 60.0
    assert char_savings_pct > 60.0


def test_parse_output_grammar():
    # Verify strict causal output grammar:
    # TICKER | STG:... | STOP:... | ACT:...
    # followed by 1-2 concise bullet directives.
    sample_output = """BHEL | STG:2 | STOP:-0.5% | ACT:EXIT_STOP
- Price breached Chandelier Exit trailing stop (-0.5% margin); structural breakdown imminent.
- Execute full capital exit under Section 111A/LTCG rules to preserve locked-in compounding gains."""

    lines = sample_output.strip().splitlines()
    assert len(lines) >= 2

    header = lines[0]
    pattern = r"^([A-Z0-9]+)\s*\|\s*STG:([^\|]+)\|\s*STOP:([^\|]+)\|\s*ACT:(.+)$"
    m = re.match(pattern, header)
    assert m is not None, f"Header did not match expected grammar: {header}"

    ticker, stg, stop, act = m.groups()
    assert ticker.strip() == "BHEL"
    assert stg.strip() == "2"
    assert stop.strip() == "-0.5%"
    assert act.strip() == "EXIT_STOP"

    # Directives check
    directives = [line.strip() for line in lines[1:] if line.strip()]
    assert 1 <= len(directives) <= 2
    for d in directives:
        assert d.startswith("-") or d.startswith("•")


@pytest.mark.anyio
async def test_generate_ai_analysis_passes_parameters():
    mock_post = AsyncMock(return_value="INFY | STG:2 | STOP:+4.2% | ACT:HOLD\n- Uptrend intact.")
    with patch("core.ai_analyst._post_ollama", mock_post):
        res = await generate_ai_analysis("INFY", {"price": 1800, "stop_loss": 1727, "stage": 2})
        assert res is not None
        assert "INFY | STG:2" in res

        # Verify options passed with temperature 0.0 and num_predict 128
        args, kwargs = mock_post.call_args
        passed_options = kwargs.get("options")
        assert passed_options is not None
        assert passed_options.get("temperature") == 0.0
        assert passed_options.get("num_predict") == 128


@pytest.mark.anyio
async def test_micro_batch_max_2_tickers():
    mock_post = AsyncMock(side_effect=[
        "BHEL | STG:2 | STOP:-0.5% | ACT:EXIT_STOP\n- Stop breached.\nTCS | STG:2 | STOP:+5.0% | ACT:HOLD\n- Steady holding.",
        "INFY | STG:1 | STOP:+2.0% | ACT:ACCUMULATE\n- Base breakout.",
    ])
    with patch("core.ai_analyst._post_ollama", mock_post):
        items = [
            {"ticker": "BHEL", "metrics": {"price": 248.75, "stop_loss": 250.0, "stage": 2}},
            {"ticker": "TCS", "metrics": {"price": 3500.0, "stop_loss": 3333.0, "stage": 2}},
            {"ticker": "INFY", "metrics": {"price": 1800.0, "stop_loss": 1764.0, "stage": 1}},
        ]
        results = await generate_batch_ai_analysis(items, batch_size=2)
        assert len(results) == 3
        assert "BHEL" in results and "BHEL" in results["BHEL"]
        assert "TCS" in results and "TCS" in results["TCS"]
        assert "INFY" in results and "INFY" in results["INFY"]

        # 3 items with batch_size=2 should result in exactly 2 Ollama calls
        assert mock_post.call_count == 2
