# node-exporter Support Design

## Summary

Add an `enable-node-exporter` config option to `alloy-sub`. When enabled, the charm installs
(or re-enables) the Canonical `node-exporter` snap on the principal's machine, connects the
snap interfaces needed for full hardware and OS metrics, and renders an Alloy scrape job that
forwards those metrics with the principal's Juju topology labels. When disabled, the scrape job
is dropped and the snap is disabled — but only if the operator had previously enabled it,
because until then the charm has no mandate to touch the machine at all. When the subordinate
unit is torn down, the charm restores the exact snap state it originally found.

Delivering this correctly requires first closing a latent gap: `alloy-sub` derives Juju
topology exclusively from the `juju-info` relation, which Juju never creates automatically.
That gap is in scope here because node-exporter metrics are worthless without topology labels.

## Goals

- Give operators a one-flag path to host-level metrics attributed to the correct Juju unit.
- Keep node-exporter management isolated from the existing Alloy APT workload path, and
  self-contained in one module so `charm.py` gains glue rather than logic.
- Make the charm useful when only `machine-observability` is related, which is the deployment
  shape operators actually hit.
- Leave existing deployments byte-identical on refresh until the operator opts in.

## Non-Goals

- Do not manage node-exporter collector selection (`snap set node-exporter collectors=...`).
  Operators can set those directly on the snap. Revisit if a real need appears.
- Do not make the scrape port configurable. The snap listens on 9100.
- Do not migrate Alloy itself from APT to snap.
- Do not issue any snap command until the operator has set `enable-node-exporter=true` at least
  once. A charm that has never been opted in leaves the machine untouched.
- Do not leave the machine altered after unit removal.
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

**All node-exporter knowledge lives in this one module.** The charm holds no snap names, no
ports, no interface lists, and no state-machine branches. `src/charm.py` gains roughly twenty
lines of glue and nothing else.

The module is internally split into three layers, following the repo's existing convention of
small focused modules of near-pure functions (`custom_args.py`, `outbound_endpoints.py`,
`grafanacloud_connectivity.py`) with `charm.py` as the orchestrator.

#### Layer 1 — constants and effects

Module-level functions over `subprocess`, mirroring the shape of `src/alloy.py`:

```python
SNAP_NAME = "node-exporter"
DEFAULT_PORT = 9100
JOB_NAME = "node-exporter"
METRICS_PATH = "/metrics"
REQUIRED_INTERFACES = ("hardware-observe", "mount-observe", "network-observe", "system-observe")

def observe() -> SnapState          # one `snap list node-exporter` call
def install() -> None               # snap install node-exporter
def enable() -> None                # snap enable node-exporter
def disable() -> None               # snap disable node-exporter
def remove() -> None                # snap remove --purge node-exporter
def connect_interfaces() -> None    # snap connect node-exporter:<iface> for each
```

`connect_interfaces()` runs on fresh install *and* on enable-of-existing, so a pre-existing
install also gets its interfaces wired, plus once on first opt-in when the snap is already
running. It does **not** run on every subsequent reconcile: `snap connect` on an already-
connected interface is a no-op, but four no-op subprocess calls on every `update-status` for the
life of the unit are pure noise. A single failed connect logs a warning and continues to the
next interface — partial metrics beat no metrics, and a missing interface on an older snap
revision must not block the charm.

#### Layer 2 — the decision, as pure functions

```python
@dataclass(frozen=True)
class SnapState:
    installed: bool
    enabled: bool
    known: bool = True   # False when snapd could not be read at all

@dataclass(frozen=True)
class Plan:
    actions: tuple[str, ...]   # ordered subset of "install"|"enable"|"disable"|"remove"|"connect"
    prior_state: str           # value to persist back into StoredState
    scrape_enabled: bool

def plan_reconcile(*, enabled: bool, prior_state: str, observe: Callable[[], SnapState]) -> Plan
def plan_teardown(*, prior_state: str, observe: Callable[[], SnapState]) -> Plan
def apply(plan: Plan) -> None      # executes plan.actions in order
```

`plan_reconcile` takes `observe` as a **callable, not a value**, and invokes it only when it
needs it. This is what makes rule 1 literal rather than approximate: when `prior_state == ""`
and `enabled` is `False`, the planner returns an empty plan without ever calling `observe`, so
the charm issues no `snap` invocation of any kind — not even a read-only `snap list`. The test
for that cell passes a spy and asserts it was never called.

Separating the decision from the effects is the point of the layer split. The entire
`prior_state` matrix becomes pure-function tests over a frozen dataclass — no subprocess mocks,
no `ops.testing` harness, no charm instantiation. Given that this state machine is where the
real complexity sits, it should be the cheapest thing in the codebase to test exhaustively.

#### Layer 3 — the scrape job

```python
def scrape_job(*, topology_labels: dict[str, str], scrape_interval: str, scrape_timeout: str) -> MetricsScrapeJob
```

Returns the fully formed `MetricsScrapeJob` targeting `localhost:9100`. `JOB_NAME`,
`METRICS_PATH`, `DEFAULT_PORT`, and the `http` scheme stay here rather than being spelled out at
the call site in `charm.py`.

This makes `node_exporter.py` import `MetricsScrapeJob` and `ScrapeTarget` from
`config_builder.py`. That dependency is one-directional and points at the lower-level module —
`config_builder.py` remains entirely unaware of node-exporter and is not modified.

The four interfaces are those the snap's own post-installation notes call out as requiring
manual connection.

### Section 2: config option

```yaml
enable-node-exporter:
  description: |
    Install and enable the node-exporter snap on the principal machine and scrape its
    metrics with the principal's Juju topology labels.

    When false, the scrape job is removed from the Alloy config and the snap is disabled,
    provided this charm had previously enabled it. A node-exporter this charm has never
    enabled is left untouched, so deploying with the default changes nothing.

    On unit removal the charm restores the snap state it originally found.
  type: boolean
  default: false
```

Default `false` means existing deployments are unaffected on refresh, and every current unit
test stays green without modification.

### Section 3: reconcile

`_stored` gains `node_exporter_prior_state: str`, one of `""` (the charm has never acted on the
snap), `"absent"`, `"disabled"`, or `"enabled"`. It is written exactly once — the first time
reconcile runs with the config `true` — and records the snap state the charm found before it
changed anything.

Two rules govern everything, and it is worth stating them separately because they answer
different questions:

1. **Opt-in gate.** Until the operator has set `enable-node-exporter=true` at least once, the
   charm issues no snap command whatsoever. `prior_state == ""` means no consent has been
   given.
2. **Config governs after opt-in.** Once `true` has been set, the operator has handed the snap
   to the charm. From then on `true` means enabled and `false` means disabled, regardless of
   what was on the machine first.

`prior_state` is not an ownership flag; it is a restore point. It records what to put back at
teardown, and its emptiness is what implements rule 1.

The matrix below is implemented by `node_exporter.plan_reconcile`, not by branches in
`charm.py`. The charm's entire share of it:

```python
def _reconcile_node_exporter(self) -> tuple[bool, str | None]:
    plan = node_exporter.plan_reconcile(
        enabled=self._node_exporter_enabled(),
        prior_state=self._stored.node_exporter_prior_state,
        observe=node_exporter.observe,
    )
    self._stored.node_exporter_prior_state = plan.prior_state
    try:
        node_exporter.apply(plan)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return plan.scrape_enabled, None
```

`prior_state` is persisted before `apply` runs. If a snap command fails partway, the recorded
restore point still reflects what was on the machine, so teardown does the right thing rather
than reading `""` and walking away.

**Every action is derived from observed reality, never from `prior_state` alone.** `snap enable`
and `snap disable` are not idempotent — both exit non-zero when the snap is already in the
requested state — so emitting an action the machine does not need is an error, not a harmless
no-op. `prior_state` answers only two questions: has consent been given (rule 1), and what must
be restored at teardown. It never answers "what is on the machine right now"; `observe()` does.

`observe()` is therefore three-valued. Only a `CalledProcessError` whose combined stdout+stderr
contains snapd's `no matching snaps installed` means the snap is genuinely absent. Every other
failure — any other `CalledProcessError` (`cannot communicate with server` while snapd is still
seeding, for instance), `FileNotFoundError`, `TimeoutExpired` — yields `known=False`: the state
is *unknown*, which is not the same as absent and must never be acted on as if it were.

The resulting behaviour, returned as `(scrape_enabled, error)`:

| prior state | observed | config `true` | config `false` |
|---|---|---|---|
| `""` — never opted in | *not read* | — | **nothing**, `observe` is never called |
| `""` — never opted in | unknown | record nothing, no action, no scrape job; retry next hook | — |
| `""` — never opted in | absent | record `absent`, `install` + `connect` | — |
| `""` — never opted in | installed, disabled | record `disabled`, `enable` + `connect` | — |
| `""` — never opted in | installed, enabled | record `enabled`, `connect` | — |
| any of `absent` / `disabled` / `enabled` | unknown | nothing, no scrape job; retry next hook | nothing; retry next hook |
| any of `absent` / `disabled` / `enabled` | absent | `install` + `connect` | nothing |
| any of `absent` / `disabled` / `enabled` | installed, disabled | `enable` + `connect` | nothing |
| any of `absent` / `disabled` / `enabled` | installed, enabled | nothing | `disable` |

On the `true` path `prior_state` is recorded on first opt-in and never rewritten afterwards; on
the `false` path it is carried through untouched. The three non-empty values behave identically
for config purposes and diverge only at teardown, which is the sole reason the distinction is
stored at all.

Three consequences worth naming, because each is a bug the earlier intent-driven matrix had:

- **`false` on an already-disabled or absent snap issues nothing.** Re-running `snap disable`
  against a disabled snap exits non-zero, which under the old matrix parked the unit in Blocked
  on every `update-status` forever.
- **`true` re-converges.** `true` → `false` → `true` ends with the snap *enabled*: the third
  step observes a disabled snap and emits `("enable", "connect")`. A failed install is likewise
  retried on the next hook rather than being assumed done.
- **`connect` is not re-run forever.** Interfaces are wired on first opt-in and whenever the
  charm installs or enables the snap. `snap connect` on an already-connected interface is a
  no-op, but running four of them on every `update-status` for the life of the unit buys
  nothing, so a steady-state reconcile of an installed-and-enabled snap emits `()`.

An unknown observation is always the do-nothing branch, and on first opt-in it records
**nothing**: `prior_state` stays `""` and the restore point is settled by the next readable
observation. A transient snapd failure must not be able to convert a foreign, pre-existing snap
into something the charm believes it installed — and, equally, must not permanently label a snap
the charm is about to install as one it found already running, which would leave node-exporter
on the machine after unit removal. Because no action is taken on the unknown branch there is
nothing to restore, so leaving `prior_state` unset loses no information; the `false` path
short-circuits on `""` without observing for exactly the same reason. The invariant that
motivated recording a value here survives: `snap remove --purge` is still reachable only from a
positively confirmed absence.

Rule 1 matters because the default is `false`. Under an unconditional "disable if installed"
rule, merely deploying `alloy-sub` onto a machine already running node-exporter for another
consumer would disable it before the operator touched any config. The `""` row makes a
default-config deployment a genuine no-op, and it is literal: with `prior_state == ""` and
`enabled=False` the planner returns before calling `observe`, so not even a read-only
`snap list` is issued.

Rule 2 matters because the alternative — the charm only ever undoing its own changes — makes
`enable-node-exporter=false` a permanent no-op on any machine where node-exporter happened to
be running first. The operator would have a config option reading `false` next to a running
node-exporter and no charm-level way to stop it. Setting `true` is the consent; after that the
option must work in both directions.

It is called at the **top of `_configure()`**, before any early return, so the `false` path runs
regardless of relation state.

On snap failure it returns the error message and `scrape_enabled=False`. `_configure()` then
skips only the node-exporter job, renders and applies the rest of the config normally, and sets
`BlockedStatus` at the end. A broken snap must not take down log forwarding. Because the
reconcile runs before the topology gate, the error is also carried into that early return: a
snap failure with no topology available reports the snap error as Blocked rather than a
`WaitingStatus` that hides it.

### Section 4: scrape job

When `scrape_enabled` is true, `_active_metrics_scrape_jobs(...)` appends one job:

```python
node_exporter.scrape_job(
    topology_labels=topology_labels,
    scrape_interval=self._global_scrape_interval(),
    scrape_timeout=self._global_scrape_timeout(),
)
```

That is the whole change at the call site — the port, job name, metrics path and scheme stay
inside `node_exporter.py`.

`topology_labels` is `principal_context.juju_labels(charm_name=payload.charm_name or None)` — the same
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
    if node_exporter_error is not None:
        self.unit.status = ops.BlockedStatus(self._status_message(
            f"node-exporter: {node_exporter_error}"))
    else:
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
    try:
        node_exporter.apply(
            node_exporter.plan_teardown(
                prior_state=self._stored.node_exporter_prior_state,
                observe=node_exporter.observe,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("node-exporter teardown failed: %s", exc)
```

Teardown is a full restore: put the machine back exactly as the charm found it, so removing the
unit leaves no trace. The mapping lives in `plan_teardown`; this is where the three non-empty
`prior_state` values finally differ.

| prior state | teardown action |
|---|---|
| `""` — never opted in | nothing |
| `absent` — we installed it | `snap remove --purge` |
| `disabled` — we enabled it | `disable()` |
| `enabled` — already running | `enable()` |

The `enabled` row calls `enable()` rather than doing nothing, because the charm may currently
have the snap disabled via `enable-node-exporter=false` under rule 2. Doing nothing there would
leave a pre-existing service switched off after the charm that switched it off is gone.

`enable` on an already-enabled snap is **not** a no-op — it exits non-zero, exactly like every
other snap state command — so `plan_teardown` takes an `observe` callable and emits an action
only when the machine still needs it: `remove` only if the snap is installed, `disable` only if
it is installed and enabled, `enable` only if it is installed and disabled. Without that, the
common removal (snap still enabled) would log `node-exporter teardown failed` when nothing
failed. Two cases skip the observation: `prior_state == ""` returns immediately under rule 1
without issuing even a read-only `snap list`, and an unknown observation emits the mapped action
anyway — teardown is the last hook this unit will ever run, so best effort beats waiting.

Removing the `juju-info` relation (or the last container-scoped relation) destroys the
subordinate unit, so Juju fires `stop` then `remove` and this runs. A snap that was already on
the machine before the charm arrived survives; only the Alloy scrape config disappears with the
unit.

Failures are logged, not raised — a failing teardown must not wedge unit removal.

`_on_stop` is unchanged; it continues to stop only Alloy.

## Testing

The layer split means the state machine is tested without mocks, and the charm tests shrink to
wiring checks.

### `tests/unit/test_node_exporter.py` (new) — layer 2, pure

The full `prior_state` matrix as pure `plan_reconcile` / `plan_teardown` assertions over
`Plan.actions`. No subprocess, no `ops.testing`, no charm instance.

Rule 1, the opt-in gate:

- `enabled=False`, `prior_state=""` → empty plan, `prior_state` stays `""`, and the `observe`
  spy is **never called** — not even a read-only `snap list`. The default-deployment no-op.
- `plan_teardown(prior_state="")` → empty plan.

Rule 2, config governs after opt-in — all three record-and-act paths, then the `false` column:

- `enabled=True`, `prior_state=""`, observed absent → `("install", "connect")`, records `absent`.
- `enabled=True`, `prior_state=""`, observed installed+disabled → `("enable", "connect")`,
  records `disabled`.
- `enabled=True`, `prior_state=""`, observed installed+enabled → `("connect",)`, records
  `enabled`.
- `enabled=True` with any non-empty `prior_state`, observed absent → `("install", "connect")`;
  observed installed+disabled → `("enable", "connect")`; observed installed+enabled → `()`.
  `prior_state` unchanged in every case.
- `enabled=False` with `prior_state` in `absent` / `disabled` / `enabled`, observed
  installed+enabled → `("disable",)` in all three cases, never `("remove",)`; observed
  installed+disabled or absent → `()`.

Unknown machine state — the branch that must never guess:

- `observe()` returns `known=False` on first opt-in → no actions, `prior_state` stays `""`,
  `scrape_enabled is False`.
- The following readable observation settles the restore point: absent → records `absent` and
  teardown from there emits `("remove",)`; installed+enabled → records `enabled` and teardown
  never emits `("remove",)`.
- `known=False` after opt-in, on either the `true` or the `false` path → no actions,
  `prior_state` unchanged.

Teardown, full restore — the only place the three values diverge, and each action is emitted
only when the observed machine still needs it:

- `absent`, observed installed → `("remove",)`; observed absent → `()`
- `disabled`, observed installed+enabled → `("disable",)`; observed installed+disabled → `()`
- `enabled`, observed installed+disabled → `("enable",)`, never `("remove",)`; observed
  installed+enabled or absent → `()`
- `prior_state=""` → `()` and the `observe` spy is never called
- `known=False` → the mapped action is emitted anyway, best effort, there is no next hook

Sequence tests, threading `prior_state` (and, where it matters, the machine's actual state)
through each step:

- Pre-installed and running, then `false` → `true` → `false` → teardown. Asserted action tuples:
  `()`, `("connect",)`, `("disable",)`, `("enable",)`. The machine ends where it started.
- `true` → `false` → `true` against a machine whose state actually changes: `("connect",)`,
  `("disable",)`, `("enable", "connect")`. The snap ends **enabled**, not left disabled.
- Two consecutive reconciles with unchanged config emit the action once and then `()` — both for
  `false` after a successful disable and for `true` on an installed+enabled snap.

`scrape_job()` returns `localhost:9100`, job name `node-exporter`, path `/metrics`, and the
topology labels it was handed.

### `tests/unit/test_node_exporter.py` — layer 1, mocked subprocess

- Each of `install` / `enable` / `disable` / `remove` / `connect_interfaces` builds the expected
  argv.
- `observe()` parses real `snap list` output into `SnapState`, including the `disabled` note, the
  genuinely-not-installed case (snapd's `no matching snaps installed`), and the three unknown
  cases — any other non-zero exit, a missing `snap` binary, and a timeout — which yield
  `known=False`.
- A failed snap command's exception message carries snapd's own stderr, not just the exit status.
- `connect_interfaces` attempts all four interfaces and continues past one failure.
- `apply()` dispatches each action name to the matching effect, in order.

### `tests/unit/test_charm.py` (additions) — wiring only

- Enabled renders a `node-exporter` job carrying the principal topology labels.
- `_reconcile_node_exporter` persists `plan.prior_state` into `StoredState`, and persists it
  even when `apply` raises.
- Snap failure → Blocked, but the rest of the config is still written.
- Snap failure with no topology available → Blocked on the snap error, not Waiting.
- Enabled, then set back to `false` → the `node-exporter` job is dropped from
  `metrics_scrape_jobs` and the unit is Active, not Blocked.
- `on.remove` calls `apply(plan_teardown(...))` with the stored `prior_state` and the real
  `observe`, so a snap that is still enabled is left alone instead of failing an `enable`.
- `juju-info` only, no `machine-observability`, enabled → Active.
- `juju-info` only, no `machine-observability`, enabled, snapd unreadable → Active with
  `node-exporter pending snap state`, and no `node-exporter` job in `metrics_scrape_jobs`:
  the status must not advertise metrics that are not being scraped.
- No `juju-info`, v2 payload with `source_topology` → Active with topology labels from the
  payload.
- No `juju-info`, v1 payload → Waiting.
- `juju-info` present and `source_topology` also present → `juju-info` wins.

### `tests/unit/test_repo_baseline.py` (addition)

- `charmcraft.yaml` declares `enable-node-exporter:`.

### Integration

Existing integration tests are unaffected by the default. No new integration test in this
slice; the snap path needs a real machine and is verified manually per the README steps.

## Files Touched

| File | Change |
|---|---|
| `src/node_exporter.py` | new — snap effects, `prior_state` state machine, scrape job |
| `src/principal_context.py` | add `from_source_topology` |
| `src/charm.py` | topology fallback, gating, and ~20 lines of node-exporter glue |
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
- **Opt-in is irreversible within a unit's lifetime.** Once `true` has been set, `prior_state`
  is recorded and the charm will act on the snap for the rest of the unit's life, including
  disabling a node-exporter that was serving another consumer. This is deliberate under rule 2,
  but it means `true` → `false` is not the same as never having enabled it. An operator who
  wants the charm to stop managing an existing snap entirely must remove the unit, which
  triggers the full restore.
- **Prior state is recorded once; machine state is read every time.** `prior_state` is written
  on first opt-in and never rewritten, but every reconcile re-reads the machine and acts on what
  it finds. So if an operator manually disables the snap while the charm has it enabled, the
  charm re-enables it on the next reconcile. That is intended — config is the declared intent —
  but it means `snap disable` by hand is not a durable override. Setting
  `enable-node-exporter=false` is.
- **Unreadable snapd stalls rather than guesses.** While `snap list` cannot be run at all — snapd
  still seeding on a fresh machine is the common case — the reconcile takes no action and
  renders no node-exporter scrape job, retrying on the next hook. The unit is Active without
  node-exporter metrics rather than Blocked, so a long-lived snapd outage is quieter than it
  might be. The alternative, treating an unreadable snapd as "absent", is what would let a
  transient failure record `absent` for a foreign snap and remove it at teardown; stalling is
  the safe direction.
- **Port collision.** Something already bound to 9100 would make the snap fail to start; Alloy
  would then scrape a dead target and surface it as a scrape failure rather than a charm error.
  Not worth guarding in this slice.
