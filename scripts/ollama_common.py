"""
Shared Ollama & SQLite helper utilities for PortfolioPi AI analysis scripts.
Pure standard-library implementation (zero external dependencies).
Supports local Ollama, remote RasPi OmniRoute (:20128), and direct RasPi Ollama (:11434).
"""

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
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
        "backups/portfoliopi_local.db"
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "backups/portfoliopi_local.db"


def resolve_ollama_endpoint(cli_url: Optional[str] = None) -> str:
    """Resolve Ollama endpoint: CLI arg -> OLLAMA_BASE_URL -> OmniRoute/Local."""
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


def stream_ollama(
    prompt: str,
    system_prompt: Optional[str] = None,
    base_url: str = "http://localhost:11434",
    model: str = "qwen2.5:7b",
    temperature: float = 0.2
) -> str:
    """Stream response from Ollama API directly to stdout and return full text."""
    endpoint = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": temperature,
            "top_p": 0.9
        }
    }
    if system_prompt:
        payload["system"] = system_prompt

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    full_response = []
    try:
        with urllib.request.urlopen(req) as resp:
            for line in resp:
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    text = chunk.get("response", "")
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    full_response.append(text)
            print()
    except urllib.error.URLError as e:
        sys.stderr.write(f"\n[Error] Unable to reach Ollama at {base_url}: {e}\n")
        sys.stderr.write("Tip: If running on RasPi or remote, specify `--ollama-url http://<host>:11434` or set OLLAMA_BASE_URL.\n")
        sys.exit(1)

    return "".join(full_response)
