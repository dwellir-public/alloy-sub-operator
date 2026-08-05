import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config_builder import (
    ConfigBuilder,
    FileLogSource,
    HostMetrics,
    MetricsScrapeJob,
    ScrapeTarget,
)
from src.outbound_endpoints import OutboundEndpoint

REMOTE_WRITE_URL = "http://mimir:9009/api/v1/push"


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


def test_build_renders_metrics_scrape_without_remote_write_sink():
    job = MetricsScrapeJob(
        job_name="polkadot",
        targets=[ScrapeTarget(address="10.0.0.5:9615", labels={"juju_application": "polkadot"})],
        metrics_path="/metrics",
    )
    builder = ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=[],
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
    assert "forward_to = []" in config
    assert 'prometheus.remote_write "metrics"' not in config


def test_build_renders_log_pipeline_without_loki_sink():
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
    assert 'loki.source.journal "journald"' in config
    assert 'loki.process "juju"' in config
    assert "forward_to = []" in config
    assert 'loki.write "main"' not in config


def test_build_renders_remote_write_with_basic_auth_and_ca_pem():
    job = MetricsScrapeJob(
        job_name="polkadot",
        targets=[ScrapeTarget(address="10.0.0.5:9615")],
    )
    builder = ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=[
            OutboundEndpoint(
                url="https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push",
                username="1076854",
                password="glc_token",
                tls_ca_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            )
        ],
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

    assert "basic_auth {" in config
    assert 'username = "1076854"' in config
    assert 'password = "glc_token"' in config
    assert 'ca_pem = "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----\\n"' in config


def test_build_renders_loki_write_with_basic_auth_and_ca_pem():
    builder = ConfigBuilder(
        loki_endpoints=[
            OutboundEndpoint(
                url="https://logs-prod-006.grafana.net/loki/api/v1/push",
                username="1076854",
                password="glc_token",
                tls_ca_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            )
        ],
        remote_write_endpoints=[],
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

    assert 'loki.write "main"' in config
    assert "basic_auth {" in config
    assert 'username = "1076854"' in config
    assert 'password = "glc_token"' in config
    assert 'ca_pem = "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----\\n"' in config


def _host_metrics_builder(*, host_metrics, remote_write_endpoints=(REMOTE_WRITE_URL,)):
    return ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=list(remote_write_endpoints),
        metrics_scrape_jobs=[],
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
        host_metrics=host_metrics,
    )


def test_build_omits_host_metrics_when_not_requested():
    config = _host_metrics_builder(host_metrics=None).build()

    assert "prometheus.exporter.unix" not in config
    assert "discovery.relabel" not in config


def test_build_renders_exporter_relabel_and_scrape_for_host_metrics():
    config = _host_metrics_builder(
        host_metrics=HostMetrics(
            topology_labels={"juju_application": "polkadot", "juju_unit": "polkadot/0"},
            scrape_timeout="5s",
        )
    ).build()

    assert 'prometheus.exporter.unix "node" {' in config
    assert 'discovery.relabel "node" {' in config
    assert "  targets = prometheus.exporter.unix.node.targets" in config
    assert 'prometheus.scrape "node" {' in config
    assert "  targets = discovery.relabel.node.output" in config
    assert '  job_name = "node-exporter"' in config
    assert '  scrape_timeout = "5s"' in config
    assert "  forward_to = [prometheus.remote_write.metrics.receiver]" in config


def test_host_metrics_scrape_interval_is_pinned_regardless_of_global():
    config = _host_metrics_builder(host_metrics=HostMetrics()).build()

    assert '  scrape_interval = "15s"' in config
    assert '  scrape_interval = "1m"' not in config


def test_host_metrics_falls_back_to_the_global_scrape_timeout():
    config = _host_metrics_builder(host_metrics=HostMetrics()).build()

    assert '  scrape_timeout = "10s"' in config


def test_host_metrics_attaches_topology_labels_as_relabel_rules():
    config = _host_metrics_builder(host_metrics=HostMetrics(topology_labels={"juju_unit": "polkadot/0"})).build()

    assert '    target_label = "juju_unit"' in config
    assert '    replacement  = "polkadot/0"' in config


def test_host_metrics_renders_disabled_collectors():
    config = _host_metrics_builder(host_metrics=HostMetrics(disable_collectors=["mdadm", "zfs"])).build()

    assert '  disable_collectors = ["mdadm", "zfs"]' in config


def test_host_metrics_forwards_nowhere_without_remote_write():
    config = _host_metrics_builder(host_metrics=HostMetrics(), remote_write_endpoints=()).build()

    assert "  forward_to = []" in config
