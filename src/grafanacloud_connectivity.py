"""Outbound endpoint connectivity checks for Grafana Cloud sinks."""

from __future__ import annotations

import os
import tempfile

import requests

try:
    from .outbound_endpoints import OutboundEndpoint
except ImportError:
    from outbound_endpoints import OutboundEndpoint


def probe_endpoint(endpoint: OutboundEndpoint, *, timeout: float = 5.0) -> tuple[bool, str]:
    """Probe one HTTP endpoint with optional basic auth and CA material."""
    auth = None
    if endpoint.username and endpoint.password:
        auth = (endpoint.username, endpoint.password)

    verify: bool | str = True
    ca_path = None
    if endpoint.tls_ca_pem:
        ca_path = _write_ca_file(endpoint.tls_ca_pem)
        verify = ca_path

    try:
        response = requests.request(
            "HEAD",
            endpoint.url,
            timeout=timeout,
            auth=auth,
            verify=verify,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return False, str(exc)
    finally:
        if ca_path is not None:
            os.unlink(ca_path)

    if response.status_code in {200, 204, 401, 403, 405}:
        return True, f"http {response.status_code}"
    return False, f"http {response.status_code}"


def _write_ca_file(tls_ca_pem: str) -> str:
    """Write CA PEM content to a temporary file for requests verification."""
    with tempfile.NamedTemporaryFile("w", delete=False) as handle:
        handle.write(tls_ca_pem)
        return handle.name
