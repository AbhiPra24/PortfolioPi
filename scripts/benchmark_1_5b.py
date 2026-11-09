#!/usr/bin/env python3
"""
Benchmark qwen2.5:1.5b against PortfolioPi live databases:
1. Signal audit
2. Cashflow audit
3. Direct triage query
"""

import json
import time

import requests
from analyse_cashflow import extract_cashflow_data
from analyse_signals import extract_signals_data

TARGET = {"name": "Raspberry Pi 5 Local CPU (qwen2.5:1.5b)", "url": "http://127.0.0.1:11434", "model": "qwen2.5:1.5b"}


def query_model(target, prompt, system_prompt=None):
    payload = {
        "model": target["model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 300},
    }
    if system_prompt:
        payload["system"] = system_prompt

    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{target['url']}/api/generate", json=payload, timeout=120)
        dur = time.perf_counter() - t0
        data = resp.json()
        eval_count = data.get("eval_count", 0)
        eval_dur_s = data.get("eval_duration", 0) / 1e9
        tps = eval_count / eval_dur_s if eval_dur_s > 0 else 0
        p_count = data.get("prompt_eval_count", 0)
        p_dur_s = data.get("prompt_eval_duration", 0) / 1e9
        p_tps = p_count / p_dur_s if p_dur_s > 0 else 0
        return {
            "success": True,
            "response": data.get("response", "").strip(),
            "time_s": dur,
            "prompt_tokens": p_count,
            "prompt_tps": p_tps,
            "gen_tokens": eval_count,
            "gen_tps": tps,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def run():
    db_path = "/data/Work/PortfolioPi/data/portfoliopi.db"

    # 1. Signals Data
    sig_data = extract_signals_data(db_path)
    # Top 5 critical sells, top 3 trims to evaluate ingestion on 1.5b
    sig_data["critical_sell_signals"] = sig_data.get("critical_sell_signals", [])[:5]
    sig_data["trim_warnings"] = sig_data.get("trim_warnings", [])[:3]
    # Limit momentum/oversold to top 3
    sig_data["weak_momentum_holdings"] = sig_data.get("weak_momentum_holdings", [])[:3]
    sig_data["oversold_holdings"] = sig_data.get("oversold_holdings", [])[:3]
    sys_signals = "You are an expert quantitative technical trader and disciplined risk manager. Prioritize strict capital preservation."
    prompt_signals = f"""Analyze these top signals from PortfolioPi:
```json
{json.dumps(sig_data, indent=2)}
```
Deliver a concise 3-bullet execution plan for Monday open with specific tickers."""

    # 2. Cashflow Data
    cf_data = extract_cashflow_data(db_path)
    cf_data["misaligned_sips"] = cf_data.get("misaligned_sips", [])[:3]
    cf_data["top_dividend_contributors"] = cf_data.get("top_dividend_contributors", [])[:3]
    cf_data["upcoming_payouts"] = cf_data.get("upcoming_payouts", [])[:3]
    sys_cf = "You are an income investing strategist and personal financial controller."
    prompt_cf = f"""Review the following cashflow and SIP metrics:
```json
{json.dumps(cf_data, indent=2)}
```
Deliver a concise 3-bullet action plan addressing misaligned SIPs and dividend reinvestment."""

    # 3. Direct Query
    sys_ask = "You are a quantitative risk auditor. Ground all answers strictly in the provided data."
    prompt_ask = """Current Live Holdings:
- Symbol: TATAMOTORS, Buy Price: 920.00, Current Price: 875.00, PnL: -4.89%, Distribution Days: 4, Chandelier Exit: 865.00, Stage: 3 Topping.
- Symbol: GOLDEX, Buy Price: 113.00, Current Price: 119.83, PnL: +6.03%, Distribution Days: 2, Chandelier Exit: 123.08, Stage: 4 Decline.

User Question: Which stock requires immediate exit and why? Give a direct 3-bullet actionable answer."""

    tests = [
        {"name": "Test 1: Technical Signals Audit (analyse_signals)", "sys": sys_signals, "prompt": prompt_signals},
        {"name": "Test 2: Cashflow & SIP Audit (analyse_cashflow)", "sys": sys_cf, "prompt": prompt_cf},
        {"name": "Test 3: Risk Triage & Exit Query (ask_portfolio)", "sys": sys_ask, "prompt": prompt_ask},
    ]

    for t in tests:
        print(f"\n{'=' * 70}")
        print(f"RUNNING: {t['name']}")
        print(f"{'=' * 70}")
        res = query_model(TARGET, t["prompt"], t.get("sys"))
        if res["success"]:
            print(f"[*] Total Time: {res['time_s']:.2f}s")
            print(f"[*] Prompt Ingestion: {res['prompt_tokens']} tokens @ {res['prompt_tps']:.1f} tok/s")
            print(f"[*] Generation Speed: {res['gen_tokens']} tokens @ {res['gen_tps']:.1f} tok/s")
            print("\n--- Model Output ---")
            print(res["response"])
        else:
            print(f"[!] FAILED: {res.get('error')}")


if __name__ == "__main__":
    run()
