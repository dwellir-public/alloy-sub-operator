# alloy-sub

`alloy-sub` is a machine subordinate that attaches to a principal via `juju-info`.

The workload is Grafana Alloy and is installed via the official Grafana deb repository.

To properly integrate with the principal, it consumes `machine-observability` declarations for logs and metrics if your charm supports the `machine-observability` interface.

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

For the shared observability deployment, `send-remote-write` uses the plain
`prometheus_remote_write` URL contract. `alloy-sub` does not publish tenant
identity or tenant metadata on that relation. Shared Mimir partitioning is done
through metric labels such as Juju topology rather than tenant-specific
remote-write extensions.

## Validation Flow

Deploy the subordinate and principal, relate both relation endpoints, then inspect
`/etc/alloy/config.alloy` on the subordinate unit to confirm that:

- declared `systemd_units` render `loki.source.journal` blocks
- declared file log globs render `local.file_match` and `loki.source.file`
- declared metrics jobs render `prometheus.scrape` blocks
- outbound Loki and remote-write endpoints are included when related
