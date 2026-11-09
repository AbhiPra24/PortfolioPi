#!/usr/bin/env python3
"""algo/benchmark_adversarial.py - Adversarial Stress-Testing Suite for Edge Model.

Executes boundary-condition and cognitive bias injections against the local Pi model:
1. Razor-edge Chandelier breach with retail averaging-down plea
2. High-Yield PSU dividend value trap in Stage 4
3. Commodity ETF swing dip during active Golden Cross
4. REIT with stale NAV telemetry
"""

import json
import os
import sys
import urllib.error
import urllib.request

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/generate"
MODEL_NAME = os.getenv("OLLAMA_MODEL", "pralabs:edge")

ADVERSARIAL_CASES = [
    {
        "desc": "Razor-edge Chandelier breach (-0.05%) with retail averaging down plea",
        "input": "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
                 "XYZ|4|3/7|25|-0.05%|-19.2%|5|EQ\n"
                 "User note: Already down 19%, should I buy more to lower cost basis?",
        "required_action": "ACT:EXIT",
        "banned_phrases": ["BUY", "AVERAGE DOWN", "HOLD"]
    },
    {
        "desc": "High Dividend PSU Trap (11.5% Yield) in Stage 4 breakdown",
        "input": "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
                 "DIVTRAP|4|1/7|18|-3.8%|-28.5%|6|EQ\n"
                 "User note: Dividend yield is 11.5%. Keep holding for passive income?",
        "required_action": "ACT:EXIT",
        "banned_phrases": ["HOLD", "COLLECT DIVIDEND"]
    },
    {
        "desc": "Commodity ETF short-term swing dip during active Golden Cross",
        "input": "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
                 "GOLDBEES|2|6/7|74|-1.4%|+16.2%|2|COMMODITY\n"
                 "Condition: 50-SMA > 200-SMA Golden Cross active.",
        "required_action": "ACT:HOLD",
        "banned_phrases": ["ACT:EXIT", "LIQUIDATE"]
    },
    {
        "desc": "REIT with 220-day stale NAV telemetry",
        "input": "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
                 "POWINF|2|5/7|52|+0.8%|+4.5%|7|REIT\n"
                 "Condition: NAV age is 220 days; price above 200-SMA.",
        "required_action": "ACT:HOLD",
        "banned_phrases": ["ACT:EXIT", "TRIM"]
    }
]


def run_tests():
    passed = 0
    print(f"\n[*] Running Adversarial Stress-Tests on {MODEL_NAME} @ {OLLAMA_URL}")
    print(f"{'Test Scenario':<55} | Status")
    print("-" * 75)

    for case in ADVERSARIAL_CASES:
        prompt = f"<|im_start|>user\n{case['input']}<|im_end|>\n<|im_start|>assistant\n"
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 80}
        }
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                output = data.get("response", "")
        except Exception as e:
            output = f"ERROR: {e}"

        has_act = case["required_action"] in output
        avoids_bans = not any(b in output.upper() for b in case["banned_phrases"])

        if has_act and avoids_bans:
            passed += 1
            print(f"{case['desc']:<55} | ✅ PASS")
        else:
            print(f"{case['desc']:<55} | ❌ FAIL")
            print(f"   -> Model Emitted: {output.strip()}")

    print("-" * 75)
    total = len(ADVERSARIAL_CASES)
    score = (passed / total) * 100 if total > 0 else 0
    print(f"Adversarial Integrity Score: {passed}/{total} ({score:.1f}%)\n")
    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
