"""Render Alloy configuration for subordinate-hosted workload telemetry."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

try:
    from .outbound_endpoints import OutboundEndpoint
except ImportError:
    from outbound_endpoints import OutboundEndpoint

DEFAULT_CONFIG_DIR = "/etc/alloy"
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy")
DEFAULT_PACKAGE_CONFIG_BACKUP_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy.package-default")
DEFAULT_CONFIG_BACKUP_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy.bak")
DEFAULT_SYSTEMD_DEFAULTS_PATH = "/etc/default/alloy"
REMOTE_WRITE_COMPONENT_NAME = "metrics"

HOST_METRICS_COMPONENT_NAME = "node"
HOST_METRICS_JOB_NAME = "node-exporter"

# Host metrics are cheap and their value is in the resolution, so this job is
# pinned here rather than following the charm's global scrape interval.
HOST_METRICS_SCRAPE_INTERVAL = "15s"


@dataclass(frozen=True)
class ScrapeTarget:
    """One rendered Alloy scrape target."""

    address: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricsScrapeJob:
    """A translated subset of a Prometheus scrape job."""

    job_name: str
    targets: list[ScrapeTarget]
    metrics_path: str = "/metrics"
    scheme: str = "http"
    scrape_interval: str = ""
    scrape_timeout: str = ""
    tls_config: dict[str, str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class HostMetrics:
    """Host-level metrics collected by Alloy's built-in node_exporter.

    Alloy embeds node_exporter as ``prometheus.exporter.unix``, so host metrics
    need no second process, no listening port, and nothing installed on the
    machine. The charm therefore owns no host state here: this is config only.
    """

    topology_labels: dict[str, str] = field(default_factory=dict)
    scrape_timeout: str = ""
    disable_collectors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FileLogSource:
    """One translated file log source."""

    include: list[str]
    exclude: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)


class ConfigBuilder:
    """Build Alloy config text from relation-driven observability inputs."""

    def __init__(
        self,
        *,
        loki_endpoints: Sequence[str | OutboundEndpoint],
        remote_write_endpoints: Sequence[str | OutboundEndpoint],
        metrics_scrape_jobs: list[MetricsScrapeJob],
        systemd_units: list[str],
        journal_match_expressions: list[str],
        file_log_sources: list[FileLogSource],
        topology_labels: dict[str, str],
        global_scrape_interval: str,
        global_scrape_timeout: str,
        path_exclude: list[str],
        queue_size: int,
        max_elapsed_time_min: int,
        tls_insecure_skip_verify: bool,
        host_metrics: HostMetrics | None = None,
    ):
        self._loki_endpoints = loki_endpoints
        self._remote_write_endpoints = remote_write_endpoints
        self._metrics_scrape_jobs = metrics_scrape_jobs
        self._systemd_units = systemd_units
        self._journal_match_expressions = journal_match_expressions
        self._file_log_sources = file_log_sources
        self._topology_labels = topology_labels
        self._global_scrape_interval = global_scrape_interval
        self._global_scrape_timeout = global_scrape_timeout
        self._path_exclude = path_exclude
        self._queue_size = queue_size
        self._max_elapsed_time_min = max_elapsed_time_min
        self._tls_insecure_skip_verify = tls_insecure_skip_verify
        self._host_metrics = host_metrics

    def build(self) -> str:
        """Return the rendered Alloy configuration."""
        blocks = []
        if self._remote_write_endpoints:
            blocks.extend([self._render_remote_write(), ""])
        if self._host_metrics is not None:
            blocks.extend([self._render_host_metrics(self._host_metrics), ""])
        for job in self._metrics_scrape_jobs:
            blocks.extend([self._render_metrics_scrape(job), ""])
        if self._has_log_sources():
            blocks.extend([self._render_juju_processor(), ""])
            blocks.extend(self._render_journal_sources())
            filelog_sources = self._render_filelog_sources()
            if filelog_sources:
                blocks.extend(filelog_sources)
            if self._loki_endpoints:
                blocks.extend([self._render_loki_writer(), ""])
        return "\n".join(blocks).rstrip() + "\n"

    @staticmethod
    def _normalize_endpoints(endpoints: Sequence[str | OutboundEndpoint]) -> list[OutboundEndpoint]:
        return [
            endpoint if isinstance(endpoint, OutboundEndpoint) else OutboundEndpoint(url=endpoint)
            for endpoint in endpoints
        ]

    def _render_remote_write(self) -> str:
        endpoint_blocks = "\n".join(
            "\n".join(self._render_endpoint_block(endpoint))
            for endpoint in self._normalize_endpoints(self._remote_write_endpoints)
        )
        return "\n".join(
            [
                f'prometheus.remote_write "{REMOTE_WRITE_COMPONENT_NAME}" {{',
                endpoint_blocks,
                "",
                "  wal {",
                '    min_keepalive_time = "0s"',
                f'    max_keepalive_time = "{self._max_elapsed_time_min}m"',
                "  }",
                "}",
            ]
        )

    def _render_endpoint_block(self, endpoint: OutboundEndpoint) -> list[str]:
        lines = ["  endpoint {", f'    url = "{endpoint.url}"']
        if endpoint.username and endpoint.password:
            lines.extend(
                [
                    "    basic_auth {",
                    f"      username = {json.dumps(endpoint.username)}",
                    f"      password = {json.dumps(endpoint.password)}",
                    "    }",
                ]
            )
        if endpoint.tls_ca_pem:
            lines.extend(
                [
                    "    tls_config {",
                    f"      ca_pem = {json.dumps(endpoint.tls_ca_pem)}",
                    "    }",
                ]
            )
        lines.append("  }")
        return lines

    def _render_host_metrics(self, host_metrics: HostMetrics) -> str:
        """Render the exporter, relabel, and scrape blocks for host metrics.

        The exporter's targets are produced by the component rather than built
        here, so the Juju topology labels have to be attached with a
        ``discovery.relabel`` block instead of being written into the target map.
        """
        name = HOST_METRICS_COMPONENT_NAME
        lines = [f'prometheus.exporter.unix "{name}" {{']
        if host_metrics.disable_collectors:
            collectors = ", ".join(json.dumps(collector) for collector in host_metrics.disable_collectors)
            lines.append(f"  disable_collectors = [{collectors}]")
        lines.extend(
            [
                "}",
                "",
                f'discovery.relabel "{name}" {{',
                f"  targets = prometheus.exporter.unix.{name}.targets",
            ]
        )
        for key in sorted(host_metrics.topology_labels):
            lines.extend(
                [
                    "",
                    "  rule {",
                    f"    target_label = {json.dumps(key)}",
                    f"    replacement  = {json.dumps(host_metrics.topology_labels[key])}",
                    "  }",
                ]
            )
        lines.extend(
            [
                "}",
                "",
                f'prometheus.scrape "{name}" {{',
                f"  targets = discovery.relabel.{name}.output",
                f"  job_name = {json.dumps(HOST_METRICS_JOB_NAME)}",
                f"  scrape_interval = {json.dumps(HOST_METRICS_SCRAPE_INTERVAL)}",
                f"  scrape_timeout = {json.dumps(host_metrics.scrape_timeout or self._global_scrape_timeout)}",
                f"  forward_to = {self._metrics_forward_to()}",
                "}",
            ]
        )
        return "\n".join(lines)

    def _metrics_forward_to(self) -> str:
        return (
            f"[prometheus.remote_write.{REMOTE_WRITE_COMPONENT_NAME}.receiver]"
            if self._remote_write_endpoints
            else "[]"
        )

    def _render_metrics_scrape(self, scrape_job: MetricsScrapeJob) -> str:
        component_name = self._sanitize_component_name(scrape_job.job_name)
        forward_to = self._metrics_forward_to()
        lines = [
            f'prometheus.scrape "{component_name}" {{',
            "  targets = [",
            *self._render_targets(scrape_job.targets),
            "  ]",
            f"  job_name = {json.dumps(scrape_job.job_name)}",
            f"  metrics_path = {json.dumps(scrape_job.metrics_path)}",
            f"  scheme = {json.dumps(scrape_job.scheme)}",
            f"  scrape_interval = {json.dumps(scrape_job.scrape_interval or self._global_scrape_interval)}",
            f"  scrape_timeout = {json.dumps(scrape_job.scrape_timeout or self._global_scrape_timeout)}",
            f"  forward_to = {forward_to}",
        ]
        if scrape_job.tls_config or self._tls_insecure_skip_verify:
            tls_config = dict(scrape_job.tls_config)
            if self._tls_insecure_skip_verify:
                tls_config.setdefault("insecure_skip_verify", True)
            lines.extend(self._render_tls_config(tls_config))
        lines.append("}")
        return "\n".join(lines)

    def _render_targets(self, targets: list[ScrapeTarget]) -> list[str]:
        rendered: list[str] = []
        for target in targets:
            rendered.extend(
                [
                    "    {",
                    f'      __address__ = "{target.address}",',
                    *self._render_label_lines(target.labels, indent="      "),
                    "    },",
                ]
            )
        return rendered

    def _render_tls_config(self, tls_config: dict[str, str | bool]) -> list[str]:
        lines = ["  tls_config {"]
        for key in sorted(tls_config):
            value = tls_config[key]
            rendered = "true" if value is True else "false" if value is False else json.dumps(value)
            lines.append(f"    {self._render_key(key)} = {rendered}")
        lines.append("  }")
        return lines

    def _render_journal_sources(self) -> list[str]:
        sources = []
        for index, unit in enumerate(self._systemd_units):
            name = "journald" if len(self._systemd_units) == 1 else f"journald_{index}"
            sources.extend(
                [
                    "\n".join(
                        [
                            f'loki.source.journal "{name}" {{',
                            f'  matches = "{self._format_unit_match(unit)}"',
                            '  relabel_rules = loki.relabel.journal.rules',
                            f'  labels = {{log_source = "journal", systemd_unit = "{unit}"}}',
                            "  forward_to = [loki.process.juju.receiver]",
                            "}",
                        ]
                    ),
                    "",
                ]
            )
        for index, match in enumerate(self._journal_match_expressions):
            name = "journal_match" if len(self._journal_match_expressions) == 1 else f"journal_match_{index}"
            sources.extend(
                [
                    "\n".join(
                        [
                            f'loki.source.journal "{name}" {{',
                            f'  matches = "{match}"',
                            '  relabel_rules = loki.relabel.journal.rules',
                            '  labels = {log_source = "journal"}',
                            "  forward_to = [loki.process.juju.receiver]",
                            "}",
                        ]
                    ),
                    "",
                ]
            )
        if sources:
            sources.extend(
                [
                    "\n".join(
                        [
                            'loki.relabel "journal" {',
                            "  forward_to = []",
                            "",
                            "  rule {",
                            '    source_labels = ["__journal__systemd_unit"]',
                            '    target_label  = "systemd_unit"',
                            "  }",
                            "",
                            "  rule {",
                            '    source_labels = ["__journal_syslog_identifier"]',
                            '    target_label  = "syslog_identifier"',
                            "  }",
                            "",
                            "  rule {",
                            '    source_labels = ["__journal_priority_keyword"]',
                            '    target_label  = "level"',
                            "  }",
                            "",
                            "  rule {",
                            '    source_labels = ["__journal_priority"]',
                            '    target_label  = "severity"',
                            "  }",
                            "}",
                            "",
                        ]
                    )
                ]
            )
        return sources

    def _render_filelog_sources(self) -> list[str]:
        if not self._file_log_sources:
            return []
        blocks = [
            'local.file_match "filelogs" {',
            "  path_targets = [",
            *self._render_file_targets(),
            "  ]",
            "}",
            "",
            'loki.source.file "filelogs" {',
            "  targets    = local.file_match.filelogs.targets",
            "  forward_to = [loki.process.juju.receiver]",
            "}",
            "",
        ]
        return blocks

    def _render_file_targets(self) -> list[str]:
        rendered: list[str] = []
        for source in self._file_log_sources:
            excludes = [*source.exclude, *self._path_exclude]
            for include in source.include:
                rendered.extend(
                    [
                        "    {",
                        f'      __path__ = "{include}",',
                        *([f'      __path_exclude__ = "{self._combine_excludes(excludes)}",'] if excludes else []),
                        *self._render_label_lines(source.attributes, indent="      "),
                        "    },",
                    ]
                )
        return rendered

    def _render_juju_processor(self) -> str:
        forward_to = "[loki.write.main.receiver]" if self._loki_endpoints else "[]"
        return "\n".join(
            [
                'loki.process "juju" {',
                "  stage.static_labels {",
                "    values = {",
                *self._render_label_lines(self._topology_labels, indent="      "),
                "    }",
                "  }",
                f"  forward_to = {forward_to}",
                "}",
            ]
        )

    def _render_loki_writer(self) -> str:
        endpoint_blocks = "\n".join(
            "\n".join(self._render_endpoint_block(endpoint))
            for endpoint in self._normalize_endpoints(self._loki_endpoints)
        )
        return "\n".join(
            [
                'loki.write "main" {',
                endpoint_blocks,
                "}",
            ]
        )

    def _has_log_sources(self) -> bool:
        return bool(self._systemd_units or self._journal_match_expressions or self._file_log_sources)

    @staticmethod
    def _sanitize_component_name(name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
        return sanitized or "metrics"

    @staticmethod
    def _format_unit_match(unit: str) -> str:
        return f"_SYSTEMD_UNIT={unit}"

    @staticmethod
    def _combine_excludes(excludes: list[str]) -> str:
        return ",".join(excludes)

    @staticmethod
    def _render_key(key: str) -> str:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            return key
        return json.dumps(key)

    def _render_label_lines(self, labels: dict[str, str], *, indent: str) -> list[str]:
        return [f"{indent}{self._render_key(key)} = {json.dumps(labels[key])}," for key in sorted(labels)]
