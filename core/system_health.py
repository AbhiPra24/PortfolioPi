"""System, daemon, and broker sync health check utilities for PortfolioPi."""

import asyncio
import datetime
import logging
from typing import Any, Dict
from zoneinfo import ZoneInfo

import aiosqlite

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


async def check_service_active(service_name: str) -> str:
    """Check systemd service status via systemctl is-active."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        status = stdout.decode().strip()
        return status if status else "unknown"
    except Exception:
        return "unknown"


async def inspect_recent_journal_anomalies(hours: int = 3) -> Dict[str, Any]:
    """Inspect recent journalctl logs for portfoliopi and overwatcher to detect anomalies."""
    try:
        since_arg = f"{hours} hours ago"
        proc = await asyncio.create_subprocess_exec(
            "journalctl",
            "-u",
            "portfoliopi",
            "-u",
            "overwatcher",
            "-b",
            "0",
            "--since",
            since_arg,
            "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        lines = stdout.decode().splitlines()

        crashes = 0
        network_errors = 0
        in_network_trace = False

        for line in lines:
            # Filter out expected or noise patterns
            if "Suppressed BLE alert" in line or "due to fingerprint recency" in line:
                continue

            lower_line = line.lower()

            # Detect network polling exceptions (ReadError, gaierror, NetworkError)
            if "readerror" in lower_line or "gaierror" in lower_line or "networkerror" in lower_line:
                network_errors += 1
                in_network_trace = True
                continue

            if in_network_trace:
                if "traceback" in lower_line or "file \"" in lower_line or "during handling" in lower_line:
                    continue
                else:
                    in_network_trace = False

            if "conflict: terminated by other getupdates" in lower_line:
                crashes += 1
            elif "exception happened while polling for updates" in lower_line:
                network_errors += 1
                in_network_trace = True
            elif "traceback (most recent call last)" in lower_line or "critical" in lower_line or "fatal" in lower_line:
                crashes += 1

        if crashes > 0:
            level = "CRITICAL"
            summary = f"⚠️ {crashes} exception/crash events in last {hours}h"
        elif network_errors > 0:
            level = "WARNING"
            summary = f"⚡ {network_errors} intermittent Telegram network drops (auto-recovered)"
        else:
            level = "HEALTHY"
            summary = "✅ Stable (0 errors in last 3h)"

        return {
            "level": level,
            "crash_count": crashes,
            "network_error_count": network_errors,
            "summary": summary,
        }
    except Exception as e:
        logger.debug(f"Could not inspect journal logs: {e}")
        return {
            "level": "UNKNOWN",
            "critical_count": 0,
            "network_error_count": 0,
            "summary": "Log inspection unavailable",
        }


async def get_system_and_sync_health(db: aiosqlite.Connection) -> Dict[str, Any]:
    """Inspect ICICI Breeze auth freshness, services uptime, anomalies, and data health."""
    now_ist = datetime.datetime.now(IST)

    # 1. Breeze Sync Status
    breeze_info = {
        "status": "MISSING",
        "badge": "⚪",
        "detail": "No session recorded",
        "last_sync": "Never",
        "action_required": True,
    }

    try:
        async with db.execute(
            "SELECT token, timestamp FROM session_tokens ORDER BY timestamp DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if row and row[1]:
                ts_str = row[1]
                # Format: YYYY-MM-DD HH:MM:SS (UTC)
                try:
                    dt_utc = datetime.datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=datetime.timezone.utc
                    )
                    dt_ist = dt_utc.astimezone(IST)
                    breeze_info["last_sync"] = dt_ist.strftime("%d %b %H:%M IST")

                    if dt_ist.date() == now_ist.date():
                        breeze_info["status"] = "ACTIVE"
                        breeze_info["badge"] = "🟢"
                        breeze_info["detail"] = f"Breeze Active (Token fresh from {dt_ist.strftime('%H:%M IST')})"
                        breeze_info["action_required"] = False
                    else:
                        breeze_info["status"] = "EXPIRED"
                        breeze_info["badge"] = "🟡"
                        breeze_info["detail"] = (
                            f"Breeze Expired (Last synced {dt_ist.strftime('%d %b')}, YFinance fallback active)"
                        )
                        breeze_info["action_required"] = True
                except Exception:
                    breeze_info["last_sync"] = ts_str
    except Exception as e:
        logger.warning(f"Error checking session_tokens: {e}")

    # 2. Systemd Services
    portfoliopi_status = await check_service_active("portfoliopi")
    overwatcher_status = await check_service_active("overwatcher")

    # 3. Recent Anomalies
    anomalies = await inspect_recent_journal_anomalies(hours=4)

    # 4. Data Health (failures in past 24 hours)
    failures_24h = 0
    try:
        async with db.execute(
            "SELECT COUNT(*) FROM data_health WHERE timestamp IS NOT NULL AND timestamp >= datetime('now', '-24 hours') AND status = 'failed'"
        ) as cur:
            row = await cur.fetchone()
            if row:
                failures_24h = row[0]
    except Exception:
        pass

    return {
        "breeze": breeze_info,
        "services": {
            "portfoliopi": portfoliopi_status,
            "overwatcher": overwatcher_status,
        },
        "anomalies": anomalies,
        "data_health_failures_24h": failures_24h,
    }
