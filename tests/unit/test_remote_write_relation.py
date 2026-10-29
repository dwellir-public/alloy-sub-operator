import json
import sys
from pathlib import Path

import ops
from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm
from host_alert_rules import build_host_alert_rule_groups

MODEL_NAME = "polka-obs"
MODEL_UUID = "00000000-0000-4000-8000-000000000222"


def test_send_remote_write_relation_publishes_empty_rule_state_and_standard_metadata():
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.set_model_name(MODEL_NAME)
    harness.set_model_uuid(MODEL_UUID)
    harness.begin()

    juju_info = harness.add_relation("juju-info", "polkadot")
    harness.add_relation_unit(juju_info, "polkadot/0")
    harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})

    relation_id = harness.add_relation("send-remote-write", "mimir-gateway-vm")
    harness.add_relation_unit(relation_id, "mimir-gateway-vm/0")

    relation_data = harness.get_relation_data(relation_id, harness.charm.app.name)

    assert json.loads(relation_data["alert_rules"]) == {"groups": []}
    assert json.loads(relation_data["metadata"]) == {
        "application": "alloy-sub",
        "model": MODEL_NAME,
        "model_uuid": MODEL_UUID,
        "unit": "alloy-sub/0",
    }
    assert "tenant" not in relation_data


def _leader_harness(monkeypatch, *, leader=True, with_remote_write=True, with_principal=True):
    monkeypatch.setattr(AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(leader)
    harness.set_model_name(MODEL_NAME)
    harness.set_model_uuid(MODEL_UUID)
    harness.begin()
    if with_principal:
        juju_info = harness.add_relation("juju-info", "polkadot")
        harness.add_relation_unit(juju_info, "polkadot/0")
        harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})
    relation_id = harness.add_relation("send-remote-write", "mimir-gateway-vm") if with_remote_write else None
    return harness, relation_id


def _published_groups(harness, relation_id):
    return json.loads(harness.get_relation_data(relation_id, harness.charm.app.name)["alert_rules"])["groups"]


def test_enabling_host_metrics_publishes_the_host_alert_group(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch)

    harness.update_config({"enable-host-metrics": True})

    assert _published_groups(harness, relation_id) == build_host_alert_rule_groups(
        model_uuid=MODEL_UUID, principal_application="polkadot"
    )


def test_host_alert_rules_are_not_published_without_a_principal(monkeypatch):
    """Without a principal the rules cannot be scoped, and no host metrics are rendered either."""
    harness, relation_id = _leader_harness(monkeypatch, with_principal=False)

    harness.update_config({"enable-host-metrics": True})

    assert _published_groups(harness, relation_id) == []


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
