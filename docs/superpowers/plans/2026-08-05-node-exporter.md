# node-exporter Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `enable-node-exporter` config option that manages the Canonical `node-exporter` snap on the principal machine and scrapes it into Alloy with the principal's Juju topology labels.

**Architecture:** All node-exporter knowledge lives in one new module, `src/node_exporter.py`, split into three layers — subprocess effects, a pure decision layer returning a frozen `Plan`, and a scrape-job factory. `src/charm.py` gains roughly twenty lines of glue and no state-machine branches. A prerequisite fix teaches `_principal_context()` to fall back to the v2 payload's `source_topology` when the `juju-info` relation is absent.

**Tech Stack:** Python 3.10+, `ops` 3.x, `ops[testing]` (Scenario), pytest, ruff, pyright, tox, uv.

## Global Constraints

- Line length is 120 (`[tool.ruff] line-length = 120`).
- Ruff lint selects `E, W, F, C, N, D, I001`. **Every module, class, and public function in `src/` needs a docstring.** Imports must be sorted (`I001`). Max mccabe complexity is 10.
- `tests/*` has per-file-ignores for `D100,D101,D102,D103,D104` — test functions do not need docstrings.
- pyright runs over `src/**.py` only. Annotations in `src/` must be correct.
- Every new module in `src/` uses the repo's dual-import pattern (`try: from .x import ...` / `except ImportError: from x import ...`) so it works both as a package and with `src/` directly on `sys.path`.
- Config option names in `charmcraft.yaml` use kebab-case for this option: `enable-node-exporter`.
- Default of `enable-node-exporter` is `false`. Existing unit tests must stay green.
- Do not modify `src/config_builder.py` or `src/alloy.py`.
- Full check before any commit that touches `src/`: `tox -e lint && tox -e static && tox -e unit`.

## Spec Deviations

Two deliberate departures from `docs/superpowers/specs/2026-08-05-node-exporter-design.md`, both discovered while checking the spec against the real code:

1. **Spec Section 4** says `_active_metrics_scrape_jobs(...)` appends the node-exporter job. This plan appends it in `_configure()` instead. Reason: three tests at `tests/unit/test_charm.py:638`, `:691`, and `:747` stub `_active_metrics_scrape_jobs=lambda payload, principal_context: []` on a `SimpleNamespace` fake charm. Changing that method's signature breaks all three for no benefit. `_configure()` already computes the topology labels it needs.

2. **The spec omits `_missing_relation_requirements`.** `_configure()` calls it at `src/charm.py:283` and parks the unit in `WaitingStatus` whenever it returns a non-empty list — and it reports `machine-observability relation` as missing unconditionally (`src/charm.py:364`). Without a change there, the spec's "Active, node-exporter metrics only" row is unreachable: the unit would render the config and then immediately go Waiting. Task 7 fixes this. **This is a real defect in the spec, not a detail.**

---

### Task 1: `PrincipalContext.from_source_topology`

Pure dataclass constructor. No charm, no relations.

**Files:**
- Modify: `src/principal_context.py` (add a classmethod after `from_relation`, which ends at line 54)
- Test: `tests/unit/test_observability_relation.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `PrincipalContext.from_source_topology(topology: Any, *, model_name: str = "", model_uuid: str = "") -> PrincipalContext`. Task 2 calls this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_observability_relation.py`:

```python
def test_principal_context_from_source_topology_uses_payload_topology():
    topology = SimpleNamespace(
        model="prod",
        model_uuid="uuid-from-payload",
        application="dwellir-observability-reference",
        unit="dwellir-observability-reference/0",
        charm_name="dwellir-observability-reference",
    )

    context = PrincipalContext.from_source_topology(topology, model_name="fallback", model_uuid="fallback-uuid")

    assert context.application == "dwellir-observability-reference"
    assert context.unit == "dwellir-observability-reference/0"
    assert context.model == "prod"
    assert context.model_uuid == "uuid-from-payload"
    assert context.charm_name == "dwellir-observability-reference"


def test_principal_context_from_source_topology_falls_back_to_charm_model():
    topology = SimpleNamespace(
        model="",
        model_uuid="",
        application="polkadot",
        unit="polkadot/0",
        charm_name="",
    )

    context = PrincipalContext.from_source_topology(topology, model_name="fallback", model_uuid="fallback-uuid")

    assert context.model == "fallback"
    assert context.model_uuid == "fallback-uuid"
    assert context.charm_name == ""


def test_principal_context_from_source_topology_renders_juju_labels():
    topology = SimpleNamespace(
        model="prod",
        model_uuid="uuid",
        application="polkadot",
        unit="polkadot/0",
        charm_name="polkadot",
    )

    labels = PrincipalContext.from_source_topology(topology).juju_labels()

    assert labels == {
        "juju_model": "prod",
        "juju_model_uuid": "uuid",
        "juju_application": "polkadot",
        "juju_unit": "polkadot/0",
        "juju_charm": "polkadot",
    }
```

Check the top of the file for `from types import SimpleNamespace`. If it is not already imported, add it to the existing import block.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_observability_relation.py -k from_source_topology -v`
Expected: FAIL — `AttributeError: type object 'PrincipalContext' has no attribute 'from_source_topology'`

- [ ] **Step 3: Write minimal implementation**

In `src/principal_context.py`, add this classmethod directly after `from_relation` (after line 54, before `juju_labels`):

```python
    @classmethod
    def from_source_topology(
        cls,
        topology: Any,
        *,
        model_name: str = "",
        model_uuid: str = "",
    ) -> "PrincipalContext":
        """Build principal context from an explicit v2 source topology block."""
        return cls(
            application=topology.application,
            unit=topology.unit,
            address="",
            model=getattr(topology, "model", "") or model_name,
            model_uuid=getattr(topology, "model_uuid", "") or model_uuid,
            charm_name=getattr(topology, "charm_name", "") or "",
        )
```

`address` is empty because nothing consumes `PrincipalContext.address` — verified by grep, the only `.address` readers are `ScrapeTarget.address`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_observability_relation.py -v`
Expected: PASS, all tests in the file including the three new ones.

- [ ] **Step 5: Commit**

```bash
git add src/principal_context.py tests/unit/test_observability_relation.py
git commit -m "feat: build principal context from v2 source topology"
```

---

### Task 2: `source_topology` fallback in `_principal_context()`

Makes the charm usable when only `machine-observability` is related.

**Files:**
- Modify: `src/charm.py:409-418` (the `_principal_context` method)
- Test: `tests/unit/test_charm.py` (append)

**Interfaces:**
- Consumes: `PrincipalContext.from_source_topology(...)` from Task 1.
- Produces: `_principal_context()` keeps its zero-argument signature. Tasks 7 and 8 rely on that — three existing tests patch it as `_principal_context=lambda: ...`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_charm.py`. `_machine_observability_payload` is the existing helper at line 26; it already accepts `source_topology`.

```python
SOURCE_TOPOLOGY = {
    "model": "payload-model",
    "model_uuid": "payload-uuid",
    "application": "reference",
    "unit": "reference/0",
    "charm_name": "reference-charm",
}


def test_principal_context_falls_back_to_source_topology_without_juju_info():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="reference",
                remote_unit_id=0,
                remote_app_data={
                    "payload": _machine_observability_payload(
                        schema_version=2,
                        charm_name="reference-charm",
                        source_topology=SOURCE_TOPOLOGY,
                    )
                },
            ),
        ],
        leader=True,
    )

    with ctx(ctx.on.update_status(), state) as manager:
        context = manager.charm._principal_context()

    assert context is not None
    assert context.application == "reference"
    assert context.unit == "reference/0"
    assert context.model == "payload-model"


def test_principal_context_prefers_juju_info_over_source_topology():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_app_data={
                    "payload": _machine_observability_payload(
                        schema_version=2,
                        charm_name="reference-charm",
                        source_topology=SOURCE_TOPOLOGY,
                    )
                },
            ),
        ],
        leader=True,
    )

    with ctx(ctx.on.update_status(), state) as manager:
        context = manager.charm._principal_context()

    assert context is not None
    assert context.application == "polkadot"


def test_principal_context_is_none_for_v1_payload_without_juju_info():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_app_data={"payload": _machine_observability_payload()},
            ),
        ],
        leader=True,
    )

    with ctx(ctx.on.update_status(), state) as manager:
        assert manager.charm._principal_context() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_charm.py -k principal_context -v`
Expected: `test_principal_context_falls_back_to_source_topology_without_juju_info` FAILS with `assert None is not None`. The other two pass already — they encode behaviour that must not regress.

- [ ] **Step 3: Write minimal implementation**

Replace `_principal_context` in `src/charm.py` (lines 409-418) with:

```python
    def _principal_context(self) -> PrincipalContext | None:
        """Return principal context from juju-info, falling back to v2 source topology."""
        relation = self.model.get_relation("juju-info")
        if relation is not None and relation.units:
            return PrincipalContext.from_relation(
                relation,
                model_name=self.model.name,
                model_uuid=self.model.uuid,
            )
        topology = self._observability_payload().source_topology
        if topology is not None:
            return PrincipalContext.from_source_topology(
                topology,
                model_name=self.model.name,
                model_uuid=self.model.uuid,
            )
        return None
```

Keep it zero-argument. It re-reads the payload internally rather than accepting one, which preserves the `_principal_context=lambda: ...` test seam used at `tests/unit/test_charm.py:642`, `:698`, and `:754`.

- [ ] **Step 4: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS. Nothing regresses — the existing `juju-info` tests still pass because `juju-info` is checked first.

- [ ] **Step 5: Commit**

```bash
git add src/charm.py tests/unit/test_charm.py
git commit -m "fix: fall back to source topology when juju-info is absent"
```

---

### Task 3: `node_exporter.py` layer 2 — the pure decision layer

The state machine, with no I/O. This is the heart of the feature and the cheapest thing to test.

**Files:**
- Create: `src/node_exporter.py`
- Test: `tests/unit/test_node_exporter.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all used by Tasks 4, 5, 7, and 8:
  - `SnapState(installed: bool, enabled: bool)` — frozen dataclass
  - `Plan(actions: tuple[str, ...], prior_state: str, scrape_enabled: bool)` — frozen dataclass
  - `plan_reconcile(*, enabled: bool, prior_state: str, observe: Callable[[], SnapState]) -> Plan`
  - `plan_teardown(*, prior_state: str) -> Plan`
  - Constants `ACTION_INSTALL="install"`, `ACTION_ENABLE="enable"`, `ACTION_DISABLE="disable"`, `ACTION_REMOVE="remove"`, `ACTION_CONNECT="connect"`
  - Constants `PRIOR_STATE_UNSET=""`, `PRIOR_STATE_ABSENT="absent"`, `PRIOR_STATE_DISABLED="disabled"`, `PRIOR_STATE_ENABLED="enabled"`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_node_exporter.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.node_exporter import (
    ACTION_CONNECT,
    ACTION_DISABLE,
    ACTION_ENABLE,
    ACTION_INSTALL,
    ACTION_REMOVE,
    PRIOR_STATE_ABSENT,
    PRIOR_STATE_DISABLED,
    PRIOR_STATE_ENABLED,
    PRIOR_STATE_UNSET,
    SnapState,
    plan_reconcile,
    plan_teardown,
)


class ObserveSpy:
    """Records whether the planner asked for machine state."""

    def __init__(self, state):
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.state


# --- Rule 1: the opt-in gate ---


def test_disabled_and_never_opted_in_issues_no_command_and_never_observes():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=False, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == ()
    assert plan.prior_state == PRIOR_STATE_UNSET
    assert plan.scrape_enabled is False
    assert spy.calls == 0


def test_teardown_never_opted_in_is_a_no_op():
    plan = plan_teardown(prior_state=PRIOR_STATE_UNSET)

    assert plan.actions == ()


# --- Rule 2: record on first opt-in ---


def test_enabled_from_unset_with_snap_absent_installs_and_records_absent():
    spy = ObserveSpy(SnapState(installed=False, enabled=False))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_INSTALL, ACTION_CONNECT)
    assert plan.prior_state == PRIOR_STATE_ABSENT
    assert plan.scrape_enabled is True
    assert spy.calls == 1


def test_enabled_from_unset_with_snap_disabled_enables_and_records_disabled():
    spy = ObserveSpy(SnapState(installed=True, enabled=False))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_ENABLE, ACTION_CONNECT)
    assert plan.prior_state == PRIOR_STATE_DISABLED


def test_enabled_from_unset_with_snap_running_only_connects_and_records_enabled():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_CONNECT,)
    assert plan.prior_state == PRIOR_STATE_ENABLED


# --- Rule 2: config governs after opt-in ---


def test_enabled_after_opt_in_only_connects_and_does_not_reobserve():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=False, enabled=False))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_CONNECT,), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is True, prior
        assert spy.calls == 0, prior


def test_disabled_after_opt_in_disables_for_every_prior_state():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=True))

        plan = plan_reconcile(enabled=False, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_DISABLE,), prior
        assert ACTION_REMOVE not in plan.actions, prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is False, prior


# --- Teardown: full restore ---


def test_teardown_removes_only_what_the_charm_installed():
    assert plan_teardown(prior_state=PRIOR_STATE_ABSENT).actions == (ACTION_REMOVE,)


def test_teardown_redisables_what_the_charm_enabled():
    plan = plan_teardown(prior_state=PRIOR_STATE_DISABLED)

    assert plan.actions == (ACTION_DISABLE,)
    assert ACTION_REMOVE not in plan.actions


def test_teardown_reenables_a_preexisting_running_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_ENABLED)

    assert plan.actions == (ACTION_ENABLE,)
    assert ACTION_REMOVE not in plan.actions


# --- The sequence that drove the design ---


def test_preexisting_running_snap_survives_false_true_false_teardown():
    observed = SnapState(installed=True, enabled=True)
    prior = PRIOR_STATE_UNSET
    seen = []

    for enabled in (False, True, False):
        plan = plan_reconcile(enabled=enabled, prior_state=prior, observe=lambda: observed)
        seen.append(plan.actions)
        prior = plan.prior_state

    seen.append(plan_teardown(prior_state=prior).actions)

    assert seen == [(), (ACTION_CONNECT,), (ACTION_DISABLE,), (ACTION_ENABLE,)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_node_exporter.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'src.node_exporter'`

- [ ] **Step 3: Write minimal implementation**

Create `src/node_exporter.py`:

```python
"""Manage the node-exporter snap and its Alloy scrape job."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

SNAP_NAME = "node-exporter"

ACTION_INSTALL = "install"
ACTION_ENABLE = "enable"
ACTION_DISABLE = "disable"
ACTION_REMOVE = "remove"
ACTION_CONNECT = "connect"

PRIOR_STATE_UNSET = ""
PRIOR_STATE_ABSENT = "absent"
PRIOR_STATE_DISABLED = "disabled"
PRIOR_STATE_ENABLED = "enabled"

_TEARDOWN_ACTIONS = {
    PRIOR_STATE_ABSENT: (ACTION_REMOVE,),
    PRIOR_STATE_DISABLED: (ACTION_DISABLE,),
    PRIOR_STATE_ENABLED: (ACTION_ENABLE,),
}


@dataclass(frozen=True)
class SnapState:
    """Observed state of the node-exporter snap on the machine."""

    installed: bool
    enabled: bool


@dataclass(frozen=True)
class Plan:
    """An ordered set of snap actions plus the restore point to persist."""

    actions: tuple[str, ...] = ()
    prior_state: str = PRIOR_STATE_UNSET
    scrape_enabled: bool = False


def plan_reconcile(
    *,
    enabled: bool,
    prior_state: str,
    observe: Callable[[], SnapState],
) -> Plan:
    """Decide what to do to the snap for the current config and recorded prior state.

    ``observe`` is called only when the decision needs machine state, so a charm
    that has never been opted in issues no snap command at all.
    """
    if not enabled:
        if prior_state == PRIOR_STATE_UNSET:
            return Plan()
        return Plan(actions=(ACTION_DISABLE,), prior_state=prior_state, scrape_enabled=False)

    if prior_state != PRIOR_STATE_UNSET:
        return Plan(actions=(ACTION_CONNECT,), prior_state=prior_state, scrape_enabled=True)

    state = observe()
    if not state.installed:
        return Plan(
            actions=(ACTION_INSTALL, ACTION_CONNECT),
            prior_state=PRIOR_STATE_ABSENT,
            scrape_enabled=True,
        )
    if not state.enabled:
        return Plan(
            actions=(ACTION_ENABLE, ACTION_CONNECT),
            prior_state=PRIOR_STATE_DISABLED,
            scrape_enabled=True,
        )
    return Plan(actions=(ACTION_CONNECT,), prior_state=PRIOR_STATE_ENABLED, scrape_enabled=True)


def plan_teardown(*, prior_state: str) -> Plan:
    """Decide what to do to the snap when the unit is being removed."""
    return Plan(
        actions=_TEARDOWN_ACTIONS.get(prior_state, ()),
        prior_state=prior_state,
        scrape_enabled=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_node_exporter.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Lint and type check**

Run: `tox -e lint && tox -e static`
Expected: PASS both.

- [ ] **Step 6: Commit**

```bash
git add src/node_exporter.py tests/unit/test_node_exporter.py
git commit -m "feat: add node-exporter reconcile and teardown planner"
```

---

### Task 4: `node_exporter.py` layer 1 — snap effects and `apply`

**Files:**
- Modify: `src/node_exporter.py`
- Test: `tests/unit/test_node_exporter.py` (append)

**Interfaces:**
- Consumes: `Plan`, `SnapState`, and the `ACTION_*` constants from Task 3.
- Produces, used by Tasks 7 and 8: `observe() -> SnapState`, `install()`, `enable()`, `disable()`, `remove()`, `connect_interfaces()`, `get_version() -> str | None`, `apply(plan: Plan) -> None`, and `REQUIRED_INTERFACES`.

**Domain note:** `snap list node-exporter` **exits 1** with `error: no matching snaps installed` when the snap is absent — verified on this machine. `observe()` and `get_version()` must catch `CalledProcessError`. Installed output looks like:

```
Name           Version   Rev   Tracking       Publisher   Notes
node-exporter  v1.10.2   2154  latest/stable  canonical**  disabled
```

Columns are Name, Version, Rev, Tracking, Publisher, Notes. `Notes` is `-` when there is nothing to report, and may be comma-separated (`disabled,classic`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_node_exporter.py`. Add `from unittest.mock import call, patch` to the imports at the top, and extend the `from src.node_exporter import (...)` block with `Plan`, `REQUIRED_INTERFACES`, `SNAP_NAME`, `apply`, `connect_interfaces`, `disable`, `enable`, `get_version`, `install`, `observe`, `remove`.

```python
import subprocess

SNAP_LIST_ENABLED = (
    "Name           Version   Rev   Tracking       Publisher   Notes\n"
    "node-exporter  v1.10.2   2154  latest/stable  canonical**  -\n"
)
SNAP_LIST_DISABLED = (
    "Name           Version   Rev   Tracking       Publisher   Notes\n"
    "node-exporter  v1.10.2   2154  latest/stable  canonical**  disabled\n"
)


def _completed(stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_observe_reports_absent_when_snap_list_exits_nonzero():
    with patch("src.node_exporter._run", side_effect=subprocess.CalledProcessError(1, "snap")):
        assert observe() == SnapState(installed=False, enabled=False)


def test_observe_reports_enabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_ENABLED)):
        assert observe() == SnapState(installed=True, enabled=True)


def test_observe_reports_disabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_DISABLED)):
        assert observe() == SnapState(installed=True, enabled=False)


def test_get_version_reads_the_version_column():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_ENABLED)):
        assert get_version() == "v1.10.2"


def test_get_version_is_none_when_snap_absent():
    with patch("src.node_exporter._run", side_effect=subprocess.CalledProcessError(1, "snap")):
        assert get_version() is None


def test_effects_build_expected_argv():
    with patch("src.node_exporter._run") as run_mock:
        install()
        enable()
        disable()
        remove()

    assert run_mock.call_args_list[0].args[0] == ["snap", "install", SNAP_NAME]
    assert run_mock.call_args_list[1].args[0] == ["snap", "enable", SNAP_NAME]
    assert run_mock.call_args_list[2].args[0] == ["snap", "disable", SNAP_NAME]
    assert run_mock.call_args_list[3].args[0] == ["snap", "remove", "--purge", SNAP_NAME]


def test_connect_interfaces_connects_every_required_interface():
    with patch("src.node_exporter._run") as run_mock:
        connect_interfaces()

    connected = [c.args[0][2] for c in run_mock.call_args_list]
    assert connected == [f"{SNAP_NAME}:{name}" for name in REQUIRED_INTERFACES]


def test_connect_interfaces_continues_past_a_failed_connection():
    outcomes = [subprocess.CalledProcessError(1, "snap"), _completed(), _completed(), _completed()]

    with patch("src.node_exporter._run", side_effect=outcomes) as run_mock:
        connect_interfaces()

    assert run_mock.call_count == len(REQUIRED_INTERFACES)


def test_apply_dispatches_actions_in_order():
    plan = Plan(actions=(ACTION_INSTALL, ACTION_CONNECT))

    with (
        patch("src.node_exporter.install") as install_mock,
        patch("src.node_exporter.connect_interfaces") as connect_mock,
        patch("src.node_exporter.enable") as enable_mock,
    ):
        apply(plan)

    install_mock.assert_called_once_with()
    connect_mock.assert_called_once_with()
    enable_mock.assert_not_called()


def test_apply_does_nothing_for_an_empty_plan():
    with patch("src.node_exporter._run") as run_mock:
        apply(Plan())

    run_mock.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_node_exporter.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'observe' from 'src.node_exporter'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/node_exporter.py`. Extend the existing imports at the top of the file to:

```python
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterable

logger = logging.getLogger(__name__)
```

Add these constants next to `SNAP_NAME`:

```python
REQUIRED_INTERFACES = (
    "hardware-observe",
    "mount-observe",
    "network-observe",
    "system-observe",
)

DEFAULT_SNAP_TIMEOUT = 60
DEFAULT_INSTALL_TIMEOUT = 300
```

Then append the effects section to the end of the module:

```python
def _run(cmd: Iterable[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process."""
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    return subprocess.run(
        list(cmd),
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _snap_list_fields() -> list[str] | None:
    """Return the ``snap list`` row for node-exporter, or None when absent."""
    try:
        result = _run(["snap", "list", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] == SNAP_NAME:
            return fields
    return None


def observe() -> SnapState:
    """Return the current state of the node-exporter snap."""
    fields = _snap_list_fields()
    if fields is None:
        return SnapState(installed=False, enabled=False)
    notes = fields[5] if len(fields) > 5 else "-"
    return SnapState(installed=True, enabled="disabled" not in notes.split(","))


def get_version() -> str | None:
    """Return the installed node-exporter version, or None when absent."""
    fields = _snap_list_fields()
    if fields is None or len(fields) < 2:
        return None
    return fields[1]


def install() -> None:
    """Install the node-exporter snap."""
    _run(["snap", "install", SNAP_NAME], timeout=DEFAULT_INSTALL_TIMEOUT)


def enable() -> None:
    """Enable the node-exporter snap."""
    _run(["snap", "enable", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)


def disable() -> None:
    """Disable the node-exporter snap."""
    _run(["snap", "disable", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)


def remove() -> None:
    """Remove the node-exporter snap and its data."""
    _run(["snap", "remove", "--purge", SNAP_NAME], timeout=DEFAULT_INSTALL_TIMEOUT)


def connect_interfaces() -> None:
    """Connect the snap interfaces node-exporter needs for full metrics.

    A failed connection is logged and skipped: partial metrics beat no metrics,
    and a missing interface on an older snap revision must not block the charm.
    """
    for interface in REQUIRED_INTERFACES:
        try:
            _run(["snap", "connect", f"{SNAP_NAME}:{interface}"], timeout=DEFAULT_SNAP_TIMEOUT)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("Failed to connect %s:%s: %s", SNAP_NAME, interface, exc)


def apply(plan: Plan) -> None:
    """Execute a plan's actions in order."""
    for action in plan.actions:
        if action == ACTION_INSTALL:
            install()
        elif action == ACTION_ENABLE:
            enable()
        elif action == ACTION_DISABLE:
            disable()
        elif action == ACTION_REMOVE:
            remove()
        elif action == ACTION_CONNECT:
            connect_interfaces()
```

`apply` uses an explicit if-chain rather than a module-level dispatch dict on purpose: a dict built at import time captures the original function objects, so `patch("src.node_exporter.install")` would not take effect. Looking the names up at call time keeps the module patchable.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_node_exporter.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Lint and type check**

Run: `tox -e lint && tox -e static`
Expected: PASS both.

- [ ] **Step 6: Commit**

```bash
git add src/node_exporter.py tests/unit/test_node_exporter.py
git commit -m "feat: add node-exporter snap effects and plan execution"
```

---

### Task 5: `node_exporter.scrape_job`

**Files:**
- Modify: `src/node_exporter.py`
- Test: `tests/unit/test_node_exporter.py` (append)

**Interfaces:**
- Consumes: `MetricsScrapeJob` and `ScrapeTarget` from `src/config_builder.py` (unmodified).
- Produces, used by Task 7: `scrape_job(*, topology_labels: dict[str, str], scrape_interval: str, scrape_timeout: str) -> MetricsScrapeJob`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_node_exporter.py`, and add `scrape_job` to the `src.node_exporter` import block:

```python
def test_scrape_job_targets_localhost_with_topology_labels():
    labels = {"juju_application": "polkadot", "juju_unit": "polkadot/0"}

    job = scrape_job(topology_labels=labels, scrape_interval="30s", scrape_timeout="5s")

    assert job.job_name == "node-exporter"
    assert job.metrics_path == "/metrics"
    assert job.scheme == "http"
    assert job.scrape_interval == "30s"
    assert job.scrape_timeout == "5s"
    assert len(job.targets) == 1
    assert job.targets[0].address == "localhost:9100"
    assert job.targets[0].labels == labels


def test_scrape_job_copies_the_labels_it_is_given():
    labels = {"juju_unit": "polkadot/0"}

    job = scrape_job(topology_labels=labels, scrape_interval="1m", scrape_timeout="10s")
    labels["juju_unit"] = "mutated"

    assert job.targets[0].labels == {"juju_unit": "polkadot/0"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_node_exporter.py -k scrape_job -v`
Expected: FAIL at collection — `ImportError: cannot import name 'scrape_job'`

- [ ] **Step 3: Write minimal implementation**

Add the dual import just below the `logger = logging.getLogger(__name__)` line in `src/node_exporter.py`:

```python
try:
    from .config_builder import MetricsScrapeJob, ScrapeTarget
except ImportError:
    from config_builder import MetricsScrapeJob, ScrapeTarget
```

Add these constants beside `SNAP_NAME`:

```python
DEFAULT_PORT = 9100
JOB_NAME = "node-exporter"
METRICS_PATH = "/metrics"
SCHEME = "http"
```

Append to the end of the module:

```python
def scrape_job(
    *,
    topology_labels: dict[str, str],
    scrape_interval: str,
    scrape_timeout: str,
) -> MetricsScrapeJob:
    """Build the Alloy scrape job for the local node-exporter."""
    return MetricsScrapeJob(
        job_name=JOB_NAME,
        targets=[ScrapeTarget(address=f"localhost:{DEFAULT_PORT}", labels=dict(topology_labels))],
        metrics_path=METRICS_PATH,
        scheme=SCHEME,
        scrape_interval=scrape_interval,
        scrape_timeout=scrape_timeout,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_node_exporter.py -v`
Expected: PASS, 23 tests.

- [ ] **Step 5: Lint and type check**

Run: `tox -e lint && tox -e static`
Expected: PASS both.

- [ ] **Step 6: Commit**

```bash
git add src/node_exporter.py tests/unit/test_node_exporter.py
git commit -m "feat: add node-exporter scrape job factory"
```

---

### Task 6: `enable-node-exporter` config option

**Files:**
- Modify: `charmcraft.yaml` (inside `config.options`, after the `max_elapsed_time_min` block)
- Test: `tests/unit/test_repo_baseline.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: the config key `enable-node-exporter`, read by Task 7 via `self.config.get("enable-node-exporter", False)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_repo_baseline.py`:

```python
def test_charmcraft_declares_node_exporter_option():
    charmcraft = Path("charmcraft.yaml").read_text()

    assert "enable-node-exporter:" in charmcraft
    assert "type: boolean" in charmcraft
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_repo_baseline.py::test_charmcraft_declares_node_exporter_option -v`
Expected: FAIL — `assert 'enable-node-exporter:' in charmcraft`

- [ ] **Step 3: Write minimal implementation**

In `charmcraft.yaml`, add to `config.options` immediately after the `max_elapsed_time_min` block (which ends with `default: 5`), at the same indentation as the other option keys:

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_repo_baseline.py -v`
Expected: PASS, all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add charmcraft.yaml tests/unit/test_repo_baseline.py
git commit -m "feat: add enable-node-exporter config option"
```

---

### Task 7: Charm wiring — reconcile, gating, and the scrape job

The largest task. It touches five regions of `src/charm.py` and repairs three existing tests.

**Files:**
- Modify: `src/charm.py` — imports (lines 27, 53), `__init__` (lines 166, 181-188), `_configure` (lines 266-336), `_missing_relation_requirements` (lines 355-366), and a new config accessor near line 541
- Modify: `tests/unit/test_charm.py:638`, `:691`, `:747` — the three `SimpleNamespace` fake charms
- Test: `tests/unit/test_charm.py` (append)

**Interfaces:**
- Consumes: `node_exporter.plan_reconcile`, `node_exporter.apply`, `node_exporter.observe`, `node_exporter.scrape_job` from Tasks 3-5; the `enable-node-exporter` key from Task 6; `_principal_context()` from Task 2.
- Produces, used by Task 8: `self._stored.node_exporter_prior_state` (a `str`), and `self._node_exporter_enabled() -> bool`.

**Critical:** `_missing_relation_requirements` at `src/charm.py:364` reports `machine-observability relation` as missing unconditionally, and `_configure` turns any non-empty result into `WaitingStatus` at line 330. Without the change in Step 5 below, the node-exporter-only mode renders a correct config and then goes Waiting anyway — the feature would look broken.

- [ ] **Step 1: Repair the three existing fake-charm tests**

These fail as soon as `_configure` calls a method the fake does not define. In `tests/unit/test_charm.py`, in each of the three `SimpleNamespace(...)` blocks at lines 638, 691, and 747, add these two entries immediately after the `_has_machine_observability_relation=lambda: True,` line:

```python
        _reconcile_node_exporter=lambda: (False, None),
        _node_exporter_enabled=lambda: False,
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_charm.py`:

```python
def test_reconcile_node_exporter_persists_prior_state_before_applying():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(node_exporter_prior_state=""),
        _node_exporter_enabled=lambda: True,
    )

    with (
        patch("charm.node_exporter.observe", return_value=SimpleNamespace(installed=False, enabled=False)),
        patch("charm.node_exporter.apply") as apply_mock,
    ):
        scrape_enabled, error = AlloySubCharm._reconcile_node_exporter(fake_charm)

    assert scrape_enabled is True
    assert error is None
    assert fake_charm._stored.node_exporter_prior_state == "absent"
    apply_mock.assert_called_once()


def test_reconcile_node_exporter_records_prior_state_even_when_apply_fails():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(node_exporter_prior_state=""),
        _node_exporter_enabled=lambda: True,
    )

    with (
        patch("charm.node_exporter.observe", return_value=SimpleNamespace(installed=False, enabled=False)),
        patch("charm.node_exporter.apply", side_effect=RuntimeError("snap store unreachable")),
    ):
        scrape_enabled, error = AlloySubCharm._reconcile_node_exporter(fake_charm)

    assert scrape_enabled is False
    assert error == "snap store unreachable"
    assert fake_charm._stored.node_exporter_prior_state == "absent"


def test_reconcile_node_exporter_issues_nothing_when_never_opted_in():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(node_exporter_prior_state=""),
        _node_exporter_enabled=lambda: False,
    )

    with (
        patch("charm.node_exporter.observe") as observe_mock,
        patch("charm.node_exporter.apply") as apply_mock,
    ):
        scrape_enabled, error = AlloySubCharm._reconcile_node_exporter(fake_charm)

    assert scrape_enabled is False
    assert error is None
    observe_mock.assert_not_called()
    apply_mock.assert_called_once()
    assert apply_mock.call_args.args[0].actions == ()


def test_node_exporter_job_is_rendered_with_topology_labels():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_app_data={"payload": _machine_observability_payload()},
            ),
        ],
        config={"enable-node-exporter": True},
        leader=True,
    )

    with (
        patch("charm.node_exporter.observe", return_value=SimpleNamespace(installed=True, enabled=True)),
        patch("charm.node_exporter.apply"),
        patch("charm.alloy.get_version", return_value="1.0.0"),
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text"),
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.reload"),
        patch("charm.alloy.verify_config"),
        patch("charm.ConfigBuilder") as builder_cls,
    ):
        builder_cls.return_value.build.return_value = ""
        ctx.run(ctx.on.update_status(), state)

    jobs = builder_cls.call_args.kwargs["metrics_scrape_jobs"]
    node_jobs = [job for job in jobs if job.job_name == "node-exporter"]
    assert len(node_jobs) == 1
    assert node_jobs[0].targets[0].address == "localhost:9100"
    assert node_jobs[0].targets[0].labels["juju_unit"] == "polkadot/0"


def test_node_exporter_only_mode_is_active_without_machine_observability():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
        ],
        config={"enable-node-exporter": True},
        leader=True,
    )

    with (
        patch("charm.node_exporter.observe", return_value=SimpleNamespace(installed=True, enabled=True)),
        patch("charm.node_exporter.apply"),
        patch("charm.alloy.get_version", return_value="1.0.0"),
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text"),
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.reload"),
        patch("charm.alloy.verify_config"),
        patch("charm.ConfigBuilder") as builder_cls,
    ):
        builder_cls.return_value.build.return_value = ""
        state_out = ctx.run(ctx.on.update_status(), state)

    assert state_out.unit_status.name == "active"
    assert "node-exporter metrics only" in state_out.unit_status.message


def test_snap_failure_blocks_but_still_writes_config():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_app_data={"payload": _machine_observability_payload()},
            ),
        ],
        config={"enable-node-exporter": True},
        leader=True,
    )

    with (
        patch("charm.node_exporter.observe", return_value=SimpleNamespace(installed=False, enabled=False)),
        patch("charm.node_exporter.apply", side_effect=RuntimeError("snap store unreachable")),
        patch("charm.alloy.get_version", return_value="1.0.0"),
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text") as write_config_mock,
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.reload"),
        patch("charm.alloy.verify_config"),
        patch("charm.ConfigBuilder") as builder_cls,
    ):
        builder_cls.return_value.build.return_value = ""
        state_out = ctx.run(ctx.on.update_status(), state)

    write_config_mock.assert_called()
    assert state_out.unit_status.name == "blocked"
    assert "snap store unreachable" in state_out.unit_status.message
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_charm.py -k "node_exporter or snap_failure" -v`
Expected: FAIL — `AttributeError: <module 'charm'> does not have the attribute 'node_exporter'`

- [ ] **Step 4: Wire the module in**

In `src/charm.py`, change the `try` branch import at line 27 from `from . import alloy` to:

```python
    from . import alloy, node_exporter
```

and the `except ImportError` branch at line 53 from `import alloy` to:

```python
    import alloy
    import node_exporter
```

In `__init__`, change line 166 to seed the new stored field:

```python
        self._stored.set_default(last_good_config="", last_custom_args="", node_exporter_prior_state="")
```

- [ ] **Step 5: Add the reconcile helper, the config accessor, and the requirements fix**

Add these two methods to `AlloySubCharm`. Put `_reconcile_node_exporter` directly after `_configure`, and `_node_exporter_enabled` beside the other config accessors near line 541:

```python
    def _reconcile_node_exporter(self) -> tuple[bool, str | None]:
        """Bring the node-exporter snap in line with config; return (scrape_enabled, error)."""
        plan = node_exporter.plan_reconcile(
            enabled=self._node_exporter_enabled(),
            prior_state=self._stored.node_exporter_prior_state,
            observe=node_exporter.observe,
        )
        self._stored.node_exporter_prior_state = plan.prior_state
        try:
            node_exporter.apply(plan)
        except Exception as exc:  # noqa: BLE001
            logger.warning("node-exporter reconcile failed: %s", exc)
            return False, str(exc)
        return plan.scrape_enabled, None
```

```python
    def _node_exporter_enabled(self) -> bool:
        """Return whether the operator has enabled node-exporter management."""
        return bool(self.config.get("enable-node-exporter", False))
```

`prior_state` is persisted *before* `apply` runs, so a partial snap failure still leaves a correct restore point for teardown.

Then replace `_missing_relation_requirements` (lines 355-366) with:

```python
    def _missing_relation_requirements(
        self,
        *,
        principal_context: PrincipalContext | None,
    ) -> list[str]:
        """Return required relation inputs that are still missing."""
        missing_relations: list[str] = []
        if principal_context is None:
            missing_relations.append("juju-info relation")
        if not self._has_machine_observability_relation() and not self._node_exporter_enabled():
            missing_relations.append("machine-observability relation")
        return missing_relations
```

- [ ] **Step 6: Rewrite `_configure`**

Replace the body of `_configure` (lines 266-336) with the following. Four things change: the reconcile call is hoisted to the top, the second gate consults `_node_exporter_enabled()`, `topology_labels` is hoisted out of the `ConfigBuilder(...)` call, and the tail reports the snap error and the node-exporter-only message.

```python
    def _configure(self, *, active_message: str) -> bool:
        """Render, validate, and apply Alloy config from relation data."""
        scrape_enabled, node_exporter_error = self._reconcile_node_exporter()

        principal_context = self._principal_context()
        if principal_context is None:
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(
                self._status_message("config waiting for juju-info relation or machine-observability source_topology")
            )
            return False
        if not self._has_machine_observability_relation() and not self._node_exporter_enabled():
            self._reset_config_for_missing_relations()
            self.unit.status = ops.WaitingStatus(
                self._status_message("config waiting for machine-observability relation")
            )
            return False

        payload = self._observability_payload()
        loki_endpoints = self._loki_endpoint_urls()
        remote_write_endpoints = self._remote_write_endpoint_urls()
        waiting_requirements = self._missing_relation_requirements(
            principal_context=principal_context,
        )
        logger.info("Configuring Alloy with principal context: %s and payload: %s", principal_context, payload)

        topology_labels = principal_context.juju_labels(charm_name=payload.charm_name)
        metrics_scrape_jobs = self._active_metrics_scrape_jobs(payload, principal_context)
        if scrape_enabled:
            metrics_scrape_jobs = [
                *metrics_scrape_jobs,
                node_exporter.scrape_job(
                    topology_labels=topology_labels,
                    scrape_interval=self._global_scrape_interval(),
                    scrape_timeout=self._global_scrape_timeout(),
                ),
            ]

        builder = ConfigBuilder(
            loki_endpoints=loki_endpoints,
            remote_write_endpoints=remote_write_endpoints,
            metrics_scrape_jobs=metrics_scrape_jobs,
            systemd_units=payload.systemd_units,
            journal_match_expressions=payload.journal_match_expressions,
            file_log_sources=[
                BuilderFileLogSource(
                    include=source.include,
                    exclude=merge_file_excludes(source.exclude, self._path_exclude_patterns()),
                    attributes=source.attributes,
                )
                for source in payload.log_files
            ],
            topology_labels=topology_labels,
            global_scrape_interval=self._global_scrape_interval(),
            global_scrape_timeout=self._global_scrape_timeout(),
            path_exclude=[],
            queue_size=self._queue_size(),
            max_elapsed_time_min=self._max_elapsed_time_min(),
            tls_insecure_skip_verify=self._tls_insecure_skip_verify(),
        )
        desired_custom_args = self._desired_custom_args()
        previous_custom_args = self._stored.last_custom_args
        config_text = f"{alloy.GENERATED_CONFIG_HEADER}{builder.build()}"
        self._validate_config(config_text)
        alloy.ensure_config_dir_permissions(str(Path(DEFAULT_CONFIG_PATH).parent))
        alloy.write_config_text(config_text, config_path=Path(DEFAULT_CONFIG_PATH))
        alloy.write_custom_args(desired_custom_args)
        self._stored.last_good_config = config_text
        self._stored.last_custom_args = desired_custom_args
        if alloy.is_active() or waiting_requirements:
            if alloy.is_active():
                self._apply_runtime_update(
                    desired_custom_args=desired_custom_args,
                    previous_custom_args=previous_custom_args,
                )
        else:
            self._apply_runtime_update(
                desired_custom_args=desired_custom_args,
                previous_custom_args=previous_custom_args,
            )
        if node_exporter_error is not None:
            self.unit.status = ops.BlockedStatus(self._status_message(f"node-exporter: {node_exporter_error}"))
            return False
        if waiting_requirements:
            self.unit.status = ops.WaitingStatus(
                self._status_message(self._relation_waiting_message(waiting_requirements))
            )
            return False
        if not self._has_machine_observability_relation():
            active_message = "node-exporter metrics only"
        self.unit.status = ops.ActiveStatus(self._status_message(f"config valid; {active_message}"))
        return True
```

The snap-error check goes *before* the `waiting_requirements` check so a real snap fault is never masked by a relation-waiting message.

- [ ] **Step 7: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS, including the three repaired fake-charm tests and all six new ones.

- [ ] **Step 8: Lint and type check**

Run: `tox -e lint && tox -e static`
Expected: PASS both.

- [ ] **Step 9: Commit**

```bash
git add src/charm.py tests/unit/test_charm.py
git commit -m "feat: reconcile node-exporter and render its scrape job"
```

---

### Task 8: Teardown on unit removal

**Files:**
- Modify: `src/charm.py` — one `framework.observe` line in `__init__` (after line 172) and one new handler after `_on_stop` (line 216)
- Test: `tests/unit/test_charm.py` (append)

**Interfaces:**
- Consumes: `node_exporter.plan_teardown`, `node_exporter.apply` from Tasks 3-4; `self._stored.node_exporter_prior_state` from Task 7.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_charm.py`:

```python
def _run_remove_with_prior_state(prior_state):
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
        ],
        stored_states={
            testing.StoredState(
                owner_path="AlloySubCharm",
                name="_stored",
                content={
                    "last_good_config": "",
                    "last_custom_args": "",
                    "node_exporter_prior_state": prior_state,
                },
            )
        },
        leader=True,
    )

    with (
        patch("charm.node_exporter.remove") as remove_mock,
        patch("charm.node_exporter.enable") as enable_mock,
        patch("charm.node_exporter.disable") as disable_mock,
    ):
        ctx.run(ctx.on.remove(), state)

    return remove_mock, enable_mock, disable_mock


def test_remove_removes_only_a_snap_the_charm_installed():
    remove_mock, enable_mock, disable_mock = _run_remove_with_prior_state("absent")

    remove_mock.assert_called_once()
    enable_mock.assert_not_called()
    disable_mock.assert_not_called()


def test_remove_redisables_a_snap_the_charm_enabled():
    remove_mock, enable_mock, disable_mock = _run_remove_with_prior_state("disabled")

    disable_mock.assert_called_once()
    remove_mock.assert_not_called()
    enable_mock.assert_not_called()


def test_remove_reenables_a_preexisting_running_snap():
    remove_mock, enable_mock, disable_mock = _run_remove_with_prior_state("enabled")

    enable_mock.assert_called_once()
    remove_mock.assert_not_called()
    disable_mock.assert_not_called()


def test_remove_touches_nothing_when_never_opted_in():
    remove_mock, enable_mock, disable_mock = _run_remove_with_prior_state("")

    remove_mock.assert_not_called()
    enable_mock.assert_not_called()
    disable_mock.assert_not_called()


def test_remove_does_not_raise_when_teardown_fails():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        stored_states={
            testing.StoredState(
                owner_path="AlloySubCharm",
                name="_stored",
                content={
                    "last_good_config": "",
                    "last_custom_args": "",
                    "node_exporter_prior_state": "absent",
                },
            )
        },
        leader=True,
    )

    with patch("charm.node_exporter.remove", side_effect=RuntimeError("snapd is down")):
        ctx.run(ctx.on.remove(), state)
```

Both `testing.StoredState(name="_stored", owner_path="AlloySubCharm", content=...)` and `ctx.on.remove()` were verified against the `ops` version pinned in this repo — seeding `_stored` this way reads back correctly inside the handler.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_charm.py -k remove -v`
Expected: FAIL — the mocks are never called because no `remove` handler is registered.

- [ ] **Step 3: Write minimal implementation**

In `src/charm.py` `__init__`, add this observer directly after the `self.on.stop` line (line 172):

```python
        self.framework.observe(self.on.remove, self._on_remove)
```

Add the handler directly after `_on_stop` (which ends at line 216):

```python
    def _on_remove(self, _: ops.RemoveEvent) -> None:
        """Restore the snap state the charm originally found before the unit goes away."""
        try:
            node_exporter.apply(
                node_exporter.plan_teardown(prior_state=self._stored.node_exporter_prior_state)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("node-exporter teardown failed: %s", exc)
```

Failures are logged rather than raised — a failing teardown must not wedge unit removal.

- [ ] **Step 4: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS, including all five new removal tests.

- [ ] **Step 5: Lint and type check**

Run: `tox -e lint && tox -e static`
Expected: PASS both.

- [ ] **Step 6: Commit**

```bash
git add src/charm.py tests/unit/test_charm.py
git commit -m "feat: restore node-exporter snap state on unit removal"
```

---

### Task 9: Document the option and the topology fallback

**Files:**
- Modify: `README.md`
- Test: `tests/unit/test_repo_baseline.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_repo_baseline.py`:

```python
def test_readme_documents_node_exporter():
    readme = Path("README.md").read_text()

    assert "## node-exporter" in readme
    assert "enable-node-exporter" in readme
    assert "source_topology" in readme
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_repo_baseline.py::test_readme_documents_node_exporter -v`
Expected: FAIL — `assert '## node-exporter' in readme`

- [ ] **Step 3: Write the documentation**

Insert this section in `README.md` immediately before the `## Validation Flow` heading:

```markdown
## node-exporter

Set `enable-node-exporter=true` to have the subordinate manage the Canonical
`node-exporter` snap on the principal's machine and scrape it into Alloy:

```bash
juju config alloy-sub enable-node-exporter=true
```

The charm installs the snap if it is missing, connects `hardware-observe`,
`mount-observe`, `network-observe`, and `system-observe`, and renders a
`node-exporter` scrape job against `localhost:9100` carrying the principal's
Juju topology labels, so host metrics attribute to the correct Juju unit.

### Ownership rules

Two rules govern what the charm will touch:

1. **Until you set `enable-node-exporter=true` at least once, the charm issues no
   snap command at all.** Deploying `alloy-sub` with the default onto a machine
   that already runs node-exporter changes nothing.
2. **After you have set it to `true` once, the config governs in both
   directions.** Setting it back to `false` disables the snap, even if the snap
   was already running before the charm arrived.

On unit removal the charm restores exactly what it first found: a snap it
installed is removed, a snap it enabled is disabled again, and a snap that was
already running is left running.

Because rule 2 takes effect from the first `true`, `true` → `false` is not the
same as never having enabled it. To make the charm stop managing a pre-existing
snap entirely, remove the unit — that triggers the full restore.

Verify on the principal machine:

```bash
juju ssh <principal-unit> 'snap list node-exporter'
juju ssh <principal-unit> 'grep -n "node_exporter" -A12 /etc/alloy/config.alloy'
```

## Topology Without juju-info

Juju never creates the `juju-info` relation automatically, and a subordinate is
co-located by *any* container-scoped relation. Relating only
`machine-observability` therefore gives you a running `alloy-sub` unit with no
`juju-info`.

`alloy-sub` handles that: when `juju-info` is absent it derives Juju topology
from the v2 payload's `source_topology` block. `juju-info` remains the source of
truth when it is present.

A v1 principal with no `juju-info` has no topology anywhere, so the unit stays in
`WaitingStatus`. Relate `juju-info` explicitly in that case:

```bash
juju integrate alloy-sub:juju-info <principal>
```
```

- [ ] **Step 4: Run the full unit suite**

Run: `tox -e unit`
Expected: PASS.

- [ ] **Step 5: Run every check**

Run: `tox -e lint && tox -e static && tox -e unit`
Expected: PASS all three. `codespell` runs as part of lint and covers `README.md`.

- [ ] **Step 6: Commit**

```bash
git add README.md tests/unit/test_repo_baseline.py
git commit -m "docs: document node-exporter option and topology fallback"
```

---

## Manual Verification

Unit tests cover the state machine exhaustively, but the snap path needs a real machine. After packing and deploying:

```bash
charmcraft pack
juju refresh alloy-sub --path ./alloy-sub_ubuntu@24.04-amd64.charm
```

1. **Default is a no-op.** On a machine with node-exporter already running, refresh and confirm `snap list node-exporter` still shows it enabled and `/etc/alloy/config.alloy` has no `node_exporter` block.
2. **Opt in.** `juju config alloy-sub enable-node-exporter=true`, then confirm `snap list node-exporter` is enabled, `snap connections node-exporter` shows the four interfaces connected, and the Alloy config gained a `prometheus.scrape "node_exporter"` block with `juju_unit` labels.
3. **Metrics land.** Confirm `node_cpu_seconds_total` appears in the remote-write backend with the principal's `juju_unit` label.
4. **Opt out.** `juju config alloy-sub enable-node-exporter=false`, then confirm `snap list node-exporter` shows `disabled` and the scrape block is gone.
5. **Teardown restores.** `juju remove-relation alloy-sub:juju-info <principal>` and confirm the snap returns to the state from step 1.
