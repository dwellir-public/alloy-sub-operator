# Built-in host disk and memory usage alert rules

**Date:** 2026-10-29
**Status:** proposed
**Repo:** alloy-sub-operator

## Problem

`enable-host-metrics=true` makes every `alloy-sub` unit scrape its host with
Alloy's embedded node_exporter (`job="node-exporter"`, every 60s), labelled
with the attached principal's Juju topology. Nothing alerts on those series:
the only alert rules `alloy-sub` publishes come from principal v3 artifacts, so
a full disk or exhausted memory on a node goes unnoticed unless each principal
charm ships its own host rules.

## Goal

When host metrics are enabled, `alloy-sub` publishes charm-owned disk-usage and
memory-usage alert rules (warning at 80%, critical at 90%) on
`send-remote-write`, alongside any principal rules, and withdraws them when
host metrics are disabled. No downstream charm or interface changes.

## Decisions (confirmed with the owner on 2026-10-29)

| Decision | Value |
|---|---|
| Disk scope | all mounted filesystems except pseudo/ephemeral types: `fstype!~"tmpfs\|devtmpfs\|overlay\|squashfs\|iso9660\|ramfs"` |
| Memory usage | `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` |
| Disk usage | `100 * (1 - node_filesystem_avail_bytes / node_filesystem_size_bytes)` |
| Thresholds | fixed constants in the charm: warning `> 80`, critical `> 90` |
| `for` duration | `5m` on all four rules |
| Alert scope | all hosts in the model: matcher `juju_model_uuid="<model uuid>"` only (revised 2026-10-29: `job` dropped at the owner's request; `juju_application` cannot be used because the series carry the principal's application, not this charm's) |
| Severity labels | `severity: warning` / `severity: critical`; no other rule labels |
| Both fire | warning and critical both fire above 90%; suppression is Alertmanager inhibition, out of scope |

## Design

### Rule content

One Prometheus rule group named `<model uuid>-<alloy-sub application name>-host-metrics`
containing, in this order. The model UUID is part of the name because
`mimir-vm` merges every `receive-remote-write` relation into one ruler
namespace and rejects duplicate group names across relations; two models with
the default application name would otherwise freeze rule delivery for every
relation.

| alert | expr threshold | severity |
|---|---|---|
| `HostDiskUsageWarning` | disk usage `> 80` | warning |
| `HostDiskUsageCritical` | disk usage `> 90` | critical |
| `HostMemoryUsageWarning` | memory usage `> 80` | warning |
| `HostMemoryUsageCritical` | memory usage `> 90` | critical |

Every rule has `for: 5m`, `labels: {severity: ...}`, and `summary` and
`description` annotations that name `{{ $labels.juju_unit }}` (and
`{{ $labels.mountpoint }}` for disk) and the measured `{{ $value }}`.

Rules must not set any `juju_*` label: rule labels override series labels in
Prometheus, so an injected `juju_unit` would mislabel every alert as the
leader's principal unit. The series already carry the correct topology from the
`discovery.relabel "node"` block.

The model UUID is inserted with `json.dumps` so a quote or backslash cannot
break out of the matcher.

### Ownership and publication

The rules are charm-owned, not artifact-owned, so they bypass the
machine-observability LKG cache entirely. The leader computes them from
`enable-host-metrics`, `model.uuid`, and `app.name` on every rule reconcile
and appends them after the flattened principal Prometheus groups before
`publish_rule_groups(self, "send-remote-write", ...)`. Loki publication is
untouched. Non-leaders publish nothing, as today. Toggling the option off
reconciles rules on `config-changed` and withdraws the group.

The host group's bytes count toward the 60 KiB publication ceiling:
`_rule_state_is_publishable` and `_bounded_existing_rule_state` take the host
groups as extra Prometheus groups so a near-limit principal state cannot push
the published value over the limit and raise inside the hook.

### Unit status

`_set_rule_destination_status` continues to consider principal groups only.
Host metrics may legitimately flow to Grafana Cloud via `grafana-cloud-config`,
which carries no alert rules; enabling host metrics there must not flip an
`Active` unit to `Waiting for send-remote-write relation`. The README states
that host alarms are delivered only over `send-remote-write`.

### Validation

The rules are static and schema-checked in unit tests with
`alert_rules.validate_rule_groups`. They are not run through `cos-tool` at hook
time; `cos-tool` is reserved for untrusted principal artifacts.

### Files

- Create `src/host_alert_rules.py`: constants and
  `build_host_alert_rule_groups(*, model_uuid, application)`.
- Modify `src/charm.py`: import, `_host_alert_rule_groups`, publishable-check
  parameters, `_reconcile_rule_groups` append.
- Create `tests/unit/test_host_alert_rules.py`; extend
  `tests/unit/test_remote_write_relation.py`,
  `tests/unit/test_observability_relation.py`,
  `tests/unit/test_repo_baseline.py`.
- Modify `README.md`, `charmcraft.yaml` (option description),
  `docs/charm-architecture.md`.

## Non-goals

- Configurable thresholds or durations.
- CPU, load, inode, or swap alarms.
- Delivering rules to Grafana Cloud.
- Interaction with the proposed peer-relation rule aggregation
  (`2026-08-27-shared-subordinate-rule-aggregation-design.md`); that work
  changes how principal groups are collected, and this feature appends after
  flattening, so the hook point survives it.
