#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""Subordinate charm for machine-local Grafana Alloy telemetry collection."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import tempfile
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias, cast

import ops

try:
    from charms.dwellir_observability.v0.machine_observability import (
        MachineObservabilityConsumer,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )
    from charms.grafana_cloud_integrator.v0.cloud_config_requirer import (
        GrafanaCloudConfigRequirer,
    )

    from . import alloy
    from .alert_rules import CosToolRuleValidator, build_rule_state, publish_rule_groups, validate_rule_groups
    from .config_builder import (
        DEFAULT_CONFIG_PATH,
        ConfigBuilder,
        HostMetrics,
        ScrapeTarget,
    )
    from .config_builder import (
        FileLogSource as BuilderFileLogSource,
    )
    from .config_builder import (
        MetricsScrapeJob as BuilderMetricsScrapeJob,
    )
    from .custom_args import build_effective_custom_args
    from .grafanacloud_connectivity import probe_endpoint
    from .outbound_endpoints import OutboundEndpoint, dedupe_endpoints
    from .principal_context import PrincipalContext
except ImportError:
    from charms.dwellir_observability.v0.machine_observability import (
        MachineObservabilityConsumer,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )
    from charms.grafana_cloud_integrator.v0.cloud_config_requirer import (
        GrafanaCloudConfigRequirer,
    )

    import alloy
    from alert_rules import CosToolRuleValidator, build_rule_state, publish_rule_groups, validate_rule_groups
    from config_builder import (
        DEFAULT_CONFIG_PATH,
        ConfigBuilder,
        HostMetrics,
        ScrapeTarget,
    )
    from config_builder import (
        FileLogSource as BuilderFileLogSource,
    )
    from config_builder import (
        MetricsScrapeJob as BuilderMetricsScrapeJob,
    )
    from custom_args import build_effective_custom_args
    from grafanacloud_connectivity import probe_endpoint
    from outbound_endpoints import OutboundEndpoint, dedupe_endpoints
    from principal_context import PrincipalContext

logger = logging.getLogger(__name__)

RULE_CACHE_KEY = "_alloy_sub_rule_state_v1"
RULE_CACHE_VALUE_LIMIT = 60 * 1024
RULE_CACHE_DECODED_LIMIT = 2 * 1024 * 1024
RULE_PUBLICATION_VALUE_LIMIT = 60 * 1024
_RULE_CACHE_READ_VALUE_LIMIT = 64 * 1024
_RULE_CACHE_READ_DECODED_LIMIT = 2 * 1024 * 1024
_RULE_CACHE_MAX_DEPTH = 64
_RULE_CACHE_MAX_CONTAINER_ITEMS = 10_000
_RULE_CACHE_MAX_NODES = 50_000
_RULE_CACHE_MAX_STRING_BYTES = 64 * 1024
_RULE_ARTIFACT_TYPES = {
    "prometheus_alert_rules": "prometheus",
    "loki_alert_rules": "loki",
}
_CACHE_ARTIFACT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
RuleCacheState: TypeAlias = dict[str, dict[str, object]]


def _valid_ownership_component(value: object) -> bool:
    """Accept a bounded printable cache ownership path component."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value.encode("utf-8")) <= 256
        and "/" not in value
        and value.isprintable()
    )


class _MachineObservabilityLogFilter(logging.Filter):
    """Redact canonical consumer validation details that may contain artifact input."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace detailed validation output with its relation-scoped category."""
        if record.name == "charms.dwellir_observability.v0.machine_observability" and str(record.msg).startswith(
            "Invalid machine-observability payload on relation"
        ):
            relation_id = record.args[0] if isinstance(record.args, tuple) and record.args else "unknown"
            record.msg = "Invalid machine-observability payload on relation %s: validation"
            record.args = (relation_id,)
        return True


def merge_file_excludes(file_log_excludes: list[str], path_exclude: str) -> list[str]:
    """Append semi-colon separated path excludes to workload file excludes."""
    extra = [pattern.strip() for pattern in path_exclude.split(";") if pattern.strip()]
    return [*file_log_excludes, *extra]


def translate_metrics_endpoint(
    endpoint: MetricsEndpoint,
    *,
    principal_application: str,
    source_index: int,
    global_scrape_interval: str,
    global_scrape_timeout: str,
) -> BuilderMetricsScrapeJob:
    """Translate one relation metrics endpoint into a config-builder scrape job."""
    job_name = principal_application if source_index == 0 else f"{principal_application}-{source_index}"
    targets = [ScrapeTarget(address=target) for target in endpoint.targets]
    return BuilderMetricsScrapeJob(
        job_name=job_name,
        targets=targets,
        metrics_path=endpoint.path,
        scheme=endpoint.scheme,
        scrape_interval=endpoint.interval or global_scrape_interval,
        scrape_timeout=endpoint.timeout or global_scrape_timeout,
        tls_config=endpoint.tls,
    )


def _urls_from_databag(
    databag: Mapping[str, str],
    *,
    direct_keys: tuple[str, ...] = (),
    json_keys: tuple[str, ...] = (),
) -> list[str]:
    """Extract URL values from one relation databag."""
    urls: list[str] = []
    for key in direct_keys:
        value = databag.get(key)
        if value:
            urls.append(value)
    for key in json_keys:
        value = databag.get(key)
        if not value:
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("url"):
            urls.append(str(parsed["url"]))
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]))
    return urls


def relation_urls(
    relations: list[ops.Relation],
    *,
    direct_keys: tuple[str, ...] = (),
    json_keys: tuple[str, ...] = (),
) -> list[str]:
    """Extract endpoint URLs from app and unit relation databags."""
    urls: list[str] = []
    for relation in relations:
        app = getattr(relation, "app", None)
        if app is not None:
            urls.extend(
                _urls_from_databag(
                    relation.data.get(app, {}),
                    direct_keys=direct_keys,
                    json_keys=json_keys,
                )
            )
        for unit in getattr(relation, "units", ()):
            urls.extend(
                _urls_from_databag(
                    relation.data.get(unit, {}),
                    direct_keys=direct_keys,
                    json_keys=json_keys,
                )
            )
    return urls


class AlloySubCharm(ops.CharmBase):
    """Subordinate charm that renders Alloy config from principal relation data."""

    _stored = ops.StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        consumer_logger = logging.getLogger("charms.dwellir_observability.v0.machine_observability")
        if not any(isinstance(item, _MachineObservabilityLogFilter) for item in consumer_logger.filters):
            consumer_logger.addFilter(_MachineObservabilityLogFilter())
        self._stored.set_default(last_good_config="", last_custom_args="")
        self.machine_observability_consumer = MachineObservabilityConsumer(self)
        self.grafana_cloud = GrafanaCloudConfigRequirer(self)
        self._rule_validator = CosToolRuleValidator()

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.juju_info_relation_joined, self._on_relation_event)
        self.framework.observe(self.on.juju_info_relation_changed, self._on_relation_event)
        self.framework.observe(self.on.juju_info_relation_broken, self._on_relation_event)

        for relation_name in (
            "machine-observability",
            "send-loki-logs",
            "send-remote-write",
            "grafana-cloud-config",
        ):
            for event in (
                "relation_joined",
                "relation_changed",
                "relation_broken",
            ):
                self.framework.observe(getattr(self.on[relation_name], event), self._on_relation_event)
        self.framework.observe(
            self.on["machine-observability"].relation_departed,
            self._on_relation_event,
        )

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install Alloy and preserve the package-provided config."""
        self.unit.status = ops.MaintenanceStatus("Installing Alloy")
        try:
            alloy.install()
            alloy.preserve_default_config(config_path=Path(DEFAULT_CONFIG_PATH))
            alloy.write_custom_args(self._desired_custom_args())
        except Exception as exc:  # noqa: BLE001
            self.unit.status = ops.BlockedStatus(f"Installation failed: {exc}")
            event.defer()

    def _on_start(self, event: ops.StartEvent) -> None:
        """Start the workload and configure it if relation data is present."""
        try:
            alloy.start()
            version = alloy.get_version()
            if version:
                self.unit.set_workload_version(version)
            self._configure(active_message="Alloy is running")
        except Exception as exc:  # noqa: BLE001
            self.unit.status = ops.BlockedStatus(self._status_message(f"config invalid: {exc}"))
            event.defer()
        finally:
            self._reconcile_rule_groups(event)

    def _on_stop(self, _: ops.StopEvent) -> None:
        """Stop the workload."""
        alloy.stop()
        self.unit.status = ops.ActiveStatus("Alloy stopped")

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Rewrite and apply config after charm config changes."""
        try:
            self._configure(active_message="Alloy config updated")
        except Exception as exc:  # noqa: BLE001
            self.unit.status = ops.BlockedStatus(self._status_message(f"config invalid: {exc}"))
            event.defer()
        finally:
            self._reconcile_rule_groups(event)

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Reconcile config and workload health during periodic status updates."""
        try:
            version = alloy.get_version()
            if version:
                self.unit.set_workload_version(version)
            configured = self._configure(active_message="Alloy config updated")
            if configured:
                connectivity_error = self._grafana_cloud_status_error()
                if connectivity_error is not None:
                    self.unit.status = ops.BlockedStatus(self._status_message(connectivity_error))
        except Exception as exc:  # noqa: BLE001
            self.unit.status = ops.BlockedStatus(self._status_message(f"config invalid: {exc}"))
        finally:
            self._reconcile_rule_groups(event)

    def _on_leader_elected(self, event: ops.LeaderElectedEvent) -> None:
        """Reconcile config after a leadership change."""
        self._reconcile_config(event)

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent) -> None:
        """Reconcile config after charm upgrade."""
        self._reconcile_config(event)

    def _on_relation_event(self, event: ops.RelationEvent) -> None:
        """Re-render config when principal relations change."""
        self._reconcile_config(event, defer_on_failure=True)

    def _reconcile_config(self, event: ops.EventBase, defer_on_failure: bool = False) -> None:
        """Re-render config for lifecycle and relation events.

        Relation events may be deferred on transient failures; other event types
        are reconciled immediately without assuming defer support.
        """
        try:
            self._configure(active_message="Alloy config updated")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Relation-driven config update failed: %s", exc)
            self.unit.status = ops.BlockedStatus(self._status_message(f"config invalid: {exc}"))
            if defer_on_failure:
                event.defer()
        finally:
            self._reconcile_rule_groups(event)

    def _reconcile_rule_groups(self, event: ops.EventBase) -> None:
        """Publish transformed principal rules as complete standard-relation state."""
        if not self.unit.is_leader():
            return
        self._rule_validator.begin_reconcile()

        removed_relation_id = None
        event_relation = getattr(event, "relation", None)
        if (
            isinstance(event, ops.RelationBrokenEvent)
            and event_relation is not None
            and event_relation.name == "machine-observability"
        ):
            removed_relation_id = str(event_relation.id)

        relations = {
            relation.id: relation
            for relation in self.model.relations.get("machine-observability", [])
            if str(relation.id) != removed_relation_id
        }
        previous_states = {
            relation_id: self._load_relation_rule_cache(relation) for relation_id, relation in sorted(relations.items())
        }
        relation_state = self._bounded_existing_rule_state(previous_states)
        for relation_id, relation in sorted(relations.items()):
            desired = self._desired_relation_rule_state(relation, previous_states[relation_id])
            if desired is None:
                continue
            desired = self._without_ownership_conflicts(
                desired,
                {other_id: state for other_id, state in relation_state.items() if other_id < relation_id},
                relation_id=relation_id,
                fallback=previous_states[relation_id],
            )
            candidate = self._normalize_ownership_state({**relation_state, relation_id: desired})
            if not self._rule_state_is_publishable(candidate):
                logger.warning(
                    "Machine-observability rules on relation %s rejected: publish-size",
                    relation_id,
                )
                continue
            if self._store_relation_rule_cache(relation, desired):
                relation_state = candidate

        prometheus_groups, loki_groups = self._flatten_rule_state(relation_state)
        publish_rule_groups(self, "send-remote-write", prometheus_groups)
        publish_rule_groups(self, "send-loki-logs", loki_groups)
        self._set_rule_destination_status(prometheus_groups, loki_groups)

    def _set_rule_destination_status(
        self,
        prometheus_groups: list[dict[str, object]],
        loki_groups: list[dict[str, object]],
    ) -> None:
        """Report accepted rule state waiting for its standard backend relation."""
        missing: list[str] = []
        if prometheus_groups and not self.model.relations.get("send-remote-write", []):
            missing.append("send-remote-write relation")
        if loki_groups and not self.model.relations.get("send-loki-logs", []):
            missing.append("send-loki-logs relation")
        if missing and isinstance(self.unit.status, ops.ActiveStatus):
            self.unit.status = ops.WaitingStatus(self._status_message(self._relation_waiting_message(missing)))

    def _desired_relation_rule_state(
        self,
        relation: ops.Relation,
        previous: RuleCacheState,
    ) -> RuleCacheState | None:
        """Parse current relation input and apply it to the relation's durable LKG."""
        raw_payload = relation.data[relation.app].get("payload", "{}") if relation.app else "{}"
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: json",
                relation.id,
            )
            return None
        return self._build_relation_rule_state(payload, previous, str(relation.id))

    def _bounded_existing_rule_state(
        self,
        previous_states: dict[int, RuleCacheState],
    ) -> dict[int, RuleCacheState]:
        """Admit cached relation states deterministically without exceeding publication limits."""
        admitted: dict[int, RuleCacheState] = {}
        for relation_id, state in sorted(previous_states.items()):
            state = self._without_ownership_conflicts(state, admitted, relation_id=relation_id)
            candidate = {**admitted, relation_id: state}
            if self._rule_state_is_publishable(candidate):
                admitted = candidate
            else:
                logger.warning(
                    "Machine-observability cached rules on relation %s skipped: publish-size",
                    relation_id,
                )
        return admitted

    @staticmethod
    def _normalize_ownership_state(states: dict[int, RuleCacheState]) -> dict[int, RuleCacheState]:
        """Resolve all cross-relation ownership collisions by ascending relation id."""
        admitted: dict[int, RuleCacheState] = {}
        for relation_id, state in sorted(states.items()):
            admitted[relation_id] = AlloySubCharm._without_ownership_conflicts(
                state,
                admitted,
                relation_id=relation_id,
            )
        return admitted

    @staticmethod
    def _without_ownership_conflicts(
        state: RuleCacheState,
        admitted: dict[int, RuleCacheState],
        *,
        relation_id: int,
        fallback: RuleCacheState | None = None,
    ) -> RuleCacheState:
        """Resolve later collisions, retaining a non-conflicting prior artifact when possible."""
        claimed = {
            cast(str, entry["ownership"])
            for other_relation, other_state in admitted.items()
            if other_relation != relation_id
            for entry in other_state.values()
        }
        result: RuleCacheState = {}
        for identity, entry in state.items():
            ownership = cast(str, entry["ownership"])
            if ownership in claimed:
                logger.warning(
                    "Machine-observability rules on relation %s rejected: duplicate-ownership %s",
                    relation_id,
                    ownership,
                )
                prior = fallback.get(identity) if fallback is not None else None
                if prior is not None:
                    prior_ownership = cast(str, prior["ownership"])
                    if prior_ownership not in claimed:
                        claimed.add(prior_ownership)
                        result[identity] = prior
                continue
            claimed.add(ownership)
            result[identity] = entry
        return result

    @staticmethod
    def _flatten_rule_state(
        relation_state: dict[int, RuleCacheState],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        """Flatten relation-partitioned ownership state into deterministic backend groups."""
        prometheus: dict[str, list[dict[str, object]]] = {}
        loki: dict[str, list[dict[str, object]]] = {}
        for relation_id in sorted(relation_state):
            state = relation_state[relation_id]
            for entry in state.values():
                target = prometheus if entry["backend"] == "prometheus" else loki
                ownership = cast(str, entry["ownership"])
                if ownership in target:
                    raise ValueError("duplicate-ownership")
                target[ownership] = cast(list[dict[str, object]], entry["groups"])
        prometheus_groups = [group for ownership in sorted(prometheus) for group in prometheus[ownership]]
        loki_groups = [group for ownership in sorted(loki) for group in loki[ownership]]
        for groups in (prometheus_groups, loki_groups):
            names = [cast(str, group["name"]) for group in groups]
            if len(names) != len(set(names)):
                raise ValueError("duplicate-group")
        return prometheus_groups, loki_groups

    @staticmethod
    def _rule_state_is_publishable(relation_state: dict[int, RuleCacheState]) -> bool:
        """Check the exact compact downstream values against a conservative Juju ceiling."""
        try:
            flattened = AlloySubCharm._flatten_rule_state(relation_state)
        except (KeyError, TypeError, ValueError):
            return False
        for groups in flattened:
            payload = json.dumps({"groups": groups}, sort_keys=True, separators=(",", ":"))
            if len(payload.encode("utf-8")) > RULE_PUBLICATION_VALUE_LIMIT:
                return False
        return True

    def _load_relation_rule_cache(self, relation: ops.Relation) -> RuleCacheState:
        """Load and validate transformed LKG state from this relation's app databag."""
        raw_cache = relation.data[self.app].get(RULE_CACHE_KEY)
        if not raw_cache:
            return {}
        try:
            state = self._decode_rule_cache(raw_cache)
            if not self._valid_rule_cache_state(state):
                raise ValueError("schema")
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            binascii.Error,
            zlib.error,
            json.JSONDecodeError,
            RecursionError,
        ):
            logger.warning(
                "Invalid machine-observability rule cache on relation %s: cache-validation",
                relation.id,
            )
            return {}
        return cast(RuleCacheState, state)

    def _store_relation_rule_cache(self, relation: ops.Relation, state: RuleCacheState) -> bool:
        """Persist transformed LKG state before publication, retaining prior state on failure."""
        content = self._serialize_rule_cache(state)
        encoded = self._encode_rule_cache_content(content)
        if len(content) > RULE_CACHE_DECODED_LIMIT or len(encoded.encode("utf-8")) > RULE_CACHE_VALUE_LIMIT:
            logger.warning(
                "Machine-observability rule cache on relation %s rejected: cache-size",
                relation.id,
            )
            return False
        if relation.data[self.app].get(RULE_CACHE_KEY) == encoded:
            return True
        try:
            relation.data[self.app][RULE_CACHE_KEY] = encoded
        except (ops.ModelError, RuntimeError):
            logger.warning(
                "Machine-observability rule cache on relation %s rejected: cache-write",
                relation.id,
            )
            return False
        return True

    @staticmethod
    def _encode_rule_cache(state: RuleCacheState) -> str:
        """Encode transformed state as deterministic compact compressed JSON."""
        content = AlloySubCharm._serialize_rule_cache(state)
        return AlloySubCharm._encode_rule_cache_content(content)

    @staticmethod
    def _serialize_rule_cache(state: RuleCacheState) -> bytes:
        """Serialize transformed state as deterministic compact JSON bytes."""
        return json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _encode_rule_cache_content(content: bytes) -> str:
        """Compress and base64-encode serialized cache bytes with a version prefix."""
        return "v1:" + base64.b64encode(zlib.compress(content, level=9)).decode("ascii")

    @staticmethod
    def _decode_rule_cache(raw_cache: str) -> object:
        """Decode a versioned cache with a strict decompressed-size ceiling."""
        if not raw_cache.startswith("v1:") or len(raw_cache.encode("utf-8")) > _RULE_CACHE_READ_VALUE_LIMIT:
            raise ValueError("format")
        compressed = base64.b64decode(raw_cache[3:], validate=True)
        decoder = zlib.decompressobj()
        content = decoder.decompress(compressed, _RULE_CACHE_READ_DECODED_LIMIT + 1)
        if len(content) > _RULE_CACHE_READ_DECODED_LIMIT or not decoder.eof or decoder.unused_data:
            raise ValueError("size")
        parsed = json.loads(content)
        if not AlloySubCharm._cache_structure_is_bounded(parsed):
            raise ValueError("structure")
        return parsed

    @staticmethod
    def _cache_structure_is_bounded(value: object) -> bool:
        """Bound cache nesting, fan-out, nodes, and strings using an iterative walk."""
        nodes = 0
        stack: list[tuple[object, int]] = [(value, 0)]
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if nodes > _RULE_CACHE_MAX_NODES or depth > _RULE_CACHE_MAX_DEPTH:
                return False
            if isinstance(item, str):
                if len(item.encode("utf-8")) > _RULE_CACHE_MAX_STRING_BYTES:
                    return False
            elif isinstance(item, dict):
                if len(item) > _RULE_CACHE_MAX_CONTAINER_ITEMS:
                    return False
                stack.extend((key, depth + 1) for key in item)
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                if len(item) > _RULE_CACHE_MAX_CONTAINER_ITEMS:
                    return False
                stack.extend((child, depth + 1) for child in item)
        return True

    def _valid_rule_cache_state(self, state: object) -> bool:
        """Accept only canonical transformed ownership entries from the private cache."""
        if not isinstance(state, dict):
            return False
        for identity, entry in state.items():
            if not isinstance(identity, str) or identity.count("/") != 1 or not isinstance(entry, dict):
                return False
            artifact_type, artifact_id = identity.split("/", 1)
            expected_backend = _RULE_ARTIFACT_TYPES.get(artifact_type)
            if (
                not expected_backend
                or _CACHE_ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None
                or set(entry) != {"backend", "ownership", "groups"}
            ):
                return False
            ownership = entry.get("ownership")
            if not isinstance(ownership, str) or ownership.count("/") != 3:
                return False
            model_uuid, application, owned_type, owned_id = ownership.split("/", 3)
            if (
                not _valid_ownership_component(model_uuid)
                or not _valid_ownership_component(application)
                or (owned_type, owned_id) != (artifact_type, artifact_id)
            ):
                return False
            groups = entry.get("groups")
            if entry.get("backend") != expected_backend or not validate_rule_groups(groups):
                return False
        return True

    def _build_relation_rule_state(
        self,
        payload: object,
        previous: RuleCacheState,
        relation_id: str,
    ) -> RuleCacheState | None:
        """Apply one structurally valid relation snapshot to its per-artifact LKG."""
        snapshot = self._validated_rule_snapshot(payload, relation_id)
        if snapshot is None:
            return None
        schema_version, identities = snapshot
        if schema_version in (1, 2):
            return {}

        result = build_rule_state(payload, validator=self._validate_artifact_rules)
        for error in result.errors:
            logger.warning("Invalid machine-observability artifact: %s", error)
        desired = {identity: previous[identity] for identity in identities if identity in previous}
        for backend, ownership_state in (
            ("prometheus", result.prometheus),
            ("loki", result.loki),
        ):
            for ownership, groups in ownership_state.items():
                artifact_type, artifact_id = ownership.rsplit("/", 2)[-2:]
                desired[f"{artifact_type}/{artifact_id}"] = {
                    "backend": backend,
                    "ownership": ownership,
                    "groups": groups,
                }
        return desired

    def _validate_artifact_rules(self, artifact_type: str, groups: list[dict[str, object]]) -> bool:
        """Validate one transformed artifact with the packaged backend parser."""
        return self._rule_validator(artifact_type, groups)

    @staticmethod
    def _validated_rule_snapshot(
        payload: object,
        relation_id: str,
    ) -> tuple[int, list[str]] | None:
        """Validate outer payload structure without rejecting individual artifacts."""
        header = AlloySubCharm._validated_rule_snapshot_header(payload, relation_id)
        if header is None:
            return None
        schema_version, artifacts = header
        identities: list[str] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_type = artifact.get("artifact_type")
            artifact_id = artifact.get("artifact_id")
            if artifact_type not in _RULE_ARTIFACT_TYPES or not isinstance(artifact_id, str):
                continue
            if _CACHE_ARTIFACT_ID_PATTERN.fullmatch(artifact_id) is None:
                continue
            identities.append(f"{artifact_type}/{artifact_id}")
        if len(identities) != len(set(identities)):
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: duplicate-identity",
                relation_id,
            )
            return None
        return schema_version, identities

    @staticmethod
    def _validated_rule_snapshot_header(
        payload: object,
        relation_id: str,
    ) -> tuple[int, list[object]] | None:
        """Validate relation-level schema, version, artifact collection, and topology."""
        if not isinstance(payload, dict):
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: schema",
                relation_id,
            )
            return None
        schema_version = payload.get("schema_version", 1)
        if schema_version not in (1, 2, 3):
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: schema-version",
                relation_id,
            )
            return None
        artifacts = payload.get("artifacts", [])
        if not isinstance(artifacts, list):
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: schema",
                relation_id,
            )
            return None
        if schema_version in (1, 2) and artifacts:
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: schema",
                relation_id,
            )
            return None
        if schema_version == 3 and artifacts:
            topology = payload.get("source_topology")
            if not isinstance(topology, dict) or any(
                not _valid_ownership_component(topology.get(field)) for field in ("model_uuid", "application")
            ):
                logger.warning(
                    "Invalid machine-observability rule payload on relation %s: topology",
                    relation_id,
                )
                return None
        base_payload = {**payload, "artifacts": []}
        try:
            MachineObservabilityPayload.model_validate(base_payload)
        except Exception:  # noqa: BLE001 - only a safe category is reported
            logger.warning(
                "Invalid machine-observability rule payload on relation %s: schema",
                relation_id,
            )
            return None

        return schema_version, artifacts

    def _configure(self, *, active_message: str) -> bool:
        """Render, validate, and apply Alloy config from relation data."""
        principal_context = self._principal_context()
        if principal_context is None:
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(self._status_message("config waiting for juju-info relation"))
            return False
        if not self._has_machine_observability_relation() and not self._host_metrics_enabled():
            # Host metrics alone are enough to render a useful config, so the
            # relation is only required when nothing else would be collected.
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(
                self._status_message("config waiting for machine-observability relation")
            )
            return False

        payload = self._observability_payload()
        loki_endpoints = self._loki_endpoint_urls()
        remote_write_endpoints = self._remote_write_endpoint_urls()
        waiting_requirements = self._missing_relation_requirements(
            principal_context=principal_context,
        )
        artifact_identities = [
            f"{artifact.artifact_type}/{artifact.artifact_id}" for artifact in getattr(payload, "artifacts", [])
        ]
        logger.info(
            "Configuring Alloy with principal context %s; payload schema=%s metrics=%s "
            "systemd=%s journal=%s files=%s artifacts=%s",
            principal_context,
            getattr(payload, "schema_version", 1),
            len(getattr(payload, "metrics_endpoints", [])),
            len(getattr(payload, "systemd_units", [])),
            len(getattr(payload, "journal_match_expressions", [])),
            len(getattr(payload, "log_files", [])),
            artifact_identities,
        )

        topology_labels = principal_context.juju_labels(charm_name=payload.charm_name)
        builder = ConfigBuilder(
            loki_endpoints=loki_endpoints,
            remote_write_endpoints=remote_write_endpoints,
            metrics_scrape_jobs=self._active_metrics_scrape_jobs(payload, principal_context),
            systemd_units=payload.systemd_units,
            journal_match_expressions=payload.journal_match_expressions,
            file_log_sources=[
                BuilderFileLogSource(
                    include=source.include,
                    exclude=merge_file_excludes(source.exclude, self._path_exclude_patterns()),
                    attributes=source.attributes,
                )
                for source in payload.log_files
            ],
            topology_labels=topology_labels,
            global_scrape_interval=self._global_scrape_interval(),
            global_scrape_timeout=self._global_scrape_timeout(),
            path_exclude=[],
            queue_size=self._queue_size(),
            max_elapsed_time_min=self._max_elapsed_time_min(),
            tls_insecure_skip_verify=self._tls_insecure_skip_verify(),
            host_metrics=(
                HostMetrics(
                    topology_labels=topology_labels,
                    scrape_timeout=self._global_scrape_timeout(),
                )
                if self._host_metrics_enabled()
                else None
            ),
        )
        desired_custom_args = self._desired_custom_args()
        previous_custom_args = self._stored.last_custom_args
        config_text = f"{alloy.GENERATED_CONFIG_HEADER}{builder.build()}"
        self._validate_config(config_text)
        alloy.ensure_config_dir_permissions(str(Path(DEFAULT_CONFIG_PATH).parent))
        alloy.write_config_text(config_text, config_path=Path(DEFAULT_CONFIG_PATH))
        alloy.write_custom_args(desired_custom_args)
        self._stored.last_good_config = config_text
        self._stored.last_custom_args = desired_custom_args
        if alloy.is_active() or waiting_requirements:
            if alloy.is_active():
                self._apply_runtime_update(
                    desired_custom_args=desired_custom_args,
                    previous_custom_args=previous_custom_args,
                )
        else:
            self._apply_runtime_update(
                desired_custom_args=desired_custom_args,
                previous_custom_args=previous_custom_args,
            )
        if waiting_requirements:
            self.unit.status = ops.WaitingStatus(
                self._status_message(self._relation_waiting_message(waiting_requirements))
            )
            return False
        self.unit.status = ops.ActiveStatus(
            self._status_message(f"config valid; {self._active_message(active_message)}")
        )
        return True

    def _active_message(self, default: str) -> str:
        """Return the active-status message for the currently rendered config."""
        if not self._has_machine_observability_relation():
            # Only host metrics are being collected; saying more would advertise
            # workload telemetry that nothing is producing.
            return "host metrics only"
        return default

    @staticmethod
    def _logs_declared(payload: MachineObservabilityPayload) -> bool:
        """Return whether the principal has declared any log sources."""
        return bool(payload.systemd_units or payload.journal_match_expressions or payload.log_files)

    @staticmethod
    def _relation_waiting_message(missing_relations: list[str]) -> str:
        """Render a waiting message for the currently missing relation requirements."""
        if len(missing_relations) == 1:
            return f"config valid; waiting for {missing_relations[0]}"
        return f"config valid; waiting for {' and '.join(missing_relations)}"

    def _status_message(self, config_status: str) -> str:
        """Render a status message including workload and config state."""
        service_state = "service running" if alloy.is_active() else "service down"
        return f"Alloy {service_state}; {config_status}"

    def _missing_relation_requirements(
        self,
        *,
        principal_context: PrincipalContext | None,
    ) -> list[str]:
        """Return required relation inputs that are still missing."""
        missing_relations: list[str] = []
        if principal_context is None:
            missing_relations.append("juju-info relation")
        if not self._has_machine_observability_relation() and not self._host_metrics_enabled():
            # Host metrics are a complete pipeline on their own, so the relation
            # is only outstanding when nothing else would be collected.
            missing_relations.append("machine-observability relation")
        return missing_relations

    def _reset_config_for_missing_relations(self) -> None:
        """Restore a safe config when required relations are missing."""
        if not self._stored.last_good_config:
            return

        desired_custom_args = self._desired_custom_args()
        previous_custom_args = self._stored.last_custom_args

        alloy.ensure_config_dir_permissions(str(Path(DEFAULT_CONFIG_PATH).parent))
        config_reset = alloy.restore_preserved_config(config_path=Path(DEFAULT_CONFIG_PATH))
        alloy.write_custom_args(desired_custom_args)
        self._stored.last_good_config = ""
        self._stored.last_custom_args = desired_custom_args

        if alloy.is_active() and (
            config_reset
            or previous_custom_args != desired_custom_args
            or not alloy.custom_args_applied(desired_custom_args)
        ):
            self._apply_runtime_update(
                desired_custom_args=desired_custom_args,
                previous_custom_args=previous_custom_args,
            )

    def _apply_runtime_update(self, *, desired_custom_args: str, previous_custom_args: str) -> None:
        """Apply updated config or custom args to the running Alloy service."""
        if previous_custom_args != desired_custom_args or not alloy.custom_args_applied(desired_custom_args):
            alloy.restart()
        else:
            alloy.reload()

    def _validate_config(self, config_text: str) -> None:
        """Validate config text using a temporary file."""
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(config_text)
            tmp_path = Path(handle.name)
        try:
            alloy.verify_config(config_path=tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _principal_context(self) -> PrincipalContext | None:
        """Return principal context from juju-info, falling back to v2 source topology."""
        relation = self.model.get_relation("juju-info")
        if relation is not None and relation.units:
            return PrincipalContext.from_relation(
                relation,
                model_name=self.model.name,
                model_uuid=self.model.uuid,
            )
        topology = self._observability_payload().source_topology
        if topology is not None:
            return PrincipalContext.from_source_topology(
                topology,
                model_name=self.model.name,
                model_uuid=self.model.uuid,
            )
        return None

    def _observability_payload(self):
        """Return the current machine-observability payload if present."""
        relations = sorted(
            self.model.relations.get("machine-observability", []),
            key=lambda relation: relation.id,
        )
        if not relations:
            return MachineObservabilityPayload()
        return self.machine_observability_consumer.get_payload(relations[0])

    def _has_machine_observability_relation(self) -> bool:
        """Return whether the machine-observability relation is currently present."""
        return bool(self.model.relations.get("machine-observability", []))

    def _desired_custom_args(self) -> str:
        """Return the desired Alloy service args."""
        return build_effective_custom_args(str(self.config.get("custom-args", "")))

    def _grafana_cloud_metrics_endpoints(self) -> list[OutboundEndpoint]:
        """Return Grafana Cloud remote-write endpoints."""
        if not self.grafana_cloud.prometheus_ready:
            return []
        credentials = self.grafana_cloud.prometheus_credentials
        return [
            OutboundEndpoint(
                url=self.grafana_cloud.prometheus_url,
                username=credentials.username if credentials else "",
                password=credentials.password if credentials else "",
                tls_ca_pem=self.grafana_cloud.tls_ca,
            )
        ]

    def _grafana_cloud_loki_endpoints(self) -> list[OutboundEndpoint]:
        """Return Grafana Cloud Loki endpoints."""
        if not self.grafana_cloud.loki_ready:
            return []
        credentials = self.grafana_cloud.loki_credentials
        return [
            OutboundEndpoint(
                url=self.grafana_cloud.loki_url,
                username=credentials.username if credentials else "",
                password=credentials.password if credentials else "",
                tls_ca_pem=self.grafana_cloud.tls_ca,
            )
        ]

    def _loki_endpoint_urls(self) -> list[OutboundEndpoint]:
        """Return outbound Loki endpoints from all configured relations."""
        relation_endpoints = [
            OutboundEndpoint(url=url)
            for url in relation_urls(
                self.model.relations.get("send-loki-logs", []),
                direct_keys=("url",),
                json_keys=("endpoint", "endpoints"),
            )
        ]
        return dedupe_endpoints([*relation_endpoints, *self._grafana_cloud_loki_endpoints()])

    def _remote_write_endpoint_urls(self) -> list[OutboundEndpoint]:
        """Return outbound remote-write endpoints from all configured relations."""
        relation_endpoints = [
            OutboundEndpoint(url=url)
            for url in relation_urls(
                self.model.relations.get("send-remote-write", []),
                direct_keys=("url",),
                json_keys=("remote_write", "endpoints"),
            )
        ]
        return dedupe_endpoints([*relation_endpoints, *self._grafana_cloud_metrics_endpoints()])

    def _active_metrics_scrape_jobs(
        self, payload: MachineObservabilityPayload, principal_context: PrincipalContext
    ) -> list[BuilderMetricsScrapeJob]:
        """Translate active metrics endpoints from the machine-observability payload."""
        topology_labels = principal_context.juju_labels(charm_name=payload.charm_name)
        translated_jobs = [
            translate_metrics_endpoint(
                endpoint,
                principal_application=principal_context.application,
                source_index=index,
                global_scrape_interval=self._global_scrape_interval(),
                global_scrape_timeout=self._global_scrape_timeout(),
            )
            for index, endpoint in enumerate(payload.metrics_endpoints)
        ]
        return [
            BuilderMetricsScrapeJob(
                job_name=job.job_name,
                targets=[ScrapeTarget(address=target.address, labels=topology_labels) for target in job.targets],
                metrics_path=job.metrics_path,
                scheme=job.scheme,
                scrape_interval=job.scrape_interval,
                scrape_timeout=job.scrape_timeout,
                tls_config=job.tls_config,
            )
            for job in translated_jobs
        ]

    def _grafana_cloud_status_error(self) -> str | None:
        """Return the first Grafana Cloud connectivity failure, if any."""
        for endpoint in self._grafana_cloud_metrics_endpoints():
            ok, reason = probe_endpoint(endpoint)
            if not ok:
                return f"Grafana Cloud metrics connectivity failed: {reason}"
        for endpoint in self._grafana_cloud_loki_endpoints():
            ok, reason = probe_endpoint(endpoint)
            if not ok:
                return f"Grafana Cloud logs connectivity failed: {reason}"
        return None

    def _path_exclude_patterns(self) -> str:
        """Return raw path exclude config for file-log translation."""
        return str(self.config.get("path_exclude", "")).strip()

    def _global_scrape_interval(self) -> str:
        """Return the default scrape interval."""
        return str(self.config.get("global_scrape_interval", "1m"))

    def _global_scrape_timeout(self) -> str:
        """Return the default scrape timeout."""
        return str(self.config.get("global_scrape_timeout", "10s"))

    def _tls_insecure_skip_verify(self) -> bool:
        """Return whether scrape TLS verification should be skipped."""
        return bool(self.config.get("tls_insecure_skip_verify", False))

    def _host_metrics_enabled(self) -> bool:
        """Return whether host metrics should be collected."""
        return bool(self.config.get("enable-host-metrics", False))

    def _queue_size(self) -> int:
        """Return queue size for outbound telemetry buffering."""
        return int(self.config.get("queue_size", 1000))

    def _max_elapsed_time_min(self) -> int:
        """Return the max retry window in minutes."""
        return int(self.config.get("max_elapsed_time_min", 5))


if __name__ == "__main__":
    ops.main(AlloySubCharm)
