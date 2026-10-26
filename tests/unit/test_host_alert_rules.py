import json
import re
import sys
from pathlib import Path

import pytest
from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloySubCharm
from host_alert_rules import host_filesystem_rule_groups

MODEL_NAME = "robinhood-obs"
MODEL_UUID = "00000000-0000-4000-8000-000000000333"
HOST_GROUP = f"{MODEL_UUID}-nitro-host-filesystem"


def _harness(*, host_metrics: bool) -> testing.Harness:
    harness = testing.Harness(AlloySubCharm)
    harness.set_leader(True)
    harness.set_model_name(MODEL_NAME)
    harness.set_model_uuid(MODEL_UUID)
    harness.update_config({"enable-host-metrics": host_metrics})
    harness.begin()
    juju_info = harness.add_relation("juju-info", "nitro")
    harness.add_relation_unit(juju_info, "nitro/0")
    harness.update_relation_data(juju_info, "nitro/0", {"private-address": "10.0.0.5"})
    return harness


def _published(harness: testing.Harness, endpoint: str, remote_app: str) -> list[dict]:
    relation_id = harness.add_relation(endpoint, remote_app)
    harness.add_relation_unit(relation_id, f"{remote_app}/0")
    data = harness.get_relation_data(relation_id, harness.charm.app.name)
    return json.loads(data["alert_rules"])["groups"]


def test_host_metrics_publish_filesystem_rules_scoped_to_principal():
    harness = _harness(host_metrics=True)

    groups = _published(harness, "send-remote-write", "prometheus")

    assert [group["name"] for group in groups] == [HOST_GROUP]
    rules = {rule["alert"]: rule for rule in groups[0]["rules"]}
    assert set(rules) == {
        "HostDiskSpaceWarning",
        "HostDiskSpace",
        "HostFilesystemReadOnly",
        "HostFilesystemDeviceError",
    }
    for rule in rules.values():
        assert f'juju_model_uuid="{MODEL_UUID}"' in rule["expr"]
        assert 'juju_application="nitro"' in rule["expr"]
        assert rule["labels"]["juju_application"] == "nitro"
        assert rule["labels"]["juju_model_uuid"] == MODEL_UUID
    assert rules["HostDiskSpace"]["labels"]["severity"] == "critical"
    assert rules["HostDiskSpace"]["expr"].endswith("> 90")
    assert rules["HostDiskSpaceWarning"]["labels"]["severity"] == "warning"
    assert rules["HostDiskSpaceWarning"]["expr"].endswith("> 80")
    assert " unless on (instance, device) " in rules["HostFilesystemDeviceError"]["expr"]


def test_host_rules_stay_off_loki_and_without_host_metrics():
    assert _published(_harness(host_metrics=True), "send-loki-logs", "loki") == []
    assert _published(_harness(host_metrics=False), "send-remote-write", "prometheus") == []


@pytest.mark.parametrize(
    ("mountpoint", "excluded"),
    [
        ("/run/user/1000", True),
        ("/proc/sys/fs/binfmt_misc", True),
        ("/dev/shm", True),
        ("/snap/core22/1380", True),
        ("/", False),
        ("/mnt/blockchaindata0", False),
        ("/var/snap/polkadot/common", False),
        ("/runtime", False),
        ("/system-data", False),
    ],
)
def test_host_rules_skip_only_pseudo_mountpoints(mountpoint, excluded):
    [group] = host_filesystem_rule_groups(model="m", model_uuid="u", application="nitro", charm_name="")
    [pattern] = {re.search(r'mountpoint!~"([^"]*)"', rule["expr"]).group(1) for rule in group["rules"]}
    # Prometheus anchors label regexes, so fullmatch mirrors its evaluation.
    assert bool(re.fullmatch(pattern, mountpoint)) is excluded


@pytest.mark.parametrize(
    ("fstype", "excluded"),
    [
        ("tmpfs", True),
        ("squashfs", True),
        ("fuse.lxcfs", True),
        ("fuse.snapfuse", True),
        ("xfs", False),
        ("ext4", False),
        ("fuseblk", False),
    ],
)
def test_host_rules_skip_only_pseudo_fstypes(fstype, excluded):
    [group] = host_filesystem_rule_groups(model="m", model_uuid="u", application="nitro", charm_name="")
    [quoted] = {
        re.search(r'fstype!~("[^"]*")', rule["expr"]).group(1) for rule in group["rules"] if "fstype!~" in rule["expr"]
    }
    # Decode the PromQL string literal the way Prometheus does before compiling the regex.
    pattern = json.loads(quoted)
    assert bool(re.fullmatch(pattern, fstype)) is excluded
