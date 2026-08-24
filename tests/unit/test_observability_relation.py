import base64
import json
import logging
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from ops import testing
from ops.charm import CharmBase
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charms.dwellir_observability.v0.machine_observability import (
    MAX_SERIALIZED_PAYLOAD_BYTES,
    MachineObservabilityPayload,
    MachineObservabilityProvider,
    PayloadTooLargeError,
    SourceTopology,
    build_machine_observability_payload,
    encode_artifact,
    load_machine_observability_payload,
)

from src.charm import AlloySubCharm
from src.principal_context import PrincipalContext


@pytest.fixture(autouse=True)
def _accept_backend_rules(monkeypatch):
    monkeypatch.setattr(AlloySubCharm, "_validate_artifact_rules", lambda *_args, **_kwargs: True)


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


def test_consumer_payload_size_allows_exact_limit_and_rejects_one_extra_byte():
    base_payload = '{"schema_version":1}'
    exact_payload = base_payload + " " * (MAX_SERIALIZED_PAYLOAD_BYTES - len(base_payload))
    exact_relation = testing.Relation(
        "machine-observability",
        remote_app_name="polkadot",
        remote_app_data={"payload": exact_payload},
    )
    oversized_relation = testing.Relation(
        "machine-observability",
        remote_app_name="polkadot",
        remote_app_data={"payload": exact_payload + " "},
    )

    assert len(exact_payload.encode("utf-8")) == MAX_SERIALIZED_PAYLOAD_BYTES
    assert load_machine_observability_payload(exact_relation).schema_version == 1
    with pytest.raises(PayloadTooLargeError):
        load_machine_observability_payload(oversized_relation)


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


def _rule_artifact(artifact_type, artifact_id, group_name, *, checksum_valid=True):
    artifact = encode_artifact(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        content=json.dumps(
            {
                "groups": [
                    {
                        "name": group_name,
                        "rules": [{"alert": "Down", "expr": "up{%%juju_topology%%} == 0"}],
                    }
                ]
            }
        ).encode(),
    ).model_dump()
    if not checksum_valid:
        artifact["sha256"] = "0" * 64
    return artifact


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


def test_machine_rules_publish_to_matching_standard_backends_and_withdraw(monkeypatch):
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
        {
            "payload": json.dumps(
                _v3_payload(
                    _rule_artifact("prometheus_alert_rules", "metrics", "Metrics"),
                    _rule_artifact("loki_alert_rules", "logs", "Logs"),
                )
            )
        },
    )

    prometheus_groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])[
        "groups"
    ]
    loki_groups = json.loads(harness.get_relation_data(loki, harness.charm.app.name)["alert_rules"])["groups"]
    assert [group["name"] for group in prometheus_groups] == sorted(group["name"] for group in prometheus_groups)
    assert len(prometheus_groups) == 1
    assert len(loki_groups) == 1

    harness.remove_relation(machine)

    assert json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]) == {"groups": []}
    assert json.loads(harness.get_relation_data(loki, harness.charm.app.name)["alert_rules"]) == {"groups": []}


@pytest.mark.parametrize(
    ("artifact_type", "destination_relation"),
    [
        ("prometheus_alert_rules", "send-remote-write"),
        ("loki_alert_rules", "send-loki-logs"),
    ],
)
def test_cached_rules_wait_for_destination_and_publish_when_it_joins(
    monkeypatch,
    artifact_type,
    destination_relation,
):
    import src.charm as charm_module

    def configure(charm, **_kwargs):
        charm.unit.status = testing.ActiveStatus("configured")
        return True

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", configure)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact(artifact_type, "rules", "Rules")))},
    )

    assert harness.model.unit.status == testing.WaitingStatus(
        f"Alloy service down; config valid; waiting for {destination_relation} relation"
    )
    cached = harness.get_relation_data(machine, harness.charm.app.name)[charm_module.RULE_CACHE_KEY]

    destination = harness.add_relation(destination_relation, "backend")
    harness.add_relation_unit(destination, "backend/0")

    published = json.loads(harness.get_relation_data(destination, harness.charm.app.name)["alert_rules"])
    assert len(published["groups"]) == 1
    assert harness.model.unit.status == testing.ActiveStatus("configured")

    harness.remove_relation(destination)

    assert harness.model.unit.status == testing.WaitingStatus(
        f"Alloy service down; config valid; waiting for {destination_relation} relation"
    )
    assert harness.get_relation_data(machine, harness.charm.app.name)[charm_module.RULE_CACHE_KEY] == cached


def test_config_changed_reconciles_rules_after_configure(monkeypatch):
    calls = []

    monkeypatch.setattr(
        AlloySubCharm,
        "_configure",
        lambda *_args, **_kwargs: calls.append("configure") or True,
    )
    monkeypatch.setattr(
        AlloySubCharm,
        "_reconcile_rule_groups",
        lambda *_args, **_kwargs: calls.append("rules"),
    )
    harness = testing.Harness(AlloySubCharm)
    harness.begin()
    calls.clear()

    harness.update_config({"global_scrape_interval": "30s"})

    assert calls == ["configure", "rules"]


def test_config_changed_rule_reconcile_preserves_blocked_config_status(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(
        charm_module.AlloySubCharm,
        "_configure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("broken config")),
    )
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine_id = harness.add_relation("machine-observability", "polkadot")
    machine = harness.model.get_relation("machine-observability", machine_id)
    state = {
        "prometheus_alert_rules/owned": {
            "backend": "prometheus",
            "ownership": "principal-uuid/polkadot/prometheus_alert_rules/owned",
            "groups": [{"name": "rules", "rules": [{"alert": "Down", "expr": "up == 0"}]}],
        }
    }
    machine.data[harness.charm.app][charm_module.RULE_CACHE_KEY] = harness.charm._encode_rule_cache(state)
    machine.data[machine.app]["payload"] = "invalid-current"

    harness.update_config({"global_scrape_interval": "30s"})

    assert harness.model.unit.status == testing.BlockedStatus("Alloy service down; config invalid: broken config")


def test_bad_artifact_does_not_block_valid_sibling_and_fixed_payload_converges(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    good = _rule_artifact("prometheus_alert_rules", "good", "Good v1")
    sibling = _rule_artifact("prometheus_alert_rules", "sibling", "Sibling v1")

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(good, sibling))},
    )

    bad = _rule_artifact("prometheus_alert_rules", "good", "Sensitive replacement", checksum_valid=False)
    updated_sibling = _rule_artifact("prometheus_alert_rules", "sibling", "Sibling v2")

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(bad, updated_sibling))},
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2
    assert any(group["name"].endswith("good-Good-v1") for group in groups)
    assert any(group["name"].endswith("sibling-Sibling-v2") for group in groups)
    assert "prometheus_alert_rules/good: checksum" in caplog.text
    assert "Sensitive replacement" not in caplog.text

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "Good v2")))},
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-Good-v2")
    assert "checksum" not in harness.charm.unit.status.message


def test_unidentifiable_artifact_does_not_block_valid_sibling_update(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "sibling", "V1")))},
    )
    payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "sibling", "V2"))
    payload["artifacts"].append(None)

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(payload)})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("sibling-V2")


def test_semantically_invalid_rule_retains_artifact_lkg(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    invalid = encode_artifact(
        artifact_type="prometheus_alert_rules",
        artifact_id="good",
        content=json.dumps(
            {"groups": [{"name": "INVALID", "rules": [{"alert": "Down", "record": "down", "expr": "up"}]}]}
        ).encode(),
    ).model_dump()

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(_v3_payload(invalid))})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-LAST-GOOD")


@pytest.mark.parametrize("artifact_type", ["prometheus_alert_rules", "loki_alert_rules"])
def test_backend_invalid_artifact_retains_only_its_lkg_and_valid_sibling_updates(monkeypatch, caplog, artifact_type):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)

    def validate(_self, _kind, groups):
        return all("INVALID-BACKEND" not in rule["expr"] for group in groups for rule in group["rules"])

    monkeypatch.setattr(charm_module.AlloySubCharm, "_validate_artifact_rules", validate)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    backend_relation = harness.add_relation(
        "send-remote-write" if artifact_type == "prometheus_alert_rules" else "send-loki-logs",
        "backend",
    )
    harness.update_relation_data(
        machine,
        "polkadot",
        {
            "payload": json.dumps(
                _v3_payload(
                    _rule_artifact(artifact_type, "owned", "LAST-GOOD"),
                    _rule_artifact(artifact_type, "sibling", "SIBLING-V1"),
                )
            )
        },
    )
    invalid = encode_artifact(
        artifact_type=artifact_type,
        artifact_id="owned",
        content=json.dumps(
            {"groups": [{"name": "INVALID", "rules": [{"alert": "Down", "expr": "INVALID-BACKEND"}]}]}
        ).encode(),
    ).model_dump()

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(invalid, _rule_artifact(artifact_type, "sibling", "SIBLING-V2")))},
    )

    groups = json.loads(harness.get_relation_data(backend_relation, harness.charm.app.name)["alert_rules"])["groups"]
    assert any(group["name"].endswith("owned-LAST-GOOD") for group in groups)
    assert any(group["name"].endswith("sibling-SIBLING-V2") for group in groups)
    assert f"{artifact_type}/owned: validation" in caplog.text
    assert "INVALID-BACKEND" not in caplog.text


def test_malformed_outer_payload_retains_rules_and_v2_withdraws_them(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    rule = _rule_artifact("prometheus_alert_rules", "good", "Good")
    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(_v3_payload(rule))})

    harness.update_relation_data(machine, "polkadot", {"payload": "not-json-secret"})

    assert len(json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]) == 1
    assert "not-json-secret" not in caplog.text

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps({"schema_version": 99, "artifacts": []})},
    )
    assert len(json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]) == 1

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps({"schema_version": 2, "artifacts": [rule]})},
    )
    assert len(json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]) == 1

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps({"schema_version": 2, "source_topology": _v3_payload()["source_topology"]})},
    )
    assert json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]) == {"groups": []}


def test_oversized_payload_retains_rule_lkg_and_next_valid_payload_converges(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "V1")))},
    )
    oversized = json.dumps(_v3_payload())
    oversized += " " * (MAX_SERIALIZED_PAYLOAD_BYTES + 1 - len(oversized.encode("utf-8")))

    harness.update_relation_data(machine, "polkadot", {"payload": oversized})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-V1")

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "V2")))},
    )
    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-V2")


@pytest.mark.parametrize(
    "invalid_payload",
    [
        "not-json",
        json.dumps({"schema_version": 99, "artifacts": []}),
    ],
)
def test_shared_relation_cache_survives_new_leader_with_invalid_current_payload(monkeypatch, invalid_payload):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    first = testing.Harness(AlloySubCharm)
    first.set_leader(True)
    first.begin()
    machine = first.add_relation("machine-observability", "polkadot")
    first.add_relation("send-remote-write", "mimir")
    first.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    shared_cache = first.get_relation_data(machine, first.charm.app.name)[charm_module.RULE_CACHE_KEY]

    replacement = testing.Harness(AlloySubCharm)
    replacement.begin()
    replacement_machine = replacement.add_relation("machine-observability", "polkadot")
    prometheus = replacement.add_relation("send-remote-write", "mimir")
    replacement.update_relation_data(
        replacement_machine,
        replacement.charm.app.name,
        {charm_module.RULE_CACHE_KEY: shared_cache},
    )
    replacement.update_relation_data(
        replacement_machine,
        "polkadot",
        {"payload": invalid_payload},
    )

    replacement.set_leader(True)

    groups = json.loads(replacement.get_relation_data(prometheus, replacement.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-LAST-GOOD")


def test_corrupt_shared_relation_cache_fails_closed_without_logging_content(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    attacker = {
        "prometheus_alert_rules/owned": {
            "backend": "prometheus",
            "ownership": "principal-uuid/polkadot/prometheus_alert_rules/owned",
            "groups": [
                {
                    "name": "apparently-valid",
                    "rules": [
                        {
                            "alert": "Down",
                            "expr": "up == 0",
                            "UNIQUE-CACHE-MARKER": {"attacker": "structure"},
                        }
                    ],
                }
            ],
        }
    }
    encoded = base64.b64encode(zlib.compress(json.dumps(attacker, separators=(",", ":")).encode())).decode()
    harness = testing.Harness(AlloySubCharm)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        harness.charm.app.name,
        {charm_module.RULE_CACHE_KEY: f"v1:{encoded}"},
    )
    harness.update_relation_data(machine, "polkadot", {"payload": "invalid-current-secret"})
    caplog.clear()

    harness.set_leader(True)

    assert json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]) == {"groups": []}
    assert "UNIQUE-CACHE-MARKER" not in caplog.text
    assert "invalid-current-secret" not in caplog.text


def test_backend_validated_shared_cache_loads_without_subprocess_validation(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        charm_module.AlloySubCharm,
        "_validate_artifact_rules",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache must not invoke cos-tool")),
    )
    state = {
        "prometheus_alert_rules/owned": {
            "backend": "prometheus",
            "ownership": "principal-uuid/polkadot/prometheus_alert_rules/owned",
            "groups": [{"name": "rules", "rules": [{"alert": "Down", "expr": "up == 0"}]}],
        }
    }
    harness = testing.Harness(AlloySubCharm)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        harness.charm.app.name,
        {charm_module.RULE_CACHE_KEY: harness.charm._encode_rule_cache(state)},
    )
    harness.update_relation_data(machine, "polkadot", {"payload": "invalid-current-secret"})
    caplog.clear()

    harness.set_leader(True)

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert groups == state["prometheus_alert_rules/owned"]["groups"]
    assert "cache-validation" not in caplog.text
    assert "invalid-current-secret" not in caplog.text


def test_multiple_machine_relations_share_and_reset_one_validator_budget(monkeypatch):
    import src.charm as charm_module

    class BudgetValidator:
        def __init__(self):
            self.calls_per_reconcile = []

        def begin_reconcile(self):
            self.calls_per_reconcile.append(0)

        def __call__(self, _artifact_type, _groups):
            if self.calls_per_reconcile[-1] >= 32:
                return False
            self.calls_per_reconcile[-1] += 1
            return True

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        charm_module.AlloySubCharm,
        "_validate_artifact_rules",
        lambda self, artifact_type, groups: self._rule_validator(artifact_type, groups),
    )
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "first")
    second = harness.add_relation("machine-observability", "second")
    first_app = harness.model.get_relation("machine-observability", first).app
    second_app = harness.model.get_relation("machine-observability", second).app
    first_payload = _v3_payload(
        *[_rule_artifact("prometheus_alert_rules", f"first-{index:02d}", f"First {index}") for index in range(20)]
    )
    second_payload = _v3_payload(
        *[_rule_artifact("prometheus_alert_rules", f"second-{index:02d}", f"Second {index}") for index in range(20)]
    )
    first_payload["source_topology"]["application"] = "first"
    second_payload["source_topology"]["application"] = "second"
    harness.model.get_relation("machine-observability", first).data[first_app]["payload"] = json.dumps(first_payload)
    harness.model.get_relation("machine-observability", second).data[second_app]["payload"] = json.dumps(second_payload)
    validator = BudgetValidator()
    harness.charm._rule_validator = validator

    harness.charm._reconcile_rule_groups(SimpleNamespace())
    harness.charm._reconcile_rule_groups(SimpleNamespace())

    assert validator.calls_per_reconcile == [32, 32]


def test_artifact_beyond_exhausted_limit_retains_its_lkg(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "z-last-good", "LAST-GOOD")))},
    )
    candidates = [
        _rule_artifact("prometheus_alert_rules", f"a-{index:02d}", f"Candidate {index}") for index in range(32)
    ]
    candidates.append(_rule_artifact("prometheus_alert_rules", "z-last-good", "REPLACEMENT"))

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(_v3_payload(*candidates))})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 33
    assert any(group["name"].endswith("z-last-good-LAST-GOOD") for group in groups)
    assert not any(group["name"].endswith("z-last-good-REPLACEMENT") for group in groups)


def test_later_relation_retains_lkg_after_shared_validator_budget_is_exhausted(monkeypatch):
    import src.charm as charm_module

    class BudgetValidator:
        def begin_reconcile(self):
            self.remaining = 32

        def __call__(self, _artifact_type, _groups):
            if self.remaining == 0:
                return False
            self.remaining -= 1
            return True

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        charm_module.AlloySubCharm,
        "_validate_artifact_rules",
        lambda self, artifact_type, groups: self._rule_validator(artifact_type, groups),
    )
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "first")
    later = harness.add_relation("machine-observability", "later")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    first_relation = harness.model.get_relation("machine-observability", first)
    later_relation = harness.model.get_relation("machine-observability", later)
    first_payload = _v3_payload(
        *[_rule_artifact("prometheus_alert_rules", f"first-{index:02d}", f"First {index}") for index in range(32)]
    )
    first_payload["source_topology"]["application"] = "first"
    later_payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "owned", "REPLACEMENT"))
    later_payload["source_topology"]["application"] = "later"
    first_relation.data[first_relation.app]["payload"] = json.dumps(first_payload)
    later_relation.data[later_relation.app]["payload"] = json.dumps(later_payload)
    lkg = {
        "prometheus_alert_rules/owned": {
            "backend": "prometheus",
            "ownership": "principal-uuid/later/prometheus_alert_rules/owned",
            "groups": [{"name": "last-good", "rules": [{"alert": "Down", "expr": "up == 0"}]}],
        }
    }
    later_relation.data[harness.charm.app][charm_module.RULE_CACHE_KEY] = harness.charm._encode_rule_cache(lkg)
    harness.charm._rule_validator = BudgetValidator()

    harness.charm._reconcile_rule_groups(SimpleNamespace())

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 33
    assert any(group["name"] == "last-good" for group in groups)
    assert not any(group["name"].endswith("owned-REPLACEMENT") for group in groups)


@pytest.mark.parametrize(
    "cache_json",
    [
        "[" * 10_000 + "0" + "]" * 10_000,
        json.dumps({str(index): {} for index in range(20_000)}, separators=(",", ":")),
        json.dumps({"nested": [{"children": [{}] * 1000}] * 100}, separators=(",", ":")),
    ],
    ids=("deep", "broad", "many-nodes"),
)
def test_adversarial_cache_structure_fails_closed_and_reconcile_continues(monkeypatch, caplog, cache_json):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    encoded = base64.b64encode(zlib.compress(cache_json.encode())).decode()
    harness = testing.Harness(AlloySubCharm)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        harness.charm.app.name,
        {charm_module.RULE_CACHE_KEY: f"v1:{encoded}"},
    )
    harness.update_relation_data(machine, "polkadot", {"payload": "invalid-current-secret"})

    harness.set_leader(True)

    assert json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]) == {"groups": []}
    assert "cache-validation" in caplog.text
    assert "invalid-current-secret" not in caplog.text


def test_relation_cache_size_failure_retains_prior_durable_state(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    monkeypatch.setattr(charm_module, "RULE_CACHE_VALUE_LIMIT", 20)

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "TOO-LARGE")))},
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert groups[0]["name"].endswith("good-LAST-GOOD")
    assert "TOO-LARGE" not in caplog.text
    assert "cache-size" in caplog.text


def test_relation_cache_decoded_size_failure_retains_prior_durable_state(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    monkeypatch.setattr(charm_module, "RULE_CACHE_DECODED_LIMIT", 200)

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "COMPRESSIBLE")))},
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert groups[0]["name"].endswith("good-LAST-GOOD")
    assert "COMPRESSIBLE" not in caplog.text
    assert "cache-size" in caplog.text


def test_v3_empty_artifacts_without_topology_withdraws_relation_lkg(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps({"schema_version": 3, "artifacts": []})},
    )

    assert json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]) == {"groups": []}


def test_downstream_value_overflow_retains_prior_durable_state(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    prior_payload = harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]
    prior_cache = harness.get_relation_data(machine, harness.charm.app.name)[charm_module.RULE_CACHE_KEY]
    monkeypatch.setattr(charm_module, "RULE_PUBLICATION_VALUE_LIMIT", len(prior_payload.encode()) + 50)

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "X" * 2000)))},
    )

    assert harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"] == prior_payload
    assert harness.get_relation_data(machine, harness.charm.app.name)[charm_module.RULE_CACHE_KEY] == prior_cache
    assert "publish-size" in caplog.text
    assert "X" * 2000 not in caplog.text


def test_multi_relation_aggregate_overflow_admits_states_deterministically(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "polkadot")
    second = harness.add_relation("machine-observability", "kusama")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        first,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "first", "FIRST")))},
    )
    first_payload = harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"]
    monkeypatch.setattr(charm_module, "RULE_PUBLICATION_VALUE_LIMIT", len(first_payload.encode()) + 50)
    second_payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "second", "SECOND"))
    second_payload["source_topology"] = {
        **second_payload["source_topology"],
        "model_uuid": "second-uuid",
        "application": "kusama",
        "unit": "kusama/0",
    }

    harness.update_relation_data(second, "kusama", {"payload": json.dumps(second_payload)})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("first-FIRST")
    second_cache = harness.get_relation_data(second, harness.charm.app.name)[charm_module.RULE_CACHE_KEY]
    assert harness.charm._decode_rule_cache(second_cache) == {}


def test_duplicate_ownership_lower_relation_wins_without_losing_unrelated_rules(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "polkadot")
    second = harness.add_relation("machine-observability", "kusama")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        first,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "shared", "FIRST")))},
    )
    harness.update_relation_data(
        second,
        "kusama",
        {
            "payload": json.dumps(
                _v3_payload(
                    _rule_artifact("prometheus_alert_rules", "shared", "SECOND"),
                    _rule_artifact("prometheus_alert_rules", "unrelated", "UNRELATED"),
                )
            )
        },
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2
    assert any(group["name"].endswith("shared-FIRST") for group in groups)
    assert not any(group["name"].endswith("shared-SECOND") for group in groups)
    assert any(group["name"].endswith("unrelated-UNRELATED") for group in groups)
    second_cache = harness.charm._decode_rule_cache(
        harness.get_relation_data(second, harness.charm.app.name)[charm_module.RULE_CACHE_KEY]
    )
    assert set(second_cache) == {"prometheus_alert_rules/unrelated"}
    assert f"relation {second}" in caplog.text
    assert "principal-uuid/polkadot/prometheus_alert_rules/shared" in caplog.text

    harness.remove_relation(first)
    harness.update_relation_data(
        second,
        "kusama",
        {
            "payload": json.dumps(
                _v3_payload(
                    _rule_artifact("prometheus_alert_rules", "shared", "SECOND-LATER"),
                    _rule_artifact("prometheus_alert_rules", "unrelated", "UNRELATED"),
                )
            )
        },
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2
    assert any(group["name"].endswith("shared-SECOND-LATER") for group in groups)


def test_conflicting_replacement_retains_later_relations_nonconflicting_lkg(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "polkadot")
    second = harness.add_relation("machine-observability", "kusama")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        first,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "shared", "FIRST")))},
    )
    unique_payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "shared", "SECOND-LKG"))
    unique_payload["source_topology"] = {
        **unique_payload["source_topology"],
        "model_uuid": "second-uuid",
        "application": "kusama",
        "unit": "kusama/0",
    }
    harness.update_relation_data(second, "kusama", {"payload": json.dumps(unique_payload)})

    harness.update_relation_data(
        second,
        "kusama",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "shared", "COLLISION")))},
    )

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2
    assert any(group["name"].endswith("shared-FIRST") for group in groups)
    assert any(group["name"].endswith("shared-SECOND-LKG") for group in groups)
    assert not any(group["name"].endswith("shared-COLLISION") for group in groups)


def test_duplicate_final_group_name_makes_candidate_unpublishable():
    group = {"name": "same", "rules": [{"alert": "Down", "expr": "up"}]}
    relation_state = {
        0: {
            "prometheus_alert_rules/one": {
                "backend": "prometheus",
                "ownership": "uuid/app/prometheus_alert_rules/one",
                "groups": [group],
            },
            "prometheus_alert_rules/two": {
                "backend": "prometheus",
                "ownership": "uuid/app/prometheus_alert_rules/two",
                "groups": [group],
            },
        }
    }

    assert not AlloySubCharm._rule_state_is_publishable(relation_state)


@pytest.mark.parametrize(
    "topology",
    [
        None,
        {"application": "polkadot"},
        {"model_uuid": "principal-uuid", "application": ""},
    ],
)
def test_invalid_ownership_topology_retains_relation_lkg(monkeypatch, topology):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(_rule_artifact("prometheus_alert_rules", "good", "LAST-GOOD")))},
    )
    invalid = _v3_payload(_rule_artifact("prometheus_alert_rules", "good", "INVALID"))
    if topology is None:
        invalid.pop("source_topology")
    else:
        invalid["source_topology"] = topology

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(invalid)})

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert groups[0]["name"].endswith("good-LAST-GOOD")


def test_multiple_machine_relations_are_aggregated_and_removed_independently(monkeypatch):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    first = harness.add_relation("machine-observability", "polkadot")
    second = harness.add_relation("machine-observability", "kusama")
    prometheus = harness.add_relation("send-remote-write", "mimir")
    assert harness.charm._has_machine_observability_relation()
    assert harness.charm._observability_payload().schema_version == 1
    shared = _rule_artifact("prometheus_alert_rules", "node", "Node")
    first_payload = _v3_payload(shared)
    second_payload = _v3_payload(shared)
    second_payload["source_topology"] = {
        **second_payload["source_topology"],
        "model_uuid": "second-uuid",
        "application": "kusama",
        "unit": "kusama/0",
    }
    harness.update_relation_data(first, "polkadot", {"payload": json.dumps(first_payload)})
    harness.update_relation_data(second, "kusama", {"payload": json.dumps(second_payload)})
    harness.add_relation_unit(first, "polkadot/0")

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2
    assert any("principal-uuid-polkadot" in group["name"] for group in groups)
    assert any("second-uuid-kusama" in group["name"] for group in groups)

    harness.remove_relation_unit(first, "polkadot/0")

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 2

    harness.remove_relation(first)

    groups = json.loads(harness.get_relation_data(prometheus, harness.charm.app.name)["alert_rules"])["groups"]
    assert len(groups) == 1
    assert "second-uuid-kusama" in groups[0]["name"]


def test_configure_log_does_not_include_artifact_content(monkeypatch, caplog):
    import src.charm as charm_module

    artifact = _rule_artifact("prometheus_alert_rules", "secret", "Sensitive")
    payload = MachineObservabilityPayload.model_validate(_v3_payload(artifact))
    context = PrincipalContext(
        application="polkadot",
        unit="polkadot/0",
        address="10.0.0.5",
        model="principal-model",
        model_uuid="principal-uuid",
    )
    harness = testing.Harness(AlloySubCharm)
    harness.begin()
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(harness.charm, "_has_machine_observability_relation", lambda: True)
    monkeypatch.setattr(harness.charm, "_principal_context", lambda: context)
    monkeypatch.setattr(harness.charm, "_observability_payload", lambda: payload)
    monkeypatch.setattr(harness.charm, "_validate_config", lambda _: None)
    monkeypatch.setattr(charm_module.alloy, "ensure_config_dir_permissions", lambda _: None)
    monkeypatch.setattr(charm_module.alloy, "write_config_text", lambda *args, **kwargs: None)
    monkeypatch.setattr(charm_module.alloy, "write_custom_args", lambda _: None)
    monkeypatch.setattr(charm_module.alloy, "is_active", lambda: True)
    monkeypatch.setattr(charm_module.alloy, "custom_args_applied", lambda _: True)
    monkeypatch.setattr(charm_module.alloy, "reload", lambda: None)
    monkeypatch.setattr(charm_module.alloy, "restart", lambda: None)

    harness.charm._configure(active_message="configured")

    assert artifact["content"] not in caplog.text
    assert "prometheus_alert_rules/secret" in caplog.text


def test_invalid_artifact_error_log_does_not_include_content(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    harness.add_relation("send-remote-write", "mimir")
    artifact = _rule_artifact("prometheus_alert_rules", "bad-encoding", "Sensitive")
    artifact["encoding"] = "UNIQUE-SECRET-ENCODING"
    artifact["content"] = "UNIQUE-SECRET-ARTIFACT-BODY"

    harness.update_relation_data(
        machine,
        "polkadot",
        {"payload": json.dumps(_v3_payload(artifact))},
    )

    assert "UNIQUE-SECRET-ARTIFACT-BODY" not in caplog.text
    assert "UNIQUE-SECRET-ENCODING" not in caplog.text
    assert "prometheus_alert_rules/bad-encoding: encoding" in caplog.text


def test_invalid_artifact_logs_are_bounded(monkeypatch, caplog):
    import src.alert_rules as alert_rules
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    artifacts = [
        {"artifact_type": "prometheus_alert_rules", "artifact_id": f"rule-{index:03d}"} for index in range(500)
    ]

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(_v3_payload(*artifacts))})

    messages = [
        record.message
        for record in caplog.records
        if record.message.startswith("Invalid machine-observability artifact:")
    ]
    assert len(messages) == alert_rules.MAX_RULE_ERROR_DETAILS + 1
    assert messages[-1].endswith("artifacts: truncated (484 additional errors)")


def test_invalid_artifact_identity_does_not_inject_log_content(monkeypatch, caplog):
    import src.charm as charm_module

    monkeypatch.setattr(charm_module.AlloySubCharm, "_configure", lambda *args, **kwargs: True)
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.begin()
    machine = harness.add_relation("machine-observability", "polkadot")
    harness.add_relation("send-remote-write", "mimir")
    artifact = _rule_artifact("prometheus_alert_rules", "valid", "Sensitive")
    artifact["artifact_type"] = "UNIQUE-SECRET-TYPE\nforged-log-line"
    artifact["artifact_id"] = "UNIQUE-SECRET-ID-" + "x" * 1024

    harness.update_relation_data(machine, "polkadot", {"payload": json.dumps(_v3_payload(artifact))})

    assert "UNIQUE-SECRET" not in caplog.text
    assert "forged-log-line" not in caplog.text
    assert "unknown/unknown: schema" in caplog.text
