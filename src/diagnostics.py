"""Read-only, bounded diagnostics for the machine's Alloy service."""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from . import alloy
    from .config_builder import DEFAULT_CONFIG_PATH
except ImportError:
    import alloy
    from config_builder import DEFAULT_CONFIG_PATH

COMMAND_TIMEOUT = 10
MAX_LOG_BYTES = 16 * 1024
SERVICE_PROPERTIES = ("ActiveState", "SubState", "MainPID", "ExecMainStartTimestamp")


def _command(args: list[str]) -> str:
    """Run a fixed read-only probe without exposing subprocess error output."""
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=COMMAND_TIMEOUT).stdout


def _redact_logs(text: str) -> str:
    """Remove common credential forms from journal messages before returning them."""
    text = re.sub(r"https?://[^\s\"<>]+", "[redacted-url]", text)
    text = re.sub(r"(?i)bearer\s+[^\s\"]+", "Bearer [redacted]", text)
    return re.sub(
        r'(?i)((?:password|token|api[_-]?key|authorization)\s*[=:]\s*)("[^"\n]*"|[^\s,]+)',
        r"\1[redacted]",
        text,
    )


def _service() -> dict[str, str]:
    """Report systemd state and process identity, including inactive services."""
    try:
        output = _command(
            [
                "systemctl",
                "show",
                "alloy.service",
                "--no-pager",
                "--property=" + ",".join(SERVICE_PROPERTIES),
            ]
        )
    except (OSError, subprocess.SubprocessError):
        return {"service-state": "unknown", "service-query": "unavailable"}
    properties = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return {
        "service-query": "ok",
        "service-state": properties.get("ActiveState", "unknown"),
        "service-sub-state": properties.get("SubState", "unknown"),
        "main-pid": properties.get("MainPID", "unknown"),
        "process-start-time": properties.get("ExecMainStartTimestamp", "unknown"),
    }


def _config() -> dict[str, str]:
    """Check syntax without returning config contents or parser diagnostics."""
    path = Path(DEFAULT_CONFIG_PATH)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return {"config-syntax": "unavailable", "config-sha256": "unavailable"}
    result = {"config-sha256": digest}
    try:
        alloy.verify_config(config_path=path)
        result["config-syntax"] = "valid"
    except subprocess.CalledProcessError:
        result["config-syntax"] = "invalid"
    except (OSError, subprocess.TimeoutExpired):
        result["config-syntax"] = "unavailable"
    return result


def collect(*, lookback_minutes: int, log_lines: int) -> dict[str, str | bool]:
    """Collect diagnostics; an unavailable probe never masquerades as healthy."""
    if not 1 <= lookback_minutes <= 60 or not 0 <= log_lines <= 100:
        raise ValueError("lookback-minutes must be 1-60 and log-lines must be 0-100")
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=lookback_minutes)).strftime("%Y-%m-%d %H:%M:%S UTC")
    result: dict[str, str | bool] = {
        "observed-at": now.isoformat(),
        **_service(),
        **_config(),
        "logs-since": since,
        "logs-status": "not-requested",
        "recent-logs": "",
        "logs-truncated": False,
    }
    if not log_lines:
        return result
    try:
        logs = _command(
            [
                "journalctl",
                "--unit=alloy.service",
                "--since",
                since,
                f"--lines={log_lines}",
                "--output=short-iso",
                "--no-pager",
                "--quiet",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        result["logs-status"] = "unavailable"
        return result
    logs_bytes = _redact_logs(logs).encode("utf-8")
    result["logs-status"] = "ok"
    result["logs-truncated"] = len(logs_bytes) > MAX_LOG_BYTES
    result["recent-logs"] = logs_bytes[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
    return result
