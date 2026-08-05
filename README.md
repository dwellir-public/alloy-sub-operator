# alloy-sub

`alloy-sub` is a machine subordinate that attaches to a principal via `juju-info`.

The workload is Grafana Alloy and is installed via the official Grafana deb repository.

To properly integrate with the principal, it consumes `machine-observability` declarations for logs and metrics if your charm supports the `machine-observability` interface.

## Principal Contract

`alloy-sub` expects the principal charm to provide:

- systemd unit logs
- file log sources
- metrics endpoints
- optional `charm_name`
- optional `source_topology`

`alloy-sub` is now compatible with both:

- v1 `machine_observability` payloads, where topology is derived from the attached
  `juju-info` principal relation
- v2 payloads, where the provider also publishes explicit `source_topology`

In subordinate mode, `alloy-sub` still treats `juju-info` as the source of
truth for the attached principal unit. The v2 `source_topology` block is
accepted for forward compatibility with providers that are also intended to
work with `alloy-vm`.

## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: neutral observability declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding
- `grafana-cloud-config`: outbound Grafana Cloud endpoints and credentials

For the shared observability deployment, `send-remote-write` uses the plain
`prometheus_remote_write` URL contract. `alloy-sub` does not publish tenant
identity or tenant metadata on that relation. Shared Mimir partitioning is done
through metric labels such as Juju topology rather than tenant-specific
remote-write extensions.

## Grafana Cloud Integrator

`alloy-sub` can consume Grafana Cloud endpoints and credentials from
`grafana-cloud-integrator` over:

- endpoint name: `grafana-cloud-config`
- interface name: `grafana_cloud_config`

This relation lets the subordinate render authenticated Grafana Cloud sinks for:

- metrics through `prometheus.remote_write`
- logs through `loki.write`

The relation is additive. If the subordinate is also related to:

- `send-remote-write`
- `send-loki-logs`

then Alloy renders dual upstream forwarding and sends to both the plain
relation-provided endpoints and the Grafana Cloud endpoints.

### Credential behavior

`alloy-sub` supports both:

- legacy shared `username` and `password`
- signal-specific credentials from the relation, such as:
  - `prometheus_username` / `prometheus_password`
  - `loki_username` / `loki_password`

Signal-specific credentials are used when present, which is required for
Grafana Cloud environments where Prometheus and Loki use different instance IDs
or tokens.

### Connectivity checks

During `update-status`, `alloy-sub` probes the Grafana Cloud metrics and logs
endpoints from the relation. If a probe fails, the unit goes blocked with a
message such as:

- `Grafana Cloud metrics connectivity failed: ...`
- `Grafana Cloud logs connectivity failed: ...`

### Example deployment

Deploy and configure `grafana-cloud-integrator`:

```bash
juju deploy grafana-cloud-integrator
juju config grafana-cloud-integrator prometheus-url="https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push"
juju config grafana-cloud-integrator loki-url="https://logs-prod-025.grafana.net/loki/api/v1/push"
juju config grafana-cloud-integrator signal-credentials='
prometheus:
  username: "1076854"
  password: "<prometheus-token>"
loki:
  username: "639149"
  password: "<loki-token>"
'
```

Relate it to the subordinate:

```bash
juju relate alloy-sub:grafana-cloud-config grafana-cloud-integrator:grafana-cloud-config
```

Verify the rendered config on the principal machine where the subordinate is
attached:

```bash
juju ssh <principal-unit> 'grep -n "prometheus.remote_write \\\"metrics\\\"" -A20 /etc/alloy/config.alloy'
juju ssh <principal-unit> 'grep -n "loki.write \\\"main\\\"" -A20 /etc/alloy/config.alloy'
```

Expected result:

- the Grafana Cloud Prometheus endpoint is rendered with `basic_auth`
- the Grafana Cloud Loki endpoint is rendered with `basic_auth`
- if plain upstream relations also exist, Alloy renders both sets of endpoints

## Host metrics (node-exporter)

Set `enable-host-metrics=true` to collect host-level metrics:

```bash
juju config alloy-sub enable-host-metrics=true
```

Alloy embeds node_exporter as its `prometheus.exporter.unix` component, so this
installs nothing on the machine, starts no second process, and opens no port.
The charm renders three blocks: the exporter, a `discovery.relabel` that attaches
the principal's Juju topology labels, and a `prometheus.scrape` that forwards to
remote write. Host metrics therefore attribute to the correct Juju unit.

Because the exporter runs inside Alloy, the charm owns no host state here. There
is nothing to install, restore, or clean up on unit removal, and a pre-existing
node-exporter on the machine is unaffected — the charm neither reads nor touches
it.

Two behaviours worth knowing:

- The job scrapes every 15s regardless of `global_scrape_interval`; only
  `global_scrape_timeout` applies. Host metrics are cheap and their value is in
  the resolution.
- `enable-host-metrics=true` is a complete pipeline by itself. With it set, the
  charm renders config and reports `host metrics only` without the
  `machine-observability` relation.

Default collectors emit roughly a thousand series per host. Leave this off where
remote write volume is a concern — that is the one real reason to want it off,
which is why it defaults to `false`.

Verify on the principal machine:

```bash
juju ssh <principal-unit> 'grep -n "prometheus.exporter.unix" -A20 /etc/alloy/config.alloy'
```

## Validation Flow

Deploy the subordinate and principal, relate both relation endpoints, then inspect
`/etc/alloy/config.alloy` on the subordinate unit to confirm that:

- declared `systemd_units` render `loki.source.journal` blocks
- declared file log globs render `local.file_match` and `loki.source.file`
- declared metrics jobs render `prometheus.scrape` blocks
- outbound Loki and remote-write endpoints are included when related

## v2 Compatibility Check

In the local model `alloy-sub-e2e-20260419`, `alloy-sub` has been validated
against both:

- `polkadot` publishing the existing v1 payload
- `dwellir-observability-reference` publishing the v2 payload with
  `source_topology`

The expected result after refreshing the charm is:

- the existing `alloy-sub` unit attached to `polkadot` remains `active`
- the dedicated `alloy-sub-reference` unit attached to
  `dwellir-observability-reference` also becomes `active`
- both units render principal-specific metrics and log pipelines from their
  respective payloads
