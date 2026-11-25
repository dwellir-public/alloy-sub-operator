"""Translate direct scrape relations without collecting other principal units."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from urllib.parse import urlsplit

try:
    from .config_builder import MetricsScrapeJob, ScrapeTarget
    from .principal_context import PrincipalContext
except ImportError:
    from config_builder import MetricsScrapeJob, ScrapeTarget
    from principal_context import PrincipalContext

_JOB_FIELDS = {
    "job_name",
    "metrics_path",
    "scheme",
    "scrape_interval",
    "scrape_timeout",
    "static_configs",
    "relabel_configs",
    "tls_config",
}
_RELABEL_FIELDS = {"source_labels", "separator", "target_label", "regex", "modulus", "replacement", "action"}
_TLS_FIELDS = {"ca_file", "cert_file", "key_file", "server_name", "insecure_skip_verify"}


def _local_target(target: str, labels: Mapping[str, str], principal: PrincipalContext) -> bool:
    """Select the attached unit, or a fixed target demonstrably on its machine."""
    if labels.get("juju_application") != principal.application:
        return False
    if labels.get("juju_model_uuid") != principal.model_uuid:
        return False
    if labels.get("juju_unit"):
        return labels["juju_unit"] == principal.unit
    return urlsplit("//" + target).hostname in {principal.address, "localhost", "127.0.0.1", "::1"}


def _targets(job: Mapping[str, object], principal: PrincipalContext) -> list[ScrapeTarget]:
    """Keep provider labels and select only this subordinate's local targets."""
    result = []
    configs = job.get("static_configs", [])
    if not isinstance(configs, list):
        raise ValueError("metrics-endpoint static_configs must be a list")
    for config in configs:
        if not isinstance(config, Mapping) or set(config) - {"targets", "labels"}:
            raise ValueError("metrics-endpoint has unsupported static_config fields")
        labels = config.get("labels", {})
        if not isinstance(labels, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in labels.items()
        ):
            raise ValueError("metrics-endpoint labels must be strings")
        addresses = config.get("targets", [])
        if not isinstance(addresses, list):
            raise ValueError("metrics-endpoint targets must be a list")
        for address in addresses:
            if not isinstance(address, str):
                raise ValueError("metrics-endpoint target must be a string")
            if _local_target(address, labels, principal):
                result.append(ScrapeTarget(address=address, labels={**labels, "juju_unit": principal.unit}))
    return result


def _tls(job: Mapping[str, object]) -> dict[str, str | bool]:
    """Preserve supported Prometheus TLS fields, including inline certificate PEM."""
    raw = job.get("tls_config", {})
    if not isinstance(raw, Mapping) or set(raw) - _TLS_FIELDS:
        raise ValueError("metrics-endpoint has unsupported TLS fields")
    result: dict[str, str | bool] = {}
    for key, value in raw.items():
        if key == "insecure_skip_verify" and isinstance(value, bool):
            result[key] = value
        elif isinstance(value, str):
            result[key.replace("_file", "_pem") if "-----BEGIN" in value else key] = value
        else:
            raise ValueError("metrics-endpoint has invalid TLS value")
    return result


def related_scrape_jobs(
    jobs: Sequence[Mapping[str, object]], principal: PrincipalContext, *, existing: Sequence[MetricsScrapeJob] = ()
) -> list[MetricsScrapeJob]:
    """Translate supported local jobs, preferring existing source declarations.

    Rejected fields fail configuration validation instead of silently losing auth,
    relabeling or scrape constraints. Fixed targets without unit labels must be
    local; app-wide remote targets belong on an alloy-vm collector.
    """
    seen = {(target.address, job.metrics_path, job.scheme) for job in existing for target in job.targets}
    translated = []
    for job in jobs:
        targets = _targets(job, principal)
        if not targets:
            continue
        unsupported = set(job) - _JOB_FIELDS
        if unsupported:
            raise ValueError("metrics-endpoint has unsupported job fields: " + ", ".join(sorted(unsupported)))
        relabels = job.get("relabel_configs", [])
        if not isinstance(relabels, list) or any(
            not isinstance(rule, dict) or set(rule) - _RELABEL_FIELDS for rule in relabels
        ):
            raise ValueError("metrics-endpoint has unsupported relabel fields")
        result = MetricsScrapeJob(
            job_name=str(job["job_name"]),
            targets=targets,
            metrics_path=str(job.get("metrics_path", "/metrics")),
            scheme=str(job.get("scheme", "http")),
            scrape_interval=str(job.get("scrape_interval", "")),
            scrape_timeout=str(job.get("scrape_timeout", "")),
            tls_config=_tls(job),
            relabel_configs=relabels,
        )
        unique = []
        for target in result.targets:
            identity = (target.address, result.metrics_path, result.scheme)
            if identity not in seen:
                seen.add(identity)
                unique.append(target)
        if unique:
            translated.append(replace(result, targets=unique))
    return translated
