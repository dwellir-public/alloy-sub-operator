import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import pytest
from charms.dwellir_observability.v0.pprof_scrape import PROFILE_TYPES

from src.config_builder import (
    ConfigBuilder,
    FileLogSource,
    HostMetrics,
    MetricsScrapeJob,
    ProfilingSource,
    ScrapeTarget,
    parse_go_duration,
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

    assert '  scrape_interval = "60s"' in config
    assert '  scrape_interval = "1m"' not in config


def test_host_metrics_netstat_preserves_defaults_and_adds_tcp_fields():
    config = _host_metrics_builder(host_metrics=HostMetrics()).build()
    match = re.search(r'netstat \{ fields = "([^"]+)" \}', config)
    assert match is not None
    fields = re.compile(match.group(1))

    # Exercise every alternative in Alloy's default allowlist, including wildcards.
    # https://github.com/grafana/alloy/blob/v1.20.1/internal/static/integrations/node_exporter/config.go
    default_fields = [
        "Ip_InErrors",
        "Tcp_InErrs",
        "Ip_Forwarding",
        "Ip6_InOctets",
        "Ip6_OutOctets",
        "IpExt_InOctets",
        "IpExt_OutOctets",
        "Icmp_InMsgs",
        "Icmp_OutMsgs",
        "Icmp6_InMsgs",
        "Icmp6_OutMsgs",
        "TcpExt_ListenOverflows",
        "TcpExt_ListenDrops",
        "TcpExt_SyncookiesSent",
        "TcpExt_SyncookiesRecv",
        "TcpExt_SyncookiesFailed",
        "TcpExt_TCPSynRetrans",
        "TcpExt_TCPTimeouts",
        "Tcp_ActiveOpens",
        "Tcp_InSegs",
        "Tcp_OutSegs",
        "Tcp_OutRsts",
        "Tcp_PassiveOpens",
        "Tcp_RetransSegs",
        "Tcp_CurrEstab",
    ]
    default_fields.extend(
        f"{protocol}_{field}"
        for protocol in ("Udp", "Udp6")
        for field in ("InDatagrams", "OutDatagrams", "NoPorts", "RcvbufErrors", "SndbufErrors")
    )
    additional_fields = [
        "TcpExt_TCPDSACKRecv",
        "TcpExt_TCPLossProbes",
        "TcpExt_TCPToZeroWindowAdv",
        "TcpExt_TCPFromZeroWindowAdv",
        "TcpExt_TCPWantZeroWindowAdv",
        "TcpExt_TCPRcvQDrop",
        "TcpExt_TCPBacklogDrop",
    ]
    for field in default_fields + additional_fields:
        assert fields.search(field), f"Missing netstat field: {field}"
    for field in ("Ip_DefaultTTL", "TcpExt_TCPDSACKSent", "prefix_Tcp_ActiveOpens", "Tcp_ActiveOpens_suffix"):
        assert not fields.search(field), f"Unexpected netstat field: {field}"


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


GOLDEN_NO_PROFILING = Path(__file__).parent / "fixtures" / "golden_no_profiling.alloy"


def _profiling_builder(**overrides):
    kwargs = {
        "loki_endpoints": [],
        "remote_write_endpoints": [],
        "metrics_scrape_jobs": [],
        "systemd_units": [],
        "journal_match_expressions": [],
        "file_log_sources": [],
        "topology_labels": {},
        "global_scrape_interval": "1m",
        "global_scrape_timeout": "10s",
        "path_exclude": [],
        "queue_size": 1000,
        "max_elapsed_time_min": 5,
        "tls_insecure_skip_verify": False,
    }
    kwargs.update(overrides)
    return ConfigBuilder(**kwargs)


def _gotodo_source(unit="gotodo/0", name="gotodo", types=("process_cpu", "godeltaprof_memory", "goroutine")):
    return ProfilingSource(
        service_name=name,
        targets=["127.0.0.1:6060"],
        labels={"juju_application": name, "juju_unit": unit, "instance": unit},
        profile_types=list(types),
    )


def test_build_without_profiling_matches_main_golden():
    job = MetricsScrapeJob(
        job_name="polkadot",
        targets=[ScrapeTarget(address="10.0.0.5:9615", labels={"juju_application": "polkadot"})],
        metrics_path="/metrics",
    )
    labels = {"juju_application": "polkadot", "juju_unit": "polkadot/0"}
    builder = _profiling_builder(
        loki_endpoints=["http://loki:3100/loki/api/v1/push"],
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        metrics_scrape_jobs=[job],
        systemd_units=["snap.polkadot.polkadot.service"],
        topology_labels=labels,
        host_metrics=HostMetrics(topology_labels=labels),
    )

    assert builder.build() == GOLDEN_NO_PROFILING.read_text()


def test_build_omits_pyroscope_without_sink():
    config = _profiling_builder(profiling_sources=[_gotodo_source()], profiling_sink_url=None).build()

    assert "pyroscope." not in config


def test_build_omits_pyroscope_without_sources():
    config = _profiling_builder(profiling_sources=[], profiling_sink_url="http://10.0.0.9:4040").build()

    assert "pyroscope." not in config


def test_build_renders_pyroscope_write_and_scrape():
    config = _profiling_builder(profiling_sources=[_gotodo_source()], profiling_sink_url="http://10.0.0.9:4040").build()

    assert 'pyroscope.write "pyroscope" {' in config
    assert 'url = "http://10.0.0.9:4040"' in config
    assert 'pyroscope.scrape "gotodo_gotodo_0" {' in config
    assert '"__address__" = "127.0.0.1:6060"' in config
    assert '"service_name" = "gotodo"' in config
    assert '"instance" = "gotodo/0"' in config
    assert re.search(r"forward_to\s+=\s+\[pyroscope\.write\.pyroscope\.receiver\]", config)
    assert 'scrape_interval = "15s"' in config
    enabled = {"process_cpu", "godeltaprof_memory", "goroutine"}
    for profile_type in PROFILE_TYPES:
        pattern = rf"profile\.{profile_type} \{{\s+enabled = (true|false)\s+\}}"
        match = re.search(pattern, config)
        assert match, profile_type
        assert match.group(1) == ("true" if profile_type in enabled else "false"), profile_type


def test_build_renders_one_scrape_per_source():
    config = _profiling_builder(
        profiling_sources=[_gotodo_source(), _gotodo_source(unit="my-app/0", name="my-app")],
        profiling_sink_url="http://10.0.0.9:4040",
    ).build()

    assert config.count("pyroscope.scrape ") == 2
    assert config.count("pyroscope.write ") == 1


def test_pyroscope_component_names_are_sanitized_and_unique():
    config = _profiling_builder(
        profiling_sources=[
            _gotodo_source(unit="app-1/12", name="my.svc-x"),
            _gotodo_source(unit="app-1/13", name="my.svc-x"),
        ],
        profiling_sink_url="http://10.0.0.9:4040",
    ).build()

    assert 'pyroscope.scrape "my_svc_x_app_1_12" {' in config
    assert 'pyroscope.scrape "my_svc_x_app_1_13" {' in config


def test_profiles_scrape_interval_is_rendered():
    config = _profiling_builder(
        profiling_sources=[_gotodo_source()],
        profiling_sink_url="http://10.0.0.9:4040",
        profiles_scrape_interval="30s",
    ).build()

    assert 'scrape_interval = "30s"' in config
    assert 'scrape_interval = "15s"' not in config


@pytest.mark.parametrize(
    ("interval", "duration"),
    [
        ("15s", "14s"),
        ("10s", "9s"),
        ("3s", "2s"),
        ("2500ms", "1500ms"),
        ("1m", "59s"),
        ("1m30s", "89s"),
        ("1.5m", "89s"),
    ],
)
@pytest.mark.parametrize("delta_type", ["process_cpu", "fgprof"])
def test_delta_profiling_duration_tracks_scrape_interval(interval, duration, delta_type):
    """Alloy rejects delta_profiling_duration > scrape_interval - 1s at runtime, not in `alloy fmt`."""
    config = _profiling_builder(
        profiling_sources=[_gotodo_source(types=(delta_type, "goroutine"))],
        profiling_sink_url="http://10.0.0.9:4040",
        profiles_scrape_interval=interval,
    ).build()

    assert f'  delta_profiling_duration = "{duration}"' in config
    _assert_alloy_delta_rules(interval, duration)


def _assert_alloy_delta_rules(interval: str, duration: str) -> None:
    """Mirror pyroscope.scrape Arguments.Validate in Alloy v1.21.3 for delta targets."""
    interval_ns = parse_go_duration(interval)
    duration_ns = parse_go_duration(duration)
    assert interval_ns >= 2 * 10**9
    assert duration_ns > 10**9
    assert duration_ns <= interval_ns - 10**9


def test_delta_profiling_duration_omitted_without_delta_types():
    config = _profiling_builder(
        profiling_sources=[_gotodo_source(types=("goroutine", "godeltaprof_memory"))],
        profiling_sink_url="http://10.0.0.9:4040",
        profiles_scrape_interval="2s",
    ).build()

    assert "delta_profiling_duration" not in config
    assert 'scrape_interval = "2s"' in config


@pytest.mark.parametrize("interval", ["2s", "1500ms", "1s"])
def test_too_short_interval_with_delta_types_is_rejected(interval):
    builder = _profiling_builder(
        profiling_sources=[_gotodo_source(types=("process_cpu",))],
        profiling_sink_url="http://10.0.0.9:4040",
        profiles_scrape_interval=interval,
    )

    with pytest.raises(ValueError, match="must be longer than 2s"):
        builder.build()


@pytest.mark.parametrize("interval", ["", "10", "ten seconds", "10x", "-10s", "s"])
def test_unparseable_interval_with_delta_types_is_rejected(interval):
    builder = _profiling_builder(
        profiling_sources=[_gotodo_source(types=("process_cpu",))],
        profiling_sink_url="http://10.0.0.9:4040",
        profiles_scrape_interval=interval,
    )

    with pytest.raises(ValueError, match="invalid duration"):
        builder.build()


@pytest.mark.parametrize(
    ("text", "nanoseconds"),
    [("0", 0), ("15s", 15 * 10**9), ("1h2m3s", 3723 * 10**9), ("250ms", 250 * 10**6), ("1.5s", 1500 * 10**6)],
)
def test_parse_go_duration(text, nanoseconds):
    assert parse_go_duration(text) == nanoseconds


def test_profiling_labels_cannot_override_address_or_service_name():
    source = ProfilingSource(
        service_name="gotodo",
        targets=["127.0.0.1:6060"],
        labels={"__address__": "169.254.169.254:80", "service_name": "evil", "instance": "gotodo/0"},
        profile_types=["process_cpu"],
    )
    config = _profiling_builder(profiling_sources=[source], profiling_sink_url="http://10.0.0.9:4040").build()

    assert '"__address__" = "127.0.0.1:6060"' in config
    assert '"service_name" = "gotodo"' in config
    assert "169.254.169.254" not in config
    assert "evil" not in config
