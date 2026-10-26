"""Built-in Prometheus alert rules for the host metrics Alloy collects for its principal."""

from __future__ import annotations

import json

HOST_FILESYSTEM_GROUP_SUFFIX = "host-filesystem"

# Pseudo and per-boot filesystems never hold node data, so alerts on them are noise.
_FSTYPE_EXCLUDE = "tmpfs|devtmpfs|overlay|squashfs|fuse\\\\.lxcfs|fuse\\\\.snapfuse"
_MOUNTPOINT_EXCLUDE = "/run.*|/proc.*|/dev.*|/sys.*|/snap/.*"


def host_filesystem_rule_groups(
    *, model: str, model_uuid: str, application: str, charm_name: str
) -> list[dict[str, object]]:
    """Return filesystem alert groups scoped to the principal's host metrics.

    Host metrics carry the principal's Juju topology, so `application` and
    `charm_name` name the principal, not this subordinate.
    """
    topology = {
        "juju_model": model,
        "juju_model_uuid": model_uuid,
        "juju_application": application,
    }
    selector = ",".join(
        [
            *(f"{key}={json.dumps(value)}" for key, value in topology.items()),
            f'fstype!~"{_FSTYPE_EXCLUDE}"',
            f'mountpoint!~"{_MOUNTPOINT_EXCLUDE}"',
        ]
    )
    used_percent = f"100 * (1 - node_filesystem_avail_bytes{{{selector}}} / node_filesystem_size_bytes{{{selector}}})"
    labels = {**topology, **({"juju_charm": charm_name} if charm_name else {})}
    where = "on {{ $labels.instance }} (unit {{ $labels.juju_unit }} in model {{ $labels.juju_model }})"
    rules: list[dict[str, object]] = [
        {
            "alert": "HostDiskSpaceWarning",
            "expr": f"{used_percent} > 80",
            "for": "30m",
            "labels": {**labels, "severity": "warning"},
            "annotations": {
                "summary": f"Filesystem '{{{{ $labels.mountpoint }}}}' is over 80% full {where}",
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) uses "
                    '{{ $value | printf "%.1f" }}% of its space.'
                ),
            },
        },
        {
            "alert": "HostDiskSpace",
            "expr": f"{used_percent} > 90",
            "for": "15m",
            "labels": {**labels, "severity": "critical"},
            "annotations": {
                "summary": f"Filesystem '{{{{ $labels.mountpoint }}}}' is over 90% full {where}",
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) uses "
                    '{{ $value | printf "%.1f" }}% of its space.'
                ),
            },
        },
        {
            "alert": "HostFilesystemReadOnly",
            "expr": f"node_filesystem_readonly{{{selector}}} == 1",
            "for": "5m",
            "labels": {**labels, "severity": "critical"},
            "annotations": {
                "summary": f"Filesystem '{{{{ $labels.mountpoint }}}}' is read-only {where}",
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) is mounted "
                    "read-only. The kernel may have remounted it after an I/O or filesystem error."
                ),
            },
        },
        {
            "alert": "HostFilesystemDeviceError",
            # A bind mount into a service home often fails statfs for Alloy while another
            # mount of the same device stays readable and keeps the space alerts working.
            "expr": (
                f"node_filesystem_device_error{{{selector}}} == 1"
                f" unless on (instance, device) node_filesystem_device_error{{{selector}}} == 0"
            ),
            "for": "15m",
            "labels": {**labels, "severity": "warning"},
            "annotations": {
                "summary": f"Alloy cannot read filesystem '{{{{ $labels.mountpoint }}}}' {where}",
                "description": (
                    "statfs failed for '{{ $labels.mountpoint }}' ({{ $labels.device }}) and no other "
                    "mount of that device is readable. Space alerts for this device are blind until "
                    "Alloy can read it."
                ),
            },
        },
    ]
    return [
        {
            "name": f"{model_uuid}-{application}-{HOST_FILESYSTEM_GROUP_SUFFIX}",
            "rules": rules,
        }
    ]
