import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.node_exporter import (
    ACTION_CONNECT,
    ACTION_DISABLE,
    ACTION_ENABLE,
    ACTION_INSTALL,
    ACTION_REMOVE,
    PRIOR_STATE_ABSENT,
    PRIOR_STATE_DISABLED,
    PRIOR_STATE_ENABLED,
    PRIOR_STATE_UNSET,
    SnapState,
    plan_reconcile,
    plan_teardown,
)


class ObserveSpy:
    """Records whether the planner asked for machine state."""

    def __init__(self, state):
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.state


# --- Rule 1: the opt-in gate ---


def test_disabled_and_never_opted_in_issues_no_command_and_never_observes():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=False, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == ()
    assert plan.prior_state == PRIOR_STATE_UNSET
    assert plan.scrape_enabled is False
    assert spy.calls == 0


def test_teardown_never_opted_in_is_a_no_op():
    plan = plan_teardown(prior_state=PRIOR_STATE_UNSET)

    assert plan.actions == ()


# --- Rule 2: record on first opt-in ---


def test_enabled_from_unset_with_snap_absent_installs_and_records_absent():
    spy = ObserveSpy(SnapState(installed=False, enabled=False))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_INSTALL, ACTION_CONNECT)
    assert plan.prior_state == PRIOR_STATE_ABSENT
    assert plan.scrape_enabled is True
    assert spy.calls == 1


def test_enabled_from_unset_with_snap_disabled_enables_and_records_disabled():
    spy = ObserveSpy(SnapState(installed=True, enabled=False))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_ENABLE, ACTION_CONNECT)
    assert plan.prior_state == PRIOR_STATE_DISABLED


def test_enabled_from_unset_with_snap_running_only_connects_and_records_enabled():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == (ACTION_CONNECT,)
    assert plan.prior_state == PRIOR_STATE_ENABLED


# --- Rule 2: config governs after opt-in ---


def test_enabled_after_opt_in_only_connects_and_does_not_reobserve():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=False, enabled=False))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_CONNECT,), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is True, prior
        assert spy.calls == 0, prior


def test_disabled_after_opt_in_disables_for_every_prior_state():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=True))

        plan = plan_reconcile(enabled=False, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_DISABLE,), prior
        assert ACTION_REMOVE not in plan.actions, prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is False, prior


# --- Teardown: full restore ---


def test_teardown_removes_only_what_the_charm_installed():
    assert plan_teardown(prior_state=PRIOR_STATE_ABSENT).actions == (ACTION_REMOVE,)


def test_teardown_redisables_what_the_charm_enabled():
    plan = plan_teardown(prior_state=PRIOR_STATE_DISABLED)

    assert plan.actions == (ACTION_DISABLE,)
    assert ACTION_REMOVE not in plan.actions


def test_teardown_reenables_a_preexisting_running_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_ENABLED)

    assert plan.actions == (ACTION_ENABLE,)
    assert ACTION_REMOVE not in plan.actions


# --- The sequence that drove the design ---


def test_preexisting_running_snap_survives_false_true_false_teardown():
    observed = SnapState(installed=True, enabled=True)
    prior = PRIOR_STATE_UNSET
    seen = []

    for enabled in (False, True, False):
        plan = plan_reconcile(enabled=enabled, prior_state=prior, observe=lambda: observed)
        seen.append(plan.actions)
        prior = plan.prior_state

    seen.append(plan_teardown(prior_state=prior).actions)

    assert seen == [(), (ACTION_CONNECT,), (ACTION_DISABLE,), (ACTION_ENABLE,)]
