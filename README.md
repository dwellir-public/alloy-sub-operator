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

`alloy-sub` is compatible with:

- v1 `machine_observability` payloads, where topology is derived from the attached
  `juju-info` principal relation
- v2 payloads, where the provider also publishes explicit `source_topology`
- v3 payloads, which retain v2 sources and add Prometheus and Loki alert-rule
  artifacts

In subordinate mode, `alloy-sub` still treats `juju-info` as the source of
truth for telemetry topology and may accept a v1 payload without
`source_topology`. Separately, a non-empty v3 rule set requires valid
`source_topology.model_uuid` and `source_topology.application` for stable rule
ownership. Rule labels are injected from the original payload topology, not
from the telemetry fallback.

### Alert-rule artifacts

For v3, `alloy-sub` decodes `gzip+base64` artifacts, verifies the SHA-256 of
decoded bytes, applies resource bounds, injects the principal's Juju topology
exactly once, and validates PromQL or LogQL with packaged `cos-tool`. This is an
internal rule-validation CLI, not a service, plugin, or datasource.

The complete payload may be exactly `60 * 1024` bytes but no larger, and is not
chunked. At most 32 artifacts are admitted per relation. Expressions, names,
and non-topology labels are not rewritten. A malformed, future-version, or
structurally invalid outer payload retains the whole relation's leader-shared
last-known-good (LKG) state. Within a valid v3 payload, invalid encoding,
checksum, or expression retains only that artifact's LKG while unrelated
artifacts and telemetry continue. Valid omission or relation removal withdraws
rules.

## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: neutral observability declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding
- `grafana-cloud-config`: outbound Grafana Cloud endpoints and credentials

Prometheus rules travel on `send-remote-write`; Loki rules travel on
`send-loki-logs`. Dashboards bypass Alloy and principals publish them directly
to Grafana over `grafana_dashboard`.

Direct backend relations:

```bash
juju relate principal:machine-observability alloy-sub:machine-observability
juju relate alloy-sub:send-loki-logs loki-vm:loki_push_api
juju relate alloy-sub:send-remote-write mimir-vm:receive-remote-write
```

Gateway relations keep ingestion and rule forwarding as separate planes:

```bash
juju relate alloy-sub:send-loki-logs loki-loadbalancer-vm:loki_push_api
juju relate loki-loadbalancer-vm:loki-alert-rules loki-vm:loki_push_api
juju relate loki-loadbalancer-vm:ingress loki-vm:ingress
juju relate alloy-sub:send-remote-write mimir-gateway-vm:receive-remote-write
juju relate mimir-gateway-vm:mimir-alert-rules mimir-vm:receive-remote-write
juju relate mimir-gateway-vm:backend mimir-vm:backend
```

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

## Host metrics

Set `enable-host-metrics=true` to collect host-level metrics, labelled with the
principal's Juju topology so they attribute to the correct unit:

```bash
juju config alloy-sub enable-host-metrics=true
```

Alloy collects these itself, so nothing is installed on the machine and no port
is opened. Any node-exporter already on the host is left alone.

- The job scrapes every 15s regardless of `global_scrape_interval`; only
  `global_scrape_timeout` applies.
- This is a complete pipeline on its own. With it set, the charm renders config
  and reports `host metrics only` without the `machine-observability` relation.

It defaults to `false`

## Validation Flow

Deploy the subordinate and principal, relate both relation endpoints, then inspect
`/etc/alloy/config.alloy` on the subordinate unit to confirm that:

- declared `systemd_units` render `loki.source.journal` blocks
- declared file log globs render `local.file_match` and `loki.source.file`
- declared metrics jobs render `prometheus.scrape` blocks
- outbound Loki and remote-write endpoints are included when related

## Contract compatibility

The supported compatibility matrix includes existing v1 providers such as
`polkadot` and the v3 `dwellir-observability-reference` provider. Verify valid
v3 forwarding and withdrawal after artifact removal in the target deployment.
Upgrade safely in this order: reference library, both Alloy variants, both
gateways, then Grafana VM.

The expected compatibility result after refreshing the charm is:

- the existing `alloy-sub` unit attached to `polkadot` remains `active`
- the dedicated `alloy-sub-reference` unit attached to
  `dwellir-observability-reference` also becomes `active`
- both units render principal-specific metrics and log pipelines from their
  respective payloads
