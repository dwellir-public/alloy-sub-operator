#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

import asyncio
import logging
import os
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
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
