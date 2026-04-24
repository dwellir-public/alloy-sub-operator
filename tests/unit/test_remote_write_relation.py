import sys
from pathlib import Path

from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm

MODEL_NAME = "polka-obs"
MODEL_UUID = "00000000-0000-4000-8000-000000000222"

def test_send_remote_write_relation_does_not_publish_tenant_metadata():
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

    assert relation_data == {}
