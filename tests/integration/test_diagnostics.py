"""Exercise a built charm's diagnostic action in a disposable local model."""

import os
from pathlib import Path

import jubilant
import pytest

ARTIFACT = os.environ.get("ALLOY_DIAGNOSTICS_CHARM_PATH")
BASE = os.environ.get("ALLOY_DIAGNOSTICS_BASE", "ubuntu@24.04")
APP = "alloy-sub"


@pytest.mark.skipif(not ARTIFACT, reason="ALLOY_DIAGNOSTICS_CHARM_PATH is not set")
def test_diagnostics_preserves_service_and_config():
    """Read the real service, config and journal through actions without host access."""
    assert ARTIFACT is not None
    artifact = Path(ARTIFACT).resolve()
    assert artifact.is_file()
    with jubilant.temp_model(controller="local") as juju:
        juju.deploy(artifact, app=APP, base=BASE)
        if APP == "alloy-sub":
            juju.deploy("ubuntu", app="principal", base=BASE, channel="latest/stable")
            juju.integrate(f"{APP}:juju-info", "principal:juju-info")
        juju.wait(
            lambda status: bool(status.get_units(APP)) and jubilant.all_agents_idle(status),
            error=jubilant.any_error,
            timeout=1200,
        )
        unit = next(iter(juju.status().get_units(APP)))
        before = juju.run(unit, "get-diagnostics", {"log-lines": 0}, wait=60).results
        assert before["service-state"] == "active"
        assert before["config-syntax"] == "valid"
        assert str(before["owned-by-unit"]).lower() == "true"
        assert int(before["main-pid"]) > 0
        after = juju.run(unit, "get-diagnostics", {"lookback-minutes": 1, "log-lines": 5}, wait=60).results
        for key in ("main-pid", "process-start-time", "config-sha256", "owner", "unit-status"):
            assert after[key] == before[key]
        assert after["logs-status"] == "ok"
        assert len(after["recent-logs"].encode("utf-8")) <= 16 * 1024
