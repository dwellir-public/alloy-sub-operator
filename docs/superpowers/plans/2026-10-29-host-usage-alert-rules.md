# Built-in Host Usage Alert Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `enable-host-metrics=true`, the `alloy-sub` leader publishes four charm-owned Prometheus alert rules (disk and memory usage, 80% warning / 90% critical) on `send-remote-write`, and withdraws them when the option is turned off.

**Architecture:** A new pure module `src/host_alert_rules.py` builds one deterministic rule group from the model UUID and application name. `AlloySubCharm._reconcile_rule_groups` appends that group after the flattened principal Prometheus groups, counts its bytes toward the existing 60 KiB publication ceiling, and leaves Loki publication and destination-status logic untouched.

**Tech Stack:** Python 3.10+, `ops` (Harness tests), pytest, ruff, pyright. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-29-host-usage-alert-rules-design.md`

## Global Constraints

- `requires-python = ">=3.10"`; ruff `line-length = 120`; `tox -e lint` (ruff check + format + codespell) and `tox -e static` (pyright on `src`) must pass.
- `charm.py` imports every sibling module twice (`from .x import` in the `try` branch, `from x import` in the `except ImportError` branch); the new module must be added to both.
- Rule labels must never contain a key starting with `juju_`.
- Thresholds, `for` duration, fstype exclusion, and alert names are the exact values in the spec's tables.
- Commit messages follow the repo style: one imperative sentence, no conventional-commit prefix (see `git log --oneline -5`).

## Review Focus

1. A rule group that carries `juju_unit` or any `juju_*` label would overwrite the series topology and attribute every alert to the leader's principal. Pinned by `test_rules_do_not_set_topology_labels` (Task 1).
2. A model UUID containing `"` or `\` must not break out of the PromQL matcher. Pinned by `test_model_uuid_is_json_escaped_in_matchers` (Task 1).
3. A principal rule state sitting exactly at the 60 KiB ceiling must be rejected once the host group is added, instead of `publish_rule_groups` raising inside the hook. Pinned by `test_host_alert_groups_count_toward_publication_size` (Task 2).
4. Enabling host metrics on a leader whose only metrics sink is Grafana Cloud (no `send-remote-write` relation) must not move an `Active` unit to `Waiting`. Pinned by `test_host_alert_rules_do_not_change_destination_status` (Task 2).
5. A non-leader must not write application relation data when host metrics are enabled. Pinned by `test_non_leader_does_not_publish_host_alert_rules` (Task 2).

---

### Task 1: Host alert rule builder

**Files:**
- Create: `src/host_alert_rules.py`
- Test: `tests/unit/test_host_alert_rules.py`

**Interfaces:**
- Consumes: `alert_rules.validate_rule_groups(groups: object) -> bool` (existing, `src/alert_rules.py`).
- Produces:
  - `build_host_alert_rule_groups(*, model_uuid: str, application: str) -> list[dict[str, object]]` returning exactly one group.
  - Constants: `HOST_ALERT_GROUP_SUFFIX = "host-metrics"`, `HOST_USAGE_WARNING_PERCENT = 80`, `HOST_USAGE_CRITICAL_PERCENT = 90`, `HOST_ALERT_FOR = "5m"`, `HOST_DISK_FSTYPE_EXCLUDE = "tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"`, `HOST_METRICS_JOB_NAME` imported from `config_builder` (do not redefine it).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_host_alert_rules.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from alert_rules import validate_rule_groups
from host_alert_rules import build_host_alert_rule_groups

MODEL_UUID = "00000000-0000-4000-8000-000000000222"
BASE_MATCHER = f'juju_model_uuid="{MODEL_UUID}",job="node-exporter"'
DISK_MATCHER = f'{BASE_MATCHER},fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"'


def _rules():
    groups = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-sub")
    assert len(groups) == 1
    return groups[0]["rules"]


def test_build_returns_one_group_named_after_the_application():
    groups = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-sub")
    assert [group["name"] for group in groups] == ["alloy-sub-host-metrics"]
    assert set(groups[0]) == {"name", "rules"}


def test_rules_are_ordered_disk_then_memory_warning_then_critical():
    assert [rule["alert"] for rule in _rules()] == [
        "HostDiskUsageWarning",
        "HostDiskUsageCritical",
        "HostMemoryUsageWarning",
        "HostMemoryUsageCritical",
    ]


def test_disk_rules_measure_used_fraction_of_real_filesystems():
    disk_warning, disk_critical, _, _ = _rules()
    usage = f"100 * (1 - node_filesystem_avail_bytes{{{DISK_MATCHER}}} / node_filesystem_size_bytes{{{DISK_MATCHER}}})"
    assert disk_warning["expr"] == f"{usage} > 80"
    assert disk_critical["expr"] == f"{usage} > 90"


def test_memory_rules_measure_available_over_total():
    _, _, memory_warning, memory_critical = _rules()
    usage = f"100 * (1 - node_memory_MemAvailable_bytes{{{BASE_MATCHER}}} / node_memory_MemTotal_bytes{{{BASE_MATCHER}}})"
    assert memory_warning["expr"] == f"{usage} > 80"
    assert memory_critical["expr"] == f"{usage} > 90"


def test_every_rule_waits_five_minutes_and_carries_only_a_severity_label():
    rules = _rules()
    assert [rule["for"] for rule in rules] == ["5m"] * 4
    assert [rule["labels"] for rule in rules] == [
        {"severity": "warning"},
        {"severity": "critical"},
        {"severity": "warning"},
        {"severity": "critical"},
    ]


def test_rules_do_not_set_topology_labels():
    for rule in _rules():
        assert not any(key.startswith("juju_") for key in rule["labels"])


def test_annotations_name_the_unit_mountpoint_and_value():
    rules = _rules()
    disk_warning, memory_critical = rules[0], rules[3]
    assert disk_warning["annotations"] == {
        "summary": "Disk usage above 80% on {{ $labels.juju_unit }} ({{ $labels.mountpoint }})",
        "description": '{{ $labels.mountpoint }} on {{ $labels.juju_unit }} is {{ $value | printf "%.1f" }}% full.',
    }
    assert memory_critical["annotations"] == {
        "summary": "Memory usage above 90% on {{ $labels.juju_unit }}",
        "description": '{{ $labels.juju_unit }} is using {{ $value | printf "%.1f" }}% of memory.',
    }


def test_model_uuid_is_json_escaped_in_matchers():
    groups = build_host_alert_rule_groups(model_uuid='a"b\\c', application="alloy-sub")
    expr = groups[0]["rules"][0]["expr"]
    assert 'juju_model_uuid="a\\"b\\\\c"' in expr


def test_groups_pass_the_backend_rule_schema():
    assert validate_rule_groups(build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-sub"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit/test_host_alert_rules.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'host_alert_rules'`

- [ ] **Step 3: Implement `src/host_alert_rules.py`**

Module docstring: "Charm-owned host disk and memory usage alert rules for the host-metrics job."

Define the constants from the Interfaces block, then:

```python
def build_host_alert_rule_groups(*, model_uuid: str, application: str) -> list[dict[str, object]]:
```

Approach: build the base matcher as `f"juju_model_uuid={json.dumps(model_uuid)},job={json.dumps(HOST_METRICS_JOB_NAME)}"`, the disk matcher as the base matcher plus `,fstype!~"<HOST_DISK_FSTYPE_EXCLUDE>"`. A private `_usage_rule(alert, usage_expr, threshold, severity, summary, description)` returns one rule dict with keys `alert`, `expr` (`f"{usage_expr} > {threshold}"`), `for`, `labels`, `annotations`. The summary strings are built with the threshold number interpolated (`f"Disk usage above {threshold}% on ..."`); the description strings are the exact copy in the Step 1 test and are identical for warning and critical.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit/test_host_alert_rules.py -q`
Expected: `9 passed`

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run pyright src`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/host_alert_rules.py tests/unit/test_host_alert_rules.py
git commit -m "Add host disk and memory usage alert rule builder"
```

---

### Task 2: Publish host alert rules from the leader

**Files:**
- Modify: `src/charm.py:37` and `src/charm.py:67` (imports), `src/charm.py:359-406` (`_reconcile_rule_groups`), `src/charm.py:441-458` (`_bounded_existing_rule_state`), `src/charm.py:529-540` (`_rule_state_is_publishable`), plus a new helper next to `_host_metrics_enabled` at `src/charm.py:1175`.
- Test: `tests/unit/test_remote_write_relation.py`, `tests/unit/test_observability_relation.py`

**Interfaces:**
- Consumes: `build_host_alert_rule_groups(*, model_uuid, application)` from Task 1; existing `publish_rule_groups`, `_flatten_rule_state`, `RULE_PUBLICATION_VALUE_LIMIT`.
- Produces:
  - `AlloySubCharm._host_alert_rule_groups(self) -> list[dict[str, object]]`: `[]` unless `self._host_metrics_enabled()`, else the Task 1 group for `self.model.uuid` and `self.app.name`.
  - `AlloySubCharm._rule_state_is_publishable(relation_state, extra_prometheus_groups: Sequence[dict[str, object]] = ()) -> bool` (static; existing one-argument calls keep working).
  - `AlloySubCharm._bounded_existing_rule_state(self, previous_states, extra_prometheus_groups: Sequence[dict[str, object]] = ())`.

- [ ] **Step 1: Write the failing publication tests in `tests/unit/test_remote_write_relation.py`**

Add `import ops` and `from host_alert_rules import build_host_alert_rule_groups` next to the existing imports, and a helper that mirrors the existing test's setup but patches `_configure` so `update_config` does not touch the real Alloy installation:

```python
def _leader_harness(monkeypatch, *, leader=True, with_remote_write=True):
    monkeypatch.setattr(AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(leader)
    harness.set_model_name(MODEL_NAME)
    harness.set_model_uuid(MODEL_UUID)
    harness.begin()
    relation_id = harness.add_relation("send-remote-write", "mimir-gateway-vm") if with_remote_write else None
    return harness, relation_id


def _published_groups(harness, relation_id):
    return json.loads(harness.get_relation_data(relation_id, harness.charm.app.name)["alert_rules"])["groups"]


def test_enabling_host_metrics_publishes_the_host_alert_group(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch)

    harness.update_config({"enable-host-metrics": True})

    assert _published_groups(harness, relation_id) == build_host_alert_rule_groups(
        model_uuid=MODEL_UUID, application="alloy-sub"
    )


def test_disabling_host_metrics_withdraws_the_host_alert_group(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch)
    harness.update_config({"enable-host-metrics": True})

    harness.update_config({"enable-host-metrics": False})

    assert _published_groups(harness, relation_id) == []


def test_non_leader_does_not_publish_host_alert_rules(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch, leader=False)

    harness.update_config({"enable-host-metrics": True})

    assert "alert_rules" not in harness.get_relation_data(relation_id, harness.charm.app.name)


def test_host_alert_rules_do_not_change_destination_status(monkeypatch):
    harness, _ = _leader_harness(monkeypatch, with_remote_write=False)
    harness.charm.unit.status = ops.ActiveStatus("Alloy service running; config valid; host metrics only")

    harness.update_config({"enable-host-metrics": True})

    assert harness.charm.unit.status == ops.ActiveStatus("Alloy service running; config valid; host metrics only")
```

- [ ] **Step 2: Write the failing ordering and size-budget tests in `tests/unit/test_observability_relation.py`**

Add `from src.charm import RULE_PUBLICATION_VALUE_LIMIT` to the existing `src.charm` import line and `from src.host_alert_rules import build_host_alert_rule_groups`. Place these after `test_duplicate_final_group_name_makes_candidate_unpublishable` (line 1332):

```python
def test_host_alert_group_is_appended_after_principal_groups(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    loki = harness.add_relation("send-loki-logs", "loki")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "metrics", "Metrics")))},
    )

    harness.update_config({"enable-host-metrics": True})

    names = [group["name"] for group in json.loads(harness.get_relation_data(prometheus, "alloy-sub")["alert_rules"])["groups"]]
    assert len(names) == 2
    assert names[-1] == "alloy-sub-host-metrics"
    assert json.loads(harness.get_relation_data(loki, "alloy-sub")["alert_rules"]) == {"groups": []}


def test_host_alert_groups_count_toward_publication_size():
    host_groups = build_host_alert_rule_groups(model_uuid="uuid", application="alloy-sub")

    def _state(expr):
        return {
            0: {
                "prometheus_alert_rules/one": {
                    "backend": "prometheus",
                    "ownership": "uuid/app/prometheus_alert_rules/one",
                    "groups": [{"name": "one", "rules": [{"alert": "Down", "expr": expr}]}],
                }
            }
        }

    def _payload_bytes(state):
        groups, _ = AlloySubCharm._flatten_rule_state(state)
        return len(json.dumps({"groups": groups}, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    state = _state("x" * (RULE_PUBLICATION_VALUE_LIMIT - _payload_bytes(_state(""))))
    assert _payload_bytes(state) == RULE_PUBLICATION_VALUE_LIMIT

    assert AlloySubCharm._rule_state_is_publishable(state)
    assert not AlloySubCharm._rule_state_is_publishable(state, host_groups)
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit/test_remote_write_relation.py tests/unit/test_observability_relation.py -q -k "host_alert or non_leader_does_not_publish_host"`
Expected: the first two remote-write tests fail on an empty group list, the status test passes already (the charm does not yet publish host rules, so it serves as a regression guard), the non-leader test passes already, the ordering test fails on `assert len(names) == 2`, and the size test fails with `TypeError` (unexpected second argument).

- [ ] **Step 4: Wire the charm**

In `src/charm.py`:

1. Add `from .host_alert_rules import build_host_alert_rule_groups` after line 37 and `from host_alert_rules import build_host_alert_rule_groups` after line 67.
2. Change line 16 to `from collections.abc import Mapping, Sequence`.
3. Add the helper after `_host_metrics_enabled` (line 1177):

```python
def _host_alert_rule_groups(self) -> list[dict[str, object]]:
    """Return charm-owned host usage alert groups when host metrics are enabled."""
```

4. Change `_rule_state_is_publishable(relation_state)` to `_rule_state_is_publishable(relation_state, extra_prometheus_groups: Sequence[dict[str, object]] = ())`; inside, size-check `[*prometheus_groups, *extra_prometheus_groups]` in place of the flattened Prometheus list, Loki unchanged.
5. Change `_bounded_existing_rule_state(self, previous_states)` to accept and forward `extra_prometheus_groups` to `_rule_state_is_publishable`.
6. In `_reconcile_rule_groups`, after `self._rule_validator.begin_reconcile()`, set `host_groups = self._host_alert_rule_groups()`; pass `host_groups` to `_bounded_existing_rule_state` and to the in-loop `_rule_state_is_publishable(candidate, host_groups)`; replace the two publish/status lines with:

```python
publish_rule_groups(self, "send-remote-write", [*prometheus_groups, *host_groups])
publish_rule_groups(self, "send-loki-logs", loki_groups)
self._set_rule_destination_status(prometheus_groups, loki_groups)
```

`_set_rule_destination_status` deliberately receives the principal groups only (spec: unit status).

- [ ] **Step 5: Run the full unit suite to verify everything passes**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit -q`
Expected: all tests pass, including the pre-existing `test_send_remote_write_relation_publishes_empty_rule_state_and_standard_metadata` (host metrics off → `{"groups": []}`) and `test_duplicate_final_group_name_makes_candidate_unpublishable` (one-argument call).

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run pyright src`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/charm.py tests/unit/test_remote_write_relation.py tests/unit/test_observability_relation.py
git commit -m "Publish host disk and memory usage alert rules when host metrics are enabled"
```

---

### Task 3: Document the built-in host alarms

**Files:**
- Modify: `README.md:231-247` (Host metrics section), `charmcraft.yaml:81-89` (`enable-host-metrics` description), `docs/charm-architecture.md:11-17` (responsibilities list)
- Test: `tests/unit/test_repo_baseline.py:35-39`

**Interfaces:**
- Consumes: the alert names and thresholds from Task 1.
- Produces: nothing code-facing.

- [ ] **Step 1: Write the failing baseline assertions**

Extend `test_readme_documents_host_metrics`:

```python
    assert "HostDiskUsageWarning" in readme
    assert "HostMemoryUsageCritical" in readme
    assert "send-remote-write" in readme.split("## Host metrics")[1].split("## Validation Flow")[0]
```

And add to `test_charmcraft_declares_the_host_metrics_option`:

```python
    assert "80% warning, 90% critical" in charmcraft
```

- [ ] **Step 2: Run the baseline tests to verify they fail**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit/test_repo_baseline.py -q`
Expected: 2 failed on the new assertions

- [ ] **Step 3: Update the documents**

- `charmcraft.yaml` option description: append one sentence after the "scrapes every 60s" sentence: "When enabled, the leader also publishes host disk and memory usage alert rules (80% warning, 90% critical) over send-remote-write."
- `README.md` Host metrics section: after the existing bullet list, add a "Built-in host alarms" subsection stating that the leader publishes one rule group named `<application>-host-metrics` on `send-remote-write` containing `HostDiskUsageWarning`, `HostDiskUsageCritical`, `HostMemoryUsageWarning`, `HostMemoryUsageCritical`; thresholds 80%/90% sustained for 5m; disk covers all mounted filesystems except pseudo/ephemeral types (list them); scope is every node-exporter series in the model; labels `severity=warning|critical`; both warning and critical fire above 90% (use Alertmanager inhibition to silence the warning); rules are not delivered to Grafana Cloud; disabling the option withdraws them.
- `docs/charm-architecture.md` responsibilities list: add "- publish built-in host disk and memory usage alert rules when host metrics are enabled".

- [ ] **Step 4: Run baseline tests, codespell, and the full suite**

Run: `PYTHONPATH=lib:src uv run pytest tests/unit -q && uv run codespell README.md charmcraft.yaml docs/charm-architecture.md`
Expected: all pass, no spelling findings

- [ ] **Step 5: Commit**

```bash
git add README.md charmcraft.yaml docs/charm-architecture.md tests/unit/test_repo_baseline.py
git commit -m "Document built-in host disk and memory usage alarms"
```

---

## Manual verification (after the three tasks)

Pack and refresh on a model with `send-remote-write` related to Mimir (directly or via `mimir-gateway-vm`), set `juju config alloy-sub enable-host-metrics=true`, then confirm with the Mimir ruler API that a group named `<application>-host-metrics` lists the four alerts and that `juju config alloy-sub enable-host-metrics=false` removes it.
