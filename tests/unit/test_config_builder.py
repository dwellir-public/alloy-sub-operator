import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config_builder import ConfigBuilder, FileLogSource, MetricsScrapeJob, ScrapeTarget


def test_build_renders_only_juju_labels_for_logs():
    builder = ConfigBuilder(
        loki_endpoints=["http://loki:3100/loki/api/v1/push"],
        remote_write_endpoints=[],
        metrics_scrape_jobs=[],
        systemd_units=["snap.polkadot.polkadot.service"],
        journal_match_expressions=[],
        file_log_sources=[],
        topology_labels={"juju_application": "polkadot", "juju_unit": "polkadot/0"},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=[],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
    )

    config = builder.build()

    assert 'loki.source.journal "journald"' in config
    assert 'systemd_unit = "snap.polkadot.polkadot.service"' in config
    assert 'juju_application = "polkadot"' in config
    assert 'juju_unit = "polkadot/0"' in config
    assert "chain_name" not in config


def test_build_renders_file_log_source_attributes_and_merged_excludes():
    builder = ConfigBuilder(
        loki_endpoints=["http://loki:3100/loki/api/v1/push"],
        remote_write_endpoints=[],
        metrics_scrape_jobs=[],
        systemd_units=[],
        journal_match_expressions=[],
        file_log_sources=[
            FileLogSource(
                include=["/var/log/polkadot/*.log"],
                exclude=["/var/log/polkadot/archive/**"],
                attributes={"node_role": "rpc"},
            )
        ],
        topology_labels={"juju_application": "polkadot"},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=["/var/log/juju/**"],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
    )

    config = builder.build()

    assert 'local.file_match "filelogs"' in config
    assert "/var/log/polkadot/*.log" in config
    assert "/var/log/polkadot/archive/**" in config
    assert "/var/log/juju/**" in config
    assert 'node_role = "rpc"' in config


def test_build_renders_remote_write_metrics():
    job = MetricsScrapeJob(
        job_name="polkadot",
        targets=[ScrapeTarget(address="10.0.0.5:9615", labels={"juju_application": "polkadot"})],
        metrics_path="/metrics",
    )
    builder = ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        metrics_scrape_jobs=[job],
        systemd_units=[],
        journal_match_expressions=[],
        file_log_sources=[],
        topology_labels={},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=[],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
    )

    config = builder.build()

    assert 'prometheus.scrape "polkadot"' in config
    assert 'prometheus.remote_write "metrics"' in config
    assert 'min_keepalive_time = "0s"' in config
    assert 'max_keepalive_time = "5m"' in config
    assert '__address__ = "10.0.0.5:9615"' in config


def test_build_skips_log_pipeline_without_loki_sink():
    builder = ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        metrics_scrape_jobs=[],
        systemd_units=["snap.polkadot.polkadot.service"],
        journal_match_expressions=[],
        file_log_sources=[],
        topology_labels={"juju_application": "polkadot"},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=[],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
    )

    config = builder.build()

    assert 'prometheus.remote_write "metrics"' in config
    assert "loki.source.journal" not in config
    assert 'loki.process "juju"' not in config
    assert 'loki.write "main"' not in config
