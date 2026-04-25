#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""Subordinate charm for machine-local Grafana Alloy telemetry collection."""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path

import ops

try:
    from charms.dwellir_observability.v0.machine_observability import (
        MachineObservabilityConsumer,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )

    from . import alloy
    from .config_builder import (
        DEFAULT_CONFIG_PATH,
        ConfigBuilder,
        ScrapeTarget,
    )
    from .config_builder import (
        FileLogSource as BuilderFileLogSource,
    )
    from .config_builder import (
        MetricsScrapeJob as BuilderMetricsScrapeJob,
    )
    from .custom_args import build_effective_custom_args
    from .principal_context import PrincipalContext
except ImportError:
    from charms.dwellir_observability.v0.machine_observability import (
        MachineObservabilityConsumer,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )

    import alloy
    from config_builder import (
        DEFAULT_CONFIG_PATH,
        ConfigBuilder,
        ScrapeTarget,
    )
    from config_builder import (
        FileLogSource as BuilderFileLogSource,
    )
    from config_builder import (
        MetricsScrapeJob as BuilderMetricsScrapeJob,
    )
    from custom_args import build_effective_custom_args
    from principal_context import PrincipalContext

logger = logging.getLogger(__name__)


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
        self._stored.set_default(last_good_config="", last_custom_args="")
        self.machine_observability_consumer = MachineObservabilityConsumer(self)

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

        for relation_name in ("machine-observability", "send-loki-logs", "send-remote-write"):
            for event in ("relation_joined", "relation_changed", "relation_broken"):
                self.framework.observe(getattr(self.on[relation_name], event), self._on_relation_event)

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

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Reconcile config and workload health during periodic status updates."""
        try:
            version = alloy.get_version()
            if version:
                self.unit.set_workload_version(version)
            self._configure(active_message="Alloy config updated")
        except Exception as exc:  # noqa: BLE001
            self.unit.status = ops.BlockedStatus(self._status_message(f"config invalid: {exc}"))

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

    def _configure(self, *, active_message: str) -> bool:
        """Render, validate, and apply Alloy config from relation data."""
        principal_context = self._principal_context()
        if principal_context is None:
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(
                self._status_message("config waiting for juju-info relation")
            )
            return False
        if not self._has_machine_observability_relation():
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(
                self._status_message("config waiting for machine-observability relation")
            )
            return False

        payload = self._observability_payload()
        loki_endpoints = self._loki_endpoint_urls()
        remote_write_endpoints = self._remote_write_endpoint_urls()
        logs_declared = self._logs_declared(payload)
        metrics_declared = bool(payload.metrics_endpoints)
        logs_enabled = logs_declared and bool(loki_endpoints)
        metrics_enabled = metrics_declared and bool(remote_write_endpoints)
        waiting_requirements = self._missing_relation_requirements(
            principal_context=principal_context,
            payload=payload,
            loki_endpoints=loki_endpoints,
            remote_write_endpoints=remote_write_endpoints,
        )
        logger.info("Configuring Alloy with principal context: %s and payload: %s", principal_context, payload)

        builder = ConfigBuilder(
            loki_endpoints=loki_endpoints if logs_enabled else [],
            remote_write_endpoints=remote_write_endpoints if metrics_enabled else [],
            metrics_scrape_jobs=(
                self._active_metrics_scrape_jobs(payload, principal_context) if metrics_enabled else []
            ),
            systemd_units=payload.systemd_units if logs_enabled else [],
            journal_match_expressions=payload.journal_match_expressions if logs_enabled else [],
            file_log_sources=(
                [
                    BuilderFileLogSource(
                        include=source.include,
                        exclude=merge_file_excludes(source.exclude, self._path_exclude_patterns()),
                        attributes=source.attributes,
                    )
                    for source in payload.log_files
                ]
                if logs_enabled
                else []
            ),
            topology_labels=principal_context.juju_labels(charm_name=payload.charm_name),
            global_scrape_interval=self._global_scrape_interval(),
            global_scrape_timeout=self._global_scrape_timeout(),
            path_exclude=[],
            queue_size=self._queue_size(),
            max_elapsed_time_min=self._max_elapsed_time_min(),
            tls_insecure_skip_verify=self._tls_insecure_skip_verify(),
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
        self.unit.status = ops.ActiveStatus(self._status_message(f"config valid; {active_message}"))
        return True

    @staticmethod
    def _logs_declared(payload: MachineObservabilityPayload) -> bool:
        """Return whether the principal has declared any log sources."""
        return bool(
            payload.systemd_units or payload.journal_match_expressions or payload.log_files
        )

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
        payload: MachineObservabilityPayload | None = None,
        loki_endpoints: list[str],
        remote_write_endpoints: list[str],
    ) -> list[str]:
        """Return required relation inputs that are still missing."""
        missing_relations: list[str] = []
        if principal_context is None:
            missing_relations.append("juju-info relation")
        if not self._has_machine_observability_relation():
            missing_relations.append("machine-observability relation")
        if payload is None:
            return missing_relations
        if self._logs_declared(payload) and not loki_endpoints:
            missing_relations.append("send-loki-logs relation for declared log sources")
        if payload.metrics_endpoints and not remote_write_endpoints:
            missing_relations.append(
                "send-remote-write relation for declared metrics endpoints"
            )
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
        """Return principal context from the subordinate attachment relation."""
        relation = self.model.get_relation("juju-info")
        if relation is None or not relation.units:
            return None
        return PrincipalContext.from_relation(
            relation,
            model_name=self.model.name,
            model_uuid=self.model.uuid,
        )

    def _observability_payload(self):
        """Return the current machine-observability payload if present."""
        relation = self.model.get_relation("machine-observability")
        if relation is None:
            return MachineObservabilityPayload()
        return self.machine_observability_consumer.get_payload(relation)

    def _has_machine_observability_relation(self) -> bool:
        """Return whether the machine-observability relation is currently present."""
        return self.model.get_relation("machine-observability") is not None

    def _desired_custom_args(self) -> str:
        """Return the desired Alloy service args."""
        return build_effective_custom_args(str(self.config.get("custom-args", "")))

    def _loki_endpoint_urls(self) -> list[str]:
        """Return outbound Loki endpoint URLs from related apps."""
        return relation_urls(
            self.model.relations.get("send-loki-logs", []),
            direct_keys=("url",),
            json_keys=("endpoint", "endpoints"),
        )

    def _remote_write_endpoint_urls(self) -> list[str]:
        """Return outbound remote-write endpoint URLs from related apps."""
        return relation_urls(
            self.model.relations.get("send-remote-write", []),
            direct_keys=("url",),
            json_keys=("remote_write", "endpoints"),
        )

    def _active_metrics_scrape_jobs(
        self, payload: MachineObservabilityPayload, principal_context: PrincipalContext
    ) -> list[BuilderMetricsScrapeJob]:
        """Translate active metrics endpoints from the machine-observability payload."""
        if not self._remote_write_endpoint_urls():
            return []
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

    def _queue_size(self) -> int:
        """Return queue size for outbound telemetry buffering."""
        return int(self.config.get("queue_size", 1000))

    def _max_elapsed_time_min(self) -> int:
        """Return the max retry window in minutes."""
        return int(self.config.get("max_elapsed_time_min", 5))


if __name__ == "__main__":
    ops.main(AlloySubCharm)
