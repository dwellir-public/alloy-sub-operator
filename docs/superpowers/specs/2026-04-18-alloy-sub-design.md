# Alloy-Sub Design

## Summary

`alloy-sub` will become a production-grade subordinate charm for machine workloads that need:

- metrics scraping from workload-local HTTP endpoints
- journald and systemd-unit log capture
- file-log capture using patterns close to `opentelemetry-collector`
- forwarding to Loki and Mimir-compatible backends

The first supported principal family is `polkadot` and similar Substrate-style node charms in
`/home/erik/dwellir-public/ops/juju/charms`.

The design intentionally shadows `/home/erik/dwellir-public/alloy-vm-operator` for Alloy-specific
implementation patterns and shadows
`/home/erik/dwellir-public/opentelemetry-collector-operator` for Juju topology labels, relation
semantics, and operator-facing tuning options where Alloy has a credible equivalent.

## Goals

- Provide a subordinate-based observability path for blockchain machine charms with minimal
  principal-charm changes.
- Keep principal observability declarations neutral so `opentelemetry-collector` can consume the
  same contract later if it gains journald/systemd support.
- Reuse `alloy-vm` implementation patterns as directly as possible.
- Preserve Canonical-style Juju labels on logs and metrics.
- Support both systemd journal capture and file-log capture in the first real design.
- Keep principal-charm work small, explicit, and repeatable across the Substrate-style family.

## Non-Goals

- Do not make `alloy-sub` a host-wide observability charm like `alloy-vm`.
- Do not require principals to embed Alloy logic.
- Do not depend on `COSAgentProvider` for the steady-state design.
- Do not make trace ingestion a first-slice requirement.
- Do not refactor the whole `ops/juju/charms` fleet in the first implementation cycle.

## Current State

### `alloy-vm`

`alloy-vm` already provides the most relevant workload behavior:

- relation-driven metrics scraping over `metrics-endpoint`
- remote-write forwarding over `send-remote-write`
- journald capture for named systemd units
- broader host journald capture using match expressions
- Loki forwarding over `send-loki-logs`
- clean Juju topology relabeling in the Alloy config builder

The implementation pattern in `alloy-vm` is the correct blueprint for:

- module boundaries
- Alloy config generation
- restart/reload behavior
- relation-driven metrics translation
- journald capture blocks

### `opentelemetry-collector`

`opentelemetry-collector` is the primary behavior reference for:

- subordinate-to-principal topology handling
- Juju label naming on logs and metrics
- metrics scrape job labeling
- operator-facing tuning options such as:
  - `path_exclude`
  - `global_scrape_interval`
  - `global_scrape_timeout`
  - `tls_insecure_skip_verify`
  - `queue_size`
  - `max_elapsed_time_min`

Its current subordinate model derives principal identity from the attached principal unit and
injects `juju_*` labels into metrics jobs and logs. `alloy-sub` should preserve that behavior.

### Existing `alloy-sub`

`/home/erik/dwellir-public/ops/juju/charms/alloy-sub` is an early prototype. It is not a useful
production baseline because it:

- relies on charm config rather than relation-driven workload declarations
- is Loki-URL driven instead of relation-driven
- lacks `alloy-vm`-level config builder and workload management
- does not implement a reusable principal contract

The correct approach is to treat the current repo as the target repository name and replace its
structure and behavior with a new design derived from `alloy-vm`.

## Options Considered

### Option A: Adapt Principals to the Current `opentelemetry-collector`

Use `opentelemetry-collector` subordinate directly for the first family, extending principals only
enough to expose metrics and logs in its current supported paths.

Pros:

- Reuses an existing Canonical subordinate.
- Already has a strong metrics and topology story.

Cons:

- Current deployed collector build lacks journald support.
- The current charm expects either `cos-agent` snap log slots or file-log receivers, not Alloy-style
  journald capture.
- Requires more subordinate-side change before it can satisfy the current blockchain charm fleet.

Conclusion:

- Not the least-change path for the first family.

### Option B: Extend `alloy-vm` Directly Into a Subordinate

Refactor `alloy-vm` itself into a subordinate or build `alloy-sub` as a close derivative of
`alloy-vm`.

Pros:

- Reuses the best available implementation for systemd journal capture.
- Lowest technical risk for the first slice.

Cons:

- `alloy-vm` is a principal charm and includes host-wide behavior that does not belong in a
  subordinate.
- Requires introducing a principal contract and subordinate-specific identity handling anyway.

Conclusion:

- Correct implementation blueprint, but not the correct deployment form.

### Option C: Build a Real `alloy-sub` With a Neutral Principal Contract

Create a production `alloy-sub` subordinate that:

- attaches via `juju-info`
- consumes a new neutral principal relation carrying observability declarations
- renders Alloy config using `alloy-vm` patterns
- labels data like `opentelemetry-collector`

Pros:

- Lowest total churn for Substrate-style principals.
- Best future migration path to `opentelemetry-collector`.
- Reuses existing, working Alloy journald logic.

Cons:

- Requires defining and versioning a new relation contract.
- Needs a real subordinate implementation rather than incremental tweaks to the prototype.

Conclusion:

- Recommended.

## Chosen Design

Implement `alloy-sub` as a subordinate charm with two relation roles:

- `juju-info` for subordinate attachment to a principal
- `machine-observability` as a new neutral principal-provided relation carrying workload
  observability declarations

The principal charm will publish workload intent over `machine-observability`. `alloy-sub` will use
that data, plus the attached principal unit identity from the subordinate relation, to render and
manage Alloy configuration.

The subordinate will forward:

- logs to `send-loki-logs`
- metrics to `send-remote-write`

The first provider implementation is `polkadot`, followed by other Substrate-style charms that
share the same systemd-and-metrics shape.

## Architecture

### Repository Structure

The target repository should evolve toward this structure:

```text
alloy-sub/
├── charmcraft.yaml
├── pyproject.toml
├── uv.lock
├── tox.ini
├── DEVELOPING.md
├── docs/
│   ├── charm-architecture.md
│   └── superpowers/specs/...
├── src/
│   ├── charm.py
│   ├── alloy.py
│   ├── config_builder.py
│   ├── observability_relation.py
│   ├── principal_context.py
│   └── types.py
├── tests/
│   ├── unit/
│   └── integration/
└── lib/
    ├── charms/loki_k8s/v1/loki_push_api.py
    ├── charms/prometheus_k8s/v0/prometheus_scrape.py
    └── charms/prometheus_k8s/v1/prometheus_remote_write.py
```

### Module Responsibilities

- `src/charm.py`
  Juju orchestration, relation events, status handling, config application sequencing.
- `src/alloy.py`
  Workload install, config validation, restart/reload, health checks, `/etc/default/alloy`
  handling.
- `src/config_builder.py`
  Alloy config rendering derived from `alloy-vm` patterns.
- `src/observability_relation.py`
  Neutral `machine-observability` relation schema, parsing, validation, and provider/requirer
  helpers.
- `src/principal_context.py`
  Extract principal unit identity from `juju-info` and build principal Juju topology for labels.
- `src/types.py`
  Typed value objects for metrics jobs, file log sources, journal sources, and extra labels.

## Principal Contract

### Relation Name

Use `machine-observability`.

The name is intentionally neutral so that `opentelemetry-collector` can consume the same contract
later without requiring principal changes.

### Provider Shape

The principal charm publishes application-level declarations and unit-level addressing data.

The contract should include these fields:

- `systemd_units`
  A list of unit names, for example `["snap.polkadot.polkadot.service"]`
- `journal_match_expressions`
  Optional additional journald match expressions
- `metrics_jobs`
  A list of Prometheus-style scrape jobs containing:
  - `job_name`
  - `metrics_path`
  - `scheme`
  - `static_configs`
  - optional `scrape_interval`
  - optional `scrape_timeout`
  - optional `tls_config`
- `log_files_include`
  Optional file globs or absolute paths to include
- `log_files_exclude`
  Optional file globs to exclude
- `log_attributes`
  Optional extra labels for file log sources
- `workload_labels`
  Optional blockchain-specific labels such as:
  - `chain_name`
  - `chain_family`
  - `client_name`
  - `node_role`

### Principal Unit Addressing

`alloy-sub` should not rely only on the contract for addressing. It should also read the principal
unit address from relation data in the same way `MetricsEndpointConsumer` and Canonical subordinate
integrations do.

This keeps the contract declarative while preserving the correct unit-local target address.

## Labeling Model

### Metrics Labels

Metrics must be labeled with principal Juju topology, not subordinate identity.

Required Juju labels:

- `juju_model`
- `juju_model_uuid`
- `juju_application`
- `juju_unit`
- `juju_charm` when available

Optional blockchain labels:

- `chain_name`
- `chain_family`
- `client_name`
- `node_role`
- `rpc_kind`
- `endpoint_scope`

This mirrors `opentelemetry-collector` behavior, where the attached principal unit identity is
applied to the generated scrape jobs.

### Log Labels

Journal logs should carry:

- `juju_model`
- `juju_model_uuid`
- `juju_application`
- `juju_unit`
- `juju_charm`
- `systemd_unit`
- `syslog_identifier`
- `level`
- `severity`
- `log_source=journal`

File logs should carry:

- `juju_model`
- `juju_model_uuid`
- `juju_application`
- `juju_unit`
- `juju_charm`
- `filename`
- `path`
- `log_source=file`

Optional blockchain labels should be appended to both sources.

### Principal Identity Handling

`alloy-sub` must derive workload identity from the attached principal unit visible on the subordinate
relation, matching the Canonical subordinate pattern. It must not label workload logs or metrics as
belonging to `alloy-sub`.

## Alloy Config Model

### Base Behavior

`alloy-sub` should render one Alloy configuration file at `/etc/alloy/config.alloy`, validate it
before apply, preserve the package default config, and use `reload` when safe and `restart` when
required, following `alloy-vm`.

### Metrics Pipeline

The metrics pipeline should:

- render one `prometheus.scrape` block per active metrics job
- apply principal Juju labels to every target
- forward to `prometheus.remote_write "metrics"` when `send-remote-write` is related
- continue to expose Alloy local metrics

The metrics translation layer should stay close to `alloy-vm`:

- principal publishes Prometheus-style jobs
- subordinate translates them into Alloy scrape blocks

### Journal Log Pipeline

The journal log pipeline should:

- render `loki.relabel "journal"` rules like `alloy-vm`
- render one `loki.source.journal` block per declared systemd unit
- optionally render additional journal match blocks from `journal_match_expressions`
- forward through a Juju-topology labeling processor before Loki write

### File Log Pipeline

The file log pipeline should preserve the user-facing semantics of `opentelemetry-collector` file
capture while mapping them onto Alloy config.

For the first implementation:

- support `log_files_include`
- support `log_files_exclude`
- add `filename` and `path` labels
- keep Juju topology labels aligned with the journal path

The operator-facing config should also preserve the otelcol-style host file exclusion knob via
`path_exclude`.

## Config Surface

### Keep From `alloy-vm`

- `custom_args`
- `alloy-livedebugging`

### Add or Adapt From `opentelemetry-collector`

- `path_exclude`
  Exclusions for broad file-log capture patterns.
- `global_scrape_interval`
  Default interval applied to metrics jobs without an explicit interval.
- `global_scrape_timeout`
  Default timeout applied to metrics jobs without an explicit timeout.
- `tls_insecure_skip_verify`
  Applied to scrape and remote write / Loki transport where relevant.
- `queue_size`
  Map to Alloy queue or WAL buffering settings where a reasonable equivalent exists.
- `max_elapsed_time_min`
  Map to retry or backoff configuration where Alloy supports a close equivalent.

### Do Not Copy Literally

- `processors`
  Otelcol processor YAML is not a drop-in Alloy feature. If an extension point is added, it should
  use Alloy-native terminology and be documented as equivalent intent, not identical syntax.
- otelcol debug exporters
  If debug behavior is added, it should be Alloy-native and clearly documented as such.

## Relation Set for `alloy-sub`

### Required

- `juju-info`
  subordinate attachment to the principal
- `machine-observability`
  neutral workload declaration contract from the principal

### Optional Outgoing

- `send-loki-logs`
  Loki forwarding over `loki_push_api`
- `send-remote-write`
  metrics forwarding over `prometheus_remote_write`

### Not in the First Slice

- trace ingestion / forwarding
- syslog receiver relation support
- dashboard forwarding
- alert rule forwarding

These can be added later if the neutral contract needs to carry more workload intent.

## First Principal: `polkadot`

### Principal Changes

`polkadot` should become the first `machine-observability` provider and publish:

- `systemd_units = ["snap.polkadot.polkadot.service"]`
- `metrics_jobs` for `localhost:9615 /metrics`
- `workload_labels` including:
  - `chain_name`
  - `chain_family=substrate`
  - `client_name=polkadot`

### Scope of Principal Work

The first principal implementation should be intentionally small:

- add relation metadata
- add provider helper code
- publish observability declarations on config/start/update events
- keep legacy observability relations during migration if needed

## Migration Strategy

### Phase 1

- Replace the current `alloy-sub` prototype with the new subordinate architecture.
- Implement the neutral relation.
- Implement `polkadot` as the first provider.
- Validate log and metrics forwarding in a dedicated Juju model.

### Phase 2

- Migrate additional Substrate-style charms that share the same shape.
- Document the provider pattern for other principal charms.

### Phase 3

- Decide whether Ethereum execution / consensus families should adopt the same relation without
  schema changes.
- Reuse the relation contract from `alloy-sub` for `opentelemetry-collector` if journald/systemd
  support becomes available there.

## Testing Strategy

### Unit Tests

Add focused unit coverage for:

- principal-context extraction from subordinate relations
- observability relation parsing and validation
- metrics job translation into Alloy scrape blocks
- journal source rendering
- file log rendering with include and exclude patterns
- Juju label rendering for logs and metrics
- config-change / relation-change orchestration

### Integration Tests

Add integration scenarios that deploy:

- `alloy-sub`
- a test principal that publishes `machine-observability`
- Loki-compatible backend
- Mimir-compatible backend

First integration scenarios:

- principal metrics visible in Mimir with principal Juju labels
- principal journald logs visible in Loki with principal Juju labels
- file log capture works with include and exclude rules
- `path_exclude` blocks noisy host logs
- relation-broken behavior removes stale config

## Risks

### Contract Drift

If the neutral relation is underspecified, later principal families will add special cases. The
schema must be explicit and versioned from the start.

### Too Much `alloy-vm` Copy-Paste

The design should reuse `alloy-vm` patterns, but not carry over host-wide behavior irrelevant to a
subordinate.

### False Config Parity With `opentelemetry-collector`

Some otelcol knobs do not have exact Alloy equivalents. The charm must document which options are:

- exact semantic matches
- close equivalents
- intentionally omitted

### Principal Label Ambiguity

The subordinate must consistently prefer principal identity over subordinate identity in generated
labels and status messages. Tests must lock this down.

## Decision Summary

- Build `alloy-sub` as a real subordinate, not as an extension of the current prototype behavior.
- Reuse `alloy-vm` implementation patterns wherever the subordinate shape allows it.
- Keep principal observability declarations neutral through a new `machine-observability` relation.
- Keep metrics and log labels aligned with `opentelemetry-collector`.
- Support both journald/systemd-unit capture and file-log capture in the first slice.
- Preserve familiar tuning knobs from `opentelemetry-collector` where Alloy has a credible
  equivalent.
- Start with `polkadot` and other Substrate-style principals before generalizing further.
