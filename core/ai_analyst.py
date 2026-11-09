import json
import logging
import aiohttp
from app_config import settings

logger = logging.getLogger(__name__)

async def _post_ollama(url: str, model: str, prompt: str, timeout_seconds: float = 15.0) -> str | None:
    """Internal helper to call Ollama generate API asynchronously."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2
        }
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as session:
            async with session.post(f"{url.rstrip('/')}/api/generate", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("response", "").strip()
                logger.warning(f"Ollama server at {url} returned HTTP {resp.status}")
                return None
    except Exception as e:
        logger.info(f"Ollama call to {url} ({model}) failed or timed out: {e}")
        return None

async def _execute_generation(prompt: str, timeout_seconds: float = 45.0) -> str | None:
    """
    Local Raspberry Pi Ollama inference using local models (pralabs:latest / pralabs:edge).
    """
    url = getattr(settings, 'ollama_base_url', 'http://host.docker.internal:11434').rstrip('/')
    model = getattr(settings, 'ollama_model', 'pralabs:latest')

    # Prepend task token for fine-tuned edge models if applicable
    fb_prompt = prompt
    if "[TASK: PORTFOLIO_AUDITOR]" not in fb_prompt and "pralabs" in model:
        fb_prompt = f"[TASK: PORTFOLIO_AUDITOR]\n{fb_prompt}"

    res = await _post_ollama(url, model, fb_prompt, timeout_seconds=timeout_seconds)
    if res:
        return res

    # If running outside container (e.g. host.docker.internal unresolved), try localhost
    if "host.docker.internal" in url:
        direct_url = url.replace("host.docker.internal", "127.0.0.1")
        res = await _post_ollama(direct_url, model, fb_prompt, timeout_seconds=timeout_seconds)
        if res:
            return res

    logger.error(f"[Ollama Inference Failed] Could not generate response from {url} ({model}).")
    return None

async def generate_ai_analysis(ticker: str, metrics: dict, portfolio_ctx: dict | None = None) -> str | None:
    """
    Calls local Ollama server running on the Raspberry Pi to produce an institutional-grade
    technical diagnosis and capital allocation recommendation.
    Falls back gracefully to None if Ollama is offline or unreachable.
    """
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
    return await _execute_generation(prompt, timeout_seconds=45.0)

async def generate_opportunity_rotation_analysis(laggards: list, leaders: list) -> str | None:
    """
    Synthesizes an active rotation strategy pairing weak/broken holdings
    with top Stage 2 breakout setups to maximize portfolio CAGR.
    """
    laggard_text = "\n".join([
        f"- {x['ticker']}: Price ₹{x['price']}, Loss {x.get('pnl_pct', 0):+.1f}%, Stage {x.get('stage')}, Composite Score {x.get('score')}/100, Stop Loss ₹{x.get('stop_loss')}"
        for x in laggards
    ])
    leader_text = "\n".join([
        f"- {x['ticker']}: Price ₹{x['price']}, Stage {x.get('stage')}, RS {x.get('rs_score')}, TT {x.get('tt_score')}/7, Composite Score {x.get('score')}/100"
        for x in leaders
    ])

    prompt = f"""You are the Chief Quantitative Strategist for PortfolioPi.
Your mandate is to MAXIMIZE long-term portfolio growth by actively pruning capital dragging positions and reallocating into top-tier Stage 2 leaders.

[CURRENT CAPITAL DRAGGERS (LAGGARDS TO PRUNE)]
{laggard_text}

[TOP WATCHLIST BREAKOUTS (HIGH MOMENTUM LEADERS)]
{leader_text}

[INSTRUCTIONS]
Provide a high-conviction Telegram HTML briefing:
1. <b>Capital Rotations (Pairs)</b>: Pair each laggard with the best breakout leader to maximize risk/reward.
2. <b>Expected Compounding Impact</b>: Why this swap protects drawdowns and boosts forward portfolio projection.
3. <b>Immediate Execution Steps</b>: Clear directives for the investor today.
Use concise bullets and HTML tags (<b>, <code>). Do not invent false prices.
"""
    return await _execute_generation(prompt, timeout_seconds=45.0)
