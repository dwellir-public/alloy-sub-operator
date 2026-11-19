# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""The charm refuses to touch an Alloy installation this unit does not own."""

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from charms.dwellir_observability.v0.machine_observability import encode_artifact
from ops import testing

import alloy_ownership
from charm import AlloySubCharm
from custom_args import DEFAULT_LISTEN_ADDRESS

pytestmark = pytest.mark.real_ownership

MUTATING = (
    "install",
    "start",
    "stop",
    "restart",
    "reload",
    "write_config_text",
    "write_custom_args",
    "preserve_default_config",
    "restore_preserved_config",
)
_SIDE_EFFECT_FREE = ("ensure_config_dir_permissions", "verify_config")
_RETURNS = {"get_version": "1.0.0", "is_active": True, "custom_args_applied": True}
OTHER = "Alloy on this machine is managed by other-sub/3; remove this unit's principal relation"
UNMANAGED = "unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy"


def _payload():
    return json.dumps(
        {
            "schema_version": 1,
            "charm_name": "polkadot",
            "systemd_units": [],
            "journal_match_expressions": [],
            "log_files": [],
            "metrics_endpoints": [],
        }
    )


def _relations():
    return [
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
            remote_app_data={"payload": _payload()},
        ),
    ]


def _run(event, state, *, installation, install_error=None):
    """Run one hook with every Alloy call mocked; return (state_out, mocks by name)."""
    ctx = testing.Context(AlloySubCharm)
    resolved = event(ctx) if callable(event) else getattr(ctx.on, event)()
    mocks = {}
    with ExitStack() as stack:
        stack.enter_context(patch("charm.alloy.installation", return_value=installation))
        for name in (*MUTATING, *_SIDE_EFFECT_FREE):
            mocks[name] = stack.enter_context(patch(f"charm.alloy.{name}", new=MagicMock()))
        for name, value in _RETURNS.items():
            mocks[name] = stack.enter_context(patch(f"charm.alloy.{name}", return_value=value))
        mocks["install"].side_effect = install_error
        state_out = ctx.run(resolved, state)
    return state_out, mocks


def _stored(**content):
    return testing.StoredState(owner_path="AlloySubCharm", content=content)


def _installed_here():
    """Return the stored state of a unit that ran this revision's install hook."""
    return _stored(install_hook_seen=True)


def _legacy():
    """Return the stored state of a pre-marker unit that already applied its custom args."""
    return _stored(last_custom_args=DEFAULT_LISTEN_ADDRESS)


def _assert_untouched(mocks):
    for name in MUTATING:
        mocks[name].assert_not_called()


def test_install_claims_and_installs_on_a_clean_machine(alloy_marker_dir):
    state_out, mocks = _run("install", testing.State(), installation="none")

    mocks["install"].assert_called_once()
    assert alloy_ownership.current_owner() == "alloy-sub/0"
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


@pytest.mark.parametrize("event_name", ["install", "start", "config_changed", "update_status", "upgrade_charm"])
def test_unit_blocks_and_touches_nothing_when_another_unit_owns_alloy(alloy_marker_dir, event_name):
    alloy_ownership.claim("other-sub/3", installed=False)
    state_out, mocks = _run(event_name, testing.State(relations=_relations()), installation="package")

    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)
    assert alloy_ownership.current_owner() == "other-sub/3"


@pytest.mark.parametrize("event_name", ["install", "start", "config_changed", "update_status"])
@pytest.mark.parametrize("installation", ["package", "other"])
def test_unit_blocks_on_unmanaged_alloy(alloy_marker_dir, event_name, installation):
    state = testing.State(relations=_relations(), stored_states=[_installed_here()])
    state_out, mocks = _run(event_name, state, installation=installation)

    assert state_out.unit_status == testing.BlockedStatus(UNMANAGED)
    _assert_untouched(mocks)
    assert alloy_ownership.current_owner() is None


def test_relation_event_on_blocked_unit_touches_nothing(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=False)
    relations = _relations()
    state_out, mocks = _run(
        lambda ctx: ctx.on.relation_changed(relations[1]),
        testing.State(relations=relations),
        installation="package",
    )

    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)


@pytest.mark.parametrize("event_name", ["upgrade_charm", "config_changed", "update_status"])
def test_legacy_unit_claims_its_running_alloy_on_its_first_hook(alloy_marker_dir, event_name):
    state = testing.State(relations=_relations(), stored_states=[_legacy()])
    state_out, mocks = _run(event_name, state, installation="package")

    assert alloy_ownership.current_owner() == "alloy-sub/0"
    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    mocks["install"].assert_not_called()
    mocks["stop"].assert_not_called()
    mocks["restart"].assert_not_called()


def test_legacy_unit_with_a_deferred_event_still_claims_on_upgrade(alloy_marker_dir):
    # ops re-emits deferred events before upgrade-charm in the same dispatch.
    relations = _relations()
    ctx = testing.Context(AlloySubCharm)
    deferred = ctx.on.relation_changed(relations[1]).deferred(handler=AlloySubCharm._on_relation_event)
    state = testing.State(relations=relations, stored_states=[_legacy()], deferred=[deferred])
    state_out, mocks = _run("upgrade_charm", state, installation="package")

    assert alloy_ownership.current_owner() == "alloy-sub/0"
    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    mocks["install"].assert_not_called()
    mocks["restart"].assert_not_called()


def test_legacy_unit_keeps_its_allowance_after_a_failed_claim(alloy_marker_dir):
    state = testing.State(relations=_relations(), stored_states=[_legacy()])
    with patch("charm.alloy_ownership.claim", side_effect=OSError("read-only file system")):
        failed, mocks = _run("upgrade_charm", state, installation="package")

    assert failed.unit_status == testing.BlockedStatus("Alloy ownership check failed: read-only file system")
    _assert_untouched(mocks)

    state_out, mocks = _run("update_status", failed, installation="package")

    assert alloy_ownership.current_owner() == "alloy-sub/0"
    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    mocks["install"].assert_not_called()
    mocks["restart"].assert_not_called()


def test_second_legacy_unit_on_the_machine_blocks_on_upgrade(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=True, allow_existing=True)
    state_out, mocks = _run("upgrade_charm", testing.State(relations=_relations()), installation="package")

    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)


@pytest.mark.parametrize("event_name", ["upgrade_charm", "config_changed", "update_status"])
def test_unit_that_blocked_as_unmanaged_does_not_auto_claim_later(alloy_marker_dir, event_name):
    blocked, _ = _run("install", testing.State(relations=_relations()), installation="package")
    state_out, mocks = _run(event_name, blocked, installation="package")

    assert state_out.unit_status == testing.BlockedStatus(UNMANAGED)
    _assert_untouched(mocks)
    assert alloy_ownership.current_owner() is None


def test_unit_whose_install_claim_failed_does_not_auto_claim_later(alloy_marker_dir):
    with patch("charm.alloy_ownership.claim", side_effect=OSError("read-only file system")):
        failed, _ = _run("install", testing.State(relations=_relations()), installation="package")
    assert alloy_ownership.current_owner() is None

    state_out, mocks = _run("update_status", failed, installation="package")

    assert state_out.unit_status == testing.BlockedStatus(UNMANAGED)
    _assert_untouched(mocks)
    assert alloy_ownership.current_owner() is None


def test_blocked_unit_installs_once_the_owner_releases(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=False)
    blocked, _ = _run("install", testing.State(relations=_relations()), installation="none")
    alloy_ownership.release("other-sub/3")

    state_out, mocks = _run("update_status", blocked, installation="none")

    mocks["install"].assert_called_once()
    assert alloy_ownership.current_owner() == "alloy-sub/0"
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_owner_stop_stops_alloy_and_releases(alloy_marker_dir):
    owned, _ = _run("install", testing.State(), installation="none")
    _, mocks = _run("stop", owned, installation="package")

    mocks["stop"].assert_called_once()
    assert alloy_ownership.current_owner() is None


def test_non_owner_stop_leaves_alloy_and_marker_alone(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=False)
    _, mocks = _run("stop", testing.State(), installation="package")

    mocks["stop"].assert_not_called()
    assert alloy_ownership.current_owner() == "other-sub/3"


def test_claim_failure_blocks_without_failing_the_hook(alloy_marker_dir):
    with patch("charm.alloy_ownership.claim", side_effect=OSError("read-only file system")):
        state_out, mocks = _run("config_changed", testing.State(relations=_relations()), installation="package")

    assert state_out.unit_status == testing.BlockedStatus("Alloy ownership check failed: read-only file system")
    _assert_untouched(mocks)


def test_install_failure_blocks_defers_keeps_the_marker_and_retries(alloy_marker_dir):
    failed, mocks = _run(
        "install", testing.State(relations=_relations()), installation="none", install_error=OSError("apt down")
    )

    assert failed.unit_status == testing.BlockedStatus("Installation failed: apt down")
    assert [deferred.name for deferred in failed.deferred] == ["install"]
    assert alloy_ownership.current_owner() == "alloy-sub/0"
    mocks["start"].assert_not_called()

    state_out, mocks = _run("update_status", failed, installation="none")

    mocks["install"].assert_called_once()
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_late_install_failure_blocks_without_failing_or_deferring_the_hook(alloy_marker_dir):
    state_out, mocks = _run(
        "update_status", testing.State(relations=_relations()), installation="none", install_error=OSError("apt down")
    )

    assert state_out.unit_status == testing.BlockedStatus("Installation failed: apt down")
    assert state_out.deferred == []
    assert alloy_ownership.current_owner() == "alloy-sub/0"
    mocks["start"].assert_not_called()
    mocks["write_config_text"].assert_not_called()


# Copied from tests/unit/test_observability_relation.py.
def _rule_artifact(artifact_type, artifact_id, group_name):
    return encode_artifact(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        content=json.dumps(
            {"groups": [{"name": group_name, "rules": [{"alert": "Down", "expr": "up{%%juju_topology%%} == 0"}]}]}
        ).encode(),
    ).model_dump()


def _v3_payload(*artifacts):
    return {
        "schema_version": 3,
        "source_topology": {
            "model": "principal-model",
            "model_uuid": "principal-uuid",
            "application": "polkadot",
            "unit": "polkadot/0",
            "charm_name": "polkadot",
        },
        "metrics_endpoints": [],
        "systemd_units": [],
        "journal_match_expressions": [],
        "log_files": [],
        "artifacts": list(artifacts),
    }


def test_blocked_leader_still_publishes_alert_rules(alloy_marker_dir, monkeypatch):
    monkeypatch.setattr(AlloySubCharm, "_validate_artifact_rules", lambda *_args, **_kwargs: True)
    alloy_ownership.claim("other-sub/3", installed=False)
    payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "metrics", "Metrics"))
    remote_write = testing.Relation("send-remote-write", remote_app_name="mimir")
    relations = [
        testing.SubordinateRelation(
            "machine-observability",
            remote_app_name="polkadot",
            remote_unit_id=0,
            remote_app_data={"payload": json.dumps(payload)},
        ),
        remote_write,
    ]
    state_out, mocks = _run("update_status", testing.State(leader=True, relations=relations), installation="package")

    published = json.loads(state_out.get_relation(remote_write.id).local_app_data["alert_rules"])
    assert len(published["groups"]) == 1
    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)
