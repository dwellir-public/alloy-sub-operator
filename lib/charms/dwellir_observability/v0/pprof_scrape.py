# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.

"""Go pprof profiling-source relation library.

Principal charms publish the loopback address of a pprof listener on every
unit; a machine-local collector (for example a subordinate Alloy feeding
Pyroscope) reads the declarations and scrapes them. The wire format is the
one iris-operator already publishes: the unit data key ``profiling_source``
holds JSON ``{"version": 1, "service_name", "targets", "profile_types",
"labels"}``.

Canonical source lives in alloy-sub-operator; vendor-copy into principals.

Provider example (principal charm, relation ``profiling-source`` with
``scope: container``)::

    from charms.dwellir_observability.v0.pprof_scrape import (
        PprofScrapeProvider,
        PprofSource,
    )

    self.profiling = PprofScrapeProvider(
        self, "profiling-source", source_factory=self._pprof_source
    )

    def _pprof_source(self) -> PprofSource | None:
        if not self.config["profiling"]:
            return None  # withdraws the declaration
        return PprofSource("myservice", ("127.0.0.1:6060",), ("process_cpu", "goroutine"))

    # After a config change: self.profiling.publish()

Requirer example::

    from charms.dwellir_observability.v0.pprof_scrape import PprofScrapeRequirer

    self.profiling = PprofScrapeRequirer(self, "profiling-source")
    for source in self.profiling.get_sources():
        ...  # source.service_name, source.targets, source.profile_types, source.labels
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ops.charm import CharmBase
from ops.framework import Object
from ops.model import Relation, Unit
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

LIBID = "c4f8b2a6d1e94a3f9b7e5d20a8c61f43"
LIBAPI = 0
LIBPATCH = 1

PYDEPS = ["pydantic>=2"]

DEFAULT_RELATION_NAME = "profiling-source"
RELATION_KEY = "profiling_source"
PAYLOAD_VERSION = 1
PROFILE_TYPES: tuple[str, ...] = (
    "process_cpu",
    "memory",
    "goroutine",
    "mutex",
    "block",
    "fgprof",
    "godeltaprof_memory",
    "godeltaprof_mutex",
    "godeltaprof_block",
)


_PORT = re.compile(r"\d{1,5}")
_FORBIDDEN = re.compile(r"[@/?#\s\\]")


def is_loopback(target: str) -> bool:
    """Return True only for a strict ``host:port`` or ``[v6]:port`` loopback target."""
    if not isinstance(target, str) or _FORBIDDEN.search(target):
        return False
    if target.startswith("["):
        host, sep, port = target[1:].partition("]:")
        if not sep:
            return False
    else:
        host, sep, port = target.rpartition(":")
        if not sep or ":" in host:
            return False
    if not _PORT.fullmatch(port) or not 1 <= int(port) <= 65535:
        return False
    if host.lower() == "localhost":
        return not target.startswith("[")
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class PprofSource:
    """A pprof listener declared by one principal unit."""

    service_name: str
    targets: tuple[str, ...]
    profile_types: tuple[str, ...]
    labels: dict[str, str] = field(default_factory=dict)
    unit_name: str = ""


class _Payload(BaseModel):
    """Wire model; unknown fields are ignored so later additions don't break v1 readers."""

    model_config = ConfigDict(extra="ignore")

    version: int
    service_name: str = Field(min_length=1)
    targets: list[str]
    profile_types: list[str]
    labels: dict[str, str] = Field(default_factory=dict)


class PprofScrapeProvider(Object):
    """Publish this unit's pprof source on the profiling-source relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
        *,
        source_factory: Callable[[], PprofSource | None],
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._source_factory = source_factory
        events = charm.on[relation_name]
        self.framework.observe(events.relation_joined, self._on_relation_event)
        self.framework.observe(events.relation_changed, self._on_relation_event)

    def _on_relation_event(self, _event: Any) -> None:
        self.publish()

    def publish(self) -> None:
        """Write or withdraw this unit's declaration on every relation."""
        source = self._source_factory()
        for relation in self.model.relations.get(self._relation_name, []):
            data = relation.data[self.model.unit]
            if source is None:
                data.pop(RELATION_KEY, None)
            else:
                data[RELATION_KEY] = json.dumps(self._payload(source), sort_keys=True)

    def _payload(self, source: PprofSource) -> dict[str, Any]:
        labels = {
            **source.labels,
            "juju_model": self.model.name,
            "juju_model_uuid": self.model.uuid,
            "juju_application": self.model.app.name,
            "juju_unit": self.model.unit.name,
            "juju_charm": self._charm.meta.name,
        }
        return {
            "version": PAYLOAD_VERSION,
            "service_name": source.service_name,
            "targets": list(source.targets),
            "profile_types": list(source.profile_types),
            "labels": labels,
        }


class PprofScrapeRequirer(Object):
    """Read validated pprof sources from every remote unit."""

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self._relation_name = relation_name

    def get_sources(self) -> list[PprofSource]:
        """Return the valid sources of all remote units, skipping invalid ones."""
        sources: list[PprofSource] = []
        for relation in self.model.relations.get(self._relation_name, []):
            for unit in relation.units:
                source = self._read_unit(relation, unit)
                if source is not None:
                    sources.append(source)
        return sources

    def _read_unit(self, relation: Relation, unit: Unit) -> PprofSource | None:
        unit_name = unit.name
        raw = relation.data[unit].get(RELATION_KEY)
        if not raw:
            return None
        try:
            payload = _Payload.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as exc:
            logger.warning("Ignoring malformed %s from %s: %s", RELATION_KEY, unit_name, exc)
            return None
        if payload.version != PAYLOAD_VERSION:
            logger.warning(
                "Ignoring %s from %s: unsupported version %s", RELATION_KEY, unit_name, payload.version
            )
            return None
        targets = tuple(t for t in payload.targets if self._keep_target(t, unit_name))
        if not targets:
            logger.warning("Ignoring %s from %s: no valid loopback targets", RELATION_KEY, unit_name)
            return None
        types = tuple(t for t in payload.profile_types if self._keep_type(t, unit_name))
        if not types:
            logger.warning("Ignoring %s from %s: no valid profile types", RELATION_KEY, unit_name)
            return None
        return PprofSource(payload.service_name, targets, types, dict(payload.labels), unit_name)

    @staticmethod
    def _keep_target(target: str, unit_name: str) -> bool:
        if is_loopback(target):
            return True
        logger.warning("Dropping non-loopback pprof target %r from %s", target, unit_name)
        return False

    @staticmethod
    def _keep_type(profile_type: str, unit_name: str) -> bool:
        if profile_type in PROFILE_TYPES:
            return True
        logger.warning("Dropping unknown profile type %r from %s", profile_type, unit_name)
        return False
