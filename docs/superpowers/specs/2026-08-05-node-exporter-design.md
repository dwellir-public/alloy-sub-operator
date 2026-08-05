# node-exporter Support Design

## Summary

Add an `enable-node-exporter` config option to `alloy-sub`. When enabled, the charm installs
(or re-enables) the Canonical `node-exporter` snap on the principal's machine, connects the
snap interfaces needed for full hardware and OS metrics, and renders an Alloy scrape job that
forwards those metrics with the principal's Juju topology labels. When disabled, the scrape job
is dropped and the charm restores whatever snap state it found before it first acted. When the
subordinate unit is torn down, a snap the charm installed is removed; anything pre-existing is
left as the charm found it.

Delivering this correctly requires first closing a latent gap: `alloy-sub` derives Juju
topology exclusively from the `juju-info` relation, which Juju never creates automatically.
That gap is in scope here because node-exporter metrics are worthless without topology labels.

## Goals

- Give operators a one-flag path to host-level metrics attributed to the correct Juju unit.
- Keep node-exporter management isolated from the existing Alloy APT workload path.
- Make the charm useful when only `machine-observability` is related, which is the deployment
  shape operators actually hit.
- Leave existing deployments byte-identical on refresh until the operator opts in.

## Non-Goals

- Do not manage node-exporter collector selection (`snap set node-exporter collectors=...`).
  Operators can set those directly on the snap. Revisit if a real need appears.
- Do not make the scrape port configurable. The snap listens on 9100.
- Do not migrate Alloy itself from APT to snap.
- Do not remove, disable, or otherwise alter a node-exporter snap that was already installed and
  running before the charm first acted on it.
- Do not add TLS or authentication to the local node-exporter scrape.

## Background

### `juju-info` is not created automatically

Every Juju application implicitly *provides* a `juju-info` endpoint, but the relation is only
created when an operator explicitly integrates it. Separately, a subordinate unit is co-located
onto the principal's machine by *any* container-scoped relation, not specifically by
`juju-info`.

`alloy-sub` declares `scope: container` on both `juju-info` and `machine-observability`. So:

```
juju integrate alloy-sub:machine-observability polkadot
```

produces a running `alloy-sub` unit with **no** `juju-info` relation.

### In that state the charm does nothing

`_configure()` calls `_principal_context()`, which reads only the `juju-info` relation. With no
`juju-info` it returns `None`, `_configure()` restores the package-default Alloy config and
parks the unit in `WaitingStatus`. No metrics, no logs.

### The topology is already available

`MachineObservabilityPayload.source_topology` (schema v2) carries `model`, `model_uuid`,
`application`, `unit`, and `charm_name` — exactly the fields `PrincipalContext` needs. The lib
parses and validates it, and the unit tests cover it, but **`src/charm.py` never reads it**.
The README's v2 compatibility claim currently means only "the payload validates", not "the
topology is used".

`PrincipalContext.address` is dead code — nothing consumes it. `juju-info` therefore
contributes nothing to the rendered config except labels, and `source_topology` supplies the
same labels.

## Design

### Section 0: topology resolution

`PrincipalContext` gains a constructor alongside `from_relation`:

```python
@classmethod
def from_source_topology(cls, topology: Any, *, model_name: str = "", model_uuid: str = "") -> "PrincipalContext"
```

It is duck-typed on `topology` (reading `.application`, `.unit`, `.model`, `.model_uuid`,
`.charm_name`), matching the existing `from_relation(relation: Any)` convention. This keeps
`src/principal_context.py` free of any dependency on the `machine_observability` lib.

`model` and `model_uuid` from the payload win when non-empty; the charm's own `model_name` /
`model_uuid` are the fallback.

`_principal_context()` becomes:

```python
def _principal_context(self) -> PrincipalContext | None:
    relation = self.model.get_relation("juju-info")
    if relation is not None and relation.units:
        return PrincipalContext.from_relation(relation, model_name=..., model_uuid=...)
    topology = self._observability_payload().source_topology
    if topology is not None:
        return PrincipalContext.from_source_topology(topology, model_name=..., model_uuid=...)
    return None
```

`juju-info` remains the source of truth when present, preserving documented behavior.
`source_topology` is a fallback, not an override.

The method stays zero-argument, re-reading the payload internally rather than accepting it as a
parameter. The extra databag read and pydantic parse per hook is negligible, and it preserves
the existing test seam where three tests in `tests/unit/test_charm.py` patch
`_principal_context=lambda: ...` with a zero-arg lambda.

### Section 1: `src/node_exporter.py`

A new module mirroring the shape of `src/alloy.py`: module-level functions over `subprocess`,
patched in tests as `charm.node_exporter.*`.

```python
SNAP_NAME = "node-exporter"
DEFAULT_PORT = 9100
REQUIRED_INTERFACES = ("hardware-observe", "mount-observe", "network-observe", "system-observe")

def is_installed() -> bool          # snap list node-exporter
def is_enabled() -> bool            # "disabled" absent from the snap list Notes column
def install() -> None               # snap install node-exporter
def enable() -> None                # snap enable node-exporter
def disable() -> None               # snap disable node-exporter
def remove() -> None                # snap remove --purge node-exporter
def connect_interfaces() -> None    # snap connect node-exporter:<iface> for each
def get_version() -> str | None
```

`connect_interfaces()` runs on fresh install *and* on enable-of-existing, so a pre-existing
install also gets its interfaces wired. `snap connect` on an already-connected interface is a
no-op. A single failed connect logs a warning and continues to the next interface — partial
metrics beat no metrics, and a missing interface on an older snap revision must not block the
charm.

The four interfaces are those the snap's own post-installation notes call out as requiring
manual connection.

### Section 2: config option

```yaml
enable-node-exporter:
  description: |
    Install and enable the node-exporter snap on the principal machine and scrape its
    metrics with the principal's Juju topology labels.

    When false, the scrape job is removed from the Alloy config and the charm restores
    whatever snap state it found before it first acted. A node-exporter that was already
    installed and running before this charm touched it is left alone.
  type: boolean
  default: false
```

Default `false` means existing deployments are unaffected on refresh, and every current unit
test stays green without modification.

### Section 3: reconcile

`_stored` gains `node_exporter_prior_state: str`, one of `""` (the charm has never acted on the
snap), `"absent"`, `"disabled"`, or `"enabled"`. It is written exactly once — the first time
reconcile runs with the config `true` — and records the snap state the charm found before it
changed anything. It is the sole authority for both the `false` path and teardown.

A single boolean would not be enough. If the snap was pre-existing but *disabled* and the charm
enabled it, flipping back to `false` should re-disable it, because the charm caused that state.
"Did we install it" cannot express that; "what did we find" can.

New `_reconcile_node_exporter() -> tuple[bool, str | None]` returns `(scrape_enabled, error)`:

| prior state | config `true` | config `false` |
|---|---|---|
| `""` — never touched | record prior state, then act per the row it lands on | **nothing** |
| `absent` — we installed it | `connect_interfaces()` | `disable()` |
| `disabled` — we enabled it | `connect_interfaces()` | `disable()` |
| `enabled` — already running | `connect_interfaces()` | **leave alone** |

Recording plus first action, for the `""` row: `absent` → `install()` then
`connect_interfaces()`; `disabled` → `enable()` then `connect_interfaces()`; `enabled` →
`connect_interfaces()` only.

The governing invariant: **the charm never leaves the snap in a state the operator did not ask
for, and never touches a snap it never enabled.**

This matters because the default is `false`. Under an unconditional "disable if installed"
rule, merely deploying `alloy-sub` onto a machine already running node-exporter for another
consumer would disable it before the operator touched any config. The `""` row makes a
default-config deployment a genuine no-op.

It is called at the **top of `_configure()`**, before any early return, so the `false` path runs
regardless of relation state.

On snap failure it returns the error message and `scrape_enabled=False`. `_configure()` then
skips only the node-exporter job, renders and applies the rest of the config normally, and sets
`BlockedStatus` at the end. A broken snap must not take down log forwarding.

### Section 4: scrape job

The charm builds one `MetricsScrapeJob` and appends it to the list returned by
`_active_metrics_scrape_jobs(...)`:

```python
BuilderMetricsScrapeJob(
    job_name="node-exporter",
    targets=[ScrapeTarget(address="localhost:9100", labels=topology_labels)],
    metrics_path="/metrics",
    scheme="http",
    scrape_interval=self._global_scrape_interval(),
    scrape_timeout=self._global_scrape_timeout(),
)
```

`topology_labels` is `principal_context.juju_labels(charm_name=payload.charm_name)` — the same
labels the principal's own jobs receive, so node metrics attach to the Juju unit.

`ConfigBuilder` is **unchanged**. It stays a generic renderer that knows nothing about
node-exporter. `_render_metrics_scrape` already handles `forward_to = []` when no remote-write
endpoint exists, and already sanitizes `node-exporter` to the component name `node_exporter`.

### Section 5: gating

`_configure()` after the reconcile call:

```python
principal_context = self._principal_context()
if principal_context is None:
    self._reset_config_for_missing_relations()
    self.unit.status = ops.WaitingStatus(self._status_message(
        "config waiting for juju-info relation or machine-observability source_topology"))
    return False

if not self._has_machine_observability_relation() and not self._node_exporter_enabled():
    self._reset_config_for_missing_relations()
    self.unit.status = ops.WaitingStatus(self._status_message(
        "config waiting for machine-observability relation"))
    return False
```

Note the asymmetry: gating uses `_node_exporter_enabled()` (the operator's intent), while
`scrape_enabled` (the reconcile outcome) decides only whether the job is rendered. If the
operator enabled node-exporter and the snap failed to install, the unit must report the snap
error as Blocked rather than falling back to "waiting for machine-observability relation",
which would hide the real fault.

Resulting behavior:

| juju-info | source_topology | machine-obs | enable-node-exporter | result |
|---|---|---|---|---|
| yes | – | yes | either | Active, unchanged from today |
| no | yes | yes | either | **Active** — the gap this spec closes |
| yes | – | no | true | Active, `config valid; node-exporter metrics only` |
| no | no | yes | true | Waiting — no topology available |
| yes | – | no | false | Waiting, unchanged from today |
| no | no | no | either | Waiting, unchanged from today |

When topology exists but `machine-observability` is absent and node-exporter is enabled, the
rendered config contains the remote-write block and the single node-exporter scrape job. The
payload is empty, so no log pipeline is rendered — `ConfigBuilder._has_log_sources()` already
handles that.

### Section 6: teardown

A new handler on `self.on.remove`:

```python
def _on_remove(self, _: ops.RemoveEvent) -> None:
    self._restore_node_exporter_prior_state()
```

Teardown restores the same recorded prior state, with one difference from the config `false`
path: an `absent` prior state means the charm installed the snap, and unit removal is the point
at which it is removed rather than merely disabled.

| prior state | teardown action |
|---|---|
| `""` — never touched | nothing |
| `absent` — we installed it | `snap remove --purge` |
| `disabled` — we enabled it | `disable()` |
| `enabled` — already running | leave alone |

Removing the `juju-info` relation (or the last container-scoped relation) destroys the
subordinate unit, so Juju fires `stop` then `remove` and this runs. A snap that was already on
the machine before the charm arrived survives; only the Alloy scrape config disappears with the
unit.

Failures are logged, not raised — a failing teardown must not wedge unit removal.

`_on_stop` is unchanged; it continues to stop only Alloy.

## Testing

### `tests/unit/test_node_exporter.py` (new)

- Each of `install` / `enable` / `disable` / `remove` / `connect_interfaces` builds the expected
  argv, against a mocked `subprocess`.
- `is_installed` and `is_enabled` parse real `snap list` output, including the `disabled` note.
- `connect_interfaces` attempts all four interfaces and continues past one failure.
- `get_version` parses real `snap list` output and returns `None` when the snap is absent.

### `tests/unit/test_charm.py` (additions)

- Enabled renders a `node-exporter` job carrying the principal topology labels.
- Enabled records `prior_state` once and does not overwrite it on a later reconcile.
- `juju-info` only, no `machine-observability`, enabled → Active.
- No `juju-info`, v2 payload with `source_topology` → Active with topology labels from the
  payload.
- No `juju-info`, v1 payload → Waiting.
- `juju-info` present and `source_topology` also present → `juju-info` wins.
- Snap install failure → Blocked, but the rest of the config is still written.

Prior-state matrix, one test per cell — these are the cells that protect a foreign snap:

- config `false`, `prior_state=""`, snap installed and running → **no snap command is issued at
  all**, and no node-exporter job is rendered. This is the default-deployment no-op.
- config `false`, `prior_state="absent"` → `disable()`, not `remove()`.
- config `false`, `prior_state="disabled"` → `disable()`.
- config `false`, `prior_state="enabled"` → no snap command.
- `remove`, `prior_state="absent"` → `snap remove --purge`.
- `remove`, `prior_state="disabled"` → `disable()`, not `remove()`.
- `remove`, `prior_state="enabled"` → no snap command.
- `remove`, `prior_state=""` → no snap command.

### `tests/unit/test_repo_baseline.py` (addition)

- `charmcraft.yaml` declares `enable-node-exporter:`.

### Integration

Existing integration tests are unaffected by the default. No new integration test in this
slice; the snap path needs a real machine and is verified manually per the README steps.

## Files Touched

| File | Change |
|---|---|
| `src/node_exporter.py` | new — snap workload management |
| `src/principal_context.py` | add `from_source_topology` |
| `src/charm.py` | topology fallback, reconcile, scrape job, gating, `_on_remove` |
| `charmcraft.yaml` | add `enable-node-exporter` |
| `README.md` | document the option and the topology fallback |
| `tests/unit/test_node_exporter.py` | new |
| `tests/unit/test_charm.py` | additions |
| `tests/unit/test_repo_baseline.py` | addition |

`src/config_builder.py` and `src/alloy.py` are not modified.

## Risks

- **Snap availability.** `snap install` needs network access to the store. Air-gapped machines
  will fail the reconcile and land in Blocked with the snap error. Acceptable and visible.
- **Prior-state loss.** `StoredState` lives in the unit's local database. If it were lost, the
  charm would read `""` and take no snap action on either the `false` path or teardown, leaving
  the snap installed and running. That is the safe failure direction: the charm errs toward
  leaving the machine alone rather than toward disabling or removing something.
- **Prior state is recorded once, not continuously.** If an operator manually disables the snap
  while the charm has it enabled, the charm re-enables it on the next reconcile. That is
  intended — config is the declared intent — but it means `snap disable` by hand is not a
  durable override. Setting `enable-node-exporter=false` is.
- **Port collision.** Something already bound to 9100 would make the snap fail to start; Alloy
  would then scrape a dead target and surface it as a scrape failure rather than a charm error.
  Not worth guarding in this slice.
