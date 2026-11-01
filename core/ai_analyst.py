import json
import logging
import aiohttp
from app_config import settings

logger = logging.getLogger(__name__)

async def generate_ai_analysis(ticker: str, metrics: dict, portfolio_ctx: dict | None = None) -> str | None:
    """
    Calls local Ollama server running on the Mac mini to produce an institutional-grade
    technical diagnosis and capital allocation recommendation.
    Falls back gracefully to None if Mac mini is offline or unreachable.
    """
    base_url = getattr(settings, 'ollama_base_url', 'http://192.168.1.39:11434').rstrip('/')
    model_name = getattr(settings, 'ollama_model', 'qwen2.5:7b')

    ctx = portfolio_ctx or {}
    is_holding = ctx.get('is_holding', False)
    pnl_pct = ctx.get('pnl_pct', 0.0)

    prompt = f"""You are the Chief Quantitative Strategist for PortfolioPi.
Your mandate is to MAXIMIZE long-term portfolio growth while strictly protecting capital.

[STOCK TELEMETRY]
Symbol: {ticker}
Current Price: ₹{metrics.get('price', metrics.get('current_price', 0))}
Weinstein Stage: Stage {metrics.get('stage', 'N/A')}
Minervini Trend Checklist: {metrics.get('tt_score', 'N/A')}/7 criteria passing
Relative Strength vs Nifty 50: {metrics.get('rs_score', 'N/A')}
Volume Ratio: {metrics.get('vol_ratio', metrics.get('volume_ratio_20d', 'N/A'))}x (20-day average)
Institutional Distribution Days: {metrics.get('dist_days', 0)}
Chandelier Trailing Stop: ₹{metrics.get('stop_loss', 'N/A')}
RSI(14): {metrics.get('rsi14', 'N/A')} | MACD: {metrics.get('macd', metrics.get('macd_line', 'N/A'))}
Algorithmic Verdict: {metrics.get('action', 'N/A')}

[PORTFOLIO CONTEXT]
Position: {'HELD' if is_holding else 'WATCHLIST ONLY'}
Unrealized PnL: {pnl_pct:+.1f}%

[INSTRUCTIONS]
Provide a crisp, professional 3-bullet assessment for Telegram:
• <b>Setup & Trend Structure</b>: Minervini criteria, moving average alignment, benchmark outperformance.
• <b>Capital Risk & Trailing Stop</b>: Downside distance to Chandelier Exit and distribution pressure.
• <b>High-Conviction Directive</b>: Exact execution directive (ACCUMULATE on dips, HOLD, TRIM, or CUT LOSS) to maximize compounding.
Keep formatting clean using Telegram HTML tags (<b>, <i>, <code>). Do not invent numbers not provided above.
"""

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2
        }
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(f"{base_url}/api/generate", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("response", "").strip()
                logger.warning(f"Ollama server returned HTTP {resp.status}")
                return None
    except Exception as e:
        logger.info(f"Ollama server at {base_url} unreachable: {e}. Falling back gracefully.")
        return None
