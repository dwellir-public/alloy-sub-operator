"""Render Alloy configuration for subordinate-hosted workload telemetry."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_CONFIG_DIR = "/etc/alloy"
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy")
DEFAULT_PACKAGE_CONFIG_BACKUP_PATH = os.path.join(
    DEFAULT_CONFIG_DIR, "config.alloy.package-default"
)
DEFAULT_CONFIG_BACKUP_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy.bak")
DEFAULT_SYSTEMD_DEFAULTS_PATH = "/etc/default/alloy"
REMOTE_WRITE_COMPONENT_NAME = "metrics"


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
        loki_endpoints: list[str],
        remote_write_endpoints: list[str],
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

    def build(self) -> str:
        """Return the rendered Alloy configuration."""

        blocks = []
        if self._remote_write_endpoints:
            blocks.extend([self._render_remote_write(), ""])
        for job in self._metrics_scrape_jobs:
            blocks.extend([self._render_metrics_scrape(job), ""])
        if self._has_logs():
            blocks.extend([self._render_juju_processor(), ""])
            blocks.extend(self._render_journal_sources())
            if self._render_filelog_sources():
                blocks.extend(self._render_filelog_sources())
            if self._loki_endpoints:
                blocks.extend([self._render_loki_writer(), ""])
        return "\n".join(blocks).rstrip() + "\n"

    def _render_remote_write(self) -> str:
        endpoint_blocks = "\n".join(
            [
                "\n".join(
                    [
                        "  endpoint {",
                        f'    url = "{endpoint}"',
                        "  }",
                    ]
                )
                for endpoint in self._remote_write_endpoints
            ]
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

    def _render_metrics_scrape(self, scrape_job: MetricsScrapeJob) -> str:
        component_name = self._sanitize_component_name(scrape_job.job_name)
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
            f"  forward_to = [prometheus.remote_write.{REMOTE_WRITE_COMPONENT_NAME}.receiver]",
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
                            '  labels = {log_source = "journal"}',
                            "  forward_to = [loki.process.juju.receiver]",
                            "}",
                        ]
                    ),
                    "",
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
                        *(
                            [f'      __path_exclude__ = "{self._combine_excludes(excludes)}",']
                            if excludes
                            else []
                        ),
                        *self._render_label_lines(source.attributes, indent="      "),
                        "    },",
                    ]
                )
        return rendered

    def _render_juju_processor(self) -> str:
        return "\n".join(
            [
                'loki.process "juju" {',
                "  stage.static_labels {",
                "    values = {",
                *self._render_label_lines(self._topology_labels, indent="      "),
                "    }",
                "  }",
                "  forward_to = [loki.write.main.receiver]",
                "}",
            ]
        )

    def _render_loki_writer(self) -> str:
        endpoint_blocks = "\n".join(
            [
                "\n".join(
                    [
                        "  endpoint {",
                        f'    url = "{endpoint}"',
                        "  }",
                    ]
                )
                for endpoint in self._loki_endpoints
            ]
        )
        return "\n".join(
            [
                'loki.write "main" {',
                endpoint_blocks,
                "}",
            ]
        )

    def _has_logs(self) -> bool:
        return bool(
            self._systemd_units or self._journal_match_expressions or self._file_log_sources
        )

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
        return [
            f"{indent}{self._render_key(key)} = {json.dumps(labels[key])},"
            for key in sorted(labels)
        ]
