"""Dual-Engine Circuit Breaker for Local Raspberry Pi Inference.

Monitors health of the primary local inference engine (e.g. Ollama localhost:11434 / host.docker.internal:11434).
If primary is healthy and responsive within timeout (default 800ms TCP probe),
routes inference to primary model (e.g. pralabs:latest).
If primary is offline, unreachable, or times out (>800ms), trips circuit to EDGE mode,
compressing payloads to positional micro-schema ([AUDIT] TKR|STG|MIN|RS|STOP_D%|PNL%|DIST_D|TAX|PRE_ACT)
and dispatching in 4-ticker micro-batches to the edge model (pralabs:edge).
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app_config import settings

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    PRIMARY = "PRIMARY"
    EDGE = "EDGE"


async def probe_endpoint_health(host: str, port: int, timeout_ms: int = 800) -> bool:
    """Async TCP probe testing whether target endpoint responds within timeout_ms.

    Parameters
    ----------
    host : str
        Host or IP address to probe.
    port : int
        TCP port number to probe.
    timeout_ms : int, default 800
        Timeout in milliseconds (default 800ms).

    Returns
    -------
    bool
        True if connection succeeded within timeout_ms, False otherwise.
    """
    timeout_sec = max(0.001, timeout_ms / 1000.0)
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout_sec,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception as e:
        logger.debug("TCP probe failed for %s:%d within %dms: %s", host, port, timeout_ms, e)
        return False


def parse_host_port_from_url(url: str, default_port: int = 11434) -> tuple[str, int]:
    """Extract host and port from HTTP/HTTPS URL."""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"http://{url}"
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or default_port
    return host, port


class DualEngineRouter:
    """Router and state manager for Dual-Engine inference on Raspberry Pi."""

    def __init__(
        self,
        primary_url: Optional[str] = None,
        primary_model: Optional[str] = None,
        edge_url: Optional[str] = None,
        edge_model: Optional[str] = None,
        probe_timeout_ms: int = 800,
        cooldown_seconds: float = 30.0,
    ):
        self.primary_url = (
            primary_url or getattr(settings, "ollama_base_url", "http://host.docker.internal:11434")
        ).rstrip("/")
        self.primary_model = primary_model or getattr(settings, "ollama_model", "pralabs:latest")
        self.edge_url = (
            edge_url or getattr(settings, "ollama_fallback_url", "http://host.docker.internal:11434")
        ).rstrip("/")
        self.edge_model = edge_model or getattr(settings, "ollama_fallback_model", "pralabs:edge")
        self.probe_timeout_ms = probe_timeout_ms
        self.cooldown_seconds = cooldown_seconds

        self._state: CircuitState = CircuitState.PRIMARY
        self._last_state_change: float = time.time()
        self._consecutive_failures: int = 0
        self._last_probe_time: float = 0.0
        self._last_probe_result: bool = True

    @property
    def state(self) -> CircuitState:
        return self._state

    def get_status_telemetry(self) -> Dict[str, Any]:
        """Telemetry reporting current circuit state."""
        return {
            "circuit_state": self._state.value,
            "primary_url": self.primary_url,
            "primary_model": self.primary_model,
            "edge_url": self.edge_url,
            "edge_model": self.edge_model,
            "probe_timeout_ms": self.probe_timeout_ms,
            "last_probe_result": self._last_probe_result,
            "last_state_change": self._last_state_change,
            "consecutive_failures": self._consecutive_failures,
        }

    async def check_and_update_state(self, force_probe: bool = False) -> CircuitState:
        """Probe primary endpoint and update circuit state."""
        now = time.time()
        # Rate limit probes if recently checked and not forced
        if not force_probe and (now - self._last_probe_time < 5.0):
            return self._state

        host, port = parse_host_port_from_url(self.primary_url)
        is_healthy = await probe_endpoint_health(host, port, timeout_ms=self.probe_timeout_ms)
        self._last_probe_time = now
        self._last_probe_result = is_healthy

        if is_healthy:
            if self._state != CircuitState.PRIMARY:
                logger.info(
                    "Primary local engine %s:%d responded healthy (<%dms). Circuit RESET -> PRIMARY (%s)",
                    host,
                    port,
                    self.probe_timeout_ms,
                    self.primary_model,
                )
            self._state = CircuitState.PRIMARY
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._state != CircuitState.EDGE:
                logger.warning(
                    "Primary local engine %s:%d failed health check (>%dms/unreachable). Circuit TRIPPED -> EDGE (%s)",
                    host,
                    port,
                    self.probe_timeout_ms,
                    self.edge_model,
                )
            self._state = CircuitState.EDGE
            self._last_state_change = now

        return self._state

    def trip_to_edge(self, reason: str = ""):
        """Manually trip circuit to EDGE mode."""
        self._state = CircuitState.EDGE
        self._last_state_change = time.time()
        logger.warning("DualEngineRouter tripped to EDGE: %s", reason)

    def reset_to_primary(self):
        """Reset circuit to PRIMARY mode."""
        self._state = CircuitState.PRIMARY
        self._last_state_change = time.time()
        self._consecutive_failures = 0
        logger.info("DualEngineRouter reset to PRIMARY")
