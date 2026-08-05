"""Manage the node-exporter snap and its Alloy scrape job."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SNAP_NAME = "node-exporter"

ACTION_INSTALL = "install"
ACTION_ENABLE = "enable"
ACTION_DISABLE = "disable"
ACTION_REMOVE = "remove"
ACTION_CONNECT = "connect"

PRIOR_STATE_UNSET = ""
PRIOR_STATE_ABSENT = "absent"
PRIOR_STATE_DISABLED = "disabled"
PRIOR_STATE_ENABLED = "enabled"

REQUIRED_INTERFACES = (
    "hardware-observe",
    "mount-observe",
    "network-observe",
    "system-observe",
)

DEFAULT_SNAP_TIMEOUT = 60
DEFAULT_INSTALL_TIMEOUT = 300

_TEARDOWN_ACTIONS = {
    PRIOR_STATE_ABSENT: (ACTION_REMOVE,),
    PRIOR_STATE_DISABLED: (ACTION_DISABLE,),
    PRIOR_STATE_ENABLED: (ACTION_ENABLE,),
}


@dataclass(frozen=True)
class SnapState:
    """Observed state of the node-exporter snap on the machine."""

    installed: bool
    enabled: bool


@dataclass(frozen=True)
class Plan:
    """An ordered set of snap actions plus the restore point to persist."""

    actions: tuple[str, ...] = ()
    prior_state: str = PRIOR_STATE_UNSET
    scrape_enabled: bool = False


def plan_reconcile(
    *,
    enabled: bool,
    prior_state: str,
    observe: Callable[[], SnapState],
) -> Plan:
    """Decide what to do to the snap for the current config and recorded prior state.

    ``observe`` is called only when the decision needs machine state, so a charm
    that has never been opted in issues no snap command at all.
    """
    if not enabled:
        if prior_state == PRIOR_STATE_UNSET:
            return Plan()
        return Plan(actions=(ACTION_DISABLE,), prior_state=prior_state, scrape_enabled=False)

    if prior_state != PRIOR_STATE_UNSET:
        return Plan(actions=(ACTION_CONNECT,), prior_state=prior_state, scrape_enabled=True)

    state = observe()
    if not state.installed:
        return Plan(
            actions=(ACTION_INSTALL, ACTION_CONNECT),
            prior_state=PRIOR_STATE_ABSENT,
            scrape_enabled=True,
        )
    if not state.enabled:
        return Plan(
            actions=(ACTION_ENABLE, ACTION_CONNECT),
            prior_state=PRIOR_STATE_DISABLED,
            scrape_enabled=True,
        )
    return Plan(actions=(ACTION_CONNECT,), prior_state=PRIOR_STATE_ENABLED, scrape_enabled=True)


def plan_teardown(*, prior_state: str) -> Plan:
    """Decide what to do to the snap when the unit is being removed."""
    return Plan(
        actions=_TEARDOWN_ACTIONS.get(prior_state, ()),
        prior_state=prior_state,
        scrape_enabled=False,
    )


def _run(cmd: Iterable[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process."""
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    return subprocess.run(
        list(cmd),
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _snap_list_fields() -> list[str] | None:
    """Return the ``snap list`` row for node-exporter, or None when absent."""
    try:
        result = _run(["snap", "list", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] == SNAP_NAME:
            return fields
    return None


def observe() -> SnapState:
    """Return the current state of the node-exporter snap."""
    fields = _snap_list_fields()
    if fields is None:
        return SnapState(installed=False, enabled=False)
    notes = fields[5] if len(fields) > 5 else "-"
    return SnapState(installed=True, enabled="disabled" not in notes.split(","))


def get_version() -> str | None:
    """Return the installed node-exporter version, or None when absent."""
    fields = _snap_list_fields()
    if fields is None or len(fields) < 2:
        return None
    return fields[1]


def install() -> None:
    """Install the node-exporter snap."""
    _run(["snap", "install", SNAP_NAME], timeout=DEFAULT_INSTALL_TIMEOUT)


def enable() -> None:
    """Enable the node-exporter snap."""
    _run(["snap", "enable", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)


def disable() -> None:
    """Disable the node-exporter snap."""
    _run(["snap", "disable", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)


def remove() -> None:
    """Remove the node-exporter snap and its data."""
    _run(["snap", "remove", "--purge", SNAP_NAME], timeout=DEFAULT_INSTALL_TIMEOUT)


def connect_interfaces() -> None:
    """Connect the snap interfaces node-exporter needs for full metrics.

    A failed connection is logged and skipped: partial metrics beat no metrics,
    and a missing interface on an older snap revision must not block the charm.
    """
    for interface in REQUIRED_INTERFACES:
        try:
            _run(["snap", "connect", f"{SNAP_NAME}:{interface}"], timeout=DEFAULT_SNAP_TIMEOUT)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("Failed to connect %s:%s: %s", SNAP_NAME, interface, exc)


def apply(plan: Plan) -> None:
    """Execute a plan's actions in order."""
    for action in plan.actions:
        if action == ACTION_INSTALL:
            install()
        elif action == ACTION_ENABLE:
            enable()
        elif action == ACTION_DISABLE:
            disable()
        elif action == ACTION_REMOVE:
            remove()
        elif action == ACTION_CONNECT:
            connect_interfaces()
