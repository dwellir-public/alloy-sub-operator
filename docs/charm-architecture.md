# alloy-sub Charm Architecture

## Overview

alloy-sub is a machine subordinate that attaches to a principal using `juju-info`
and consumes generic workload source declarations from the principal over
`machine-observability`.

## Responsibilities

- render and validate `/etc/alloy/config.alloy`
- scrape declared metrics sources
- collect declared journald and file logs
- forward logs to Loki
- forward metrics via remote write
- validate, topology-label, and forward v3 Prometheus and Loki alert rules

## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: generic workload source declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding

The same sink relations carry standard alert-rule databags. Dashboards are
published directly by principals to Grafana and are outside Alloy's role.

## Artifact lifecycle

The consumer supports v1, v2, and v3. For v3 it verifies compressed content and
checksums, parses bounded documents, injects authoritative Juju topology, and
uses packaged `cos-tool` for PromQL/LogQL validation. The CLI is a hook-time
validator, not a workload service.

Per-artifact LKG state is leader-owned relation application data. A malformed,
future-version, or structurally invalid outer payload retains the whole
relation LKG. Within a valid v3 payload, a malformed artifact retains only its
own existing LKG. Valid omission removes an artifact and relation removal
clears its ownership. Non-empty v3 rule sets require
`source_topology.model_uuid` and `source_topology.application`; labels are
injected from that original payload topology.

## Migration Notes

- principal charms declare sources, not workload identity, in v1
- v2 providers may additionally publish `source_topology`
- `alloy-sub` derives `juju_model`, `juju_model_uuid`, `juju_application`, and
  `juju_unit` from the attached principal relation
- `juju_charm` is optional metadata from the principal payload, not required for
  the core contract
- `alloy-sub` accepts all three schema versions and remains backward-compatible with
  existing v1 providers such as `polkadot`
- for telemetry label derivation and attachment, `source_topology` is optional
  and `juju-info` remains authoritative in subordinate mode
- non-empty v3 alert artifacts require the original payload's
  `source_topology.model_uuid` and `source_topology.application`; rule matchers
  and injected topology labels derive from that payload topology
- for `send-remote-write`, `alloy-sub` consumes the standard shared
  `prometheus_remote_write` URL contract only
- partitioning in the shared observability deployment is done through metric
  labels such as `juju_model`, `juju_model_uuid`, `juju_application`, and
  `juju_unit`, not by publishing tenant metadata on the relation
