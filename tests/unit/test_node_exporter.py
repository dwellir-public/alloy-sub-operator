import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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
    REQUIRED_INTERFACES,
    SNAP_NAME,
    Plan,
    SnapState,
    apply,
    connect_interfaces,
    disable,
    enable,
    install,
    observe,
    plan_reconcile,
    plan_teardown,
    remove,
    scrape_job,
)


class ObserveSpy:
    """Records whether the planner asked for machine state."""

    def __init__(self, state):
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.state


class _FakeMachine:
    """A machine whose snap state actually changes when a plan is applied."""

    def __init__(self, state):
        self.state = state
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.state

    def apply(self, plan):
        for action in plan.actions:
            if action == ACTION_INSTALL:
                self.state = SnapState(installed=True, enabled=True)
            elif action == ACTION_ENABLE:
                self.state = SnapState(installed=True, enabled=True)
            elif action == ACTION_DISABLE:
                self.state = SnapState(installed=True, enabled=False)
            elif action == ACTION_REMOVE:
                self.state = SnapState(installed=False, enabled=False)


# --- Rule 1: the opt-in gate ---


def test_disabled_and_never_opted_in_issues_no_command_and_never_observes():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=False, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == ()
    assert plan.prior_state == PRIOR_STATE_UNSET
    assert plan.scrape_enabled is False
    assert spy.calls == 0


def test_teardown_never_opted_in_is_a_no_op_and_never_observes():
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_teardown(prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == ()
    assert spy.calls == 0


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


def test_enabled_after_opt_in_reobserves_and_reinstalls_a_vanished_snap():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=False, enabled=False))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_INSTALL, ACTION_CONNECT), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is True, prior
        assert spy.calls == 1, prior


def test_enabled_after_opt_in_reenables_a_disabled_snap():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=False))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_ENABLE, ACTION_CONNECT), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is True, prior


def test_enabled_after_opt_in_on_a_running_snap_issues_nothing():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=True))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is True, prior


def test_disabled_after_opt_in_disables_a_running_snap_for_every_prior_state():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=True))

        plan = plan_reconcile(enabled=False, prior_state=prior, observe=spy)

        assert plan.actions == (ACTION_DISABLE,), prior
        assert ACTION_REMOVE not in plan.actions, prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is False, prior


def test_disabled_after_opt_in_issues_nothing_when_the_snap_is_already_disabled():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=True, enabled=False))

        plan = plan_reconcile(enabled=False, prior_state=prior, observe=spy)

        assert plan.actions == (), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is False, prior


def test_disabled_after_opt_in_issues_nothing_when_the_snap_is_absent():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=False, enabled=False))

        plan = plan_reconcile(enabled=False, prior_state=prior, observe=spy)

        assert plan.actions == (), prior
        assert plan.prior_state == prior, prior


def test_second_disable_reconcile_does_not_reissue_disable():
    machine = _FakeMachine(SnapState(installed=True, enabled=True))
    prior = PRIOR_STATE_ENABLED

    first = plan_reconcile(enabled=False, prior_state=prior, observe=machine)
    machine.apply(first)
    second = plan_reconcile(enabled=False, prior_state=first.prior_state, observe=machine)

    assert first.actions == (ACTION_DISABLE,)
    assert second.actions == ()


def test_second_enable_reconcile_on_a_running_snap_issues_nothing():
    machine = _FakeMachine(SnapState(installed=True, enabled=True))

    first = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=machine)
    machine.apply(first)
    second = plan_reconcile(enabled=True, prior_state=first.prior_state, observe=machine)

    assert first.actions == (ACTION_CONNECT,)
    assert second.actions == ()
    assert second.scrape_enabled is True


# --- Unknown machine state: never guess ---


def test_unknown_snap_state_on_first_opt_in_records_nothing_and_acts_on_nothing():
    spy = ObserveSpy(SnapState(installed=False, enabled=False, known=False))

    plan = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=spy)

    # No action was taken, so there is nothing to restore and no restore point to
    # record. Recording one here would be a label we could never verify.
    assert plan.actions == ()
    assert plan.prior_state == PRIOR_STATE_UNSET
    assert plan.scrape_enabled is False


def test_unknown_first_opt_in_then_a_readable_absent_snap_records_absent_and_tears_down():
    """An unreadable snapd at opt-in must not strand the restore point on `enabled`.

    Sequence: snapd is still seeding at the first `true`, then becomes readable and
    reports the snap genuinely absent. The charm installs it, so teardown must
    remove it again -- otherwise unit removal leaves node-exporter behind forever.
    """
    machine = _FakeMachine(SnapState(installed=False, enabled=False, known=False))

    first = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=machine)
    assert first.actions == ()
    assert first.prior_state == PRIOR_STATE_UNSET

    machine.state = SnapState(installed=False, enabled=False)
    second = plan_reconcile(enabled=True, prior_state=first.prior_state, observe=machine)
    machine.apply(second)

    assert second.actions == (ACTION_INSTALL, ACTION_CONNECT)
    assert second.prior_state == PRIOR_STATE_ABSENT

    teardown = plan_teardown(prior_state=second.prior_state, observe=machine)

    assert teardown.actions == (ACTION_REMOVE,)


def test_unknown_first_opt_in_then_a_readable_running_snap_records_enabled():
    """The mirror case: a foreign snap keeps the restore point that protects it."""
    machine = _FakeMachine(SnapState(installed=True, enabled=True, known=False))

    first = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=machine)
    machine.state = SnapState(installed=True, enabled=True)
    second = plan_reconcile(enabled=True, prior_state=first.prior_state, observe=machine)

    assert second.prior_state == PRIOR_STATE_ENABLED
    assert ACTION_REMOVE not in plan_teardown(prior_state=second.prior_state, observe=machine).actions


def test_disable_path_after_an_unknown_first_opt_in_still_issues_nothing():
    """Leaving `prior_state` unset is safe on the `false` path: nothing was done."""
    spy = ObserveSpy(SnapState(installed=True, enabled=True))

    plan = plan_reconcile(enabled=False, prior_state=PRIOR_STATE_UNSET, observe=spy)

    assert plan.actions == ()
    assert spy.calls == 0


def test_unknown_snap_state_after_opt_in_takes_no_action_and_keeps_prior_state():
    for prior in (PRIOR_STATE_ABSENT, PRIOR_STATE_DISABLED, PRIOR_STATE_ENABLED):
        spy = ObserveSpy(SnapState(installed=False, enabled=False, known=False))

        plan = plan_reconcile(enabled=True, prior_state=prior, observe=spy)

        assert plan.actions == (), prior
        assert plan.prior_state == prior, prior
        assert plan.scrape_enabled is False, prior


def test_unknown_snap_state_on_the_disable_path_takes_no_action():
    spy = ObserveSpy(SnapState(installed=False, enabled=False, known=False))

    plan = plan_reconcile(enabled=False, prior_state=PRIOR_STATE_ENABLED, observe=spy)

    assert plan.actions == ()
    assert plan.prior_state == PRIOR_STATE_ENABLED


# --- Teardown: full restore ---


def test_teardown_removes_only_what_the_charm_installed():
    plan = plan_teardown(prior_state=PRIOR_STATE_ABSENT, observe=ObserveSpy(SnapState(installed=True, enabled=True)))

    assert plan.actions == (ACTION_REMOVE,)
    assert plan.scrape_enabled is False


def test_teardown_redisables_what_the_charm_enabled():
    plan = plan_teardown(prior_state=PRIOR_STATE_DISABLED, observe=ObserveSpy(SnapState(installed=True, enabled=True)))

    assert plan.actions == (ACTION_DISABLE,)
    assert ACTION_REMOVE not in plan.actions


def test_teardown_reenables_a_preexisting_running_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_ENABLED, observe=ObserveSpy(SnapState(installed=True, enabled=False)))

    assert plan.actions == (ACTION_ENABLE,)
    assert ACTION_REMOVE not in plan.actions


def test_teardown_removes_a_bare_machine_opt_in_after_it_was_later_disabled():
    """Opt in on a bare machine (records `absent`), disable, then remove the unit.

    `_restore_actions` only checks `state.installed` for the `absent` row, so a snap
    that is installed-but-disabled at teardown must still be removed.
    """
    plan = plan_teardown(prior_state=PRIOR_STATE_ABSENT, observe=ObserveSpy(SnapState(installed=True, enabled=False)))

    assert plan.actions == (ACTION_REMOVE,)


# --- Teardown observes too: an unneeded action is an error, not a no-op ---


def test_teardown_does_not_reenable_an_already_enabled_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_ENABLED, observe=ObserveSpy(SnapState(installed=True, enabled=True)))

    assert plan.actions == ()


def test_teardown_does_not_enable_a_snap_that_is_no_longer_installed():
    plan = plan_teardown(prior_state=PRIOR_STATE_ENABLED, observe=ObserveSpy(SnapState(installed=False, enabled=False)))

    assert plan.actions == ()


def test_teardown_does_not_remove_an_already_absent_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_ABSENT, observe=ObserveSpy(SnapState(installed=False, enabled=False)))

    assert plan.actions == ()


def test_teardown_does_not_redisable_an_already_disabled_snap():
    plan = plan_teardown(prior_state=PRIOR_STATE_DISABLED, observe=ObserveSpy(SnapState(installed=True, enabled=False)))

    assert plan.actions == ()


def test_teardown_with_unknown_snap_state_still_attempts_the_restore():
    """Teardown is the last hook there will ever be, so best effort beats waiting."""
    unknown = SnapState(installed=False, enabled=False, known=False)
    expected = {
        PRIOR_STATE_ABSENT: (ACTION_REMOVE,),
        PRIOR_STATE_DISABLED: (ACTION_DISABLE,),
        PRIOR_STATE_ENABLED: (ACTION_ENABLE,),
    }

    for prior, actions in expected.items():
        plan = plan_teardown(prior_state=prior, observe=ObserveSpy(unknown))

        assert plan.actions == actions, prior


# --- The sequence that drove the design ---


def test_preexisting_running_snap_survives_false_true_false_teardown():
    machine = _FakeMachine(SnapState(installed=True, enabled=True))
    prior = PRIOR_STATE_UNSET
    seen = []

    for enabled in (False, True, False):
        plan = plan_reconcile(enabled=enabled, prior_state=prior, observe=machine)
        machine.apply(plan)
        seen.append(plan.actions)
        prior = plan.prior_state

    seen.append(plan_teardown(prior_state=prior, observe=machine).actions)

    assert seen == [(), (ACTION_CONNECT,), (ACTION_DISABLE,), (ACTION_ENABLE,)]


def test_true_false_true_leaves_the_snap_enabled():
    machine = _FakeMachine(SnapState(installed=True, enabled=True))
    prior = PRIOR_STATE_UNSET
    seen = []

    for enabled in (True, False, True):
        plan = plan_reconcile(enabled=enabled, prior_state=prior, observe=machine)
        machine.apply(plan)
        seen.append(plan.actions)
        prior = plan.prior_state

    assert seen == [(ACTION_CONNECT,), (ACTION_DISABLE,), (ACTION_ENABLE, ACTION_CONNECT)]
    assert machine.state == SnapState(installed=True, enabled=True)
    assert prior == PRIOR_STATE_ENABLED


def test_failed_install_is_retried_on_the_next_reconcile():
    machine = _FakeMachine(SnapState(installed=False, enabled=False))

    first = plan_reconcile(enabled=True, prior_state=PRIOR_STATE_UNSET, observe=machine)
    # `apply` raised: the machine is untouched.
    second = plan_reconcile(enabled=True, prior_state=first.prior_state, observe=machine)

    assert first.actions == (ACTION_INSTALL, ACTION_CONNECT)
    assert second.actions == (ACTION_INSTALL, ACTION_CONNECT)


# --- Snap effects tests ---

SNAP_LIST_ENABLED = (
    "Name           Version   Rev   Tracking       Publisher   Notes\n"
    "node-exporter  v1.10.2   2154  latest/stable  canonical**  -\n"
)
SNAP_LIST_DISABLED = (
    "Name           Version   Rev   Tracking       Publisher   Notes\n"
    "node-exporter  v1.10.2   2154  latest/stable  canonical**  disabled\n"
)


def _completed(stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _snap_error(stdout="", stderr=""):
    return subprocess.CalledProcessError(1, ["snap", "list", SNAP_NAME], output=stdout, stderr=stderr)


def test_observe_reports_absent_when_snapd_says_no_matching_snaps_installed():
    error = _snap_error(stderr="error: no matching snaps installed\n")

    with patch("src.node_exporter._run", side_effect=error):
        assert observe() == SnapState(installed=False, enabled=False, known=True)


def test_observe_reports_absent_when_the_marker_arrives_on_stdout():
    error = _snap_error(stdout="error: no matching snaps installed\n")

    with patch("src.node_exporter._run", side_effect=error):
        assert observe() == SnapState(installed=False, enabled=False, known=True)


def test_observe_reports_unknown_when_snapd_is_unreachable():
    error = _snap_error(stderr="error: cannot communicate with server: dial unix /run/snapd.socket: connect\n")

    with patch("src.node_exporter._run", side_effect=error):
        assert observe() == SnapState(installed=False, enabled=False, known=False)


def test_observe_reports_unknown_when_snap_list_exits_nonzero_without_explanation():
    with patch("src.node_exporter._run", side_effect=subprocess.CalledProcessError(1, "snap")):
        assert observe() == SnapState(installed=False, enabled=False, known=False)


def test_observe_reports_unknown_when_snap_binary_not_found():
    with patch("src.node_exporter._run", side_effect=FileNotFoundError()):
        assert observe() == SnapState(installed=False, enabled=False, known=False)


def test_observe_reports_unknown_when_snap_command_times_out():
    with patch("src.node_exporter._run", side_effect=subprocess.TimeoutExpired(cmd="snap", timeout=60)):
        assert observe() == SnapState(installed=False, enabled=False, known=False)


def test_observe_reports_absent_when_snap_list_omits_the_row():
    header = "Name  Version  Rev  Tracking  Publisher  Notes\n"

    with patch("src.node_exporter._run", return_value=_completed(header)):
        assert observe() == SnapState(installed=False, enabled=False, known=True)


def test_observe_reports_enabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_ENABLED)):
        assert observe() == SnapState(installed=True, enabled=True)


def test_observe_reports_disabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_DISABLED)):
        assert observe() == SnapState(installed=True, enabled=False)


def test_effects_build_expected_argv():
    with patch("src.node_exporter._run") as run_mock:
        install()
        enable()
        disable()
        remove()

    assert run_mock.call_args_list[0].args[0] == ["snap", "install", SNAP_NAME]
    assert run_mock.call_args_list[1].args[0] == ["snap", "enable", SNAP_NAME]
    assert run_mock.call_args_list[2].args[0] == ["snap", "disable", SNAP_NAME]
    assert run_mock.call_args_list[3].args[0] == ["snap", "remove", "--purge", SNAP_NAME]


def test_failed_snap_command_reports_snapd_own_explanation():
    completed = subprocess.CompletedProcess(args=["snap", "enable", SNAP_NAME], returncode=1, stdout="", stderr="")

    with patch("src.node_exporter.subprocess.run") as run_mock:
        run_mock.side_effect = subprocess.CalledProcessError(
            1,
            completed.args,
            output="",
            stderr='error: cannot perform the following tasks:\n- Enable snap "node-exporter" (unset)\n',
        )
        try:
            enable()
        except subprocess.CalledProcessError as exc:
            message = str(exc)
        else:  # pragma: no cover - the call above always raises
            raise AssertionError("enable() did not raise")

    assert "cannot perform the following tasks" in message
    assert 'Enable snap "node-exporter"' in message


def test_run_does_not_inject_apt_environment_variables():
    with patch("src.node_exporter.subprocess.run", return_value=_completed()) as run_mock:
        install()

    assert "env" not in run_mock.call_args.kwargs or run_mock.call_args.kwargs["env"] is None


def test_connect_interfaces_connects_every_required_interface():
    with patch("src.node_exporter._run") as run_mock:
        connect_interfaces()

    connected = [c.args[0][2] for c in run_mock.call_args_list]
    assert connected == [f"{SNAP_NAME}:{name}" for name in REQUIRED_INTERFACES]


def test_connect_interfaces_continues_past_a_failed_connection():
    outcomes = [subprocess.CalledProcessError(1, "snap"), _completed(), _completed(), _completed()]

    with patch("src.node_exporter._run", side_effect=outcomes) as run_mock:
        connect_interfaces()

    assert run_mock.call_count == len(REQUIRED_INTERFACES)


def test_connect_interfaces_continues_past_a_file_not_found_error():
    outcomes = [FileNotFoundError(), _completed(), _completed(), _completed()]

    with patch("src.node_exporter._run", side_effect=outcomes) as run_mock:
        connect_interfaces()

    assert run_mock.call_count == len(REQUIRED_INTERFACES)


def test_apply_dispatches_actions_in_order():
    plan_val = Plan(actions=(ACTION_INSTALL, ACTION_CONNECT))

    with (
        patch("src.node_exporter.install") as install_mock,
        patch("src.node_exporter.connect_interfaces") as connect_mock,
        patch("src.node_exporter.enable") as enable_mock,
    ):
        apply(plan_val)

    install_mock.assert_called_once_with()
    connect_mock.assert_called_once_with()
    enable_mock.assert_not_called()


def test_apply_does_nothing_for_an_empty_plan():
    with patch("src.node_exporter._run") as run_mock:
        apply(Plan())

    run_mock.assert_not_called()


def test_scrape_job_targets_localhost_with_topology_labels():
    labels = {"juju_application": "polkadot", "juju_unit": "polkadot/0"}

    job = scrape_job(topology_labels=labels, scrape_timeout="5s")

    assert job.job_name == "node-exporter"
    assert job.metrics_path == "/metrics"
    assert job.scheme == "http"
    assert job.scrape_interval == "15s"
    assert job.scrape_timeout == "5s"
    assert len(job.targets) == 1
    assert job.targets[0].address == "localhost:9100"
    assert job.targets[0].labels == labels


def test_scrape_job_copies_the_labels_it_is_given():
    labels = {"juju_unit": "polkadot/0"}

    job = scrape_job(topology_labels=labels, scrape_timeout="10s")
    labels["juju_unit"] = "mutated"

    assert job.targets[0].labels == {"juju_unit": "polkadot/0"}
