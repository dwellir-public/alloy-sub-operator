"""Regression proofs for subordinate-local direct scrape and textfile inputs."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest
from ops import testing

from charm import AlloySubCharm
from config_builder import MetricsScrapeJob, ScrapeTarget
from principal_context import PrincipalContext
from related_metrics import related_scrape_jobs

PRINCIPAL = PrincipalContext(
    application="iris",
    unit="iris/0",
    address="10.0.0.5",
    model="internal",
    model_uuid="8d669f32-2107-45af-8eba-8d5a0fe48585",
)


def job(*, unit="iris/0", address="10.0.0.5:9092", **extra):
    return {
        "job_name": "iris",
        "static_configs": [
            {
                "targets": [address],
                "labels": {
                    "juju_application": "iris",
                    "juju_model_uuid": "8d669f32-2107-45af-8eba-8d5a0fe48585",
                    "juju_unit": unit,
                    "gateway_region": "internal",
                },
            }
        ],
        "relabel_configs": [{"source_labels": ["juju_unit"], "target_label": "instance"}],
        **extra,
    }


def test_local_unit_selection_preserves_labels_paths_and_relabeling():
    jobs = related_scrape_jobs(
        [job(metrics_path="/custom", scrape_interval="30s"), job(unit="iris/1", address="10.0.0.6:9092")], PRINCIPAL
    )
    assert len(jobs) == 1
    assert jobs[0].targets == [
        ScrapeTarget(
            "10.0.0.5:9092",
            {
                "juju_application": "iris",
                "juju_model_uuid": "8d669f32-2107-45af-8eba-8d5a0fe48585",
                "juju_unit": "iris/0",
                "gateway_region": "internal",
            },
        )
    ]
    assert jobs[0].metrics_path == "/custom"
    assert jobs[0].scrape_interval == "30s"
    assert jobs[0].relabel_configs == job()["relabel_configs"]
    assert related_scrape_jobs([job()], replace(PRINCIPAL, application="clickhouse")) == []
    assert related_scrape_jobs([job()], replace(PRINCIPAL, model_uuid="other")) == []


def test_static_nonunit_targets_must_be_demonstrably_local():
    assert len(related_scrape_jobs([job(unit="")], PRINCIPAL)) == 1
    assert related_scrape_jobs([job(unit="", address="remote.invalid:9092")], PRINCIPAL) == []


def test_existing_source_wins_and_duplicate_jobs_are_not_scraped_twice():
    existing = MetricsScrapeJob("declared", [ScrapeTarget("10.0.0.5:9092")])
    assert related_scrape_jobs([job()], PRINCIPAL, existing=[existing]) == []
    assert len(related_scrape_jobs([job(), job()], PRINCIPAL)) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"basic_auth": {"username": "ignored-is-unsafe"}},
        {"params": {"chain": ["eth"]}},
        {"relabel_configs": [{"invalid_field": "x"}]},
    ],
)
def test_unsupported_semantics_are_rejected(extra):
    with pytest.raises(ValueError, match="unsupported"):
        related_scrape_jobs([job(**extra)], PRINCIPAL)


def test_relation_pipeline_works_without_machine_observability():
    ctx = testing.Context(AlloySubCharm)
    principal = testing.SubordinateRelation(
        "juju-info", remote_app_name="iris", remote_unit_id=0, remote_unit_data={"private-address": "10.0.0.5"}
    )
    metrics = testing.Relation(
        "metrics-endpoint",
        remote_app_name="iris",
        remote_app_data={
            "scrape_metadata": json.dumps(
                {
                    "model": "internal",
                    "model_uuid": "8d669f32-2107-45af-8eba-8d5a0fe48585",
                    "application": "iris",
                    "charm_name": "iris-operator",
                }
            ),
            "scrape_jobs": json.dumps(
                [
                    {
                        "job_name": "app",
                        "static_configs": [{"targets": ["*:9092"], "labels": {"gateway_region": "internal"}}],
                    }
                ]
            ),
        },
        remote_units_data={
            0: {"prometheus_scrape_unit_name": "iris/0", "prometheus_scrape_unit_address": "10.0.0.5"},
            1: {"prometheus_scrape_unit_name": "iris/1", "prometheus_scrape_unit_address": "10.0.0.6"},
        },
    )
    remote = testing.Relation(
        "send-remote-write", remote_app_name="mimir", remote_app_data={"url": "http://mimir:9009/api/v1/push"}
    )
    state = testing.State(
        model=testing.Model(name="internal", uuid="8d669f32-2107-45af-8eba-8d5a0fe48585"),
        relations={principal, metrics, remote},
    )
    with (
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text") as write,
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.reload"),
        patch("charm.alloy.restart"),
        patch("charm.alloy.verify_config"),
    ):
        out = ctx.run(ctx.on.relation_changed(metrics, remote_unit=0), state)
        rendered = write.call_args.args[0]
    assert isinstance(out.unit_status, testing.ActiveStatus)
    assert '"10.0.0.5:9092"' in rendered
    assert '"10.0.0.6:9092"' not in rendered
    assert 'gateway_region = "internal"' in rendered
    assert "discovery.relabel" in rendered
    assert 'juju_unit = "iris/0"' in rendered
    assert 'juju_unit = "alloy-sub/0"' not in rendered
    with (
        patch("charm.alloy.is_active", return_value=True),
        patch("charm.alloy.custom_args_applied", return_value=True),
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.write_config_text") as write,
        patch("charm.alloy.restore_preserved_config") as restore,
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.reload"),
        patch("charm.alloy.restart"),
        patch("charm.alloy.verify_config"),
    ):
        ctx.run(ctx.on.relation_broken(metrics), out)
    assert not write.called
    restore.assert_called_once()


@pytest.mark.parametrize("directory,enabled", [("relative", True), ("/tmp/metrics", False)])
def test_textfile_configuration_rejects_invalid_inputs(directory, enabled):
    ctx = testing.Context(AlloySubCharm)
    with ctx(
        ctx.on.config_changed(), testing.State(config={"textfile-directory": directory, "enable-host-metrics": enabled})
    ) as manager:
        with pytest.raises(ValueError, match="absolute path"):
            manager.charm._textfile_directory()
