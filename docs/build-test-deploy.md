# Build, Test, and Deploy

This document captures the validated local workflow for the `alloy-sub` v2
`machine_observability` consumer update using the model
`alloy-sub-e2e-20260419`.

## Goals

- keep the existing `polkadot` attachment healthy with a v1 payload
- consume the v2 payload from `dwellir-observability-reference`
- render Alloy config successfully for both subordinate applications

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

## Validate v2 Compatibility

Inspect the reference subordinate relation payload:

```bash
juju show-unit -m alloy-sub-e2e-20260419 alloy-sub-reference/0
```

Expected:

- relation `machine-observability` contains `schema_version: 2`
- relation `machine-observability` contains `source_topology`

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
