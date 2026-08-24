# Build, Test, and Deploy

This document covers local verification and an operator validation shape for
the v1/v2/v3 `machine_observability` consumer.

## Goals

- keep the existing `polkadot` attachment healthy with a v1 payload
- consume the v3 payload from `dwellir-observability-reference`
- render Alloy config successfully for both subordinate applications
- validate and forward bounded v3 Prometheus and Loki alert-rule artifacts

## Local Verification

Run the repo checks first:

```bash
cd /home/erik/dwellir-public/alloy-sub-operator
tox -e lint,static,unit
```

## Build

Build the charm artifact used for both subordinate applications:

```bash
cd /home/erik/dwellir-public/alloy-sub-operator
charmcraft pack
```

Expected artifact:

```bash
alloy-sub_ubuntu@24.04-amd64.charm
```

## Refresh In `alloy-sub-e2e-20260419`

Refresh both `alloy-sub` applications in place:

```bash
juju refresh -m alloy-sub-e2e-20260419 alloy-sub \
  --path /home/erik/dwellir-public/alloy-sub-operator/alloy-sub_ubuntu@24.04-amd64.charm

juju refresh -m alloy-sub-e2e-20260419 alloy-sub-reference \
  --path /home/erik/dwellir-public/alloy-sub-operator/alloy-sub_ubuntu@24.04-amd64.charm
```

## Validate Model Status

```bash
juju status -m alloy-sub-e2e-20260419 \
  polkadot dwellir-observability-reference alloy-sub alloy-sub-reference --relations
```

Expected:

- `alloy-sub` is `active`
- `alloy-sub-reference` is `active`
- `polkadot` remains `active`
- `dwellir-observability-reference` remains `active`

## Validate v1 Compatibility

Inspect the subordinate attached to `polkadot`:

```bash
juju ssh -m alloy-sub-e2e-20260419 alloy-sub/0 \
  'sudo sed -n "1,260p" /etc/alloy/config.alloy'
```

Expected rendered content:

- a `prometheus.scrape` block for `polkadot`
- a journald pipeline for the declared `polkadot` service
- outbound Loki and remote-write sinks still present

## Validate Reference v3 Compatibility

Inspect the reference subordinate relation payload:

```bash
juju show-unit -m alloy-sub-e2e-20260419 alloy-sub-reference/0
```

Expected:

- relation `machine-observability` contains `schema_version: 3`
- relation `machine-observability` contains `source_topology`
- non-empty rules have `source_topology.model_uuid` and
  `source_topology.application`

Inspect the rendered Alloy config for the reference attachment:

```bash
juju ssh -m alloy-sub-e2e-20260419 alloy-sub-reference/0 \
  'sudo sed -n "1,260p" /etc/alloy/config.alloy'
```

Expected rendered content:

- a `prometheus.scrape` block for `dwellir-observability-reference`
- `juju_application = "dwellir-observability-reference"`
- `juju_unit = "dwellir-observability-reference/0"`
- a `loki.source.journal` block matching
  `dwellir-observability-reference.service`

Validate the reference workload directly:

```bash
juju ssh -m alloy-sub-e2e-20260419 dwellir-observability-reference/0 \
  'curl -fsS http://localhost:9615/metrics | head'
```

Expected metrics include:

- `reference_demo_up`
- `reference_demo_requests_total`

## Validate v3 artifacts

After upgrading the reference charm and Alloy, inspect relation data for
`schema_version: 3` and both artifact types. Packaged `cos-tool` is an internal
PromQL/LogQL validator and does not run as a service.

```bash
juju relate dwellir-observability-reference:machine-observability alloy-sub-reference:machine-observability
```

For direct backends:

```bash
juju relate alloy-sub-reference:send-loki-logs loki-vm:loki_push_api
juju relate alloy-sub-reference:send-remote-write mimir-vm:receive-remote-write
```

For gateways:

```bash
juju relate alloy-sub-reference:send-loki-logs loki-loadbalancer-vm:loki_push_api
juju relate loki-loadbalancer-vm:loki-alert-rules loki-vm:loki_push_api
juju relate loki-loadbalancer-vm:ingress loki-vm:ingress
juju relate alloy-sub-reference:send-remote-write mimir-gateway-vm:receive-remote-write
juju relate mimir-gateway-vm:mimir-alert-rules mimir-vm:receive-remote-write
juju relate mimir-gateway-vm:backend mimir-vm:backend
```

Test add/update, valid omission, relation removal, bad checksum, and invalid
expression. Malformed, future-version, or structurally invalid outer data
retains the whole relation LKG. Within a valid v3 payload, only a bad artifact
retains its prior LKG; unrelated rules and telemetry continue. The payload may
be exactly `60 * 1024` bytes, rejects only larger values, and is never chunked.

Refresh the reference library first, then both Alloy variants, both gateways,
and Grafana VM last, waiting for convergence after each step.
