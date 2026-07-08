import pandas as pd
from algo.relative_strength import compute_relative_strength
from algo.stage_analysis import classify_stage
from algo.trend_template import check_trend_template
from algo.distribution_days import count_distribution_days
from algo.trailing_stop import calculate_chandelier_exit

def get_stock_action(stock_code: str, stock_df: pd.DataFrame, nifty_df: pd.DataFrame, is_holding: bool) -> dict:
    if len(stock_df) < 200 or len(nifty_df) < 200:
        return {"action": "HOLD", "rationale": "Insufficient data"}

    rs_score = compute_relative_strength(stock_df, nifty_df)
    stage_data = classify_stage(stock_df)
    tt_score = check_trend_template(stock_df)
    dist_data = count_distribution_days(stock_df)
    stop_loss = calculate_chandelier_exit(stock_df)
    
    current_close = stock_df.iloc[-1]['close']
    stage = stage_data['stage']
    dist_days = dist_data['dist_days']
    
    action = "HOLD"
    rationale = []

    if is_holding:
        if current_close < stop_loss:
            action = "SELL / STOP SIP"
            rationale.append(f"Price ({current_close:.2f}) is below Chandelier Exit ({stop_loss:.2f}).")
        elif stage == 4:
            action = "SELL / STOP SIP"
            rationale.append(f"Stock has entered Stage 4 decline.")
        elif dist_days >= 5 or stage == 3:
            action = "TRIM / PAUSE SIP"
            if dist_days >= 5:
                rationale.append(f"High distribution days ({dist_days}).")
            if stage == 3:
                rationale.append(f"Stock is in Stage 3 topping.")
        elif stage == 2 and tt_score >= 6 and rs_score > 0:
            action = "BUY MORE / CONTINUE SIP"
            rationale.append(f"Strong Stage 2 uptrend with RS {rs_score:.2f} and {tt_score}/7 trend criteria.")
        else:
            action = "HOLD"
            rationale.append("Holding steady.")
    else:
        if stage == 2 and tt_score >= 6 and rs_score > 0:
            action = "BUY MORE / CONTINUE SIP"
            rationale.append(f"Strong Stage 2 uptrend with RS {rs_score:.2f} and {tt_score}/7 trend criteria. Consider buying.")
        else:
            action = "HOLD"
            rationale.append("Not a buy setup right now.")

    return {
        "action": action,
        "rationale": " ".join(rationale),
        "stage": stage,
        "sma_150": stage_data.get("sma_150", 0.0),
        "slope": stage_data.get("slope", 0.0),
        "rs_score": rs_score,
        "tt_score": tt_score,
        "dist_days": dist_days,
        "stop_loss": stop_loss
    }
