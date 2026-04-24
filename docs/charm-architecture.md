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

## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: generic workload source declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding

## Migration Notes

- principal charms declare sources, not workload identity
- `alloy-sub` derives `juju_model`, `juju_model_uuid`, `juju_application`, and
  `juju_unit` from the attached principal relation
- `juju_charm` is optional metadata from the principal payload, not required for
  the core contract
- for `send-remote-write`, `alloy-sub` consumes the standard shared
  `prometheus_remote_write` URL contract only
- partitioning in the shared observability deployment is done through metric
  labels such as `juju_model`, `juju_model_uuid`, `juju_application`, and
  `juju_unit`, not by publishing tenant metadata on the relation
