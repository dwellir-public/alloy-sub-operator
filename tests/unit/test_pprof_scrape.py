import json
import logging
import sys
from pathlib import Path

import pytest
from ops import testing
from ops.charm import CharmBase

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charms.dwellir_observability.v0.pprof_scrape import (
    PprofScrapeProvider,
    PprofScrapeRequirer,
    PprofSource,
    is_loopback,
)

ENDPOINT = {"profiling-source": {"interface": "pprof_scrape", "scope": "container"}}
PROVIDER_META = {"name": "gotodo", "provides": ENDPOINT}
REQUIRER_META = {"name": "alloy-sub", "requires": ENDPOINT}

FACTORY = {"source": PprofSource("gotodo", ("127.0.0.1:6060",), ("process_cpu", "goroutine"))}


class ProviderCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.profiling = PprofScrapeProvider(self, source_factory=lambda: FACTORY["source"])


class RequirerCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.profiling = PprofScrapeRequirer(self)


def _provider_data(source):
    FACTORY["source"] = source
    context = testing.Context(ProviderCharm, meta=PROVIDER_META)
    relation = testing.SubordinateRelation("profiling-source", local_unit_data={"profiling_source": "stale"})
    state = context.run(context.on.relation_changed(relation), testing.State(relations=[relation]))
    return state.get_relation(relation.id).local_unit_data


def test_provider_publishes_iris_compatible_payload():
    data = _provider_data(PprofSource("gotodo", ("127.0.0.1:6060",), ("process_cpu", "goroutine")))
    payload = json.loads(data["profiling_source"])
    assert set(payload["labels"]) == {"juju_model", "juju_model_uuid", "juju_application", "juju_unit", "juju_charm"}
    assert payload["labels"]["juju_application"] == "gotodo"
    assert payload["labels"]["juju_charm"] == "gotodo"
    assert payload == {
        "version": 1,
        "service_name": "gotodo",
        "targets": ["127.0.0.1:6060"],
        "profile_types": ["process_cpu", "goroutine"],
        "labels": payload["labels"],
    }


def test_provider_withdraws_when_factory_returns_none():
    assert "profiling_source" not in _provider_data(None)


def _requirer_sources(raw):
    relation = testing.SubordinateRelation("profiling-source", remote_unit_data={"profiling_source": raw})
    context = testing.Context(RequirerCharm, meta=REQUIRER_META)
    with context(context.on.relation_changed(relation), testing.State(relations=[relation])) as mgr:
        sources = mgr.charm.profiling.get_sources()
    return sources, relation


def _payload(**overrides):
    base = {
        "version": 1,
        "service_name": "iris",
        "targets": ["127.0.0.1:6060"],
        "profile_types": ["process_cpu", "memory", "goroutine"],
        "labels": {
            "juju_model": "m",
            "juju_model_uuid": "u",
            "juju_application": "iris",
            "juju_unit": "iris/0",
            "juju_charm": "iris-operator",
        },
    }
    base.update(overrides)
    return json.dumps(base)


def test_requirer_decodes_iris_literal_payload():
    sources, _ = _requirer_sources(_payload())
    assert len(sources) == 1
    source = sources[0]
    assert source.service_name == "iris"
    assert source.targets == ("127.0.0.1:6060",)
    assert source.profile_types == ("process_cpu", "memory", "goroutine")
    assert source.labels["juju_unit"] == "iris/0"
    assert source.unit_name.endswith("/0")


@pytest.mark.parametrize("raw", [lambda: _payload(version=2), lambda: "{"])
def test_requirer_skips_wrong_version_and_malformed_json(raw, caplog):
    with caplog.at_level(logging.WARNING):
        sources, _ = _requirer_sources(raw())
    assert sources == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_requirer_drops_bad_targets_and_types_keeps_rest():
    sources, _ = _requirer_sources(
        _payload(targets=["127.0.0.1:6060", "10.0.0.5:6060"], profile_types=["process_cpu", "bogus"])
    )
    assert sources[0].targets == ("127.0.0.1:6060",)
    assert sources[0].profile_types == ("process_cpu",)


def test_requirer_skips_source_with_no_valid_targets():
    sources, _ = _requirer_sources(_payload(targets=["10.0.0.5:6060"]))
    assert sources == []


@pytest.mark.parametrize("target", ["127.0.0.1:6060", "127.5.0.1:1", "[::1]:6060", "localhost:6060"])
def test_is_loopback_true(target):
    assert is_loopback(target)


@pytest.mark.parametrize("target", ["0.0.0.0:6060", "10.0.0.5:6060"])
def test_is_loopback_false(target):
    assert not is_loopback(target)


@pytest.mark.parametrize(
    "target",
    [
        "127.0.0.1:80@evil.com",
        "[::1]@evil.com:80",
        "127.0.0.1:80/x",
        "127.0.0.1:abc",
        "localhost.evil.com:1",
        "127.0.0.1",
        "::1",
        "[::1]",
        "localhost",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:6060?x",
        "127.0.0.1:6060#x",
        "127.0.0.1: 6060",
        "[::1]:6060/x",
        "",
    ],
)
def test_is_loopback_rejects_malformed_or_hostile(target):
    assert not is_loopback(target)


def test_requirer_skips_source_with_no_valid_profile_types(caplog):
    with caplog.at_level(logging.WARNING):
        sources, _ = _requirer_sources(_payload(profile_types=["bogus"]))
    assert sources == []
    assert any("profile types" in r.message for r in caplog.records)


@pytest.mark.parametrize("raw", ["[]", "null", _payload(labels={"a": ["x"]}), _payload(labels={"a": 1})])
def test_requirer_skips_non_dict_or_non_string_labels(raw, caplog):
    with caplog.at_level(logging.WARNING):
        sources, _ = _requirer_sources(raw)
    assert sources == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)
