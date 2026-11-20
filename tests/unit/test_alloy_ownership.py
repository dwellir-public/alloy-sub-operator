import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from alloy_ownership import Claim, claim, current_owner, release, restore


def _marker(tmp_path):
    return tmp_path / "owner.json"


def test_first_unit_claims_when_alloy_is_absent(tmp_path):
    assert claim("alloy-sub/0", installed=False, marker_dir=tmp_path) == Claim("owned", "alloy-sub/0")
    assert json.loads(_marker(tmp_path).read_text()) == {"owner": "alloy-sub/0"}
    assert current_owner(marker_dir=tmp_path) == "alloy-sub/0"


def test_claim_creates_missing_marker_dir(tmp_path):
    marker_dir = tmp_path / "nested" / "juju-alloy"
    assert claim("alloy-sub/0", installed=False, marker_dir=marker_dir).state == "owned"


def test_owner_reclaims_idempotently(tmp_path):
    claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    before = _marker(tmp_path).stat().st_mtime_ns
    assert claim("alloy-sub/0", installed=True, marker_dir=tmp_path) == Claim("owned", "alloy-sub/0")
    assert _marker(tmp_path).stat().st_mtime_ns == before


def test_second_unit_is_refused_and_marker_is_untouched(tmp_path):
    claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    assert claim("other-sub/3", installed=True, marker_dir=tmp_path) == Claim("other", "alloy-sub/0")
    assert claim("other-sub/3", installed=True, allow_existing=True, marker_dir=tmp_path).state == "other"
    assert current_owner(marker_dir=tmp_path) == "alloy-sub/0"


def test_unmanaged_alloy_is_not_claimed(tmp_path):
    assert claim("alloy-sub/0", installed=True, marker_dir=tmp_path) == Claim("unmanaged")
    assert not _marker(tmp_path).exists()


def test_allow_existing_claims_unmanaged_alloy(tmp_path):
    assert claim("alloy-sub/0", installed=True, allow_existing=True, marker_dir=tmp_path).state == "owned"


def test_force_takes_over_from_another_owner(tmp_path):
    claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    assert claim("other-sub/3", installed=True, force=True, marker_dir=tmp_path) == Claim("owned", "other-sub/3")
    assert current_owner(marker_dir=tmp_path) == "other-sub/3"


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"owner": 7}', '{"other": "x"}', '{"owner": ""}'])
def test_unreadable_marker_blocks_until_forced(tmp_path, content):
    _marker(tmp_path).write_text(content)
    assert claim("alloy-sub/0", installed=True, marker_dir=tmp_path) == Claim("other", "unknown")
    assert current_owner(marker_dir=tmp_path) == "unknown"
    assert claim("alloy-sub/0", installed=True, force=True, marker_dir=tmp_path).state == "owned"


def test_release_frees_the_machine_for_the_next_unit(tmp_path):
    claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    assert release("alloy-sub/0", marker_dir=tmp_path) is True
    assert json.loads(_marker(tmp_path).read_text()) == {"owner": None}
    assert current_owner(marker_dir=tmp_path) is None
    assert claim("other-sub/3", installed=True, marker_dir=tmp_path) == Claim("owned", "other-sub/3")


def test_release_by_non_owner_changes_nothing(tmp_path):
    claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    assert release("other-sub/3", marker_dir=tmp_path) is False
    assert current_owner(marker_dir=tmp_path) == "alloy-sub/0"
    assert release("alloy-sub/9", marker_dir=tmp_path / "missing") is False


def test_simultaneous_first_claims_have_one_winner(tmp_path):
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def contend(index):
        try:
            barrier.wait()
            results.append(claim(f"alloy-sub/{index}", installed=False, marker_dir=tmp_path))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8
    winners = [result for result in results if result.state == "owned"]
    assert len(winners) == 1
    assert all(result == Claim("other", winners[0].owner) for result in results if result.state != "owned")


def _seed(tmp_path, prior):
    """Put the marker into one of the states a claim can find."""
    if prior == "missing":
        return
    if prior == "released":
        claim("alloy-sub/9", installed=False, marker_dir=tmp_path)
        release("alloy-sub/9", marker_dir=tmp_path)
    elif prior == "owner":
        claim("other-sub/3", installed=False, marker_dir=tmp_path)
    else:
        _marker(tmp_path).write_bytes(b"\xff not json")


@pytest.mark.parametrize("prior", ["missing", "released", "owner", "unreadable"])
def test_restore_puts_back_exactly_the_state_the_claim_found(tmp_path, prior):
    _seed(tmp_path, prior)
    before = _marker(tmp_path).read_bytes() if _marker(tmp_path).exists() else None

    taken = claim("alloy-sub/0", installed=True, allow_existing=True, force=True, marker_dir=tmp_path)
    assert taken.state == "owned"
    assert current_owner(marker_dir=tmp_path) == "alloy-sub/0"

    assert restore("alloy-sub/0", taken.prior, marker_dir=tmp_path) is True
    after = _marker(tmp_path).read_bytes() if _marker(tmp_path).exists() else None
    assert after == before


def test_restore_leaves_a_marker_that_no_longer_names_the_unit(tmp_path):
    taken = claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    claim("other-sub/3", installed=True, force=True, marker_dir=tmp_path)

    assert restore("alloy-sub/0", taken.prior, marker_dir=tmp_path) is False
    assert current_owner(marker_dir=tmp_path) == "other-sub/3"


def test_claim_records_prior_state_without_changing_claim_equality(tmp_path):
    taken = claim("alloy-sub/0", installed=False, marker_dir=tmp_path)
    assert taken == Claim("owned", "alloy-sub/0")
    assert taken.prior is not None
    assert taken.prior.content is None
