import logging
from typing import Any, Dict, List, Optional

import aiohttp

from core.circuit_breaker import (
    CircuitState,
    DualEngineRouter,
)

logger = logging.getLogger(__name__)

# Global DualEngineRouter singleton instance
default_router = DualEngineRouter()


async def _post_ollama(
    url: str,
    model: str,
    prompt: str,
    timeout_seconds: float = 15.0,
    options: Optional[Dict[str, Any]] = None,
) -> str | None:
    """Internal helper to call Ollama generate API asynchronously."""
    generation_options = {
        "temperature": 0.0,
        "num_predict": 128,
    }
    if options:
        generation_options.update(options)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": generation_options,
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


async def _execute_generation(
    prompt: str,
    timeout_seconds: float = 45.0,
    options: Optional[Dict[str, Any]] = None,
    router: Optional[DualEngineRouter] = None,
) -> str | None:
    """
    Dual-engine local inference with circuit breaker.
    If circuit is PRIMARY, dispatches to primary local engine.
    If circuit is EDGE, dispatches to quantized edge model.
    """
    active_router = router or default_router
    await active_router.check_and_update_state()

    if active_router.state == CircuitState.PRIMARY:
        url = active_router.primary_url
        model = active_router.primary_model
    else:
        url = active_router.edge_url
        model = active_router.edge_model

    # Prepend task token for fine-tuned edge models if applicable
    fb_prompt = prompt
    if "[TASK: PORTFOLIO_AUDITOR]" not in fb_prompt and "pralabs" in model:
        fb_prompt = f"[TASK: PORTFOLIO_AUDITOR]\n{fb_prompt}"

    res = await _post_ollama(url, model, fb_prompt, timeout_seconds=timeout_seconds, options=options)
    if res:
        return res

    # If primary failed during call, trip circuit and fallback to edge model
    if active_router.state == CircuitState.PRIMARY:
        active_router.trip_to_edge("Primary generation failed or timed out during execution")
        edge_url = active_router.edge_url
        edge_model = active_router.edge_model
        logger.info(f"Retrying generation on edge fallback {edge_url} ({edge_model})")
        res = await _post_ollama(edge_url, edge_model, fb_prompt, timeout_seconds=timeout_seconds, options=options)
        if res:
            return res

    # If running outside container (e.g. host.docker.internal unresolved), try localhost
    if "host.docker.internal" in url:
        direct_url = url.replace("host.docker.internal", "127.0.0.1")
        res = await _post_ollama(direct_url, model, fb_prompt, timeout_seconds=timeout_seconds, options=options)
        if res:
            return res

    logger.error(f"[Ollama Inference Failed] Could not generate response from {url} ({model}).")
    return None


def build_compressed_telemetry(
    ticker: str, metrics: Dict[str, Any], portfolio_ctx: Optional[Dict[str, Any]] = None
) -> str:
    """
    Constructs a positional pipe-delimited telemetry line for 1.5B edge model inference.
    Micro-schema:
    [AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|TAX|PRE_ACT
    Example:
    [AUDIT] BHEL|2|7/7|84|-0.5%|+52.2%|2|LT|EXIT_STOP
    """
    ctx = portfolio_ctx or {}

    # 1. TKR
    tkr = (ticker or metrics.get("ticker", "")).strip().upper()

    # 2. STG
    stg = metrics.get("stage", "NA")
    if stg is None or str(stg).strip() in ("", "None", "Unknown"):
        stg = "NA"
    else:
        stg = str(stg).strip()

    # 3. MIN (Minervini Trend Checklist, e.g. '7/7' or '5/7')
    raw_min = metrics.get("tt_score", metrics.get("minervini_score", metrics.get("min", "NA")))
    if raw_min is None or str(raw_min).strip() in ("", "None", "NA"):
        min_score = "NA"
    else:
        raw_min_str = str(raw_min).strip()
        if "/7" in raw_min_str:
            min_score = raw_min_str
        else:
            try:
                min_score = f"{int(float(raw_min))}/7"
            except (ValueError, TypeError):
                min_score = f"{raw_min_str}/7"

    # 4. RS (Relative Strength score, e.g. 84 or NA)
    raw_rs = metrics.get("rs_score", metrics.get("rs", "NA"))
    if raw_rs is None or str(raw_rs).strip() in ("", "None", "NA"):
        rs_str = "NA"
    else:
        try:
            rs_str = f"{round(float(raw_rs))}"
        except (ValueError, TypeError):
            rs_str = str(raw_rs).strip()

    # 5. STOP_D% (Stop distance % relative to stop: (cmp - stop) / stop * 100)
    # Price lookup
    cmp_val = metrics.get("price", metrics.get("current_price", metrics.get("ltp")))
    if cmp_val is None and "quote" in metrics and isinstance(metrics["quote"], dict):
        cmp_val = metrics["quote"].get("ltp", metrics["quote"].get("price"))

    stop_val = metrics.get("stop_loss", metrics.get("stop"))
    # Fallback to parsing stop loss from rationale if not explicitly passed
    if stop_val is None:
        rationale_str = metrics.get("rationale", "")
        if isinstance(rationale_str, str) and "Chandelier Exit" in rationale_str:
            import re

            m = re.search(r"Chandelier Exit \(([\d\.]+)\)", rationale_str)
            if m:
                try:
                    stop_val = float(m.group(1))
                except ValueError:
                    pass

    stop_d_str = "NA"
    try:
        if cmp_val is not None and stop_val is not None:
            c = float(cmp_val)
            s = float(stop_val)
            if s > 0:
                stop_margin_pct = round((c - s) / s * 100, 1)
                stop_d_str = f"{stop_margin_pct:+.1f}%"
    except (ValueError, TypeError, ZeroDivisionError):
        stop_d_str = "NA"

    # 6. PNL%
    raw_pnl = ctx.get("pnl_pct", metrics.get("pnl_pct"))
    if raw_pnl is None:
        pnl_str = "0.0%"
    else:
        try:
            pnl_str = f"{float(raw_pnl):+.1f}%"
        except (ValueError, TypeError):
            pnl_str = "0.0%"

    # 7. DIST_D (Distribution days count)
    raw_dist = metrics.get("dist_days", 0)
    try:
        dist_d = str(int(raw_dist))
    except (ValueError, TypeError):
        dist_d = "0"

    # 8. TAX (Tax term: 'LT' or 'ST')
    raw_tax = ctx.get("tax_term", ctx.get("tax_category", metrics.get("tax_term", metrics.get("tax_category"))))
    if raw_tax:
        raw_tax_upper = str(raw_tax).strip().upper()
        if "LT" in raw_tax_upper:
            tax_term = "LT"
        elif "ST" in raw_tax_upper:
            tax_term = "ST"
        else:
            tax_term = raw_tax_upper
    else:
        # Check holding_days if available
        holding_days = ctx.get("holding_days", metrics.get("holding_days"))
        if holding_days is not None:
            try:
                tax_term = "LT" if int(holding_days) > 365 else "ST"
            except (ValueError, TypeError):
                tax_term = "ST"
        else:
            tax_term = "ST"

    # 9. PRE_ACT (Pre-action classification)
    raw_action = metrics.get("action", ctx.get("action", "HOLD"))
    if isinstance(raw_action, dict):
        raw_action = raw_action.get("action", "HOLD")
    raw_action_str = str(raw_action).strip().upper() if raw_action else "HOLD"

    if "SELL" in raw_action_str or "EXIT" in raw_action_str or "STOP" in raw_action_str:
        if "STOP" in raw_action_str or (stop_d_str != "NA" and stop_d_str.startswith("-")):
            pre_act = "EXIT_STOP"
        else:
            pre_act = "EXIT"
    elif "TRIM" in raw_action_str or "PAUSE" in raw_action_str:
        pre_act = "TRIM"
    elif "BUY" in raw_action_str or "ACCUMULATE" in raw_action_str:
        pre_act = "ACCUMULATE"
    elif "HOLD" in raw_action_str:
        pre_act = "HOLD"
    else:
        pre_act = raw_action_str.replace(" ", "_")

    return f"[AUDIT] {tkr}|{stg}|{min_score}|{rs_str}|{stop_d_str}|{pnl_str}|{dist_d}|{tax_term}|{pre_act}"


def build_compressed_prompt(telemetry_lines: List[str]) -> str:
    """
    Constructs the compressed micro-prompt for the edge model with strict causal grammar.
    """
    joined_lines = "\n".join(telemetry_lines)
    prompt = (
        "Positional Schema:\n"
        "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|TAX|PRE_ACT\n\n"
        f"{joined_lines}\n\n"
        "Output Format for each ticker (exact grammar):\n"
        "<TICKER> | STG:<stg> | STOP:<margin> | ACT:<directive>\n"
        "- <Directive 1>\n"
        "- <Directive 2>"
    )
    return prompt


async def generate_ai_analysis(
    ticker: str,
    metrics: dict,
    portfolio_ctx: dict | None = None,
    router: Optional[DualEngineRouter] = None,
) -> str | None:
    """
    Generates institutional technical diagnosis using edge compressed prompt format.
    Enforces temperature: 0.0, num_predict: 128.
    """
    telemetry = build_compressed_telemetry(ticker, metrics, portfolio_ctx)
    prompt = build_compressed_prompt([telemetry])
    options = {
        "temperature": 0.0,
        "num_predict": 128,
    }
    return await _execute_generation(prompt, timeout_seconds=30.0, options=options, router=router)


async def generate_batch_ai_analysis(
    items: List[Dict[str, Any]],
    batch_size: Optional[int] = None,
    timeout_seconds: float = 30.0,
    router: Optional[DualEngineRouter] = None,
) -> Dict[str, str]:
    """
    Micro-batches inference requests.
    When circuit breaker is in EDGE fallback mode, enforces 4-ticker micro-batches (batch_size=4).
    Returns a mapping of ticker -> output text.
    """
    results: Dict[str, str] = {}
    if not items:
        return results

    active_router = router or default_router
    await active_router.check_and_update_state()

    # Determine batch size: if not explicitly given, use 4 for EDGE mode, 2 for default
    if batch_size is None:
        effective_batch_size = 4 if active_router.state == CircuitState.EDGE else 2
    else:
        effective_batch_size = batch_size

    options = {
        "temperature": 0.0,
        "num_predict": 128,
    }

    # Process in micro-batches
    for i in range(0, len(items), effective_batch_size):
        batch = items[i : i + effective_batch_size]
        telemetry_lines = []
        for item in batch:
            tkr = item.get("ticker", "")
            m = item.get("metrics", {})
            ctx = item.get("portfolio_ctx")
            telemetry_lines.append(build_compressed_telemetry(tkr, m, ctx))

        prompt = build_compressed_prompt(telemetry_lines)
        response = await _execute_generation(
            prompt,
            timeout_seconds=timeout_seconds,
            options=options,
            router=active_router,
        )

        if response:
            # Parse responses per ticker
            if len(batch) == 1:
                results[batch[0]["ticker"]] = response
            else:
                blocks = {}
                current_ticker = None
                current_lines = []
                for line in response.splitlines():
                    matched_tkr = None
                    for b_item in batch:
                        b_tkr = b_item["ticker"].upper()
                        if line.strip().startswith(b_tkr) or f"{b_tkr} |" in line.upper():
                            matched_tkr = b_tkr
                            break
                    if matched_tkr:
                        if current_ticker:
                            blocks[current_ticker] = "\n".join(current_lines).strip()
                        current_ticker = matched_tkr
                        current_lines = [line]
                    else:
                        if current_ticker:
                            current_lines.append(line)
                if current_ticker:
                    blocks[current_ticker] = "\n".join(current_lines).strip()

                for b_item in batch:
                    b_tkr = b_item["ticker"]
                    results[b_tkr] = blocks.get(b_tkr.upper(), response)
        else:
            for b_item in batch:
                results[b_item["ticker"]] = ""

    return results


async def generate_opportunity_rotation_analysis(
    laggards: list,
    leaders: list,
    router: Optional[DualEngineRouter] = None,
) -> str | None:
    """
    Synthesizes an active rotation strategy pairing weak/broken holdings
    with top Stage 2 breakout setups to maximize portfolio CAGR.
    """
    laggard_text = "\n".join(
        [
            f"- {x['ticker']}: Price ₹{x['price']}, Loss {x.get('pnl_pct', 0):+.1f}%, Stage {x.get('stage')}, Composite Score {x.get('score')}/100, Stop Loss ₹{x.get('stop_loss')}"
            for x in laggards
        ]
    )
    leader_text = "\n".join(
        [
            f"- {x['ticker']}: Price ₹{x['price']}, Stage {x.get('stage')}, RS {x.get('rs_score')}, TT {x.get('tt_score')}/7, Composite Score {x.get('score')}/100"
            for x in leaders
        ]
    )

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
    return await _execute_generation(prompt, timeout_seconds=45.0, router=router)
