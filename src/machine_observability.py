"""Local machine-observability contract definitions.

This module is intentionally shaped like a future shared library so the
contract can be extracted later with minimal code movement.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MACHINE_OBSERVABILITY_SCHEMA_VERSION = 1


class MetricsEndpoint(BaseModel):
    """One metrics scrape endpoint declared by a principal charm."""

    model_config = ConfigDict(extra="forbid")

    targets: list[str]
    path: str = "/metrics"
    scheme: str = "http"
    interval: str = ""
    timeout: str = ""
    tls: dict[str, str | bool] = Field(default_factory=dict)


class LogFileSource(BaseModel):
    """A file log source declared by a principal charm."""

    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


class MachineObservabilityPayload(BaseModel):
    """Neutral source declarations from a principal charm."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[MACHINE_OBSERVABILITY_SCHEMA_VERSION] = (
        MACHINE_OBSERVABILITY_SCHEMA_VERSION
    )
    charm_name: str = ""
    metrics_endpoints: list[MetricsEndpoint] = Field(default_factory=list)
    systemd_units: list[str] = Field(default_factory=list)
    journal_match_expressions: list[str] = Field(default_factory=list)
    log_files: list[LogFileSource] = Field(default_factory=list)


def load_machine_observability_payload(relation: Any) -> MachineObservabilityPayload:
    """Load and validate the remote application payload for machine-observability."""

    raw_payload = "{}"

    if hasattr(relation, "remote_app_data"):
        raw_payload = relation.remote_app_data.get("payload", "{}")
    else:
        app = getattr(relation, "app", None)
        if app is None:
            return MachineObservabilityPayload()
        raw_payload = relation.data[app].get("payload", "{}")

    return MachineObservabilityPayload.model_validate(json.loads(raw_payload))
