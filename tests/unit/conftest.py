"""Shared unit-test fixtures."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))


@pytest.fixture(autouse=True)
def alloy_marker_dir(request, tmp_path, monkeypatch):
    """Isolate the ownership marker; pre-existing tests run as the owning unit.

    Some test modules import the charm as ``src.charm``, a second copy of the
    module tree, so every loaded copy is patched.
    """
    import alloy_ownership
    import charm

    for ownership in {alloy_ownership, sys.modules.get("src.alloy_ownership")} - {None}:
        monkeypatch.setattr(ownership, "MARKER_DIR", tmp_path / "juju-alloy")
    if "real_ownership" not in request.keywords:
        for charm_module in {charm, sys.modules.get("src.charm")} - {None}:
            monkeypatch.setattr(charm_module.AlloySubCharm, "_ensure_alloy_ownership", lambda self, **_kwargs: True)
            monkeypatch.setattr(charm_module.alloy_ownership, "release", lambda *_args, **_kwargs: True)
    return tmp_path / "juju-alloy"
