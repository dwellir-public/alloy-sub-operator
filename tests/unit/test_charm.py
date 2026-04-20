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

from charm import AlloySubCharm, relation_urls


def test_start():
    # Arrange:
    ctx = testing.Context(AlloySubCharm)
    # Act:
    with patch("charm.alloy.start"), patch("charm.alloy.get_version", return_value="1.0.0"), patch(
        "charm.alloy.is_active", return_value=True
    ):
        state_out = ctx.run(ctx.on.start(), testing.State())
    # Assert:
    assert state_out.unit_status == testing.ActiveStatus("Alloy is running")
    assert state_out.workload_version == "1.0.0"


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
