"""algo/ai_analyst.py - Micro-Batched Edge Dispatcher with GBNF Grammar Support.

Optimized specifically for isolated Raspberry Pi CPU inference (Cortex-A76 / LPDDR4X bandwidth).
Features:
1. GBNF Grammar Enforcement at the C++ logits sampling layer (zero syntax errors / float hallucination).
2. Micro-Chunk Dispatching (max 2 tickers per call) to avoid attention dilution on small 1.5B edge models.
3. Pre-computed deltas in pure Python so the edge CPU never computes floating point divisions.
4. Deterministic fallback if the local Ollama daemon is unreachable.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/generate"
MODEL_NAME = os.getenv("OLLAMA_MODEL", "pralabs:edge")
GRAMMAR_FILE = Path(__file__).resolve().parent.parent / "config" / "audit_grammar.gbnf"


def load_gbnf_grammar() -> Optional[str]:
    """Load the GBNF context-free grammar string if available."""
    if GRAMMAR_FILE.exists():
        try:
            return GRAMMAR_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Could not load GBNF grammar from {GRAMMAR_FILE}: {e}")
    return None


def format_telemetry_line(h: Dict[str, Any]) -> str:
    """Pre-computes deltas so the 1.5B edge CPU never executes division.
    
    Format:
        TKR|STG:stage|MIN:minervini|RS:rs|STOP:stop_margin%|PNL:pnl%|DIST:dist_days|ASSET:asset_class
    """
    ticker = str(h.get("ticker", "TKR")).upper()
    stage = h.get("stage", 2)
    cmp = float(h.get("cmp", h.get("price", h.get("current_price", 100.0))))
    stop = float(h.get("stop_loss_price", h.get("stop_loss", cmp * 0.95)))
    
    if stop > 0:
        stop_margin = round(((cmp - stop) / stop) * 100, 1)
    else:
        stop_margin = 0.0
    sign = "+" if stop_margin >= 0 else ""
    
    pnl = float(h.get("unrealized_pnl_pct", h.get("pnl_pct", 0.0)))
    pnl_sign = "+" if pnl >= 0 else ""
    
    minervini = h.get("minervini", h.get("tt_score", "7/7"))
    if isinstance(minervini, int):
        minervini = f"{minervini}/7"
    
    rs = h.get("rs", h.get("rs_score", 80))
    dist = h.get("dist_days", 0)
    asset = h.get("asset_class", "EQ")
    if asset == "EQUITY":
        asset = "EQ"
    elif asset == "COMMODITY_ETF":
        asset = "COMMODITY"
    elif asset == "REIT_INVIT":
        asset = "REIT"
    
    return (
        f"{ticker}|STG:{stage}|MIN:{minervini}|"
        f"RS:{rs}|STOP:{sign}{stop_margin}%|PNL:{pnl_sign}{pnl:.1f}%|"
        f"DIST:{dist}|ASSET:{asset}"
    )


def query_edge_analyst(prompt: str, grammar: Optional[str] = None, timeout: float = 20.0) -> Optional[str]:
    """Execute raw Ollama request with optional GBNF grammar constraints."""
    grammar_text = grammar if grammar is not None else load_gbnf_grammar()
    
    options: Dict[str, Any] = {
        "temperature": 0.0,
        "num_predict": 100,
        "num_thread": 4,
    }
    if grammar_text:
        options["grammar"] = grammar_text

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except Exception as e:
        logger.warning(f"Edge Ollama call failed ({e}). Attempting fallback without grammar if present.")
        # Some older Ollama versions might reject the grammar option key; fallback cleanly:
        if grammar_text and "grammar" in options:
            options.pop("grammar", None)
            try:
                req = urllib.request.Request(
                    OLLAMA_URL,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("response", "").strip()
            except Exception as e2:
                logger.error(f"Fallback without grammar also failed: {e2}")
        return None


def audit_holdings(holdings: List[Dict[str, Any]], chunk_size: int = 2) -> List[str]:
    """Chunks 40+ portfolio holdings into discrete, high-fidelity 2-ticker payloads."""
    results = []
    grammar_text = load_gbnf_grammar()

    for i in range(0, len(holdings), chunk_size):
        chunk = holdings[i : i + chunk_size]
        header = "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|ASSET\n"
        body = "\n".join([format_telemetry_line(item) for item in chunk])
        prompt = f"<|im_start|>user\n{header}{body}<|im_end|>\n<|im_start|>assistant\n"

        resp = query_edge_analyst(prompt, grammar=grammar_text, timeout=25.0)

        if resp:
            output_lines = [line.strip() for line in resp.strip().splitlines() if line.strip()]
            results.extend(output_lines)
        else:
            # Deterministic rule-based fallback if Ollama hangs
            for h in chunk:
                ticker = h.get("ticker", "TKR")
                stg = h.get("stage", 2)
                pnl = h.get("unrealized_pnl_pct", h.get("pnl_pct", 0.0))
                cmp = float(h.get("cmp", h.get("price", h.get("current_price", 100.0))))
                stop = float(h.get("stop_loss_price", h.get("stop_loss", cmp * 0.95)))
                breached = cmp < stop

                if breached or stg == 4:
                    act = "EXIT"
                elif stg == 3 or h.get("dist_days", 0) >= 5:
                    act = "TRIM"
                elif stg == 2 and pnl > 50.0:
                    act = "LOCK_PROFIT"
                elif stg == 2:
                    act = "HOLD"
                else:
                    act = "HOLD"

                status_str = f"BREACH ({round((cmp-stop)/stop*100, 1)}%)" if breached else f"PASS (+{round((cmp-stop)/stop*100, 1)}%)"
                results.append(f"{ticker} | STG:{stg} | STOP:{status_str} | ACT:{act}")
                results.append(f"• Rule-based deterministic assessment (P&L: {pnl:+.1f}%).")

    return results
