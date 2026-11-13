"""Charm wiring for profiling-source (pprof_scrape) and send-profiles (profiling)."""

import json
import logging
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from test_charm import _machine_observability_payload, _ready_state  # noqa: E402

from charm import AlloySubCharm
from config_builder import ProfilingSource

ACTIVE_MACHINE_OBSERVABILITY = "Alloy service running; config valid; Alloy is running"


def _payload(**overrides):
    base = {
        "version": 1,
        "service_name": "my-app",
        "targets": ["127.0.0.1:6060"],
        "profile_types": ["process_cpu", "memory"],
        "labels": {
            "juju_model": "m",
            "juju_model_uuid": "u",
            "juju_application": "my-app",
            "juju_unit": "my-app/0",
            "juju_charm": "my-app-operator",
        },
    }
    base.update(overrides)
    return json.dumps(base)


def _juju_info():
    return testing.SubordinateRelation(
        "juju-info",
        remote_app_name="my-app",
        remote_unit_id=0,
        remote_unit_data={"private-address": "10.0.0.5"},
    )


def _source_relation(payload=None):
    return testing.SubordinateRelation(
        "profiling-source",
        remote_app_name="my-app",
        remote_unit_id=0,
        remote_unit_data={"profiling_source": payload or _payload()},
    )


def _sink_relation(*, insecure=True):
    return testing.Relation(
        "send-profiles",
        remote_app_name="pyroscope-vm",
        remote_app_data={
            "otlp_grpc_endpoint_url": json.dumps("10.0.0.9:4040"),
            "insecure": json.dumps(insecure),
        },
    )


def _metrics_relations():
    """Return juju-info, a metrics-bearing machine-observability relation, and a remote-write sink."""
    relations = []
    for relation in _ready_state(with_remote_write=True).relations:
        if relation.endpoint == "machine-observability":
            relation = testing.SubordinateRelation(
                "machine-observability",
                remote_app_name="polkadot",
                remote_unit_id=0,
                remote_app_data={
                    "payload": _machine_observability_payload(
                        metrics_endpoints=[{"targets": ["localhost:9615"], "path": "/metrics", "scheme": "http"}]
                    )
                },
            )
        relations.append(relation)
    return relations


def _run(event_factory, state):
    ctx = testing.Context(AlloySubCharm)
    with ExitStack() as stack:
        for name, kwargs in (
            ("charm.alloy.start", {}),
            ("charm.alloy.get_version", {"return_value": "1.0.0"}),
            ("charm.alloy.is_active", {"return_value": True}),
            ("charm.alloy.ensure_config_dir_permissions", {}),
            ("charm.alloy.write_config_text", {}),
            ("charm.alloy.write_custom_args", {}),
            ("charm.alloy.custom_args_applied", {"return_value": True}),
            ("charm.alloy.restart", {}),
            ("charm.alloy.verify_config", {}),
        ):
            stack.enter_context(patch(name, **kwargs))
        reload_mock = stack.enter_context(patch("charm.alloy.reload"))
        builder_cls = stack.enter_context(patch("charm.ConfigBuilder"))
        builder_cls.return_value.build.return_value = ""
        state_out = ctx.run(event_factory(ctx), state)
    return state_out, builder_cls, reload_mock


def _start(relations, **state_kwargs):
    return _run(lambda ctx: ctx.on.start(), testing.State(relations=relations, **state_kwargs))


def test_profiling_only_principal_is_a_valid_pipeline():
    state_out, builder_cls, _ = _start([_juju_info(), _source_relation(), _sink_relation()])

    assert state_out.unit_status == testing.ActiveStatus("Alloy service running; config valid; profiles only")
    kwargs = builder_cls.call_args.kwargs
    assert kwargs["profiling_sink_url"] == "http://10.0.0.9:4040"
    (source,) = kwargs["profiling_sources"]
    assert isinstance(source, ProfilingSource)
    assert source.service_name == "my-app"
    assert source.targets == ["127.0.0.1:6060"]
    assert source.profile_types == ["process_cpu", "memory"]
    assert source.labels["instance"] == "my-app/0"


def test_profiling_without_send_profiles_status_says_no_sink():
    state_out, _, _ = _start([_juju_info(), _source_relation()])

    assert state_out.unit_status == testing.ActiveStatus(
        "Alloy service running; config valid; profiles (no send-profiles) only"
    )


def test_profiling_sink_https_when_not_insecure():
    _, builder_cls, _ = _start([_juju_info(), _source_relation(), _sink_relation(insecure=False)])

    assert builder_cls.call_args.kwargs["profiling_sink_url"] == "https://10.0.0.9:4040"


def test_profiling_without_send_profiles_renders_no_sink(caplog):
    with caplog.at_level(logging.INFO):
        state_out, builder_cls, _ = _start([_juju_info(), _source_relation()])

    assert builder_cls.call_args.kwargs["profiling_sink_url"] is None
    assert state_out.unit_status.name == "active"
    assert "profiling sources present but no send-profiles relation; profiles not forwarded" in caplog.text


def test_juju_info_topology_overrides_payload_labels():
    payload = _payload(
        labels={"juju_application": "spoofed", "juju_unit": "spoofed/9", "team": "x", "juju_charm": "my-app-operator"}
    )
    _, builder_cls, _ = _start([_juju_info(), _source_relation(payload), _sink_relation()])

    (source,) = builder_cls.call_args.kwargs["profiling_sources"]
    assert source.labels["juju_application"] == "my-app"
    assert source.labels["juju_unit"] == "my-app/0"
    assert source.labels["team"] == "x"
    assert source.labels["juju_charm"] == "my-app-operator"


def test_payload_cannot_override_reserved_labels():
    payload = _payload(
        labels={
            "service_name": "evil",
            "instance": "evil/1",
            "__address__": "169.254.169.254:80",
            "__scheme__": "https",
            "team": "x",
        }
    )
    _, builder_cls, _ = _start([_juju_info(), _source_relation(payload), _sink_relation()])

    (source,) = builder_cls.call_args.kwargs["profiling_sources"]
    assert source.labels["instance"] == "my-app/0"
    assert "service_name" not in source.labels
    assert not [key for key in source.labels if key.startswith("__")]
    assert source.labels["team"] == "x"
    assert source.service_name == "my-app"


def test_send_profiles_broken_removes_pyroscope_blocks():
    sink = _sink_relation()
    state = testing.State(relations=[_juju_info(), _source_relation(), sink])
    # Stored custom args default to "", so pin the desired args to avoid a restart-instead-of-reload.
    with patch.object(AlloySubCharm, "_desired_custom_args", return_value=""):
        _, builder_cls, reload_mock = _run(lambda ctx: ctx.on.relation_broken(sink), state)

    assert builder_cls.call_args.kwargs["profiling_sink_url"] is None
    reload_mock.assert_called()


def test_profiles_scrape_interval_option_passed_through():
    _, builder_cls, _ = _start(
        [_juju_info(), _source_relation(), _sink_relation()], config={"profiles_scrape_interval": "30s"}
    )

    assert builder_cls.call_args.kwargs["profiles_scrape_interval"] == "30s"


def test_machine_observability_behaviour_unchanged_without_profiling():
    ctx_state = _ready_state(with_loki=True)
    state_out, builder_cls, _ = _run(lambda ctx: ctx.on.start(), ctx_state)

    assert state_out.unit_status == testing.ActiveStatus(ACTIVE_MACHINE_OBSERVABILITY)
    kwargs = builder_cls.call_args.kwargs
    assert kwargs["profiling_sources"] == []
    assert kwargs["profiling_sink_url"] is None


def test_malformed_send_profiles_does_not_block_metrics():
    bad_sink = testing.Relation(
        "send-profiles",
        remote_app_name="pyroscope-vm",
        remote_app_data={"otlp_grpc_endpoint_url": "10.0.0.9:4040", "insecure": json.dumps(True)},
    )
    state = testing.State(relations=[*_metrics_relations(), _source_relation(), bad_sink])
    state_out, builder_cls, _ = _run(lambda ctx: ctx.on.start(), state)

    assert state_out.unit_status == testing.ActiveStatus(ACTIVE_MACHINE_OBSERVABILITY)
    kwargs = builder_cls.call_args.kwargs
    assert kwargs["profiling_sink_url"] is None
    assert kwargs["metrics_scrape_jobs"]


def test_machine_observability_and_profiling_together():
    state = testing.State(relations=[*_metrics_relations(), _source_relation(), _sink_relation()])
    state_out, builder_cls, _ = _run(lambda ctx: ctx.on.start(), state)

    assert state_out.unit_status == testing.ActiveStatus(ACTIVE_MACHINE_OBSERVABILITY)
    kwargs = builder_cls.call_args.kwargs
    assert kwargs["metrics_scrape_jobs"]
    assert kwargs["profiling_sink_url"] == "http://10.0.0.9:4040"
    (source,) = kwargs["profiling_sources"]
    assert source.service_name == "my-app"


def test_invalid_label_keys_are_dropped():
    payload = _payload(labels={"": "a", 'x" = 1}': "b", "bad-key": "c", "team": "x", "juju_charm": "my-app-operator"})
    _, builder_cls, _ = _start([_juju_info(), _source_relation(payload), _sink_relation()])

    (source,) = builder_cls.call_args.kwargs["profiling_sources"]
    assert "" not in source.labels
    assert 'x" = 1}' not in source.labels
    assert "bad-key" not in source.labels
    assert source.labels["team"] == "x"
