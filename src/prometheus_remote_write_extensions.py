"""Helpers for publishing outbound telemetry relation metadata."""

from __future__ import annotations

import re


def _normalize_tenant_component(value: str) -> str:
    """Normalize relation metadata into a safe tenant-id component.

    Strategy:
    - lowercase the input
    - replace non-alphanumeric separators with `-`
    - collapse duplicate separators and trim edges
    """
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def _short_model_uuid(model_uuid: str) -> str:
    """Return the first eight hexadecimal characters from a model UUID."""
    return _normalize_tenant_component(model_uuid).replace("-", "")[:8]


def _tenant_id_for_relation(application: str, model_uuid: str) -> str:
    """Derive a readable tenant id from application and model identity.

    Strategy:
    - use the application name as the human-readable base
    - append a short model UUID suffix when available to avoid cross-model collisions
    """
    base = application
    short_model_uuid = _short_model_uuid(model_uuid)
    if short_model_uuid:
        base = f"{application}-{short_model_uuid}"
    tenant_id = _normalize_tenant_component(base)
    if not tenant_id:
        raise ValueError("unable to derive tenant metadata for remote-write relation")
    return tenant_id


def build_prometheus_remote_write_extension_metadata(
    *, application: str, model: str, model_uuid: str
) -> dict[str, str]:
    """Return extension metadata for outbound prometheus_remote_write relations."""
    application = str(application or "")
    model = str(model or "")
    model_uuid = str(model_uuid or "")
    return {
        "tenant-id": _tenant_id_for_relation(application, model_uuid),
        "application": application,
        "model": model,
        "model_uuid": model_uuid,
    }
