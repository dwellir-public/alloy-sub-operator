# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm

PEER_KEY = "principal-applications"
BLOCKED = "Alloy service running; multiple principal applications: kusama, polkadot; relate alloy-sub to one principal"


def _machine_observability_payload(
    *,
    schema_version=1,
    charm_name="polkadot",
    source_topology=None,
    metrics_endpoints=None,
    systemd_units=None,
    journal_match_expressions=None,
    log_files=None,
    artifacts=None,
):
    return json.dumps(
        {
            "schema_version": schema_version,
            "charm_name": charm_name,
            **({"source_topology": source_topology} if source_topology is not None else {}),
            "systemd_units": systemd_units or [],
            "journal_match_expressions": journal_match_expressions or [],
            "log_files": log_files or [],
            "metrics_endpoints": metrics_endpoints or [],
            **({"artifacts": artifacts} if artifacts is not None else {}),
        }
    )


def _principal_relations(*, juju_info="polkadot", observability="polkadot"):
    relations = []
    if juju_info:
        relations.append(
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name=juju_info,
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            )
        )
    if observability:
        relations.append(
            testing.SubordinateRelation(
                "machine-observability",
                remote_app_name=observability,
                remote_unit_id=0,
                remote_app_data={"payload": _machine_observability_payload()},
            )
        )
    return relations


def _peers(peers_data=None):
    return testing.PeerRelation("alloy-peers", peers_data=peers_data or {})


def _run(state, event=None):
    """Run one hook; return (state_out, write_config mock, stop mock)."""
    ctx = testing.Context(AlloySubCharm)
    if event is None:
        event = ctx.on.config_changed()
    elif callable(event):
        event = event(ctx)
    with (
        patch("charm.alloy.start"),
        patch("charm.alloy.get_version", return_value="1.0.0"),
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text") as write_config,
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.reload"),
        patch("charm.alloy.restart"),
        patch("charm.alloy.verify_config"),
        patch("charm.alloy.stop") as stop,
        patch("charm.alloy.restore_preserved_config"),
    ):
        state_out = ctx.run(event, state)
    return state_out, write_config, stop


def test_unit_publishes_its_principal_to_the_peer_relation():
    peer = _peers()
    state_out, write_config, _ = _run(testing.State(relations=[*_principal_relations(), peer]))

    assert state_out.get_relation(peer.id).local_unit_data[PEER_KEY] == '["polkadot"]'
    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    write_config.assert_called_once()


def test_peer_on_same_principal_does_not_block():
    peer = _peers({1: {PEER_KEY: '["polkadot"]'}})
    state_out, write_config, _ = _run(testing.State(relations=[*_principal_relations(), peer]))

    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    write_config.assert_called_once()


def test_peer_on_other_principal_blocks_and_leaves_alloy_untouched():
    peer = _peers({1: {PEER_KEY: '["kusama"]'}})
    state_out, write_config, stop = _run(testing.State(relations=[*_principal_relations(), peer]))

    assert state_out.unit_status == testing.BlockedStatus(BLOCKED)
    assert state_out.get_relation(peer.id).local_unit_data[PEER_KEY] == '["polkadot"]'
    write_config.assert_not_called()
    stop.assert_not_called()


def test_different_principals_on_local_endpoints_block_without_peer_relation():
    state = testing.State(relations=_principal_relations(juju_info="polkadot", observability="kusama"))
    state_out, write_config, _ = _run(state)

    assert state_out.unit_status == testing.BlockedStatus(BLOCKED)
    write_config.assert_not_called()


def test_no_peer_relation_configures_normally():
    state_out, write_config, _ = _run(testing.State(relations=_principal_relations()))

    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    write_config.assert_called_once()


@pytest.mark.parametrize("raw", ["not json", "{}", "[1]", ""])
def test_malformed_peer_value_is_ignored(raw):
    peer = _peers({1: {PEER_KEY: raw}})
    state_out, _, _ = _run(testing.State(relations=[*_principal_relations(), peer]))

    assert isinstance(state_out.unit_status, testing.ActiveStatus)


def test_unit_recovers_when_conflicting_peer_departs():
    peer = _peers({1: {PEER_KEY: '["kusama"]'}})
    blocked, _, _ = _run(testing.State(relations=[*_principal_relations(), peer]))
    assert isinstance(blocked.unit_status, testing.BlockedStatus)

    peer_after = dataclasses.replace(blocked.get_relation(peer.id), peers_data={})
    relations = [r for r in blocked.relations if r.id != peer.id] + [peer_after]
    recovered, write_config, _ = _run(
        dataclasses.replace(blocked, relations=relations),
        event=lambda ctx: ctx.on.relation_departed(peer_after, remote_unit=1),
    )

    assert isinstance(recovered.unit_status, testing.ActiveStatus)
    write_config.assert_called_once()


def test_peer_change_event_triggers_the_check():
    peer = _peers({1: {PEER_KEY: '["kusama"]'}})
    state_out, _, _ = _run(
        testing.State(relations=[*_principal_relations(), peer]),
        event=lambda ctx: ctx.on.relation_changed(peer, remote_unit=1),
    )

    assert state_out.unit_status == testing.BlockedStatus(BLOCKED)
