# ruff: noqa: E402

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from charms.dwellir_observability.v0.machine_observability import MetricsEndpoint

from charm import merge_file_excludes, translate_metrics_endpoint


def test_metrics_translation_uses_principal_application_for_first_job_name():
    endpoint = MetricsEndpoint.model_validate({"targets": ["localhost:9615"], "path": "/metrics", "scheme": "http"})

    translated = translate_metrics_endpoint(
        endpoint,
        principal_application="polkadot",
        source_index=0,
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
    )

    assert translated.job_name == "polkadot"
    assert translated.targets[0].labels == {}
    assert translated.metrics_path == "/metrics"
    assert translated.scrape_interval == "1m"
    assert translated.scrape_timeout == "10s"


def test_metrics_translation_suffixes_subsequent_job_names():
    endpoint = MetricsEndpoint.model_validate({"targets": ["localhost:9616"]})

    translated = translate_metrics_endpoint(
        endpoint,
        principal_application="polkadot",
        source_index=1,
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
    )

    assert translated.job_name == "polkadot-1"
    assert translated.targets[0].address == "localhost:9616"


def test_path_exclude_is_added_to_file_log_excludes():
    assert merge_file_excludes(
        ["/var/log/polkadot/archive/**"],
        "/var/log/juju/**;/var/log/syslog",
    ) == [
        "/var/log/polkadot/archive/**",
        "/var/log/juju/**",
        "/var/log/syslog",
    ]
