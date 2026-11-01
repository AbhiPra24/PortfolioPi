"""
Shared Ollama & SQLite helper utilities for PortfolioPi AI analysis scripts.
Pure standard-library implementation (zero external dependencies).
Supports local Ollama, remote RasPi Ollama (:11434), and automatic fallback to Antigravity CLI (`agy`).
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def resolve_db_path(cli_path: Optional[str] = None) -> str:
    """Find the best database path across environments (Local Mac, RasPi, Docker)."""
    candidates = [
        cli_path,
        os.getenv("SQLITE_DB_PATH"),
        os.getenv("DB_PATH"),
        "/data/Work/portfoliopi/data/portfoliopi.db",
        "/mnt/usb-storage/Work/portfoliopi/data/portfoliopi.db",
        "data/portfoliopi.db",
        "backups/portfoliopi_local.db",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "backups/portfoliopi_local.db"


def resolve_ollama_endpoint(cli_url: Optional[str] = None) -> str:
    """Resolve Ollama endpoint: CLI arg -> OLLAMA_BASE_URL -> Default local (:11434)."""
    if cli_url:
        return cli_url.rstrip("/")
    if os.getenv("OLLAMA_BASE_URL"):
        return os.getenv("OLLAMA_BASE_URL").rstrip("/")
    # Default local
    return "http://localhost:11434"


def resolve_ollama_model(cli_model: Optional[str] = None) -> str:
    """Resolve Ollama model: CLI arg -> OLLAMA_MODEL -> default qwen2.5:7b."""
    if cli_model:
        return cli_model
    return os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def get_db_connection(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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


def query_llm_with_fallback(
    prompt: str,
    system_prompt: Optional[str] = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    temperature: float = 0.2,
    timeout: float = 12.0,
) -> str:
    """
    Query Ollama with streaming output and automatic fallback to Antigravity CLI (`agy`).
    """
    endpoint = f"{base_url.rstrip('/')}/api/generate"
    payload = {
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
    ollama_failed = False

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
            return "".join(full_response)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        ollama_failed = True
        sys.stderr.write(f"\n[Warning] Ollama unreachable or timed out. Falling back to Antigravity CLI (`agy`)...\n")
    except Exception as e:
        ollama_failed = True
        sys.stderr.write(f"\n[Warning] Ollama call failed ({e}). Falling back to Antigravity CLI (`agy`)...\n")

    if ollama_failed:
        agy_res = execute_agy(prompt=prompt, system_prompt=system_prompt)
        if agy_res:
            return agy_res
        sys.stderr.write("[Error] Neither Ollama nor Antigravity CLI (`agy`) could generate a response.\n")
        return ""

    return "".join(full_response)


def stream_ollama(
    prompt: str,
    system_prompt: Optional[str] = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    temperature: float = 0.2,
    timeout: float = 12.0,
) -> str:
    """Stream response from Ollama API directly to stdout with automatic fallback to Antigravity CLI (`agy`)."""
    return query_llm_with_fallback(
        prompt=prompt,
        system_prompt=system_prompt,
        base_url=base_url,
        model=model,
        temperature=temperature,
        timeout=timeout,
    )
