import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from alloy_ownership import Claim, claim, current_owner, release


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


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"owner": 7}', '{"other": "x"}'])
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
    barrier = threading.Barrier(8)

    def contend(index):
        barrier.wait()
        results.append(claim(f"alloy-sub/{index}", installed=False, marker_dir=tmp_path))

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [result for result in results if result.state == "owned"]
    assert len(winners) == 1
    assert all(result == Claim("other", winners[0].owner) for result in results if result.state != "owned")
