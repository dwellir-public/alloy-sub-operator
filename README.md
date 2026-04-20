# alloy-sub

`alloy-sub` is a machine subordinate that attaches to a principal via `juju-info`
and consumes `machine-observability` declarations for logs and metrics.

## Principal Contract

`alloy-sub` expects the principal charm to provide:

- systemd unit logs
- file log sources
- metrics endpoints
- optional workload labels

## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: neutral observability declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding

## Validation Flow

Deploy the subordinate and principal, relate both relation endpoints, then inspect
`/etc/alloy/config.alloy` on the subordinate unit to confirm that:

- declared `systemd_units` render `loki.source.journal` blocks
- declared file log globs render `local.file_match` and `loki.source.file`
- declared metrics jobs render `prometheus.scrape` blocks
- outbound Loki and remote-write endpoints are included when related
