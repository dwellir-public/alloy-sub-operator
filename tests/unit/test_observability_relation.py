import json
import sys
from pathlib import Path

import pytest
from ops import testing
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.machine_observability import MachineObservabilityPayload, load_machine_observability_payload
from src.principal_context import PrincipalContext


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


def test_machine_observability_payload_rejects_unsupported_schema_version():
    with pytest.raises(ValidationError):
        MachineObservabilityPayload.model_validate(
            {
                "schema_version": 2,
                "systemd_units": ["snap.polkadot.polkadot.service"],
                "metrics_endpoints": [],
                "journal_match_expressions": [],
                "log_files": [],
            }
        )


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
