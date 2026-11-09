import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.circuit_breaker import (
    CircuitState,
    DualEngineRouter,
    probe_endpoint_health,
    parse_host_port_from_url,
)
from core.ai_analyst import (
    build_compressed_telemetry,
    build_compressed_prompt,
    generate_ai_analysis,
    generate_batch_ai_analysis,
)


@pytest.mark.anyio
async def test_probe_endpoint_health_success():
    """Verify async TCP probe returns True when port connection succeeds."""
    async def mock_open_connection(host, port):
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        return MagicMock(), writer

    with patch("asyncio.open_connection", side_effect=mock_open_connection):
        healthy = await probe_endpoint_health("127.0.0.1", 11434, timeout_ms=800)
        assert healthy is True


@pytest.mark.anyio
async def test_probe_endpoint_health_timeout():
    """Verify async TCP probe returns False when connection times out (>800ms)."""
    async def mock_timeout(host, port):
        await asyncio.sleep(0.05)
        raise asyncio.TimeoutError()

    with patch("asyncio.open_connection", side_effect=mock_timeout):
        healthy = await probe_endpoint_health("127.0.0.1", 11434, timeout_ms=10)
        assert healthy is False


@pytest.mark.anyio
async def test_probe_endpoint_health_refused():
    """Verify async TCP probe returns False when connection is refused."""
    with patch("asyncio.open_connection", side_effect=ConnectionRefusedError("Connection refused")):
        healthy = await probe_endpoint_health("127.0.0.1", 11434, timeout_ms=800)
        assert healthy is False


def test_parse_host_port():
    h1, p1 = parse_host_port_from_url("http://127.0.0.1:11434")
    assert h1 == "127.0.0.1" and p1 == 11434

    h2, p2 = parse_host_port_from_url("http://host.docker.internal:11434")
    assert h2 == "host.docker.internal" and p2 == 11434

    h3, p3 = parse_host_port_from_url("192.168.1.16:8080")
    assert h3 == "192.168.1.16" and p3 == 8080


@pytest.mark.anyio
async def test_router_trips_to_edge_on_probe_failure():
    """When primary engine probe fails, router state transitions to EDGE."""
    router = DualEngineRouter(
        primary_url="http://127.0.0.1:11434",
        primary_model="pralabs:latest",
        edge_url="http://127.0.0.1:11434",
        edge_model="pralabs:edge",
        probe_timeout_ms=800,
    )

    with patch("core.circuit_breaker.probe_endpoint_health", AsyncMock(return_value=False)):
        state = await router.check_and_update_state(force_probe=True)
        assert state == CircuitState.EDGE
        assert router.state == CircuitState.EDGE

        telemetry = router.get_status_telemetry()
        assert telemetry["circuit_state"] == "EDGE"
        assert telemetry["last_probe_result"] is False
        assert telemetry["consecutive_failures"] >= 1


@pytest.mark.anyio
async def test_router_recovers_to_primary_on_probe_success():
    """When primary engine probe succeeds after failure, circuit resets to PRIMARY."""
    router = DualEngineRouter()
    router.trip_to_edge("Manual trip for test")
    assert router.state == CircuitState.EDGE

    with patch("core.circuit_breaker.probe_endpoint_health", AsyncMock(return_value=True)):
        state = await router.check_and_update_state(force_probe=True)
        assert state == CircuitState.PRIMARY
        assert router.state == CircuitState.PRIMARY

        telemetry = router.get_status_telemetry()
        assert telemetry["circuit_state"] == "PRIMARY"
        assert telemetry["consecutive_failures"] == 0


def test_payload_compression_positional_micro_schema():
    """Verify conversion of verbose metrics to positional micro-schema [AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|TAX|PRE_ACT."""
    metrics = {
        "ticker": "BHEL",
        "stage": 2,
        "tt_score": "7/7",
        "rs_score": 84,
        "price": 248.75,
        "stop_loss": 250.0,
        "dist_days": 2,
        "action": "SELL / STOP SIP",
    }
    portfolio_ctx = {
        "pnl_pct": 52.2,
        "tax_term": "LT",
    }
    line = build_compressed_telemetry("BHEL", metrics, portfolio_ctx)
    assert line == "[AUDIT] BHEL|2|7/7|84|-0.5%|+52.2%|2|LT|EXIT_STOP"

    prompt = build_compressed_prompt([line])
    assert "Positional Schema:" in prompt
    assert "[AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|TAX|PRE_ACT" in prompt
    assert "[AUDIT] BHEL|2|7/7|84|-0.5%|+52.2%|2|LT|EXIT_STOP" in prompt


@pytest.mark.anyio
async def test_batch_micro_chunking_edge_mode_4_tickers():
    """In EDGE fallback mode, generate_batch_ai_analysis enforces 4-ticker micro-batches."""
    router = DualEngineRouter(
        edge_model="pralabs:edge",
    )
    router.trip_to_edge("Simulate tripped edge mode")
    assert router.state == CircuitState.EDGE

    mock_post = AsyncMock(side_effect=[
        "T1 | STG:2 | STOP:+1.0% | ACT:HOLD\nT2 | STG:2 | STOP:+2.0% | ACT:HOLD\nT3 | STG:2 | STOP:+3.0% | ACT:HOLD\nT4 | STG:2 | STOP:+4.0% | ACT:HOLD",
        "T5 | STG:2 | STOP:+5.0% | ACT:HOLD\nT6 | STG:2 | STOP:+6.0% | ACT:HOLD",
    ])

    items = [
        {"ticker": f"T{i}", "metrics": {"price": 100 * i, "stop_loss": 90 * i, "stage": 2}}
        for i in range(1, 7)
    ]

    with patch("core.circuit_breaker.probe_endpoint_health", AsyncMock(return_value=False)):
        with patch("core.ai_analyst._post_ollama", mock_post):
            results = await generate_batch_ai_analysis(items, router=router)
            assert len(results) == 6
            # 6 items in batches of 4 = 2 calls
            assert mock_post.call_count == 2

            # Check that model requested was edge model
            for call in mock_post.call_args_list:
                args, kwargs = call
                # url, model, prompt...
                assert args[1] == "pralabs:edge"
