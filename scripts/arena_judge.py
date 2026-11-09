#!/usr/bin/env python3
"""
LLM-as-a-Judge Evaluation Arena for PortfolioPi.
Runs PortfolioPi scripts on identical live database inputs across:
1. Mac Mini GPU (qwen2.5:7b)
2. Raspberry Pi 5 CPU (qwen2.5:3b)
"""

import json
import time

import requests
from analyse_cashflow import extract_cashflow_data
from analyse_signals import extract_signals_data

TARGET_MAC = {"name": "Mac Mini (Remote GPU)", "url": "http://192.168.1.39:11434", "model": "qwen2.5:7b"}

TARGET_PI = {"name": "Raspberry Pi 5 (Local CPU)", "url": "http://127.0.0.1:11434", "model": "qwen2.5:3b"}


def query_model(target, prompt, system_prompt=None):
    payload = {
        "model": target["model"],
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 400},
    }
    if system_prompt:
        payload["system"] = system_prompt

    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{target['url']}/api/generate", json=payload, timeout=180)
        dur = time.perf_counter() - t0
        data = resp.json()
        eval_count = data.get("eval_count", 0)
        eval_dur_s = data.get("eval_duration", 0) / 1e9
        tps = eval_count / eval_dur_s if eval_dur_s > 0 else 0
        return {
            "success": True,
            "response": data.get("response", "").strip(),
            "time_s": dur,
            "tokens": eval_count,
            "tps": tps,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_test_suite():
    db_path = "/data/Work/PortfolioPi/data/portfoliopi.db"

    # 1. Signals Data (trimmed to top 5 critical to keep prompt compact and test inference)
    signals_data = extract_signals_data(db_path)
    signals_data["critical_sell_signals"] = signals_data.get("critical_sell_signals", [])[:5]
    signals_data["trim_warnings"] = signals_data.get("trim_warnings", [])[:3]
    sys_signals = "You are an expert quantitative technical trader and disciplined risk manager. Prioritize strict capital preservation."
    prompt_signals = f"""Analyze these top critical signals from PortfolioPi:
```json
{json.dumps(signals_data, indent=2)}
```
Deliver a concise 3-bullet execution plan for Monday open. Name the symbols directly."""

    # 2. Cashflow Data
    cf_data = extract_cashflow_data(db_path)
    # Compact misaligned sips
    cf_data["misaligned_sips"] = cf_data.get("misaligned_sips", [])[:4]
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
        {
            "name": "Test 1: Technical Momentum & Signals Audit (analyse_signals)",
            "sys": sys_signals,
            "prompt": prompt_signals,
        },
        {"name": "Test 2: Cashflow & SIP Allocation Audit (analyse_cashflow)", "sys": sys_cf, "prompt": prompt_cf},
        {"name": "Test 3: Risk Triage & Exit Comparison (ask_portfolio)", "sys": sys_ask, "prompt": prompt_ask},
    ]

    arena_results = []

    for t in tests:
        print(f"\n{'=' * 70}")
        print(f"RUNNING: {t['name']}")
        print(f"{'=' * 70}")

        print(f"[*] Querying Mac Mini GPU ({TARGET_MAC['model']})...")
        res_mac = query_model(TARGET_MAC, t["prompt"], t.get("sys"))
        if res_mac["success"]:
            print(f"    -> Done in {res_mac['time_s']:.2f}s ({res_mac['tps']:.1f} tok/s, {res_mac['tokens']} tokens)")
        else:
            print(f"    -> FAILED: {res_mac.get('error')}")

        print(f"[*] Querying Raspberry Pi 5 CPU ({TARGET_PI['model']})...")
        res_pi = query_model(TARGET_PI, t["prompt"], t.get("sys"))
        if res_pi["success"]:
            print(f"    -> Done in {res_pi['time_s']:.2f}s ({res_pi['tps']:.1f} tok/s, {res_pi['tokens']} tokens)")
        else:
            print(f"    -> FAILED: {res_pi.get('error')}")

        arena_results.append({"test_name": t["name"], "prompt": t["prompt"], "mac": res_mac, "pi": res_pi})

    with open("/data/Work/PortfolioPi/data/arena_results.json", "w") as f:
        json.dump(arena_results, f, indent=2)
    print("\n[+] Arena evaluation complete! Saved to /data/Work/PortfolioPi/data/arena_results.json")


if __name__ == "__main__":
    run_test_suite()
