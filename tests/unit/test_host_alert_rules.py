import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from alert_rules import validate_rule_groups
from host_alert_rules import build_host_alert_rule_groups

MODEL_UUID = "00000000-0000-4000-8000-000000000222"
# The series carry the principal's topology, so each alloy-sub application alerts only on
# the hosts of the principal it is attached to; two apps in one model never evaluate twice.
BASE_MATCHER = f'juju_model_uuid="{MODEL_UUID}",juju_application="polkadot"'
DISK_MATCHER = f'{BASE_MATCHER},fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"'


def _rules():
    groups = build_host_alert_rule_groups(
        model_uuid=MODEL_UUID, application="alloy-sub", principal_application="polkadot"
    )
    assert len(groups) == 1
    return groups[0]["rules"]


def test_build_returns_one_group_named_after_the_model_and_application():
    """Receivers merge every relation into one namespace, so the name must be unique across models."""
    groups = build_host_alert_rule_groups(
        model_uuid=MODEL_UUID, application="alloy-sub", principal_application="polkadot"
    )
    assert [group["name"] for group in groups] == [f"{MODEL_UUID}-alloy-sub-host-metrics"]
    assert set(groups[0]) == {"name", "rules"}


def test_rules_are_ordered_disk_then_memory_warning_then_critical():
    assert [rule["alert"] for rule in _rules()] == [
        "HostDiskUsageWarning",
        "HostDiskUsageCritical",
        "HostMemoryUsageWarning",
        "HostMemoryUsageCritical",
    ]


def test_disk_rules_measure_used_fraction_of_real_filesystems():
    disk_warning, disk_critical, _, _ = _rules()
    usage = f"100 * (1 - node_filesystem_avail_bytes{{{DISK_MATCHER}}} / node_filesystem_size_bytes{{{DISK_MATCHER}}})"
    assert disk_warning["expr"] == f"{usage} > 80"
    assert disk_critical["expr"] == f"{usage} > 90"


def test_memory_rules_measure_available_over_total():
    _, _, memory_warning, memory_critical = _rules()
    usage = (
        f"100 * (1 - node_memory_MemAvailable_bytes{{{BASE_MATCHER}}} / node_memory_MemTotal_bytes{{{BASE_MATCHER}}})"
    )
    assert memory_warning["expr"] == f"{usage} > 80"
    assert memory_critical["expr"] == f"{usage} > 90"


def test_every_rule_waits_five_minutes_and_carries_only_a_severity_label():
    rules = _rules()
    assert [rule["for"] for rule in rules] == ["5m"] * 4
    assert [rule["labels"] for rule in rules] == [
        {"severity": "warning"},
        {"severity": "critical"},
        {"severity": "warning"},
        {"severity": "critical"},
    ]


def test_rules_do_not_set_topology_labels():
    for rule in _rules():
        assert not any(key.startswith("juju_") for key in rule["labels"])


def test_annotations_name_the_unit_mountpoint_and_value():
    rules = _rules()
    disk_warning, memory_critical = rules[0], rules[3]
    assert disk_warning["annotations"] == {
        "summary": "Disk usage above 80% on {{ $labels.juju_unit }} ({{ $labels.mountpoint }})",
        "description": '{{ $labels.mountpoint }} on {{ $labels.juju_unit }} is {{ $value | printf "%.1f" }}% full.',
    }
    assert memory_critical["annotations"] == {
        "summary": "Memory usage above 90% on {{ $labels.juju_unit }}",
        "description": '{{ $labels.juju_unit }} is using {{ $value | printf "%.1f" }}% of memory.',
    }


def test_model_uuid_and_principal_are_json_escaped_in_matchers():
    groups = build_host_alert_rule_groups(model_uuid='a"b\\c', application="alloy-sub", principal_application='p"q')
    expr = groups[0]["rules"][0]["expr"]
    assert 'juju_model_uuid="a\\"b\\\\c",juju_application="p\\"q"' in expr


def test_groups_pass_the_backend_rule_schema():
    assert validate_rule_groups(
        build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-sub", principal_application="polkadot")
    )
