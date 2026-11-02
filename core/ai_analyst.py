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

async def _execute_tiered_generation(prompt: str, timeout_primary: float = 12.0, timeout_fallback: float = 45.0) -> str | None:
    """
    Tiered AI Inference:
      1. Primary: Mac Mini GPU (http://192.168.1.39:11434, qwen2.5:7b)
      2. Fallback: Local Pi Ollama (http://host.docker.internal:11434 or 127.0.0.1:11434, pralabs:edge with [TASK: PORTFOLIO_AUDITOR])
    """
    primary_url = getattr(settings, 'ollama_base_url', 'http://192.168.1.39:11434').rstrip('/')
    primary_model = getattr(settings, 'ollama_model', 'qwen2.5:7b')
    fallback_url = getattr(settings, 'ollama_fallback_url', 'http://host.docker.internal:11434').rstrip('/')
    fallback_model = getattr(settings, 'ollama_fallback_model', 'pralabs:edge')

    # 1. Attempt Primary
    res = await _post_ollama(primary_url, primary_model, prompt, timeout_seconds=timeout_primary)
    if res:
        return res

    logger.warning(
        f"[Tier 1 AI Failover] Primary Ollama ({primary_model} @ {primary_url}) failed. "
        f"Switching to Tier 2 ({fallback_model} @ {fallback_url})..."
    )

    # 2. Attempt Fallback with [TASK: PORTFOLIO_AUDITOR] token prepended
    fb_prompt = prompt
    if "[TASK: PORTFOLIO_AUDITOR]" not in fb_prompt:
        fb_prompt = f"[TASK: PORTFOLIO_AUDITOR]\n{fb_prompt}"

    res = await _post_ollama(fallback_url, fallback_model, fb_prompt, timeout_seconds=timeout_fallback)
    if res:
        return res

    # If fallback_url used host.docker.internal and failed, try 127.0.0.1 directly if running outside docker
    if "host.docker.internal" in fallback_url:
        direct_url = fallback_url.replace("host.docker.internal", "127.0.0.1")
        res = await _post_ollama(direct_url, fallback_model, fb_prompt, timeout_seconds=timeout_fallback)
        if res:
            return res

    logger.error("[Tier 2 AI Failure] All Ollama inference endpoints failed.")
    return None

async def generate_ai_analysis(ticker: str, metrics: dict, portfolio_ctx: dict | None = None) -> str | None:
    """
    Produces an institutional-grade technical diagnosis and capital allocation recommendation.
    Tiers to Mac mini GPU first, falling back to local edge Ollama with [TASK: PORTFOLIO_AUDITOR].
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
    return await _execute_tiered_generation(prompt, timeout_primary=12.0, timeout_fallback=45.0)

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
    return await _execute_tiered_generation(prompt, timeout_primary=15.0, timeout_fallback=45.0)
