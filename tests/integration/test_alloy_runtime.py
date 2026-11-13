"""Runtime checks that rendered profiling config survives Alloy component evaluation.

`alloy fmt` (what the charm validates with) and `alloy validate` only check
syntax, so semantic errors such as pyroscope.scrape's delta_profiling_duration
rule only show up once `alloy run` evaluates the components. These tests run a
real Alloy binary and are skipped unless ALLOY_BINARY points at one.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charms.dwellir_observability.v0.pprof_scrape import PROFILE_TYPES

from src.config_builder import ConfigBuilder, ProfilingSource

ALLOY_BINARY = os.environ.get("ALLOY_BINARY", "")
READY_TIMEOUT_S = 30

pytestmark = pytest.mark.skipif(
    not (ALLOY_BINARY and os.access(ALLOY_BINARY, os.X_OK)),
    reason="ALLOY_BINARY not set to an executable Alloy binary",
)


def _render(interval: str) -> str:
    source = ProfilingSource(
        service_name="my-app",
        targets=["127.0.0.1:6060"],
        labels={"juju_application": "my-app", "juju_unit": "my-app/0", "instance": "my-app/0"},
        profile_types=list(PROFILE_TYPES),
    )
    return ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=[],
        metrics_scrape_jobs=[],
        systemd_units=[],
        journal_match_expressions=[],
        file_log_sources=[],
        topology_labels={},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=[],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
        profiling_sources=[source],
        profiling_sink_url="http://127.0.0.1:4040",
        profiles_scrape_interval=interval,
    ).build()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_alloy(config_text: str, tmp_path: Path) -> tuple[bool, str]:
    """Start Alloy on config_text; return (became ready while still running, log)."""
    config_path = tmp_path / "config.alloy"
    config_path.write_text(config_text)
    log_path = tmp_path / "alloy.log"
    port = _free_port()
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                ALLOY_BINARY,
                "run",
                f"--storage.path={tmp_path / 'data'}",
                f"--server.http.listen-addr=127.0.0.1:{port}",
                str(config_path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + READY_TIMEOUT_S
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    return False, log_path.read_text()
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/-/ready", timeout=1) as response:
                        if response.status == 200 and process.poll() is None:
                            return True, log_path.read_text()
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.25)
            return False, log_path.read_text()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.parametrize("interval", ["15s", "10s", "3s", "1m"])
def test_profiling_config_starts_under_alloy(interval, tmp_path):
    ready, log = _run_alloy(_render(interval), tmp_path)

    assert ready, f"Alloy did not start with profiles_scrape_interval={interval}:\n{log}"


def test_harness_detects_delta_profiling_failure(tmp_path):
    """Negative control: without the derived duration, a 10s interval must fail at runtime."""
    config = "\n".join(line for line in _render("10s").splitlines() if "delta_profiling_duration" not in line)

    ready, log = _run_alloy(config + "\n", tmp_path)

    assert not ready
    assert "delta_profiling_duration must be at least 1 second smaller than scrape_interval" in log
