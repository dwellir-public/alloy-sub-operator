# Direct metrics and textfile compatibility

Scope: requested compatibility for internal BCM telemetry, based on origin/main
`70fb1dd` on 2026-11-25. No workload upgrade, deployment or release is included.

| Outcome and authority | Before | Disposition and target | Actual evidence |
| --- | --- | --- | --- |
| Requested `prometheus_scrape` compatibility (task-required) | GAP: only machine-observability supplied workload scrapes | Add optional `metrics-endpoint`; preserve supported job fields | PASS: related-metrics unit tests and real remote-write fixture |
| Correct source ownership (OBS-001 required-baseline, capability scope) | Existing machine-observability derives principal identity | Preserve identity and select only the attached principal unit from direct jobs | PASS: two-unit relation selects iris/0 only; wrong app/model excluded |
| No duplicate scrapes (task-required) | No direct-source coexistence | Prefer existing machine-observability sources on identical address/path/scheme | PASS: identical sources and duplicate direct jobs produce one scrape |
| Existing interfaces and defaults (contract-required) | Existing host, profiling and source paths | Preserve existing defaults; new endpoint and textfile option are opt-in | PASS: 564 unit tests |
| Existing BCM `.prom` output (task-required) | GAP: embedded exporter has no textfile option | Optional absolute directory with host metrics enabled; do not mutate files or expose a port | PASS: path/enable validation and real textfile remote-write fixture |
| Live source-to-backend identity (OBS-001 required-baseline) | UNKNOWN for new direct path | Defer until reviewed deployment | Not deployed |

This is a bounded compatibility change, not a full compliance audit. Other charm
capabilities and unrelated baseline gaps were not assessed. No user decision is
needed for this scope.

The direct consumer is the unchanged Canonical `prometheus_scrape` library
API 0 patch 56, pinned in `dependencies/vendored.json`. The shared
machine-observability contract and vendored implementation are unchanged.

One subordinate application remains restricted to one principal application.
Global scrape relations can expose every principal unit; each subordinate keeps
only its attached unit. A fixed target without a unit label must have the local
principal address or a loopback host. Non-local app-wide targets belong on
`alloy-vm`. Unsupported auth, parameter, limit or metric-relabel job fields are
rejected rather than silently omitted. Existing config remains intact on a
failed reconcile.

Prefer one source contract for each endpoint. Where both are related,
machine-observability wins for an identical address/path/scheme. If a principal's
metrics-endpoint already publishes a node exporter, leave enable-host-metrics
false to avoid two collectors of the same host. BCM can enable host metrics and
set textfile-directory to `/var/lib/internal-bcm/textfile`; these are existing
collector outputs, not a network exposure of BCM admin or pprof.

## Verification

- `PYTHONPATH=lib:src uv run pytest tests/unit -q --disable-warnings`: 564 passed.
- `PYTHONPATH=lib:src uv run pyright src`: zero errors.
- Ruff lint/format, targeted codespell, `uv lock --check`, dependency provenance
  and `git diff --check`: passed.
- Ubuntu 24.04 amd64 `charmcraft pack`: passed. Other supported bases were not built.
- Checksum-verified official Alloy v1.22.0, full `alloy validate`, followed by a
  real local collector and disposable Prometheus v3.7.3 receiver: direct workload
  and textfile series both arrived once with correct principal Juju labels;
  target relabeling set the workload instance to its principal unit. The runtime
  fixture accelerates host collection from 60 seconds to one second and cleans
  up its process, HTTP fixture and disposable container.
- Actual Juju relation hooks and live central Mimir writes remain unverified for
  this new artifact. No existing production service, Grafana resource or database
  was modified. No release was published.

`BUILD-001` packaging requirements are preserved: existing explicit runtime/test
requirements, tracked lock, uv plugin and consolidated charm definitions remain.
For `DEPLOY-001`, a built artifact exists and the local runtime fixture passes;
actual built-artifact Juju deployment is deferred until PR review and is not
claimed complete. The new integration fixture uses Docker and an explicit
`ALLOY_BINARY`; it does not select any Juju model.
