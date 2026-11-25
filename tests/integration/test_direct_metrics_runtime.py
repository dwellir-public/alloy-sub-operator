"""Disposable remote-write proof for direct jobs and local canary textfiles."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from src.config_builder import ConfigBuilder, HostMetrics
from src.principal_context import PrincipalContext
from src.related_metrics import related_scrape_jobs

ALLOY_BINARY = os.environ.get("ALLOY_BINARY", "")
pytestmark = pytest.mark.skipif(not ALLOY_BINARY, reason="set ALLOY_BINARY for disposable runtime proof")


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _json(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def _samples(prometheus, metric):
    try:
        return _json(prometheus + "/api/v1/query?" + urllib.parse.urlencode({"query": metric}))["data"]["result"]
    except (OSError, urllib.error.URLError):
        return []


def test_related_and_textfile_metrics_arrive_with_principal_identity(tmp_path):
    name = "alloy-sub-direct-" + uuid.uuid4().hex[:10]
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--publish",
            "127.0.0.1::9090",
            "prom/prometheus:v3.7.3",
            "--config.file=/etc/prometheus/prometheus.yml",
            "--web.enable-remote-write-receiver",
        ],
        check=True,
        capture_output=True,
    )
    process = None
    server = None
    try:
        binding = subprocess.check_output(["docker", "port", name, "9090/tcp"], text=True).strip()
        prometheus = "http://" + binding
        directory = tmp_path / "textfile"
        directory.mkdir()
        (directory / "canary.prom").write_text('internal_bcm_check_success{target="fixture",kind="execution"} 1\n')

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"fixture_head 123\n"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        principal = PrincipalContext(
            "bcm-internal",
            "bcm-internal/1",
            "127.0.0.1",
            "internal-iris-stack-s1",
            "8d669f32-2107-45af-8eba-8d5a0fe48585",
        )
        labels = principal.juju_labels()
        jobs = related_scrape_jobs(
            [
                {
                    "job_name": "direct",
                    "scrape_interval": "1s",
                    "scrape_timeout": "500ms",
                    "static_configs": [{"targets": [f"127.0.0.1:{server.server_port}"], "labels": labels}],
                    "relabel_configs": [{"source_labels": ["juju_unit"], "target_label": "instance"}],
                }
            ],
            principal,
        )
        with patch("src.config_builder.HOST_METRICS_SCRAPE_INTERVAL", "1s"):
            config = ConfigBuilder(
                loki_endpoints=[],
                remote_write_endpoints=[prometheus + "/api/v1/write"],
                metrics_scrape_jobs=jobs,
                systemd_units=[],
                journal_match_expressions=[],
                file_log_sources=[],
                topology_labels=labels,
                global_scrape_interval="1s",
                global_scrape_timeout="500ms",
                path_exclude=[],
                queue_size=1000,
                max_elapsed_time_min=5,
                tls_insecure_skip_verify=False,
                host_metrics=HostMetrics(topology_labels=labels, textfile_directory=str(directory)),
            ).build()
        path = tmp_path / "config.alloy"
        path.write_text(config)
        subprocess.run([ALLOY_BINARY, "validate", str(path)], check=True, capture_output=True)
        with (tmp_path / "alloy.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    ALLOY_BINARY,
                    "run",
                    f"--storage.path={tmp_path / 'data'}",
                    f"--server.http.listen-addr=127.0.0.1:{_port()}",
                    str(path),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 40
            found = {}
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / "alloy.log").read_text()
                for metric in ("fixture_head", "internal_bcm_check_success"):
                    found[metric] = _samples(prometheus, metric)
                if all(found.values()):
                    break
                time.sleep(0.5)
            assert all(found.values()), (found, (tmp_path / "alloy.log").read_text())
            for metric, rows in found.items():
                assert len(rows) == 1
                for key, value in labels.items():
                    assert rows[0]["metric"][key] == value
            assert found["fixture_head"][0]["metric"]["instance"] == principal.unit
            assert found["internal_bcm_check_success"][0]["metric"]["target"] == "fixture"
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        if server is not None:
            server.shutdown()
            server.server_close()
        subprocess.run(["docker", "rm", "--force", "--volumes", name], check=True, capture_output=True)
