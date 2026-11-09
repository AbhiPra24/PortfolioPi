#!/usr/bin/env python3
import time

import requests

TARGETS = [
    {"name": "Mac Mini (Remote GPU)", "url": "http://192.168.1.39:11434", "model": "qwen2.5:7b"},
    {"name": "Raspberry Pi 5 (Local CPU)", "url": "http://127.0.0.1:11434", "model": "qwen2.5:3b"},
]

TEST_PROMPT = """Analyze this portfolio breakdown and recommend an action:
- Symbol: TATAMOTORS, Buy Price: 920, Current Price: 875, Distribution Days: 4, Chandelier Exit: 865, Stage: 3 Topping.
Provide a concise 2-sentence tactical recommendation."""


def get_ram_info():
    with open("/proc/meminfo") as f:
        lines = f.readlines()
    mem = {}
    for line in lines:
        parts = line.split(":")
        if len(parts) == 2:
            mem[parts[0].strip()] = int(parts[1].split()[0])
    total = mem.get("MemTotal", 0) / 1024  # MB
    avail = mem.get("MemAvailable", 0) / 1024  # MB
    used = total - avail
    return total, used


def benchmark(target):
    print(f"\n{'=' * 60}")
    print(f"BENCHMARK: {target['name']} ({target['model']}) @ {target['url']}")
    print(f"{'=' * 60}")

    # 1. Check reachability
    try:
        t0 = time.perf_counter()
        r = requests.get(f"{target['url']}/api/tags", timeout=5)
        api_lat = (time.perf_counter() - t0) * 1000
        print(f"[*] API Latency (/api/tags): {api_lat:.2f} ms (Status: {r.status_code})")
    except Exception as e:
        print(f"[!] Target unreachable: {e}")
        return None

    # 2. Record Initial Host Metrics
    tot_mb, used_mb_before = get_ram_info()

    # 3. Perform Inference
    payload = {"model": target["model"], "prompt": TEST_PROMPT, "stream": False}

    print(f"[*] Dispatching prompt to {target['model']}...")
    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{target['url']}/api/generate", json=payload, timeout=120)
        total_time = time.perf_counter() - t0
        data = resp.json()
    except Exception as e:
        print(f"[!] Generation failed: {e}")
        return None

    # 4. Host Metrics After
    _, used_mb_after = get_ram_info()

    eval_count = data.get("eval_count", 0)
    eval_duration_s = data.get("eval_duration", 0) / 1e9
    prompt_eval_count = data.get("prompt_eval_count", 0)
    prompt_eval_s = data.get("prompt_eval_duration", 0) / 1e9

    tps = eval_count / eval_duration_s if eval_duration_s > 0 else 0
    prompt_tps = prompt_eval_count / prompt_eval_s if prompt_eval_s > 0 else 0

    print("\n--- Performance Results ---")
    print(f"Prompt Tokens: {prompt_eval_count} ({prompt_tps:.2f} tok/s, {prompt_eval_s * 1000:.1f} ms)")
    print(f"Generated Tokens: {eval_count} ({tps:.2f} tok/s, {eval_duration_s * 1000:.1f} ms)")
    print(f"Total Turnaround Time: {total_time:.2f} s")
    print(
        f"Host RAM Used: {used_mb_after / 1024:.2f} GB / {tot_mb / 1024:.2f} GB (Delta: {(used_mb_after - used_mb_before):+.1f} MB)"
    )
    print("\n--- Generated Response ---")
    print(data.get("response", "").strip())

    return {
        "target": target["name"],
        "model": target["model"],
        "tps": tps,
        "total_time": total_time,
        "tokens": eval_count,
        "response": data.get("response", "").strip(),
    }


if __name__ == "__main__":
    results = []
    for t in TARGETS:
        res = benchmark(t)
        if res:
            results.append(res)
