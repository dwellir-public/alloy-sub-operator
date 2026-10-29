"""Charm-owned host disk and memory usage alert rules for the host-metrics job."""

from __future__ import annotations

import json

try:
    from .config_builder import HOST_METRICS_JOB_NAME
except ImportError:
    from config_builder import HOST_METRICS_JOB_NAME

HOST_ALERT_GROUP_SUFFIX = "host-metrics"
HOST_USAGE_WARNING_PERCENT = 80
HOST_USAGE_CRITICAL_PERCENT = 90
HOST_ALERT_FOR = "5m"
# node_exporter already drops most pseudo filesystems; tmpfs mounts such as
# /run survive its defaults and squashfs snaps are always 100% full.
HOST_DISK_FSTYPE_EXCLUDE = "tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"

_DISK_DESCRIPTION = '{{ $labels.mountpoint }} on {{ $labels.juju_unit }} is {{ $value | printf "%.1f" }}% full.'
_MEMORY_DESCRIPTION = '{{ $labels.juju_unit }} is using {{ $value | printf "%.1f" }}% of memory.'


def _usage_rule(
    alert: str,
    usage_expr: str,
    threshold: int,
    severity: str,
    summary: str,
    description: str,
) -> dict[str, object]:
    """Build one alerting rule that fires when a usage percentage exceeds a threshold."""
    return {
        "alert": alert,
        "expr": f"{usage_expr} > {threshold}",
        "for": HOST_ALERT_FOR,
        # Only severity: the scraped series already carry the principal's Juju
        # topology, and a rule label would override it on every alert.
        "labels": {"severity": severity},
        "annotations": {"summary": summary, "description": description},
    }


def build_host_alert_rule_groups(*, model_uuid: str, application: str) -> list[dict[str, object]]:
    """Return one Prometheus rule group alerting on host disk and memory usage model-wide."""
    base_matcher = f"juju_model_uuid={json.dumps(model_uuid)},job={json.dumps(HOST_METRICS_JOB_NAME)}"
    disk_matcher = f"{base_matcher},fstype!~{json.dumps(HOST_DISK_FSTYPE_EXCLUDE)}"
    disk_usage = (
        f"100 * (1 - node_filesystem_avail_bytes{{{disk_matcher}}} / node_filesystem_size_bytes{{{disk_matcher}}})"
    )
    memory_usage = (
        f"100 * (1 - node_memory_MemAvailable_bytes{{{base_matcher}}} / node_memory_MemTotal_bytes{{{base_matcher}}})"
    )
    rules: list[dict[str, object]] = []
    for severity, threshold in (("warning", HOST_USAGE_WARNING_PERCENT), ("critical", HOST_USAGE_CRITICAL_PERCENT)):
        rules.append(
            _usage_rule(
                f"HostDiskUsage{severity.capitalize()}",
                disk_usage,
                threshold,
                severity,
                f"Disk usage above {threshold}% on {{{{ $labels.juju_unit }}}} ({{{{ $labels.mountpoint }}}})",
                _DISK_DESCRIPTION,
            )
        )
    for severity, threshold in (("warning", HOST_USAGE_WARNING_PERCENT), ("critical", HOST_USAGE_CRITICAL_PERCENT)):
        rules.append(
            _usage_rule(
                f"HostMemoryUsage{severity.capitalize()}",
                memory_usage,
                threshold,
                severity,
                f"Memory usage above {threshold}% on {{{{ $labels.juju_unit }}}}",
                _MEMORY_DESCRIPTION,
            )
        )
    return [{"name": f"{application}-{HOST_ALERT_GROUP_SUFFIX}", "rules": rules}]
