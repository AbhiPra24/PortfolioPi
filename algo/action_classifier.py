"""Synthesizes structural algorithms into actionable portfolio guidance."""

from typing import Any, Dict, List, Optional
import pandas as pd

from algo.distribution_days import count_distribution_days
from algo.relative_strength import compute_relative_strength
from algo.sip_allocator import calculate_sip_multiplier, check_liquidity_health
from algo.stage_analysis import classify_stage
from algo.tax_lots import LTCG_ANNUAL_EXEMPTION_LIMIT, evaluate_tax_hurdle_rule
from algo.trailing_stop import calculate_chandelier_exit
from algo.trend_template import check_trend_template


def get_stock_action(
    stock_code: str,
    stock_df: pd.DataFrame,
    nifty_df: pd.DataFrame,
    is_holding: bool,
    vix_series: Optional[pd.Series] = None,
    asset_class: str = "EQUITY",
    tax_lots: Optional[List[Dict[str, Any]]] = None,
    realized_stcl_available: bool = False,
    remaining_ltcg_exemption: float = LTCG_ANNUAL_EXEMPTION_LIMIT,
    avg_daily_turnover_1m: Optional[float] = None,
    price_to_nav_spread_pct: Optional[float] = None,
) -> dict:
    if len(stock_df) < 200 or len(nifty_df) < 200:
        return {
            "action": "HOLD",
            "rationale": "Insufficient data",
            "k_t": 3.0,
            "sip_multiplier": 0.0,
        }

    rs_score = compute_relative_strength(stock_df, nifty_df)
    stage_data = classify_stage(stock_df)
    tt_score = check_trend_template(stock_df)
    dist_data = count_distribution_days(stock_df)

    stage = stage_data["stage"]
    dist_days = dist_data["dist_days"]

    # Calculate stop loss with dynamic scaling
    chandelier_res = calculate_chandelier_exit(
        stock_df,
        vix_series=vix_series,
        stage=stage,
        asset_class=asset_class,
        return_details=True,
    )
    stop_loss = chandelier_res["stop_loss"]
    k_t = chandelier_res["k_t"]

    # Calculate SIP multiplier with liquidity health gating
    sip_mult = calculate_sip_multiplier(
        stock_df,
        stage=stage,
        rs_score=rs_score,
        dist_days=dist_days,
        ticker=stock_code,
        avg_daily_turnover_1m=avg_daily_turnover_1m,
        price_to_nav_spread_pct=price_to_nav_spread_pct,
        asset_class=asset_class,
    )

    liquidity_ok = True
    liquidity_rationale = ""
    if avg_daily_turnover_1m is not None or price_to_nav_spread_pct is not None:
        liquidity_ok, liquidity_rationale = check_liquidity_health(
            ticker=stock_code,
            avg_daily_turnover_1m=avg_daily_turnover_1m if avg_daily_turnover_1m is not None else float("inf"),
            price_to_nav_spread_pct=price_to_nav_spread_pct if price_to_nav_spread_pct is not None else 0.0,
            asset_class=asset_class,
        )

    current_close = stock_df.iloc[-1]["close"]

    action = "HOLD"
    rationale = []

    if is_holding:
        if current_close < stop_loss:
            action = "SELL / STOP SIP"
            rationale.append(f"Price ({current_close:.2f}) is below Chandelier Exit ({stop_loss:.2f}).")
        elif stage == 4:
            action = "SELL / STOP SIP"
            rationale.append("Stock has entered Stage 4 decline.")
        elif dist_days >= 5 or stage == 3:
            action = "TRIM / PAUSE SIP"
            if dist_days >= 5:
                rationale.append(f"High distribution days ({dist_days}).")
            if stage == 3:
                rationale.append("Stock is in Stage 3 topping.")
        elif stage == 2 and tt_score >= 6 and rs_score > 0:
            if not liquidity_ok:
                action = "HOLD / PAUSE SIP"
                rationale.append(f"Liquidity/Spread guardrail failed: {liquidity_rationale}.")
            else:
                action = "BUY MORE / CONTINUE SIP"
                rationale.append(f"Strong Stage 2 uptrend with RS {rs_score:.2f} and {tt_score}/7 trend criteria.")
        else:
            action = "HOLD"
            rationale.append("Holding steady.")
    else:
        if stage == 2 and tt_score >= 6 and rs_score > 0:
            if not liquidity_ok:
                action = "HOLD / PAUSE SIP"
                rationale.append(f"Liquidity/Spread guardrail failed: {liquidity_rationale}.")
            else:
                action = "BUY MORE / CONTINUE SIP"
                rationale.append(f"Strong Stage 2 uptrend with RS {rs_score:.2f} and {tt_score}/7 trend criteria. Consider buying.")
        else:
            action = "HOLD"
            rationale.append("Not a buy setup right now.")

    tax_eval = None
    if is_holding and tax_lots:
        # Evaluate tax hurdle across active lots
        primary_lot = tax_lots[0]
        hard_stop_breached = bool(current_close < stop_loss)
        tax_eval = evaluate_tax_hurdle_rule(
            lot=primary_lot,
            base_action=action,
            stage=stage,
            hard_stop_breached=hard_stop_breached,
            realized_stcl_available=realized_stcl_available,
            remaining_ltcg_exemption=remaining_ltcg_exemption,
        )
        if tax_eval.get("tax_action") and tax_eval["tax_action"] != action:
            rationale.append(f"[Tax Lot: {tax_eval['tax_action']}] {tax_eval['rationale']}")

    return {
        "action": action,
        "tax_action": tax_eval.get("tax_action") if tax_eval else action,
        "tax_rationale": tax_eval.get("rationale") if tax_eval else None,
        "rationale": " ".join(rationale),
        "stage": stage,
        "sma_150": stage_data.get("sma_150", 0.0),
        "slope": stage_data.get("slope", 0.0),
        "rs_score": rs_score,
        "tt_score": tt_score,
        "dist_days": dist_days,
        "stop_loss": stop_loss,
        "k_t": k_t,
        "sip_multiplier": sip_mult,
        "asset_class": asset_class,
    }
