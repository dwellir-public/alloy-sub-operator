# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
import sys
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm, relation_urls


LOKI_URL = "http://loki:3100/loki/api/v1/push"
REMOTE_WRITE_URL = "http://mimir:9009/api/v1/push"


def _machine_observability_payload(*, metrics_endpoints=None):
    return json.dumps(
        {
            "schema_version": 1,
            "charm_name": "polkadot",
            "systemd_units": [],
            "journal_match_expressions": [],
            "log_files": [],
            "metrics_endpoints": metrics_endpoints or [],
        }
    )


def _ready_state(*, with_loki=False, with_remote_write=False):
    relations = [
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
    ]
    if with_loki:
        relations.append(
            testing.Relation(
                "send-loki-logs",
                remote_app_name="loki",
                remote_app_data={"url": LOKI_URL},
            )
        )
    if with_remote_write:
        relations.append(
            testing.Relation(
                "send-remote-write",
                remote_app_name="mimir",
                remote_app_data={"remote_write": json.dumps({"url": REMOTE_WRITE_URL})},
            )
        )
    return testing.State(relations=relations)


def test_start_becomes_active_when_required_relations_present():
    ctx = testing.Context(AlloySubCharm)

    with patch("charm.alloy.start"), patch("charm.alloy.get_version", return_value="1.0.0"), patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.ensure_config_dir_permissions"), patch(
        "charm.alloy.write_config_text"
    ), patch("charm.alloy.write_custom_args"), patch(
        "charm.alloy.custom_args_applied", return_value=True
    ), patch("charm.alloy.reload"), patch("charm.alloy.restart"), patch(
        "charm.alloy.verify_config"
    ), patch("charm.ConfigBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = ""
        state_out = ctx.run(ctx.on.start(), _ready_state(with_loki=True))

    assert state_out.unit_status == testing.ActiveStatus("Alloy is running")
    assert state_out.workload_version == "1.0.0"


def test_start_waits_for_required_relations_when_missing():
    ctx = testing.Context(AlloySubCharm)

    with patch("charm.alloy.start"), patch("charm.alloy.get_version", return_value="1.0.0"):
        state_out = ctx.run(ctx.on.start(), testing.State())

    assert state_out.unit_status == testing.WaitingStatus(
        "Waiting for juju-info relation, machine-observability relation, and one of send-loki-logs or send-remote-write relations"
    )


def test_start_waits_for_machine_observability_relation():
    ctx = testing.Context(AlloySubCharm)
    state = testing.State(
        relations=[
            testing.SubordinateRelation(
                "juju-info",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_unit_data={"private-address": "10.0.0.5"},
            ),
            testing.Relation(
                "send-loki-logs",
                remote_app_name="loki",
                remote_app_data={"url": LOKI_URL},
            ),
        ]
    )

    with patch("charm.alloy.start"), patch("charm.alloy.get_version", return_value="1.0.0"):
        state_out = ctx.run(ctx.on.start(), state)

    assert state_out.unit_status == testing.WaitingStatus(
        "Waiting for machine-observability relation"
    )


def test_start_waits_for_sink_relation():
    ctx = testing.Context(AlloySubCharm)

    with patch("charm.alloy.start"), patch("charm.alloy.get_version", return_value="1.0.0"):
        state_out = ctx.run(ctx.on.start(), _ready_state())

    assert state_out.unit_status == testing.WaitingStatus(
        "Waiting for one of send-loki-logs or send-remote-write relations"
    )


class _Entity:
    def __init__(self, name: str):
        self.name = name


def test_relation_urls_reads_unit_loki_endpoint_json():
    app = _Entity("loki")
    unit = _Entity("loki/0")
    relation = SimpleNamespace(
        app=app,
        units={unit},
        data={
            app: {},
            unit: {"endpoint": json.dumps({"url": "http://loki:3100/loki/api/v1/push"})},
        },
    )

    assert relation_urls([relation], json_keys=("endpoint",)) == [
        "http://loki:3100/loki/api/v1/push"
    ]


def test_relation_urls_reads_unit_remote_write_json():
    app = _Entity("mimir")
    unit = _Entity("mimir/0")
    relation = SimpleNamespace(
        app=app,
        units={unit},
        data={
            app: {},
            unit: {"remote_write": json.dumps({"url": "http://mimir:9009/api/v1/push"})},
        },
    )

    assert relation_urls([relation], json_keys=("remote_write",)) == [
        "http://mimir:9009/api/v1/push"
    ]


def test_configure_restarts_alloy_when_custom_args_change():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(last_good_config="", last_custom_args=""),
        _principal_context=lambda: SimpleNamespace(
            juju_labels=lambda charm_name=None: {"juju_charm": charm_name or "polkadot"}
        ),
        _observability_payload=lambda: SimpleNamespace(
            charm_name="polkadot",
            systemd_units=[],
            journal_match_expressions=[],
            log_files=[],
            metrics_endpoints=[],
        ),
        _loki_endpoint_urls=lambda: [],
        _remote_write_endpoint_urls=lambda: [],
        _active_metrics_scrape_jobs=lambda payload, principal_context: [],
        _path_exclude_patterns=lambda: "",
        _global_scrape_interval=lambda: "1m",
        _global_scrape_timeout=lambda: "10s",
        _queue_size=lambda: 1000,
        _max_elapsed_time_min=lambda: 5,
        _tls_insecure_skip_verify=lambda: False,
        _validate_config=lambda config_text: None,
        _desired_custom_args=lambda: "--server.http.listen-addr=0.0.0.0:6987",
        _missing_relation_requirements=lambda **kwargs: [],
        _apply_runtime_update=lambda desired_custom_args, previous_custom_args: AlloySubCharm._apply_runtime_update(
            None,
            desired_custom_args=desired_custom_args,
            previous_custom_args=previous_custom_args,
        ),
    )

    with patch("charm.ConfigBuilder") as builder_cls, patch(
        "charm.alloy.ensure_config_dir_permissions"
    ), patch("charm.alloy.write_config_text"), patch("charm.alloy.write_custom_args"), patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.custom_args_applied", return_value=False), patch(
        "charm.alloy.reload"
    ) as reload_mock, patch("charm.alloy.restart") as restart_mock:
        builder_cls.return_value.build.return_value = ""
        AlloySubCharm._configure(fake_charm)

    restart_mock.assert_called_once()
    reload_mock.assert_not_called()


def test_configure_restarts_alloy_when_custom_args_not_applied():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(
            last_good_config="",
            last_custom_args="--server.http.listen-addr=0.0.0.0:6987",
        ),
        _principal_context=lambda: SimpleNamespace(
            juju_labels=lambda charm_name=None: {"juju_charm": charm_name or "polkadot"}
        ),
        _observability_payload=lambda: SimpleNamespace(
            charm_name="polkadot",
            systemd_units=[],
            journal_match_expressions=[],
            log_files=[],
            metrics_endpoints=[],
        ),
        _loki_endpoint_urls=lambda: [],
        _remote_write_endpoint_urls=lambda: [],
        _active_metrics_scrape_jobs=lambda payload, principal_context: [],
        _path_exclude_patterns=lambda: "",
        _global_scrape_interval=lambda: "1m",
        _global_scrape_timeout=lambda: "10s",
        _queue_size=lambda: 1000,
        _max_elapsed_time_min=lambda: 5,
        _tls_insecure_skip_verify=lambda: False,
        _validate_config=lambda config_text: None,
        _desired_custom_args=lambda: "--server.http.listen-addr=0.0.0.0:6987",
        _missing_relation_requirements=lambda **kwargs: [],
        _apply_runtime_update=lambda desired_custom_args, previous_custom_args: AlloySubCharm._apply_runtime_update(
            None,
            desired_custom_args=desired_custom_args,
            previous_custom_args=previous_custom_args,
        ),
    )

    with patch("charm.ConfigBuilder") as builder_cls, patch(
        "charm.alloy.ensure_config_dir_permissions"
    ), patch("charm.alloy.write_config_text"), patch("charm.alloy.write_custom_args"), patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.custom_args_applied", return_value=False), patch(
        "charm.alloy.reload"
    ) as reload_mock, patch("charm.alloy.restart") as restart_mock:
        builder_cls.return_value.build.return_value = ""
        AlloySubCharm._configure(fake_charm)

    restart_mock.assert_called_once()
    reload_mock.assert_not_called()


def test_configure_reloads_alloy_when_custom_args_do_not_change():
    fake_charm = SimpleNamespace(
        _stored=SimpleNamespace(
            last_good_config="",
            last_custom_args="--server.http.listen-addr=0.0.0.0:6987",
        ),
        _principal_context=lambda: SimpleNamespace(
            juju_labels=lambda charm_name=None: {"juju_charm": charm_name or "polkadot"}
        ),
        _observability_payload=lambda: SimpleNamespace(
            charm_name="polkadot",
            systemd_units=[],
            journal_match_expressions=[],
            log_files=[],
            metrics_endpoints=[],
        ),
        _loki_endpoint_urls=lambda: [],
        _remote_write_endpoint_urls=lambda: [],
        _active_metrics_scrape_jobs=lambda payload, principal_context: [],
        _path_exclude_patterns=lambda: "",
        _global_scrape_interval=lambda: "1m",
        _global_scrape_timeout=lambda: "10s",
        _queue_size=lambda: 1000,
        _max_elapsed_time_min=lambda: 5,
        _tls_insecure_skip_verify=lambda: False,
        _validate_config=lambda config_text: None,
        _desired_custom_args=lambda: "--server.http.listen-addr=0.0.0.0:6987",
        _missing_relation_requirements=lambda **kwargs: [],
        _apply_runtime_update=lambda desired_custom_args, previous_custom_args: AlloySubCharm._apply_runtime_update(
            None,
            desired_custom_args=desired_custom_args,
            previous_custom_args=previous_custom_args,
        ),
    )

    with patch("charm.ConfigBuilder") as builder_cls, patch(
        "charm.alloy.ensure_config_dir_permissions"
    ), patch("charm.alloy.write_config_text"), patch("charm.alloy.write_custom_args"), patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.custom_args_applied", return_value=True), patch(
        "charm.alloy.reload"
    ) as reload_mock, patch("charm.alloy.restart") as restart_mock:
        builder_cls.return_value.build.return_value = ""
        AlloySubCharm._configure(fake_charm)

    reload_mock.assert_called_once()
    restart_mock.assert_not_called()


def test_removing_loki_relation_rewrites_config_when_remote_write_remains():
    payload = _machine_observability_payload(
        metrics_endpoints=[{"targets": ["localhost:9615"], "path": "/metrics", "scheme": "http"}]
    )

    with patch("charm.alloy.install"), patch("charm.alloy.preserve_default_config"), patch(
        "charm.alloy.write_custom_args"
    ), patch("charm.alloy.ensure_config_dir_permissions"), patch(
        "charm.alloy.write_config_text"
    ) as write_config, patch("charm.alloy.is_active", return_value=True), patch(
        "charm.alloy.custom_args_applied", return_value=True
    ), patch("charm.alloy.reload"), patch("charm.alloy.restart"), patch(
        "charm.alloy.verify_config"
    ), patch("charm.ConfigBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = ""
        harness = testing.Harness(AlloySubCharm)
        harness.begin()

        juju_info = harness.add_relation("juju-info", "polkadot")
        harness.add_relation_unit(juju_info, "polkadot/0")
        harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})

        observability = harness.add_relation("machine-observability", "polkadot")
        harness.add_relation_unit(observability, "polkadot/0")
        harness.update_relation_data(observability, "polkadot", {"payload": payload})

        loki = harness.add_relation("send-loki-logs", "loki")
        harness.add_relation_unit(loki, "loki/0")
        harness.update_relation_data(loki, "loki", {"url": LOKI_URL})

        remote_write = harness.add_relation("send-remote-write", "mimir")
        harness.add_relation_unit(remote_write, "mimir/0")
        harness.update_relation_data(
            remote_write,
            "mimir",
            {"remote_write": json.dumps({"url": REMOTE_WRITE_URL})},
        )

        writes_before_remove = write_config.call_count
        harness.remove_relation(loki)

    assert write_config.call_count == writes_before_remove + 1
    assert builder_cls.call_args.kwargs["loki_endpoints"] == []
    assert builder_cls.call_args.kwargs["remote_write_endpoints"] == [REMOTE_WRITE_URL]
    assert harness.model.unit.status == testing.ActiveStatus("Alloy config updated")


def test_removing_last_sink_relation_sets_waiting_status():
    with patch("charm.alloy.install"), patch("charm.alloy.preserve_default_config"), patch(
        "charm.alloy.write_custom_args"
    ), patch("charm.alloy.ensure_config_dir_permissions"), patch(
        "charm.alloy.write_config_text"
    ), patch("charm.alloy.restore_preserved_config"), patch(
        "charm.alloy.is_active", return_value=True
    ), patch(
        "charm.alloy.custom_args_applied", return_value=True
    ), patch("charm.alloy.reload"), patch("charm.alloy.restart"), patch(
        "charm.alloy.verify_config"
    ), patch("charm.ConfigBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = ""
        harness = testing.Harness(AlloySubCharm)
        harness.begin()

        juju_info = harness.add_relation("juju-info", "polkadot")
        harness.add_relation_unit(juju_info, "polkadot/0")
        harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})

        observability = harness.add_relation("machine-observability", "polkadot")
        harness.add_relation_unit(observability, "polkadot/0")
        harness.update_relation_data(
            observability,
            "polkadot",
            {"payload": _machine_observability_payload()},
        )

        loki = harness.add_relation("send-loki-logs", "loki")
        harness.add_relation_unit(loki, "loki/0")
        harness.update_relation_data(loki, "loki", {"url": LOKI_URL})

        harness.remove_relation(loki)

    assert harness.model.unit.status == testing.WaitingStatus(
        "Waiting for one of send-loki-logs or send-remote-write relations"
    )


def test_removing_last_sink_restores_safe_config_after_remote_write_then_loki():
    payload = _machine_observability_payload(
        metrics_endpoints=[{"targets": ["localhost:9615"], "path": "/metrics", "scheme": "http"}]
    )

    with patch("charm.alloy.install"), patch("charm.alloy.preserve_default_config"), patch(
        "charm.alloy.write_custom_args"
    ), patch("charm.alloy.ensure_config_dir_permissions"), patch(
        "charm.alloy.write_config_text"
    ), patch("charm.alloy.restore_preserved_config") as restore_config, patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.custom_args_applied", return_value=True), patch(
        "charm.alloy.reload"
    ) as reload_mock, patch("charm.alloy.restart"), patch(
        "charm.alloy.verify_config"
    ), patch("charm.ConfigBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = ""
        harness = testing.Harness(AlloySubCharm)
        harness.begin()

        juju_info = harness.add_relation("juju-info", "polkadot")
        harness.add_relation_unit(juju_info, "polkadot/0")
        harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})

        observability = harness.add_relation("machine-observability", "polkadot")
        harness.add_relation_unit(observability, "polkadot/0")
        harness.update_relation_data(observability, "polkadot", {"payload": payload})

        loki = harness.add_relation("send-loki-logs", "loki")
        harness.add_relation_unit(loki, "loki/0")
        harness.update_relation_data(loki, "loki", {"url": LOKI_URL})

        remote_write = harness.add_relation("send-remote-write", "mimir")
        harness.add_relation_unit(remote_write, "mimir/0")
        harness.update_relation_data(
            remote_write,
            "mimir",
            {"remote_write": json.dumps({"url": REMOTE_WRITE_URL})},
        )

        harness.remove_relation(remote_write)
        restores_before_last_remove = restore_config.call_count
        reloads_before_last_remove = reload_mock.call_count
        harness.remove_relation(loki)

    assert restore_config.call_count == restores_before_last_remove + 1
    assert reload_mock.call_count == reloads_before_last_remove + 1
    assert harness.model.unit.status == testing.WaitingStatus(
        "Waiting for one of send-loki-logs or send-remote-write relations"
    )


def test_removing_last_sink_restores_safe_config_after_loki_then_remote_write():
    payload = _machine_observability_payload(
        metrics_endpoints=[{"targets": ["localhost:9615"], "path": "/metrics", "scheme": "http"}]
    )

    with patch("charm.alloy.install"), patch("charm.alloy.preserve_default_config"), patch(
        "charm.alloy.write_custom_args"
    ), patch("charm.alloy.ensure_config_dir_permissions"), patch(
        "charm.alloy.write_config_text"
    ), patch("charm.alloy.restore_preserved_config") as restore_config, patch(
        "charm.alloy.is_active", return_value=True
    ), patch("charm.alloy.custom_args_applied", return_value=True), patch(
        "charm.alloy.reload"
    ) as reload_mock, patch("charm.alloy.restart"), patch(
        "charm.alloy.verify_config"
    ), patch("charm.ConfigBuilder") as builder_cls:
        builder_cls.return_value.build.return_value = ""
        harness = testing.Harness(AlloySubCharm)
        harness.begin()

        juju_info = harness.add_relation("juju-info", "polkadot")
        harness.add_relation_unit(juju_info, "polkadot/0")
        harness.update_relation_data(juju_info, "polkadot/0", {"private-address": "10.0.0.5"})

        observability = harness.add_relation("machine-observability", "polkadot")
        harness.add_relation_unit(observability, "polkadot/0")
        harness.update_relation_data(observability, "polkadot", {"payload": payload})

        loki = harness.add_relation("send-loki-logs", "loki")
        harness.add_relation_unit(loki, "loki/0")
        harness.update_relation_data(loki, "loki", {"url": LOKI_URL})

        remote_write = harness.add_relation("send-remote-write", "mimir")
        harness.add_relation_unit(remote_write, "mimir/0")
        harness.update_relation_data(
            remote_write,
            "mimir",
            {"remote_write": json.dumps({"url": REMOTE_WRITE_URL})},
        )

        harness.remove_relation(loki)
        restores_before_last_remove = restore_config.call_count
        reloads_before_last_remove = reload_mock.call_count
        harness.remove_relation(remote_write)

    assert restore_config.call_count == restores_before_last_remove + 1
    assert reload_mock.call_count == reloads_before_last_remove + 1
    assert harness.model.unit.status == testing.WaitingStatus(
        "Waiting for one of send-loki-logs or send-remote-write relations"
    )
