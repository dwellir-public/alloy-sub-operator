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

Each `metrics_endpoints[].labels` entry (optional static `name: value` pairs) is
applied to the series scraped from that endpoint's targets. Juju topology labels
take precedence over an endpoint label of the same name.

Each target also gets `internal_ip`, this unit's address.

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
chunked. At most 32 artifacts are admitted per relation. Placeholder-bearing
expressions and group names are rewritten for topology injection and
deterministic scoping; non-topology labels are preserved. A malformed, future-version, or
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
- `profiling-source`: pprof targets the principal publishes (`pprof_scrape`, optional, container scope)
- `send-profiles`: outbound profile push to Pyroscope (`profiling`, optional, one remote)

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

## Single principal

One `alloy-sub` application serves exactly one principal application. The
`juju-info`, `machine-observability`, and `profiling-source` endpoints each
declare `limit: 1`, so Juju rejects a second relation on any of them:

```text
ERROR cannot add relation "alloy-sub:juju-info other:juju-info": establishing a new relation for alloy-sub:juju-info would exceed its maximum relation limit of 1 (quota limit exceeded)
```

Juju 3.6 prints that message only with `juju integrate --debug`; without it the
command exits non-zero and adds no relation.

The limit counts relations, not units: the one principal application can have
any number of units, and each gets its own `alloy-sub` unit. To observe another
principal application, deploy the charm again under its own name:

```bash
juju deploy alloy-sub alloy-sub-other
juju relate alloy-sub-other:juju-info other
```

Juju checks each endpoint separately, so it accepts `juju-info` to one
application and `machine-observability` or `profiling-source` to another. The
charm catches that itself: every unit publishes its principal application name
on the `alloy-peers` peer relation, and when the units of one application see
more than one name they all report:

```text
Alloy service running; multiple principal applications: kusama, polkadot; relate alloy-sub to one principal
```

A blocked unit keeps Alloy running on the last applied config and applies no
further config changes. Remove the relations to all but one principal
application and the units return to their normal status on their own.

### Upgrading an application that has several principals

`juju refresh` refuses a revision whose limit is lower than the number of
relations already established (as with `juju integrate`, Juju 3.6 shows the
reason only with `--debug`). Before refreshing, keep one principal and move
the others to their own `alloy-sub` application:

```bash
juju remove-relation alloy-sub:juju-info other:juju-info
juju remove-relation alloy-sub:machine-observability other:machine-observability
juju remove-relation alloy-sub:profiling-source other:profiling-source  # if related
juju deploy alloy-sub alloy-sub-other
juju relate alloy-sub-other:juju-info other
juju relate alloy-sub-other:machine-observability other:machine-observability
juju relate alloy-sub-other:profiling-source other:profiling-source  # if it was related
juju refresh alloy-sub
```

An old-revision `alloy-sub` unit leaves the Alloy package installed and
records no owner. After the old `alloy-sub` units have left the moved
principal's machines, each `alloy-sub-other` unit there reports
`unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy` and
stays blocked. Run `juju run alloy-sub-other/<n> adopt-alloy` on each of those
units, or purge the package; see "One Alloy per machine". Do not run it while
the old unit is still on the machine.

Relate `alloy-sub-other` to the same Loki, remote-write, and Pyroscope
applications as `alloy-sub` so the moved principal keeps forwarding telemetry
and alert rules.

## One Alloy per machine

Alloy is one machine-wide service with one config file, so exactly one unit
may manage it. The owning unit is recorded in
`/var/lib/juju-alloy/owner.json`. A unit that does not own Alloy installs
nothing, writes no config, and never starts or stops the service.

For several workloads on one machine, use one `alloy-vm` principal related to
every colocated workload over `machine-observability`, not one `alloy-sub` per
workload.

`alloy-vm` uses the same ownership marker from the revision that adds its own
`adopt-alloy` action; a unit of either charm then blocks, naming the owner, on
a machine the other one manages. An older `alloy-vm` revision records no
ownership, so `alloy-sub` cannot tell its Alloy from a manual install: refresh
that `alloy-vm` first. Never run `adopt-alloy` on a machine where an `alloy-vm`
unit runs.

| Status | Meaning | What to do |
| --- | --- | --- |
| `Alloy on this machine is managed by <unit>; remove this unit's principal relation` | Another unit already owns Alloy here. | Remove this unit's relation, or the other unit's. The remaining unit takes over within one `update-status` interval. |
| `unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy` | Alloy is installed but no unit owns it: a manual install, or an `alloy-vm` or `alloy-sub` unit on an older revision that has not been refreshed yet. | If an older `alloy-vm` or `alloy-sub` unit runs here, refresh it first; do not adopt. Otherwise adopt or uninstall it, below. |

### Adopting a manually installed Alloy

```bash
juju run alloy-sub/0 adopt-alloy
```

The unit takes ownership and keeps the installed version. Your existing
`/etc/alloy/config.alloy` is preserved at
`/etc/alloy/config.alloy.package-default`, and Alloy keeps running on that
config until the unit has its relations. On the first reconcile with relations
the charm writes its rendered config and restarts Alloy; from then on, anything
your old config collected that the charm does not render stops being
collected. The charm restores the preserved config whenever the unit has no
relations. Only an Alloy installed from the `alloy` deb package can be adopted.

To start clean instead, remove the package and let the unit install its own:

```bash
juju ssh alloy-sub/0 sudo apt-get purge alloy
```

### An owner that no longer exists

If the owning unit was removed without running its hooks, the marker still
names it. After checking with `juju status` that the unit is gone:

```bash
juju run alloy-sub/0 adopt-alloy force=true
```

Never force while the named unit exists: two managers overwrite each other.

### Upgrading

A unit that already runs Alloy claims ownership on its first hook after the refresh
and keeps its service running. Where two `alloy-sub` units already share a machine,
the first to upgrade keeps Alloy and the second blocks; remove the second one's
relation. A new unit placed on a machine that an older-revision unit left behind
reports `unmanaged Alloy found` and needs `adopt-alloy`. A unit from an older
revision that was stuck failing its install when refreshed is treated as newly
installed and may need `adopt-alloy`.

## Profiling

`alloy-sub` pulls pprof profiles from the principal and pushes them to Pyroscope.

| Relation | Interface | Notes |
| --- | --- | --- |
| `profiling-source` | `pprof_scrape` | optional, `scope: container`, `limit: 1` |
| `send-profiles` | `profiling` | optional, `limit: 1` |

```bash
juju relate alloy-sub:juju-info gotodo
juju relate application:profiling-source alloy-sub:profiling-source
juju relate alloy-sub:send-profiles pyroscope-vm:profiling
```

- `profiles_scrape_interval` (default `"15s"`) sets how often Alloy pulls each
  pprof profile from profiling-source targets. It takes a Go duration such as
  `10s` or `1m`. When a source enables a delta profile type (`process_cpu` or
  `fgprof`), the charm renders `delta_profiling_duration` as the interval minus
  1s, so the CPU capture fills the interval. In that case the interval must be
  longer than 2s; shorter values block the unit with `config invalid`.
- Targets must be loopback only: `host:port` or `[v6]:port`, port 1-65535.
- Supported profile types (`PROFILE_TYPES`): `process_cpu`, `memory`,
  `goroutine`, `mutex`, `block`, `fgprof`, `godeltaprof_memory`,
  `godeltaprof_mutex`, `godeltaprof_block`.
- A principal with only `profiling-source` (no `machine-observability`) is a
  valid pipeline; status reads "...; profiles only".
- Profiling sources without `send-profiles` render nothing and only log.
- Payload labels `service_name`, `instance` and `__*` are ignored.
  `instance` is the principal unit name, and Juju topology from `juju-info`
  wins over payload labels.

`send-profiles` expects Pyroscope's own `profiling` endpoint (for example
`pyroscope-vm:profiling`). `alloy-sub` pushes Pyroscope's native format to the
advertised `host:port`; the field is named `otlp_grpc`, but Pyroscope serves
native ingest on the same HTTP listener.

`opentelemetry-collector:receive-profiles` does not work. It is an OTLP-only
receiver, and the collector charm's otelcol v0.130.0 profile layout is rejected
by Pyroscope 1.21.0 with HTTP 400 `wrong wireType`.

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

## Configured systemd journal logs

Set `systemd-service` to collect a systemd unit's journal on the principal machine:

```bash
juju config alloy-sub systemd-service=my-app.service
```

The default is empty. The configured unit is collected alongside `systemd_units`
declared through `machine-observability`; exact duplicate names are collected
once. Collection works with a `juju-info` attachment even without a
`machine-observability` declaration. It can run alongside host metrics and file
logs. Configure `send-loki-logs` or `grafana-cloud-config` to forward logs to Loki.

Clear the option to remove the config-selected journal source. Relation-declared
journal sources, file logs and host metrics remain:

```bash
juju config alloy-sub systemd-service=''
```

## Configured file logs

Use `log-files` to collect additional files on the principal machine:

```bash
juju config alloy-sub log-files='/tmp/example.log;/var/log/my-app/*.log'
```

The option is a string of absolute paths or globs separated by semicolons.
Whitespace, empty entries and repeated paths are ignored. Its default is empty,
so it adds no file sources until configured. This follows the semicolon syntax
of the OpenTelemetry collector charm's `path_exclude` option; that charm has no
equivalent configurable include list.

Configured files are collected alongside file and systemd journal sources
declared through `machine-observability`. Exact paths already declared by the
principal keep their relation-provided attributes. Avoid overlapping globs if
you want to prevent the same file from appearing in multiple targets.

The existing `path_exclude` option applies to configured and relation-provided
file sources:

```bash
juju config alloy-sub path_exclude='/var/log/my-app/debug.log;/tmp/ignored.log'
```

Alloy must be able to read the files. A newly discovered file with no saved
position is read from the beginning, then followed as lines are appended.
Existing positions resume after a restart. This matches the OpenTelemetry
collector's initial-file behavior. `log-files` can collect files with only a
`juju-info` attachment, without a `machine-observability` relation. Configure
`send-loki-logs` or `grafana-cloud-config` to forward the collected logs.

Clear the option to stop collecting config-selected files. Relation-provided
file and journal sources remain:

```bash
juju config alloy-sub log-files=''
```

## Host metrics

Set `enable-host-metrics=true` to collect host-level metrics, labelled with the
principal's Juju topology so they attribute to the correct unit:

```bash
juju config alloy-sub enable-host-metrics=true
```

Alloy collects these itself, so nothing is installed on the machine and no port
is opened. Any node-exporter already on the host is left alone.

- The job scrapes every 60s regardless of `global_scrape_interval`; only
  `global_scrape_timeout` applies.
- This is a complete pipeline on its own. With it set, the charm renders config
  and reports `host metrics only` without the `machine-observability` relation.

It defaults to `false`

### Built-in host alarms

While host metrics are enabled, the leader publishes one Prometheus rule group
named `<model-uuid>-<principal application>-host-metrics` (for example
`da137c2d-...-polkadot-host-metrics`) on
`send-remote-write`, alongside any principal rules:

| Alert | Fires when | Severity |
|---|---|---|
| `HostDiskUsageWarning` | filesystem usage above 80% | `warning` |
| `HostDiskUsageCritical` | filesystem usage above 90% | `critical` |
| `HostMemoryUsageWarning` | memory usage above 80% | `warning` |
| `HostMemoryUsageCritical` | memory usage above 90% | `critical` |

- Usage must stay above the threshold for `5m` before an alert fires.
- Disk usage is `1 - avail / size` for every mounted filesystem except
  `tmpfs`, `devtmpfs`, `overlay`, `squashfs`, `iso9660`, and `ramfs`; one alert
  per unit and mountpoint. Memory usage is `1 - MemAvailable / MemTotal`.
- The rules match `job="integrations/unix"` (the label Alloy's built-in
  exporter stamps on host-metric series) plus the `juju_model_uuid` and
  `juju_application` of the principal the
  leader is attached to, so each alloy-sub application alerts only on the
  hosts it scrapes, two applications in one model never evaluate the same
  host twice, and a node-exporter endpoint the principal declares itself is
  not alerted on a second time. An alloy-sub application attached to several
  principals alarms only the leader's principal. Alerts carry the series'
  Juju topology labels; the rule adds only `severity`. The model UUID in the
  group name keeps it unique when several models feed one Mimir ruler
  namespace, which rejects duplicate group names. Deploy one alloy-sub
  application per principal application: two attached to the same principal
  would scrape each host twice and publish the same group name.
- Warning and critical both fire above 90%. Silence the warning with an
  Alertmanager inhibition rule if you only want one notification.
- Rules travel only on `send-remote-write`; Grafana Cloud forwarding via
  `grafana-cloud-config` carries metrics but not alert rules.
- Setting `enable-host-metrics=false` withdraws the group.

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


## Alert rule transport

Alert rules use the receiver's `alert_rules_encodings` advertisement to negotiate
Canonical-compatible LZMA/base64 encoding in the existing `alert_rules` field.
Legacy receivers continue to receive JSON. The publisher uses Canonical's public
`cosl.LZMABase64` codec when the receiver advertises LZMA support. Publishing keeps
an 8 MiB decoded budget and a 60 KiB relation-value ceiling; oversized output is
reported rather than truncated. Artifact validation and last-known-good handling
remain local to this charm.

Upgrade Loki/Mimir receivers and gateways before collectors when aggregates exceed
the JSON size limit. Encoded values remain below 60 KiB, with the collector's
existing stricter publication guard retained. Compression does not change source
topology, machine-observability schema/artifact encoding, or artifact-count limits.
