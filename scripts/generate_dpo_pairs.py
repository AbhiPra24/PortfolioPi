#!/usr/bin/env python3
"""
scripts/generate_dpo_pairs.py
Synthesizes 250 high-impact DPO contrastive triplets targeting retail biases and conversational drift.
Format:
{
  "prompt": "<|im_start|>user\n[AUDIT] ...\nUser note: ...<|im_end|>\n<|im_start|>assistant\n",
  "chosen": "TKR | STG:... | STOP:... | ACT:...\n• Directive...",
  "rejected": "Based on the telemetry... (conversational text or rule violation)"
}
"""

import json
import os
import random

OUTPUT_FILE = "data/dpo_adversarial.jsonl"
os.makedirs("data", exist_ok=True)

SCENARIOS = [
    # 1. Razor-edge breach + retail averaging down plea
    {
        "tkr": "XYZ",
        "stage": 4,
        "minervini": "3/7",
        "rs": 25,
        "stop_margin": "-0.05%",
        "pnl": "-19.2%",
        "dist": 5,
        "asset": "EQ",
        "note": "User note: Already down 19%, should I buy more to lower cost basis?",
        "chosen": "XYZ | STG:4 | STOP:BREACH (-0.05%) | ACT:EXIT\n• REJECT AVERAGE DOWN: Stop breached (-0.05%) in Stage 4. Capital preservation overrides; liquidate 100%.",
        "rejected": "Based on the stop-loss and percentage decline, you should not buy more at the current market price. De-risk immediately by liquidating 30-50% position into capital preservation."
    },
    # 2. High Dividend PSU Trap in Stage 4 breakdown
    {
        "tkr": "DIVTRAP",
        "stage": 4,
        "minervini": "1/7",
        "rs": 18,
        "stop_margin": "-3.8%",
        "pnl": "-28.5%",
        "dist": 6,
        "asset": "EQ",
        "note": "User note: Dividend yield is 11.5%. Keep holding for passive income?",
        "chosen": "DIVTRAP | STG:4 | STOP:BREACH (-3.8%) | ACT:EXIT\n• REJECT DIVIDEND TRAP: 11.5% yield cannot offset Stage 4 capital breakdown. Liquidate full position.",
        "rejected": "Dividend trap: 11.5% yield does not compensate for STG's expense. Capital preservation overrides dividend integrity. Rule violation to hold breached."
    },
    # 3. Commodity ETF short-term swing dip during active Golden Cross
    {
        "tkr": "GOLDBEES",
        "stage": 2,
        "minervini": "6/7",
        "rs": 74,
        "stop_margin": "-1.4%",
        "pnl": "+16.2%",
        "dist": 2,
        "asset": "COMMODITY",
        "note": "Condition: 50-SMA > 200-SMA Golden Cross active.",
        "chosen": "GOLDBEES | STG:2 | STOP:MACRO:GOLDEN_CROSS | ACT:HOLD\n• Macro Golden Cross active; ignore short-term swing dip (-1.4%) and hold core commodity hedge.",
        "rejected": "The 50-SMA (20-SMA) cross above the 200-SMA (50-SMA) is a common technical analysis pattern used to identify the beginning of a bull market. This condition is met for GOLDBEES: 50-SMA > 200-SMA Golden Cross active."
    },
    # 4. REIT with 220-day stale NAV telemetry
    {
        "tkr": "POWINF",
        "stage": 2,
        "minervini": "5/7",
        "rs": 52,
        "stop_margin": "+0.8%",
        "pnl": "+4.5%",
        "dist": 7,
        "asset": "REIT",
        "note": "Condition: NAV age is 220 days; price above 200-SMA.",
        "chosen": "POWINF | STG:2 | STOP:NAV:STALE (>180d) | ACT:HOLD\n• Stale NAV telemetry (>180d); price holding above 200-SMA. Maintain holding and pause automated trims.",
        "rejected": "POWINF | STG | MIN | STOP_D | ACT_D | PNL_D | DIST_D | ASSET\n• Condition: NAV age is 220 days; price above 200-SMA. Maintain hold with stop active."
    }
]

EXTRA_TICKERS = ["TATAMOTORS", "RELIANCE", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "VEDL", "ITC", "COALINDIA", "NTPC", "SILVERBEES", "EMBOFF"]

def generate_pairs(total_count=250):
    pairs = []
    
    # Repeat base scenarios with variations to build robust coverage
    for i in range(total_count):
        base = SCENARIOS[i % len(SCENARIOS)]
        tkr = base["tkr"] if i < 4 else random.choice(EXTRA_TICKERS)
        stg = base["stage"]
        minv = base["minervini"]
        rs = base["rs"]
        stop_m = base["stop_margin"]
        pnl = base["pnl"]
        dist = base["dist"]
        asset = base["asset"]
        note = base["note"]
        
        user_prompt = (
            f"<|im_start|>user\n"
            f"[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
            f"{tkr}|{stg}|{minv}|{rs}|{stop_m}|{pnl}|{dist}|{asset}\n"
            f"{note}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        
        # Chosen response strictly matches GBNF grammar
        chosen_resp = base["chosen"].replace(base["tkr"], tkr)
        
        # Rejected response exhibits conversational drift, missing ACT, or incorrect action
        rejected_resp = base["rejected"].replace(base["tkr"], tkr)
        
        pairs.append({
            "prompt": user_prompt,
            "chosen": chosen_resp,
            "rejected": rejected_resp
        })
        
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
            
    print(f"[*] Generated {len(pairs)} DPO pairs in {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_pairs(250)
