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


SPLIT_APP_NAME = "alloy-sub-split"


@pytest.mark.abort_on_fail
@pytest.mark.skipif(not POLKADOT_CHARM_PATH, reason="POLKADOT_CHARM_PATH not set")
async def test_second_principal_is_rejected_on_limited_endpoints(ops_test: OpsTest):
    """Juju refuses a second principal application on a limit: 1 endpoint."""
    await ops_test.model.deploy("ubuntu", application_name="second-principal", channel="latest/stable")

    # Juju 3.6 exits non-zero without printing the reason unless --debug is given.
    return_code, stdout, stderr = await ops_test.juju(
        "integrate", "--debug", f"{APP_NAME}:juju-info", "second-principal:juju-info"
    )

    assert return_code != 0, stdout
    assert "maximum relation limit of 1" in stderr


@pytest.mark.abort_on_fail
@pytest.mark.skipif(not POLKADOT_CHARM_PATH, reason="POLKADOT_CHARM_PATH not set")
async def test_split_principals_block_every_unit(ops_test: OpsTest):
    """juju-info to one principal and machine-observability to another blocks all units."""
    # A dedicated machine-observability principal: a second Alloy subordinate on
    # polkadot's machine would share, and on removal stop, alloy-sub's Alloy service.
    alloy_sub = await ops_test.build_charm(".")
    polkadot_charm = await ops_test.build_charm(POLKADOT_CHARM_PATH)
    await ops_test.model.deploy(alloy_sub, application_name=SPLIT_APP_NAME)
    await ops_test.model.deploy(
        polkadot_charm,
        application_name="polkadot-split",
        config={
            "service-args": "--chain=polkadot --rpc-port=9933",
            "snap-name": "polkadot",
        },
    )
    await ops_test.model.integrate(f"{SPLIT_APP_NAME}:juju-info", "second-principal:juju-info")
    await ops_test.model.integrate(f"{SPLIT_APP_NAME}:machine-observability", "polkadot-split:machine-observability")

    expected = "multiple principal applications: polkadot-split, second-principal; relate alloy-sub to one principal"
    await ops_test.model.block_until(
        lambda: (
            len(ops_test.model.applications[SPLIT_APP_NAME].units) == 2
            and all(
                unit.workload_status == "blocked" and expected in unit.workload_status_message
                for unit in ops_test.model.applications[SPLIT_APP_NAME].units
            )
        ),
        timeout=900,
    )

    await ops_test.juju(
        "remove-relation", f"{SPLIT_APP_NAME}:machine-observability", "polkadot-split:machine-observability"
    )
    await ops_test.model.block_until(
        lambda: (
            len(ops_test.model.applications[SPLIT_APP_NAME].units) == 1
            and all(unit.workload_status != "blocked" for unit in ops_test.model.applications[SPLIT_APP_NAME].units)
        ),
        timeout=900,
    )

    await ops_test.model.remove_application(SPLIT_APP_NAME, block_until_done=True)
    await ops_test.model.remove_application("second-principal", block_until_done=True)
    await ops_test.model.remove_application("polkadot-split", block_until_done=True)


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

    # Intervals below 15s used to leave delta_profiling_duration at Alloy's 14s
    # default, which `alloy fmt` accepts but `alloy run` rejects at startup.
    await ops_test.model.applications[PROF_APP_NAME].set_config({"profiles_scrape_interval": "10s"})
    await ops_test.model.wait_for_idle(apps=[PROF_APP_NAME], status="active", timeout=600)
    prof_unit = ops_test.model.applications[PROF_APP_NAME].units[0].name
    rc, stdout, stderr = await ops_test.juju("ssh", prof_unit, "sudo", "cat", "/etc/alloy/config.alloy")
    assert rc == 0, stderr
    assert 'delta_profiling_duration = "9s"' in stdout
    await asyncio.sleep(30)
    rc, stdout, stderr = await ops_test.juju("ssh", prof_unit, "systemctl", "is-active", "alloy")
    assert stdout.strip() == "active", f"alloy not running after 10s profiles_scrape_interval: {stdout} {stderr}"
