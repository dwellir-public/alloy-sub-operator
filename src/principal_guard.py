# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""Pure helpers for tracking which principal applications this charm serves."""

import json
from collections.abc import Iterable

PRINCIPAL_ENDPOINTS: tuple[str, ...] = (
    "juju-info",
    "machine-observability",
    "profiling-source",
)
PEER_RELATION = "alloy-peers"
PEER_KEY = "principal-applications"


def encode_principals(names: Iterable[str]) -> str:
    """Encode principal application names as a sorted, de-duplicated JSON list."""
    return json.dumps(sorted(set(names)), separators=(",", ":"))


def decode_principals(raw: str | None) -> frozenset[str]:
    """Decode a peer databag value; malformed input yields an empty set."""
    if not raw:
        return frozenset()
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return frozenset()
    if not isinstance(value, list):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item)


def conflicting_principals(local: Iterable[str], peers: Iterable[Iterable[str]]) -> list[str]:
    """Return all principal names when more than one distinct principal is in use."""
    union = set(local)
    for peer in peers:
        union.update(peer)
    return sorted(union) if len(union) > 1 else []
