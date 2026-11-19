import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from principal_guard import (
    PEER_KEY,
    PEER_RELATION,
    PRINCIPAL_ENDPOINTS,
    conflicting_principals,
    decode_principals,
    encode_principals,
)


def test_constants():
    assert PRINCIPAL_ENDPOINTS == ("juju-info", "machine-observability", "profiling-source")
    assert PEER_RELATION == "alloy-peers"
    assert PEER_KEY == "principal-applications"


def test_encode_is_sorted_unique_compact_json():
    assert encode_principals(["polkadot", "kusama", "polkadot"]) == '["kusama","polkadot"]'
    assert encode_principals([]) == "[]"


def test_decode_round_trips():
    assert decode_principals('["kusama","polkadot"]') == frozenset({"kusama", "polkadot"})


@pytest.mark.parametrize(
    "raw",
    [None, "", "not json", "{}", '"polkadot"', "[1, null]", '[""]', "[[]]", "[" * 100000],
)
def test_decode_ignores_malformed_values(raw):
    assert decode_principals(raw) == frozenset()


def test_decode_keeps_valid_names_next_to_invalid_entries():
    assert decode_principals('["polkadot", 7, ""]') == frozenset({"polkadot"})


@pytest.mark.parametrize(
    ("local", "peers", "expected"),
    [
        ([], [], []),
        (["polkadot"], [], []),
        (["polkadot"], [["polkadot"], ["polkadot"]], []),
        (["polkadot"], [[]], []),
        ([], [["polkadot"], ["polkadot"]], []),
        (["polkadot"], [["kusama"]], ["kusama", "polkadot"]),
        (["polkadot", "kusama"], [], ["kusama", "polkadot"]),
        ([], [["polkadot"], ["kusama"]], ["kusama", "polkadot"]),
    ],
)
def test_conflicting_principals(local, peers, expected):
    assert conflicting_principals(local, peers) == expected
