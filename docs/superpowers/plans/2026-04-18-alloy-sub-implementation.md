# Alloy-Sub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `alloy-sub` prototype with a production-grade subordinate charm that reuses `alloy-vm` patterns, consumes a new neutral `machine-observability` relation, labels logs and metrics like `opentelemetry-collector`, and supports `polkadot-operator` as the first provider.

**Architecture:** Keep `src/charm.py` orchestration-focused and move workload, config generation, relation parsing, and principal-context handling into dedicated modules. Implement relation-driven Alloy config generation modeled on `alloy-vm`, but derive labels and unit identity from the attached principal unit in the same way Canonical subordinate observability patterns do. Make the first implementation cross-repo: the main work lives in `ops/juju/charms/alloy-sub`, and a small provider implementation lands in `polkadot-operator`.

**Tech Stack:** Python `ops`, Grafana Alloy, `prometheus_scrape` charmlib, `prometheus_remote_write` charmlib, `loki_push_api` charmlib, `ops.testing`, `pytest`, `tox`, `uv`, Juju subordinate relations.

---

### Task 1: Rebuild `alloy-sub` Repository Baseline

**Files:**
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/DEVELOPING.md`
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/docs/charm-architecture.md`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/charmcraft.yaml`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/pyproject.toml`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tox.ini`
- Test: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/unit/test_charm.py`

- [ ] **Step 1: Write the failing repository-baseline tests**

```python
from pathlib import Path


def test_developing_doc_exists():
    assert Path("DEVELOPING.md").exists()


def test_architecture_doc_exists():
    assert Path("docs/charm-architecture.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_repo_baseline.py -v`
Expected: FAIL with missing `DEVELOPING.md` and `docs/charm-architecture.md`.

- [ ] **Step 3: Replace the current metadata and packaging baseline with a `uv`-style machine-charm baseline**

```yaml
name: alloy-sub
type: charm
title: Grafana Alloy Subordinate
summary: Subordinate charm for workload-local metrics and logs using Grafana Alloy.
description: |
  alloy-sub is a machine subordinate that attaches to a principal over juju-info,
  consumes workload observability declarations over machine-observability, scrapes
  workload-local metrics, captures journald and file logs, and forwards telemetry
  to Loki and Prometheus-compatible remote write backends.
subordinate: true

platforms:
  ubuntu@24.04:amd64:

parts:
  charm:
    plugin: uv
    source: .
    build-snaps:
      - astral-uv

requires:
  juju-info:
    interface: juju-info
    scope: container
  machine-observability:
    interface: machine_observability
    scope: container
    optional: true
  send-loki-logs:
    interface: loki_push_api
    optional: true
  send-remote-write:
    interface: prometheus_remote_write
    optional: true
```

```toml
[project]
name = "alloy-sub"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "ops>=3,<4",
  "jinja2",
  "requests",
  "pydantic>=2,<3",
]

[dependency-groups]
lint = ["ruff", "codespell"]
unit = ["pytest", "coverage[toml]", "ops[testing]"]
integration = ["pytest", "pytest-operator", "juju"]
dev = [
  {include-group = "lint"},
  {include-group = "unit"},
  {include-group = "integration"},
  "pyright",
]
```

```ini
[tox]
no_package = True
skip_missing_interpreters = True
env_list = format, lint, static, unit
min_version = 4.0.0

[testenv]
set_env =
    PYTHONPATH = {tox_root}/lib:{tox_root}/src
pass_env =
    CHARM_PATH

[testenv:format]
commands =
    uv run ruff format src tests
    uv run ruff check --fix src tests

[testenv:lint]
commands =
    uv run codespell {tox_root}
    uv run ruff check src tests
    uv run ruff format --check src tests

[testenv:static]
commands =
    uv run pyright src

[testenv:unit]
commands =
    uv run coverage run --source=src -m pytest -v tests/unit
    uv run coverage report
```

- [ ] **Step 4: Add the missing developer docs**

```md
# Developing alloy-sub

## Local setup

```bash
uv sync --group dev
tox -e format
tox -e lint
tox -e static
tox -e unit
charmcraft pack
```

## Integration

```bash
CHARM_PATH=/path/to/alloy-sub.charm uv run pytest tests/integration -v
```
```

```md
# alloy-sub Charm Architecture

## Overview

alloy-sub is a machine subordinate that attaches to a principal using `juju-info`
and consumes workload observability declarations from the principal over
`machine-observability`.

## Responsibilities

- render and validate `/etc/alloy/config.alloy`
- scrape principal metrics
- collect principal journald and file logs
- forward logs to Loki
- forward metrics via remote write
```

- [ ] **Step 5: Run the baseline tests**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_repo_baseline.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git -C /home/erik/dwellir-public/ops add \
  juju/charms/alloy-sub/charmcraft.yaml \
  juju/charms/alloy-sub/pyproject.toml \
  juju/charms/alloy-sub/tox.ini \
  juju/charms/alloy-sub/DEVELOPING.md \
  juju/charms/alloy-sub/docs/charm-architecture.md \
  juju/charms/alloy-sub/tests/unit/test_repo_baseline.py
git -C /home/erik/dwellir-public/ops commit -m "chore: reset alloy-sub baseline"
```

### Task 2: Add Neutral `machine-observability` Relation Parsing and Principal Context

**Files:**
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/types.py`
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/observability_relation.py`
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/principal_context.py`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/charm.py`
- Test: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/unit/test_observability_relation.py`

- [ ] **Step 1: Write failing unit tests for relation parsing and principal context**

```python
from ops import testing

from observability_relation import MachineObservabilityPayload
from principal_context import PrincipalContext


def test_principal_context_prefers_attached_principal_unit():
    relation = testing.SubordinateRelation(
        "juju-info",
        remote_app_name="polkadot",
        remote_unit_data={"private-address": "10.0.0.5"},
    )
    ctx = PrincipalContext.from_relation(relation)
    assert ctx.application == "polkadot"
    assert ctx.unit == "polkadot/0"


def test_machine_observability_payload_parses_journal_and_metrics():
    payload = MachineObservabilityPayload.model_validate(
        {
            "systemd_units": ["snap.polkadot.polkadot.service"],
            "metrics_jobs": [
                {
                    "job_name": "polkadot",
                    "metrics_path": "/metrics",
                    "static_configs": [{"targets": ["localhost:9615"]}],
                }
            ],
        }
    )
    assert payload.systemd_units == ["snap.polkadot.polkadot.service"]
    assert payload.metrics_jobs[0].job_name == "polkadot"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_observability_relation.py -v`
Expected: FAIL with missing modules and models.

- [ ] **Step 3: Add typed value objects and payload schema**

```python
from pydantic import BaseModel, Field


class MetricsStaticConfig(BaseModel):
    targets: list[str]
    labels: dict[str, str] = Field(default_factory=dict)


class MetricsJob(BaseModel):
    job_name: str
    metrics_path: str = "/metrics"
    scheme: str = "http"
    scrape_interval: str = ""
    scrape_timeout: str = ""
    static_configs: list[MetricsStaticConfig]
    tls_config: dict[str, str | bool] = Field(default_factory=dict)


class MachineObservabilityPayload(BaseModel):
    systemd_units: list[str] = Field(default_factory=list)
    journal_match_expressions: list[str] = Field(default_factory=list)
    metrics_jobs: list[MetricsJob] = Field(default_factory=list)
    log_files_include: list[str] = Field(default_factory=list)
    log_files_exclude: list[str] = Field(default_factory=list)
    log_attributes: dict[str, str] = Field(default_factory=dict)
    workload_labels: dict[str, str] = Field(default_factory=dict)
```

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PrincipalContext:
    model: str
    model_uuid: str
    application: str
    unit: str
    charm_name: str
    address: str

    def juju_labels(self) -> dict[str, str]:
        return {
            "juju_model": self.model,
            "juju_model_uuid": self.model_uuid,
            "juju_application": self.application,
            "juju_unit": self.unit,
            "juju_charm": self.charm_name,
        }
```

- [ ] **Step 4: Add relation helper code that reads payload from the principal app databag**

```python
import json

from ops.model import Relation

from types import MachineObservabilityPayload


def load_machine_observability_payload(relation: Relation) -> MachineObservabilityPayload:
    if relation.app is None:
        return MachineObservabilityPayload()
    raw = relation.data[relation.app].get("payload", "{}")
    return MachineObservabilityPayload.model_validate(json.loads(raw))
```

- [ ] **Step 5: Wire `src/charm.py` to read principal context and payload**

```python
self.framework.observe(
    self.on["machine-observability"].relation_changed,
    self._on_observability_relation_changed,
)
self.framework.observe(
    self.on.juju_info_relation_joined,
    self._on_principal_relation_changed,
)
```

```python
def _principal_context(self) -> PrincipalContext | None:
    relation = self.model.get_relation("juju-info")
    if relation is None or not relation.units:
        return None
    return PrincipalContext.from_relation(self.model, relation)
```

- [ ] **Step 6: Run the new relation and principal-context tests**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_observability_relation.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git -C /home/erik/dwellir-public/ops add \
  juju/charms/alloy-sub/src/types.py \
  juju/charms/alloy-sub/src/observability_relation.py \
  juju/charms/alloy-sub/src/principal_context.py \
  juju/charms/alloy-sub/src/charm.py \
  juju/charms/alloy-sub/tests/unit/test_observability_relation.py
git -C /home/erik/dwellir-public/ops commit -m "feat: add alloy-sub observability contract"
```

### Task 3: Port `alloy-vm` Workload and Config Builder Into Subordinate Form

**Files:**
- Replace: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/alloy.py`
- Create: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/config_builder.py`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/charm.py`
- Test: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/unit/test_config_builder.py`
- Test: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/unit/test_charm.py`

- [ ] **Step 1: Write failing config-builder tests for subordinate journald, file logs, and metrics**

```python
from config_builder import ConfigBuilder, MetricsScrapeJob, ScrapeTarget


def test_build_renders_journal_source_with_principal_labels():
    builder = ConfigBuilder(
        loki_endpoints=["http://loki:3100/loki/api/v1/push"],
        remote_write_endpoints=[],
        metrics_scrape_jobs=[],
        systemd_units=["snap.polkadot.polkadot.service"],
        journal_match_expressions=[],
        file_log_includes=[],
        file_log_excludes=[],
        file_log_attributes={},
        topology_labels={"juju_application": "polkadot", "juju_unit": "polkadot/0"},
        workload_labels={"chain_name": "polkadot"},
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


def test_build_renders_file_log_source():
    builder = ConfigBuilder(
        loki_endpoints=["http://loki:3100/loki/api/v1/push"],
        remote_write_endpoints=[],
        metrics_scrape_jobs=[],
        systemd_units=[],
        journal_match_expressions=[],
        file_log_includes=["/var/log/polkadot/*.log"],
        file_log_excludes=["/var/log/polkadot/archive/**"],
        file_log_attributes={"node_role": "rpc"},
        topology_labels={"juju_application": "polkadot"},
        workload_labels={},
        global_scrape_interval="1m",
        global_scrape_timeout="10s",
        path_exclude=[],
        queue_size=1000,
        max_elapsed_time_min=5,
        tls_insecure_skip_verify=False,
    )
    config = builder.build()
    assert 'local.file_match "filelogs"' in config
    assert '/var/log/polkadot/*.log' in config
    assert '/var/log/polkadot/archive/**' in config


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
        file_log_includes=[],
        file_log_excludes=[],
        file_log_attributes={},
        topology_labels={},
        workload_labels={},
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_config_builder.py -v`
Expected: FAIL with missing builder and unsupported rendered blocks.

- [ ] **Step 3: Replace the prototype `src/alloy.py` with the `alloy-vm` workload helper shape**

```python
DEFAULT_CONFIG_DIR = "/etc/alloy"
DEFAULT_CONFIG_PATH = f"{DEFAULT_CONFIG_DIR}/config.alloy"
DEFAULT_PACKAGE_CONFIG_BACKUP_PATH = f"{DEFAULT_CONFIG_DIR}/config.alloy.package-default"
DEFAULT_CONFIG_BACKUP_PATH = f"{DEFAULT_CONFIG_DIR}/config.alloy.bak"
DEFAULT_SYSTEMD_DEFAULTS_PATH = "/etc/default/alloy"


def install() -> None:
    _run(["apt-get", "install", "-y", "alloy"])
    _systemctl("enable", "alloy")


def restart() -> None:
    _systemctl("restart", "alloy")
    _wait_for_active("alloy")


def verify_config(*, config_path: Path, timeout: int = 30) -> None:
    _run(["alloy", "fmt", str(config_path)], timeout=timeout)
```

- [ ] **Step 4: Port and adapt the `alloy-vm` config builder**

```python
class ConfigBuilder:
    def __init__(
        self,
        *,
        loki_endpoints: list[str],
        remote_write_endpoints: list[str],
        metrics_scrape_jobs: list[MetricsScrapeJob],
        systemd_units: list[str],
        journal_match_expressions: list[str],
        file_log_includes: list[str],
        file_log_excludes: list[str],
        file_log_attributes: dict[str, str],
        topology_labels: dict[str, str],
        workload_labels: dict[str, str],
        global_scrape_interval: str,
        global_scrape_timeout: str,
        path_exclude: list[str],
        queue_size: int,
        max_elapsed_time_min: int,
        tls_insecure_skip_verify: bool,
    ):
        ...
```

```python
def _render_service_journal_sources(self) -> list[str]:
    return [
        "\n".join(
            [
                f'loki.source.journal "{name}" {{',
                f'  matches = "{self._format_unit_match(unit)}"',
                '  relabel_rules = loki.relabel.journal.rules',
                '  labels = {log_source = "journal", systemd_unit = "%s"}' % unit,
                "  forward_to = [loki.process.juju.receiver]",
                "}",
            ]
        )
        for name, unit in self._enumerated_systemd_units()
    ]
```

```python
def _render_filelog_sources(self) -> list[str]:
    if not self._file_log_includes:
        return []
    return [
        'local.file_match "filelogs" {',
        "  path_targets = [",
        *self._render_file_targets(),
        "  ]",
        "}",
        "",
        'loki.source.file "filelogs" {',
        "  targets    = local.file_match.filelogs.targets",
        "  forward_to = [loki.process.juju.receiver]",
        "}",
    ]
```

- [ ] **Step 5: Rewrite `src/charm.py` around the new config builder and relation-driven inputs**

```python
self._loki_consumer = LokiPushApiConsumer(self, relation_name="send-loki-logs", forward_alert_rules=False)
self._remote_write_consumer = PrometheusRemoteWriteConsumer(
    self,
    relation_name="send-remote-write",
    peer_relation_name="alloy-peers",
    forward_alert_rules=False,
)
```

```python
builder = ConfigBuilder(
    loki_endpoints=self._loki_endpoint_urls(),
    remote_write_endpoints=self._remote_write_endpoint_urls(),
    metrics_scrape_jobs=self._active_metrics_scrape_jobs(),
    systemd_units=payload.systemd_units,
    journal_match_expressions=payload.journal_match_expressions,
    file_log_includes=payload.log_files_include,
    file_log_excludes=payload.log_files_exclude + self._path_exclude_patterns(),
    file_log_attributes=payload.log_attributes,
    topology_labels=principal_context.juju_labels(),
    workload_labels=payload.workload_labels,
    global_scrape_interval=str(self.config.get("global_scrape_interval")),
    global_scrape_timeout=str(self.config.get("global_scrape_timeout")),
    path_exclude=self._path_exclude_patterns(),
    queue_size=int(self.config.get("queue_size")),
    max_elapsed_time_min=int(self.config.get("max_elapsed_time_min")),
    tls_insecure_skip_verify=bool(self.config.get("tls_insecure_skip_verify")),
)
```

- [ ] **Step 6: Run unit tests for builder and charm flow**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_config_builder.py tests/unit/test_charm.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git -C /home/erik/dwellir-public/ops add \
  juju/charms/alloy-sub/src/alloy.py \
  juju/charms/alloy-sub/src/config_builder.py \
  juju/charms/alloy-sub/src/charm.py \
  juju/charms/alloy-sub/tests/unit/test_config_builder.py \
  juju/charms/alloy-sub/tests/unit/test_charm.py
git -C /home/erik/dwellir-public/ops commit -m "feat: port alloy-vm logic into alloy-sub"
```

### Task 4: Add Canonical-Compatible Labels, Metrics Translation, and Tuning Knobs

**Files:**
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/charmcraft.yaml`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/charm.py`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/src/config_builder.py`
- Test: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/unit/test_metrics_translation.py`

- [ ] **Step 1: Write failing tests for label compatibility and config tuning**

```python
def test_metrics_translation_keeps_principal_juju_labels():
    job = {
        "job_name": "polkadot",
        "metrics_path": "/metrics",
        "static_configs": [
            {
                "targets": ["localhost:9615"],
                "labels": {
                    "juju_model": "polka-obs",
                    "juju_model_uuid": "uuid",
                    "juju_application": "polkadot",
                    "juju_unit": "polkadot/0",
                },
            }
        ],
    }
    translated = translate_metrics_job(job)
    assert translated.targets[0].labels["juju_application"] == "polkadot"


def test_path_exclude_is_added_to_file_log_excludes():
    assert merge_file_excludes(
        ["/var/log/polkadot/archive/**"],
        "/var/log/juju/**;/var/log/syslog",
    ) == [
        "/var/log/polkadot/archive/**",
        "/var/log/juju/**",
        "/var/log/syslog",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_metrics_translation.py -v`
Expected: FAIL with missing translation helpers and config knobs.

- [ ] **Step 3: Add otelcol-inspired config knobs to `charmcraft.yaml`**

```yaml
config:
  options:
    path_exclude:
      type: string
      default: ""
    global_scrape_interval:
      type: string
      default: "1m"
    global_scrape_timeout:
      type: string
      default: "10s"
    tls_insecure_skip_verify:
      type: boolean
      default: false
    queue_size:
      type: int
      default: 1000
    max_elapsed_time_min:
      type: int
      default: 5
```

- [ ] **Step 4: Add translation helpers modeled on `alloy-vm` and `opentelemetry-collector`**

```python
def _path_exclude_patterns(self) -> list[str]:
    raw = str(self.config.get("path_exclude", "")).strip()
    return [pattern.strip() for pattern in raw.split(";") if pattern.strip()]
```

```python
def _translate_metrics_job(self, job: Mapping[str, object]) -> MetricsScrapeJob | None:
    static_configs = self._translate_static_configs(job)
    if not static_configs:
        return None
    return MetricsScrapeJob(
        job_name=str(job.get("job_name", "metrics-endpoint")),
        targets=static_configs,
        metrics_path=str(job.get("metrics_path", "/metrics")),
        scheme=str(job.get("scheme", "http")),
        scrape_interval=str(job.get("scrape_interval", self.config["global_scrape_interval"])),
        scrape_timeout=str(job.get("scrape_timeout", self.config["global_scrape_timeout"])),
        tls_config=self._translate_tls_config(job),
    )
```

- [ ] **Step 5: Map queue and retry knobs into Alloy-native config**

```python
def _render_remote_write(self) -> str:
    return "\n".join(
        [
            'prometheus.remote_write "metrics" {',
            *self._render_remote_write_endpoints(),
            "  wal {",
            f'    max_keepalive_time = "{self._max_elapsed_time_min}m"',
            "  }",
            "}",
        ]
    )
```

```python
def _render_loki_writer(self) -> str:
    return "\n".join(
        [
            'loki.write "main" {',
            *self._render_loki_endpoints(),
            "  external_labels = {}",
            "}",
        ]
    )
```

- [ ] **Step 6: Run metrics-translation tests**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && uv run pytest tests/unit/test_metrics_translation.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git -C /home/erik/dwellir-public/ops add \
  juju/charms/alloy-sub/charmcraft.yaml \
  juju/charms/alloy-sub/src/charm.py \
  juju/charms/alloy-sub/src/config_builder.py \
  juju/charms/alloy-sub/tests/unit/test_metrics_translation.py
git -C /home/erik/dwellir-public/ops commit -m "feat: align alloy-sub labels and tuning"
```

### Task 5: Implement `polkadot-operator` as the First `machine-observability` Provider

**Files:**
- Create: `/home/erik/dwellir-public/polkadot-operator/src/interface_machine_observability_provider.py`
- Modify: `/home/erik/dwellir-public/polkadot-operator/metadata.yaml`
- Modify: `/home/erik/dwellir-public/polkadot-operator/src/charm.py`
- Modify: `/home/erik/dwellir-public/polkadot-operator/tests/unit/test_charm.py`

- [ ] **Step 1: Write failing tests for the new provider payload**

```python
def test_machine_observability_payload_contains_polkadot_unit_and_metrics():
    payload = build_machine_observability_payload(
        service_name="snap.polkadot.polkadot.service",
        metrics_port=9615,
        chain_name="polkadot",
    )
    assert payload["systemd_units"] == ["snap.polkadot.polkadot.service"]
    assert payload["metrics_jobs"][0]["static_configs"][0]["targets"] == ["localhost:9615"]
    assert payload["workload_labels"]["chain_name"] == "polkadot"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/erik/dwellir-public/polkadot-operator && pytest tests/unit/test_charm.py -v`
Expected: FAIL with missing helper/provider.

- [ ] **Step 3: Add the new provider relation to metadata while keeping legacy relations during migration**

```yaml
provides:
  machine-observability:
    interface: machine_observability
  polkadot-prometheus:
    interface: prometheus-manual
  grafana-agent:
    interface: cos_agent
```

- [ ] **Step 4: Add a provider helper that publishes observability intent**

```python
import json


def build_machine_observability_payload(*, service_name: str, chain_name: str) -> dict:
    return {
        "systemd_units": [service_name],
        "journal_match_expressions": [],
        "metrics_jobs": [
            {
                "job_name": "polkadot",
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["localhost:9615"]}],
            }
        ],
        "log_files_include": [],
        "log_files_exclude": [],
        "log_attributes": {},
        "workload_labels": {
            "chain_name": chain_name,
            "chain_family": "substrate",
            "client_name": "polkadot",
        },
    }
```

```python
class MachineObservabilityProvider(Object):
    def publish(self, payload: dict) -> None:
        relation = self.model.get_relation("machine-observability")
        if relation is None or self.model.app is None:
            return
        relation.data[self.model.app]["payload"] = json.dumps(payload, sort_keys=True)
```

- [ ] **Step 5: Wire the provider into `src/charm.py`**

```python
self.machine_observability_provider = MachineObservabilityProvider(self, "machine-observability")
```

```python
def _publish_machine_observability(self) -> None:
    service_name = "snap.polkadot.polkadot.service"
    chain_name = ServiceArgs(self.config, self.rpc_urls()).chain_name
    payload = build_machine_observability_payload(
        service_name=service_name,
        chain_name=chain_name,
    )
    self.machine_observability_provider.publish(payload)
```

- [ ] **Step 6: Publish payload on install, config-changed, start, and update-status**

```python
self._publish_machine_observability()
```

- [ ] **Step 7: Run the polkadot unit tests**

Run: `cd /home/erik/dwellir-public/polkadot-operator && pytest tests/unit/test_charm.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git -C /home/erik/dwellir-public/polkadot-operator add \
  metadata.yaml \
  src/charm.py \
  src/interface_machine_observability_provider.py \
  tests/unit/test_charm.py
git -C /home/erik/dwellir-public/polkadot-operator commit -m "feat: publish machine observability data"
```

### Task 6: Add End-to-End Validation and Documentation

**Files:**
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/tests/integration/test_charm.py`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/README.md`
- Modify: `/home/erik/dwellir-public/ops/juju/charms/alloy-sub/docs/charm-architecture.md`

- [ ] **Step 1: Write failing integration tests for the subordinate relation shape**

```python
@pytest.mark.abort_on_fail
async def test_build_deploy_and_integrate_with_principal(ops_test: OpsTest):
    alloy_sub = await ops_test.build_charm(".")
    polkadot = "/home/erik/dwellir-public/polkadot-operator"
    polkadot_charm = await ops_test.build_charm(polkadot)

    await ops_test.model.deploy(alloy_sub, application_name="alloy-sub")
    await ops_test.model.deploy(
        polkadot_charm,
        application_name="polkadot",
        config={"service-args": "--chain=polkadot --rpc-port=9933", "snap-name": "polkadot"},
    )
    await ops_test.model.integrate("alloy-sub:juju-info", "polkadot:juju-info")
    await ops_test.model.integrate("alloy-sub:machine-observability", "polkadot:machine-observability")
```

- [ ] **Step 2: Run integration tests to verify they fail**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && CHARM_PATH=$(pwd) uv run pytest tests/integration/test_charm.py -v`
Expected: FAIL before the new relation and config wiring are implemented.

- [ ] **Step 3: Add integration assertions for rendered config and status**

```python
config = await ops_test.juju("ssh", "alloy-sub/0", "grep -n 'snap.polkadot.polkadot.service' /etc/alloy/config.alloy")
assert "snap.polkadot.polkadot.service" in config[1]

config = await ops_test.juju("ssh", "alloy-sub/0", "grep -n 'prometheus.scrape \"polkadot\"' /etc/alloy/config.alloy")
assert 'prometheus.scrape "polkadot"' in config[1]
```

- [ ] **Step 4: Update README with the new relation-driven subordinate flow**

```md
## Principal contract

`alloy-sub` attaches to a principal with `juju-info` and consumes
`machine-observability` declarations for:

- systemd unit logs
- file log sources
- metrics endpoints
- optional workload labels
```

- [ ] **Step 5: Document the final architecture and migration notes**

```md
## Relation Flows

- `juju-info`: subordinate attachment and principal unit discovery
- `machine-observability`: neutral observability declarations from the principal
- `send-loki-logs`: outbound Loki forwarding
- `send-remote-write`: outbound metrics forwarding
```

- [ ] **Step 6: Run the repository verification set**

Run: `cd /home/erik/dwellir-public/ops/juju/charms/alloy-sub && tox -e format && tox -e lint && tox -e static && tox -e unit`
Expected: PASS

Run: `cd /home/erik/dwellir-public/polkadot-operator && pytest tests/unit/test_charm.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git -C /home/erik/dwellir-public/ops add \
  juju/charms/alloy-sub/tests/integration/test_charm.py \
  juju/charms/alloy-sub/README.md \
  juju/charms/alloy-sub/docs/charm-architecture.md
git -C /home/erik/dwellir-public/ops commit -m "test: validate alloy-sub subordinate flow"
```

## Self-Review Notes

- Spec coverage:
  - neutral `machine-observability` relation: covered in Tasks 2 and 5
  - `alloy-vm`-style subordinate implementation: covered in Tasks 1, 3, and 4
  - otelcol-style labels and tuning parity: covered in Task 4
  - first provider `polkadot`: covered in Task 5
  - docs and validation: covered in Task 6
- Placeholder scan:
  - no `TBD` or `TODO` placeholders remain
  - the plan explicitly marks the first scope boundary: `polkadot-operator` as the first external principal repo
- Type consistency:
  - `MachineObservabilityPayload`, `MetricsJob`, `PrincipalContext`, `ConfigBuilder`, `MetricsScrapeJob`, and `ScrapeTarget` are named consistently across tasks
