import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from static_labels import StaticLabelsError, parse_static_labels


@pytest.mark.parametrize(
    ("raw", "labels"),
    [
        ("", {}),
        ("  ", {}),
        ("gateway_region=dc1", {"gateway_region": "dc1"}),
        (" gateway_region = dc1 , environment=production, ", {"gateway_region": "dc1", "environment": "production"}),
        ("expr=a=b", {"expr": "a=b"}),
    ],
)
def test_parse_static_labels(raw, labels):
    result = parse_static_labels(raw)
    assert result == labels
    assert list(result) == list(labels)


@pytest.mark.parametrize(
    "raw",
    [
        "gateway_region",
        "=dc1",
        "a=",
        "a=1,a=2",
        "gateway-region=x",
        "1a=x",
        "juju_unit=x",
        "__name__=x",
        "job=x",
        "instance=x",
        "a=" + "v" * 129,
        "n" * 129 + "=x",
        ",".join(f"l{i}=x" for i in range(17)),
    ],
)
def test_parse_static_labels_rejects(raw):
    with pytest.raises(StaticLabelsError):
        parse_static_labels(raw)


def test_parse_static_labels_accepts_the_limits():
    raw = ",".join(f"l{i}=x" for i in range(16))
    assert len(parse_static_labels(raw)) == 16
    assert parse_static_labels("n" * 128 + "=" + "v" * 128) == {"n" * 128: "v" * 128}


def test_parse_error_names_the_entry():
    with pytest.raises(StaticLabelsError, match="gateway-region"):
        parse_static_labels("ok=1,gateway-region=x")
