"""Manage the node-exporter snap and its Alloy scrape job."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from .config_builder import MetricsScrapeJob, ScrapeTarget
except ImportError:
    from config_builder import MetricsScrapeJob, ScrapeTarget

SNAP_NAME = "node-exporter"
DEFAULT_PORT = 9100
JOB_NAME = "node-exporter"
METRICS_PATH = "/metrics"
SCHEME = "http"

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

# snapd's own wording when the snap is genuinely not installed. Any other failure
# means we could not read snapd, which is not the same thing as "absent".
SNAP_ABSENT_MARKER = "no matching snaps installed"

_TEARDOWN_ACTIONS = {
    PRIOR_STATE_ABSENT: (ACTION_REMOVE,),
    PRIOR_STATE_DISABLED: (ACTION_DISABLE,),
    PRIOR_STATE_ENABLED: (ACTION_ENABLE,),
}


class SnapCommandError(subprocess.CalledProcessError):
    """A snap command failed, carrying snapd's own explanation in its message.

    ``subprocess.CalledProcessError`` alone renders as "returned non-zero exit
    status 1" and hides snapd's reason on ``stderr``, which is exactly the detail
    an operator needs. Subclassing keeps every existing ``except
    subprocess.CalledProcessError`` handler working.
    """

    def __str__(self) -> str:
        detail = combined_output(self)
        base = super().__str__()
        return f"{base}: {detail}" if detail else base


def combined_output(exc: subprocess.CalledProcessError) -> str:
    """Return a failed command's stdout and stderr joined into one string."""
    parts = [str(stream).strip() for stream in (exc.output, exc.stderr) if stream]
    return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class SnapState:
    """Observed state of the node-exporter snap on the machine.

    ``known`` is ``False`` when snapd could not be read at all. An unreadable
    snapd is not evidence that the snap is absent, and the two must never be
    confused: acting on the guess is how a foreign snap gets removed.
    """

    installed: bool
    enabled: bool
    known: bool = True


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

    Every action is derived from what ``observe`` reports about the machine, never
    from the recorded intent alone: ``snap enable`` and ``snap disable`` both fail
    when the snap is already in the requested state, so an action the machine does
    not need is an error, not a no-op.

    ``observe`` is called only when the decision needs machine state, so a charm
    that has never been opted in issues no snap command at all.
    """
    if not enabled:
        return _plan_disabled(prior_state=prior_state, observe=observe)
    return _plan_enabled(prior_state=prior_state, observe=observe)


def _plan_disabled(*, prior_state: str, observe: Callable[[], SnapState]) -> Plan:
    """Plan the ``enable-node-exporter=false`` path."""
    if prior_state == PRIOR_STATE_UNSET:
        # The opt-in gate: no consent has ever been given, so issue nothing at
        # all -- not even a read-only `snap list`.
        return Plan()

    state = observe()
    if not state.known:
        return Plan(prior_state=prior_state)
    if state.installed and state.enabled:
        return Plan(actions=(ACTION_DISABLE,), prior_state=prior_state)
    return Plan(prior_state=prior_state)


def _plan_enabled(*, prior_state: str, observe: Callable[[], SnapState]) -> Plan:
    """Plan the ``enable-node-exporter=true`` path."""
    state = observe()
    first_opt_in = prior_state == PRIOR_STATE_UNSET
    prior = _restore_point(state) if first_opt_in else prior_state

    if not state.known:
        # Record the restore point, then wait: a snap we could not read is a snap
        # we must not act on, and must not claim to be scraping either.
        return Plan(prior_state=prior, scrape_enabled=False)

    if not state.installed:
        actions = (ACTION_INSTALL, ACTION_CONNECT)
    elif not state.enabled:
        actions = (ACTION_ENABLE, ACTION_CONNECT)
    else:
        # Already installed and running. Interfaces are wired once on opt-in and
        # again whenever we install or enable; re-running `snap connect` on every
        # update-status forever buys nothing.
        actions = (ACTION_CONNECT,) if first_opt_in else ()
    return Plan(actions=actions, prior_state=prior, scrape_enabled=True)


def _restore_point(state: SnapState) -> str:
    """Return the prior state to record for a first opt-in observation."""
    if not state.known:
        # Nothing was done to the machine, so there is nothing to restore and no
        # restore point we could honestly record. Staying unset keeps the opt-in
        # gate closed for one more hook; the next readable observation records the
        # true restore point instead of a guess we could never correct.
        return PRIOR_STATE_UNSET
    if not state.installed:
        return PRIOR_STATE_ABSENT
    if not state.enabled:
        return PRIOR_STATE_DISABLED
    return PRIOR_STATE_ENABLED


def plan_teardown(*, prior_state: str, observe: Callable[[], SnapState]) -> Plan:
    """Decide what to restore to the snap when the unit is being removed.

    Like :func:`plan_reconcile`, the restore action is derived from what the
    machine actually reports: re-enabling an already-enabled snap exits non-zero
    and would surface as a teardown failure when nothing failed.

    ``observe`` is not called when the charm was never opted in, and its verdict is
    ignored when snapd is unreadable -- teardown is the last hook this unit will
    ever run, so a best-effort restore beats waiting for a hook that never comes.
    """
    if prior_state == PRIOR_STATE_UNSET:
        # The opt-in gate: the charm never touched this machine.
        return Plan()

    state = observe()
    actions = _TEARDOWN_ACTIONS.get(prior_state, ()) if not state.known else _restore_actions(prior_state, state)
    return Plan(actions=actions, prior_state=prior_state, scrape_enabled=False)


def _restore_actions(prior_state: str, state: SnapState) -> tuple[str, ...]:
    """Return the teardown actions the observed machine still needs."""
    if prior_state == PRIOR_STATE_ABSENT:
        return (ACTION_REMOVE,) if state.installed else ()
    if prior_state == PRIOR_STATE_DISABLED:
        return (ACTION_DISABLE,) if state.installed and state.enabled else ()
    if prior_state == PRIOR_STATE_ENABLED:
        return (ACTION_ENABLE,) if state.installed and not state.enabled else ()
    return ()  # pragma: no cover - no current revision writes any other prior_state


def _run(cmd: Iterable[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process.

    A non-zero exit is re-raised as :class:`SnapCommandError` so snapd's own
    explanation reaches the operator instead of being discarded into
    ``exc.stderr`` behind a bare exit-status message.
    """
    try:
        return subprocess.run(
            list(cmd),
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        raise SnapCommandError(exc.returncode, exc.cmd, output=exc.output, stderr=exc.stderr) from exc


def _snap_list_fields() -> tuple[list[str] | None, bool]:
    """Return the ``snap list`` row for node-exporter and whether snapd was readable.

    Returns:
        A ``(fields, known)`` pair. ``fields`` is the parsed row, or ``None`` when
        the snap is absent or snapd could not be read. ``known`` is ``False`` only
        when the read itself failed, so callers never mistake an unreachable
        snapd for a missing snap.
    """
    try:
        result = _run(["snap", "list", SNAP_NAME], timeout=DEFAULT_SNAP_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        return None, SNAP_ABSENT_MARKER in combined_output(exc).lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, False
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] == SNAP_NAME:
            return fields, True
    return None, True


def observe() -> SnapState:
    """Return the current state of the node-exporter snap."""
    fields, known = _snap_list_fields()
    if fields is None:
        return SnapState(installed=False, enabled=False, known=known)
    notes = fields[5] if len(fields) > 5 else "-"
    return SnapState(installed=True, enabled="disabled" not in notes.split(","))


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


def scrape_job(
    *,
    topology_labels: dict[str, str],
    scrape_interval: str,
    scrape_timeout: str,
) -> MetricsScrapeJob:
    """Build the Alloy scrape job for the local node-exporter."""
    return MetricsScrapeJob(
        job_name=JOB_NAME,
        targets=[ScrapeTarget(address=f"localhost:{DEFAULT_PORT}", labels=dict(topology_labels))],
        metrics_path=METRICS_PATH,
        scheme=SCHEME,
        scrape_interval=scrape_interval,
        scrape_timeout=scrape_timeout,
    )
