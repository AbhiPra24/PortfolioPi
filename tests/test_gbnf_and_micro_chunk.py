"""Unit and integration tests for GBNF grammar formatting, micro-batched dispatch, and prompt compression."""

import pytest
from pathlib import Path
from algo.ai_analyst import (
    load_gbnf_grammar,
    format_telemetry_line,
    audit_holdings,
    GRAMMAR_FILE,
)


def test_gbnf_grammar_file_exists():
    assert GRAMMAR_FILE.exists(), f"Missing GBNF grammar at {GRAMMAR_FILE}"
    grammar = load_gbnf_grammar()
    assert grammar is not None
    assert "root ::=" in grammar
    assert "ticker ::=" in grammar
    assert "stop_status ::=" in grammar
    assert "action ::=" in grammar


def test_format_telemetry_line():
    sample_holding = {
        "ticker": "BHEL",
        "stage": 2,
        "cmp": 406.20,
        "stop_loss_price": 408.31,
        "minervini": "7/7",
        "rs": 84,
        "unrealized_pnl_pct": 52.2,
        "dist_days": 2,
        "asset_class": "EQUITY",
    }
    line = format_telemetry_line(sample_holding)
    # Target format: TKR|STG:stage|MIN:min|RS:rs|STOP:margin|PNL:pnl|DIST:dist|ASSET:EQ
    assert line == "BHEL|STG:2|MIN:7/7|RS:84|STOP:-0.5%|PNL:+52.2%|DIST:2|ASSET:EQ"


def test_audit_holdings_fallback_deterministic(monkeypatch):
    # Test fallback path when Ollama raises an error or is unreachable
    import algo.ai_analyst as analyst_mod
    monkeypatch.setattr(analyst_mod, "query_edge_analyst", lambda *args, **kwargs: None)

    holdings = [
        {"ticker": "XYZ", "stage": 4, "cmp": 100, "stop_loss": 105, "pnl_pct": -15.0},
        {"ticker": "ABC", "stage": 2, "cmp": 200, "stop_loss": 180, "pnl_pct": 60.0},
    ]
    results = audit_holdings(holdings, chunk_size=2)
    assert len(results) >= 2
    assert any("XYZ" in r and "EXIT" in r for r in results)
    assert any("ABC" in r and "LOCK_PROFIT" in r for r in results)
