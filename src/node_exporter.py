"""Manage the node-exporter snap and its Alloy scrape job."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
