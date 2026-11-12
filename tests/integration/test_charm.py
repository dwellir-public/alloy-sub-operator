#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

import asyncio
import logging
import os
import time
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
PROF_APP_NAME = "alloy-sub-prof"
EXPECTED_PROFILE_TYPE_IDS = (
    "process_cpu:cpu:nanoseconds:cpu:nanoseconds",
    "memory:alloc_space:bytes:space:bytes",
    "goroutine:goroutine:count:goroutine:count",
    "mutex:delay:nanoseconds:contentions:count",
    "block:delay:nanoseconds:contentions:count",
)
POLKADOT_CHARM_PATH = os.environ.get("POLKADOT_CHARM_PATH")


@pytest.mark.abort_on_fail
@pytest.mark.skipif(not POLKADOT_CHARM_PATH, reason="POLKADOT_CHARM_PATH not set")
async def test_build_deploy_and_integrate_with_principal(ops_test: OpsTest):
    """Build both charms, deploy them, and validate the rendered subordinate config."""
    alloy_sub = await ops_test.build_charm(".")
    polkadot_charm = await ops_test.build_charm(POLKADOT_CHARM_PATH)

    await ops_test.model.deploy(alloy_sub, application_name=APP_NAME)
    await ops_test.model.deploy(
        polkadot_charm,
        application_name="polkadot",
        config={
            "service-args": "--chain=polkadot --rpc-port=9933",
            "snap-name": "polkadot",
        },
    )
    await ops_test.model.integrate(f"{APP_NAME}:juju-info", "polkadot:juju-info")
    await ops_test.model.integrate(
        f"{APP_NAME}:machine-observability",
        "polkadot:machine-observability",
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, "polkadot"],
        raise_on_blocked=False,
        timeout=1500,
    )

    action = await ops_test.juju(
        "ssh",
        f"{APP_NAME}/0",
        "grep",
        "-n",
        "snap.polkadot.polkadot.service",
        "/etc/alloy/config.alloy",
    )
    assert "snap.polkadot.polkadot.service" in action[1]

    action = await ops_test.juju(
        "ssh",
        f"{APP_NAME}/0",
        "grep",
        "-n",
        'prometheus.scrape "polkadot"',
        "/etc/alloy/config.alloy",
    )
    assert 'prometheus.scrape "polkadot"' in action[1]


GOTODO_CHARM_PATH = os.environ.get("GOTODO_CHARM_PATH")
PYROSCOPE_CHARM_PATH = os.environ.get("PYROSCOPE_CHARM_PATH")


@pytest.mark.abort_on_fail
@pytest.mark.skipif(
    not (GOTODO_CHARM_PATH and PYROSCOPE_CHARM_PATH),
    reason="GOTODO_CHARM_PATH and PYROSCOPE_CHARM_PATH not set",
)
async def test_profiling_reaches_pyroscope(ops_test: OpsTest):
    """Profiles pulled from gotodo land in Pyroscope under service_name gotodo."""
    alloy_sub = await ops_test.build_charm(".")

    await ops_test.model.deploy(GOTODO_CHARM_PATH, application_name="gotodo")
    await ops_test.model.deploy(alloy_sub, application_name=PROF_APP_NAME)
    await ops_test.model.deploy(PYROSCOPE_CHARM_PATH, application_name="pyroscope-vm")

    await ops_test.model.integrate(f"{PROF_APP_NAME}:juju-info", "gotodo:juju-info")
    await ops_test.model.integrate("gotodo:profiling-source", f"{PROF_APP_NAME}:profiling-source")
    await ops_test.model.integrate(f"{PROF_APP_NAME}:send-profiles", "pyroscope-vm:profiling")

    await ops_test.model.wait_for_idle(
        apps=[PROF_APP_NAME, "gotodo", "pyroscope-vm"],
        status="active",
        timeout=1500,
    )

    status = await ops_test.model.get_status()
    address = status.applications["pyroscope-vm"].units["pyroscope-vm/0"].public_address
    url = f"http://{address}:4040/querier.v1.QuerierService/LabelValues"

    found = False
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rc, stdout, _ = await ops_test.run(
            "curl",
            "-s",
            "-XPOST",
            "-H",
            "Content-Type: application/json",
            url,
            "-d",
            '{"name":"service_name"}',
        )
        if rc == 0 and "gotodo" in stdout:
            found = True
            break
        await asyncio.sleep(10)

    assert found, "service_name gotodo never appeared in Pyroscope"

    types_url = f"http://{address}:4040/querier.v1.QuerierService/ProfileTypes"
    missing = set(EXPECTED_PROFILE_TYPE_IDS)
    deadline = time.monotonic() + 180
    while missing and time.monotonic() < deadline:
        rc, stdout, _ = await ops_test.run(
            "curl", "-s", "-XPOST", "-H", "Content-Type: application/json", types_url, "-d", "{}"
        )
        if rc == 0:
            missing = {type_id for type_id in missing if type_id not in stdout}
        if missing:
            await asyncio.sleep(10)

    assert not missing, f"profile types never appeared in Pyroscope: {sorted(missing)}"
