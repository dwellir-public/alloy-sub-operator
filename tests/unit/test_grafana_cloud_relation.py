import sys
from pathlib import Path

from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm


def test_grafana_cloud_relation_exposes_metrics_and_logs():
    harness = testing.Harness(AlloySubCharm)
    harness.begin()

    relation_id = harness.add_relation("grafana-cloud-config", "grafana-cloud-integrator")
    harness.add_relation_unit(relation_id, "grafana-cloud-integrator/0")
    harness.update_relation_data(
        relation_id,
        "grafana-cloud-integrator",
        {
            "prometheus_url": "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push",
            "loki_url": "https://logs-prod-006.grafana.net/loki/api/v1/push",
            "username": "1076854",
            "password": "glc_token",
            "tls-ca": "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
        },
    )

    metrics = harness.charm._grafana_cloud_metrics_endpoints()
    logs = harness.charm._grafana_cloud_loki_endpoints()

    assert len(metrics) == 1
    assert metrics[0].url == "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push"
    assert metrics[0].username == "1076854"
    assert metrics[0].password == "glc_token"
    assert metrics[0].tls_ca_pem.startswith("-----BEGIN CERTIFICATE-----")

    assert len(logs) == 1
    assert logs[0].url == "https://logs-prod-006.grafana.net/loki/api/v1/push"
    assert logs[0].username == "1076854"
    assert logs[0].password == "glc_token"
    assert logs[0].tls_ca_pem.startswith("-----BEGIN CERTIFICATE-----")


def test_grafana_cloud_relation_merges_with_existing_remote_write_and_loki_relations():
    harness = testing.Harness(AlloySubCharm)
    harness.begin()

    loki_relation_id = harness.add_relation("send-loki-logs", "loki")
    harness.add_relation_unit(loki_relation_id, "loki/0")
    harness.update_relation_data(
        loki_relation_id,
        "loki",
        {"url": "http://loki:3100/loki/api/v1/push"},
    )

    remote_write_relation_id = harness.add_relation("send-remote-write", "mimir")
    harness.add_relation_unit(remote_write_relation_id, "mimir/0")
    harness.update_relation_data(
        remote_write_relation_id,
        "mimir",
        {"remote_write": '{"url": "http://mimir:9009/api/v1/push"}'},
    )

    cloud_relation_id = harness.add_relation("grafana-cloud-config", "grafana-cloud-integrator")
    harness.add_relation_unit(cloud_relation_id, "grafana-cloud-integrator/0")
    harness.update_relation_data(
        cloud_relation_id,
        "grafana-cloud-integrator",
        {
            "prometheus_url": "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push",
            "loki_url": "https://logs-prod-006.grafana.net/loki/api/v1/push",
            "username": "1076854",
            "password": "glc_token",
        },
    )

    remote_write = harness.charm._remote_write_endpoint_urls()
    loki = harness.charm._loki_endpoint_urls()

    assert [endpoint.url for endpoint in remote_write] == [
        "http://mimir:9009/api/v1/push",
        "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push",
    ]
    assert [endpoint.url for endpoint in loki] == [
        "http://loki:3100/loki/api/v1/push",
        "https://logs-prod-006.grafana.net/loki/api/v1/push",
    ]


def test_grafana_cloud_relation_uses_signal_specific_credentials():
    harness = testing.Harness(AlloySubCharm)
    harness.begin()

    relation_id = harness.add_relation("grafana-cloud-config", "grafana-cloud-integrator")
    harness.add_relation_unit(relation_id, "grafana-cloud-integrator/0")
    harness.update_relation_data(
        relation_id,
        "grafana-cloud-integrator",
        {
            "prometheus_url": "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push",
            "prometheus_username": "1076854",
            "prometheus_password": "prom-token",
            "loki_url": "https://logs-prod-025.grafana.net/loki/api/v1/push",
            "loki_username": "639149",
            "loki_password": "loki-token",
        },
    )

    metrics = harness.charm._grafana_cloud_metrics_endpoints()
    logs = harness.charm._grafana_cloud_loki_endpoints()

    assert len(metrics) == 1
    assert metrics[0].username == "1076854"
    assert metrics[0].password == "prom-token"

    assert len(logs) == 1
    assert logs[0].username == "639149"
    assert logs[0].password == "loki-token"
