"""
Shared Ollama & SQLite helper utilities for PortfolioPi AI analysis scripts.
Pure standard-library implementation (zero external dependencies).
Supports multi-tiered LLM routing:
  1. Primary: Mac Mini GPU (http://192.168.1.39:11434, qwen2.5:7b)
  2. Fallback: Local Pi Ollama (http://127.0.0.1:11434 or host.docker.internal:11434, pralabs:edge with [TASK: PORTFOLIO_AUDITOR])
  3. Offline CLI Fallback: Antigravity CLI (`agy`)
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


def resolve_db_path(cli_path: Optional[str] = None) -> str:
    """Find the best database path across environments (Local Mac, RasPi, Docker)."""
    candidates = [
        cli_path,
        os.getenv("SQLITE_DB_PATH"),
        os.getenv("DB_PATH"),
        "/data/Work/PortfolioPi/data/portfoliopi.db",
        "/data/Work/portfoliopi/data/portfoliopi.db",
        "/mnt/usb-storage/Work/PortfolioPi/data/portfoliopi.db",
        "/mnt/usb-storage/Work/portfoliopi/data/portfoliopi.db",
        "data/portfoliopi.db",
        "backups/portfoliopi_local.db",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "backups/portfoliopi_local.db"


def resolve_ollama_endpoint(cli_url: Optional[str] = None) -> str:
    """Resolve primary Ollama endpoint: CLI arg -> OLLAMA_BASE_URL -> Default local host (:11434)."""
    if cli_url:
        return cli_url.rstrip("/")
    if os.getenv("OLLAMA_BASE_URL"):
        return os.getenv("OLLAMA_BASE_URL").rstrip("/")
    return "http://localhost:11434"


def resolve_ollama_model(cli_model: Optional[str] = None) -> str:
    """Resolve Ollama model: CLI arg -> OLLAMA_MODEL -> default pralabs:latest."""
    if cli_model:
        return cli_model
    return os.getenv("OLLAMA_MODEL", "pralabs:latest")


def resolve_fallback_endpoint(cli_fallback_url: Optional[str] = None) -> str:
    """Resolve fallback Ollama endpoint: CLI arg -> OLLAMA_FALLBACK_URL -> default local 127.0.0.1:11434."""
    if cli_fallback_url:
        return cli_fallback_url.rstrip("/")
    if os.getenv("OLLAMA_FALLBACK_URL"):
        return os.getenv("OLLAMA_FALLBACK_URL").rstrip("/")
    return "http://127.0.0.1:11434"


def resolve_fallback_model(cli_fallback_model: Optional[str] = None) -> str:
    """Resolve fallback Ollama model: CLI arg -> OLLAMA_FALLBACK_MODEL -> default pralabs:edge."""
    if cli_fallback_model:
        return cli_fallback_model
    return os.getenv("OLLAMA_FALLBACK_MODEL", "pralabs:edge")


def get_db_connection(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from core.database import configure_sqlite3_connection

        configure_sqlite3_connection(conn)
    except Exception:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA temp_store=MEMORY;")
        cur.execute("PRAGMA cache_size=-64000;")
        cur.close()
    return conn


def find_agy_executable() -> Optional[str]:
    """Check if `agy` exists (`shutil.which('agy')` or `/usr/local/bin/agy`)."""
    agy_path = shutil.which("agy")
    if agy_path:
        return agy_path
    for candidate in ["/usr/local/bin/agy", "/usr/bin/agy", os.path.expanduser("~/.local/bin/agy")]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def execute_agy(prompt: str, system_prompt: Optional[str] = None) -> str:
    """Stream response from Antigravity CLI (`agy`) directly to stdout and return full text."""
    agy_path = find_agy_executable()
    if not agy_path:
        sys.stderr.write("[Error] Antigravity CLI (`agy`) not found in PATH or standard locations.\n")
        return ""

    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    full_response = []
    try:
        proc = subprocess.Popen(
            [agy_path, "-p", full_prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if proc.stdout:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                full_response.append(line)
        _, stderr_output = proc.communicate()
        if proc.returncode != 0:
            sys.stderr.write(f"\n[Warning] `agy` returned non-zero code {proc.returncode}: {stderr_output.strip()}\n")
            if not full_response:
                return ""
    except Exception as e:
        sys.stderr.write(f"\n[Error] Failed to execute Antigravity CLI (`agy`): {e}\n")
        return ""

    return "".join(full_response)


def _call_ollama_stream(
    endpoint_url: str,
    model: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.2,
    timeout: float = 12.0,
) -> Tuple[bool, str]:
    """
    Internal helper to stream from an Ollama endpoint.
    Returns (success: bool, response_text: str).
    """
    endpoint = f"{endpoint_url.rstrip('/')}/api/generate"
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
        },
    }
    if system_prompt:
        payload["system"] = system_prompt

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    full_response = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    text = chunk.get("response", "")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    full_response.append(text)
            print()
            return True, "".join(full_response)
    except Exception as e:
        return False, str(e)


def query_llm_with_fallback(
    prompt: str,
    system_prompt: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    fallback_url: Optional[str] = None,
    fallback_model: Optional[str] = None,
    temperature: float = 0.2,
    timeout: float = 8.0,
    fallback_timeout: float = 45.0,
) -> str:
    """
    Tiered LLM Query Engine:
      1. Primary: Mac Mini GPU (default 192.168.1.39:11434, qwen2.5:7b)
      2. Fallback: Local Pi Ollama (default 127.0.0.1:11434, pralabs:edge with [TASK: PORTFOLIO_AUDITOR])
      3. Guaranteed Offline: Antigravity CLI (`agy`)
    """
    primary_url = resolve_ollama_endpoint(base_url)
    primary_model = resolve_ollama_model(model)
    local_url = resolve_fallback_endpoint(fallback_url)
    local_model = resolve_fallback_model(fallback_model)

    # 1. Try Primary (Mac Mini GPU)
    success, result = _call_ollama_stream(
        endpoint_url=primary_url,
        model=primary_model,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        timeout=timeout,
    )
    if success:
        return result

    sys.stderr.write(
        f"\n[Tier 1 Warning] Primary Ollama ({primary_model} @ {primary_url}) unreachable or failed: {result}.\n"
        f"[*] Tier 2 Failover: Routing to local edge model ({local_model} @ {local_url})...\n"
    )

    # 2. Try Fallback (Local Pi Ollama: pralabs:edge with [TASK: PORTFOLIO_AUDITOR] token)
    fallback_prompt = prompt
    if "[TASK: PORTFOLIO_AUDITOR]" not in fallback_prompt:
        fallback_prompt = f"[TASK: PORTFOLIO_AUDITOR]\n{fallback_prompt}"

    fb_success, fb_result = _call_ollama_stream(
        endpoint_url=local_url,
        model=local_model,
        prompt=fallback_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        timeout=fallback_timeout,
    )
    if fb_success:
        return fb_result

    sys.stderr.write(
        f"\n[Tier 2 Warning] Fallback Ollama ({local_model} @ {local_url}) unreachable or failed: {fb_result}.\n"
        f"[*] Tier 3 Failover: Routing to Antigravity CLI (`agy`)...\n"
    )

    # 3. Try Antigravity CLI
    agy_res = execute_agy(prompt=fallback_prompt, system_prompt=system_prompt)
    if agy_res:
        return agy_res

    sys.stderr.write("[Error] All LLM tiers failed (Mac GPU, Local Ollama, and Antigravity CLI).\n")
    return ""


def stream_ollama(
    prompt: str,
    system_prompt: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    fallback_url: Optional[str] = None,
    fallback_model: Optional[str] = None,
    temperature: float = 0.2,
    timeout: float = 8.0,
    fallback_timeout: float = 45.0,
) -> str:
    """Stream response from tiered LLM pipeline directly to stdout."""
    return query_llm_with_fallback(
        prompt=prompt,
        system_prompt=system_prompt,
        base_url=base_url,
        model=model,
        fallback_url=fallback_url,
        fallback_model=fallback_model,
        temperature=temperature,
        timeout=timeout,
        fallback_timeout=fallback_timeout,
    )
