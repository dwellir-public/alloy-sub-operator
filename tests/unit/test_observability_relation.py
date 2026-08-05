import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from ops import testing
from ops.charm import CharmBase
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charms.dwellir_observability.v0.machine_observability import (
    MachineObservabilityPayload,
    MachineObservabilityProvider,
    SourceTopology,
    build_machine_observability_payload,
    load_machine_observability_payload,
)

from src.principal_context import PrincipalContext


class _ProviderCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.machine_observability_provider = MachineObservabilityProvider(
            self,
            payload_factory=self._build_payload,
        )

    def _build_payload(self):
        return build_machine_observability_payload(
            service_name="snap.polkadot.polkadot.service",
            charm_name=self.meta.name,
        )


def test_principal_context_prefers_attached_principal_unit():
    relation = testing.SubordinateRelation(
        "juju-info",
        remote_app_name="polkadot",
        remote_unit_id=0,
        remote_unit_data={"private-address": "10.0.0.5"},
    )

    context = PrincipalContext.from_relation(relation)

    assert context.application == "polkadot"
    assert context.unit == "polkadot/0"
    assert context.address == "10.0.0.5"


def test_machine_observability_payload_parses_generic_sources():
    payload = MachineObservabilityPayload.model_validate(
        {
            "schema_version": 1,
            "charm_name": "polkadot",
            "systemd_units": ["snap.polkadot.polkadot.service"],
            "journal_match_expressions": [],
            "log_files": [],
            "metrics_endpoints": [
                {
                    "targets": ["localhost:9615"],
                    "path": "/metrics",
                    "scheme": "http",
                }
            ],
        }
    )

    assert payload.schema_version == 1
    assert payload.charm_name == "polkadot"
    assert payload.systemd_units == ["snap.polkadot.polkadot.service"]
    assert payload.metrics_endpoints[0].targets == ["localhost:9615"]
    assert payload.metrics_endpoints[0].path == "/metrics"
    assert payload.metrics_endpoints[0].interval == ""
    assert payload.metrics_endpoints[0].timeout == ""
    assert payload.metrics_endpoints[0].tls == {}


def test_machine_observability_payload_rejects_legacy_keys():
    with pytest.raises(ValidationError):
        MachineObservabilityPayload.model_validate(
            {
                "schema_version": 1,
                "systemd_units": ["snap.polkadot.polkadot.service"],
                "metrics_jobs": [],
                "workload_labels": {"chain_name": "polkadot"},
            }
        )


def test_machine_observability_payload_accepts_v2_with_source_topology():
    payload = MachineObservabilityPayload.model_validate(
        {
            "schema_version": 2,
            "charm_name": "dwellir-observability-reference",
            "source_topology": {
                "model": "alloy-sub-e2e-20260419",
                "model_uuid": "uuid-1",
                "application": "dwellir-observability-reference",
                "unit": "dwellir-observability-reference/0",
                "charm_name": "dwellir-observability-reference",
            },
            "systemd_units": ["dwellir-observability-reference.service"],
            "metrics_endpoints": [],
            "journal_match_expressions": [],
            "log_files": [],
        }
    )

    assert payload.schema_version == 2
    assert payload.source_topology is not None
    assert payload.source_topology.application == "dwellir-observability-reference"


def test_build_machine_observability_payload_returns_v2_when_source_topology_provided():
    payload = build_machine_observability_payload(
        service_name="dwellir-observability-reference.service",
        charm_name="dwellir-observability-reference",
        source_topology=SourceTopology(
            model="alloy-sub-e2e-20260419",
            model_uuid="uuid-1",
            application="dwellir-observability-reference",
            unit="dwellir-observability-reference/0",
            charm_name="dwellir-observability-reference",
        ),
    )

    assert payload.schema_version == 2
    assert payload.source_topology is not None
    assert payload.source_topology.unit == "dwellir-observability-reference/0"


def test_load_machine_observability_payload_reads_remote_app_payload():
    relation = testing.Relation(
        "machine-observability",
        remote_app_name="polkadot",
        remote_app_data={
            "payload": json.dumps(
                {
                    "schema_version": 1,
                    "systemd_units": ["snap.polkadot.polkadot.service"],
                    "metrics_endpoints": [],
                    "journal_match_expressions": [],
                    "log_files": [
                        {
                            "include": ["/var/log/polkadot/*.log"],
                            "exclude": ["/var/log/polkadot/debug.log"],
                            "attributes": {"service": "polkadot"},
                        }
                    ],
                }
            )
        },
    )

    payload = load_machine_observability_payload(relation)

    assert payload.systemd_units == ["snap.polkadot.polkadot.service"]
    assert payload.schema_version == 1
    assert payload.log_files[0].include == ["/var/log/polkadot/*.log"]
    assert payload.log_files[0].attributes == {"service": "polkadot"}


def test_provider_publishes_payload_on_relation_created():
    harness = testing.Harness(
        _ProviderCharm,
        meta="""
name: polkadot
provides:
  machine-observability:
    interface: machine_observability
""",
    )
    harness.begin()
    harness.set_leader(True)

    relation_id = harness.add_relation("machine-observability", "alloy-sub")
    harness.add_relation_unit(relation_id, "alloy-sub/0")

    payload = json.loads(harness.get_relation_data(relation_id, harness.charm.app.name)["payload"])

    assert payload["charm_name"] == "polkadot"
    assert payload["systemd_units"] == ["snap.polkadot.polkadot.service"]


def test_principal_context_omits_juju_charm_when_not_known():
    context = PrincipalContext(
        application="polkadot",
        unit="polkadot/0",
        address="10.0.0.5",
        model="test-model",
        model_uuid="uuid-1",
    )

    assert context.juju_labels() == {
        "juju_model": "test-model",
        "juju_model_uuid": "uuid-1",
        "juju_application": "polkadot",
        "juju_unit": "polkadot/0",
    }


def test_principal_context_can_render_explicit_juju_charm_label():
    context = PrincipalContext(
        application="polkadot",
        unit="polkadot/0",
        address="10.0.0.5",
        model="test-model",
        model_uuid="uuid-1",
    )

    assert context.juju_labels(charm_name="polkadot")["juju_charm"] == "polkadot"


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
