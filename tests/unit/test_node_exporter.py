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
    get_version,
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


def test_observe_reports_absent_when_snap_list_exits_nonzero():
    with patch("src.node_exporter._run", side_effect=subprocess.CalledProcessError(1, "snap")):
        assert observe() == SnapState(installed=False, enabled=False)


def test_observe_reports_absent_when_snap_binary_not_found():
    with patch("src.node_exporter._run", side_effect=FileNotFoundError()):
        assert observe() == SnapState(installed=False, enabled=False)


def test_observe_reports_absent_when_snap_command_times_out():
    with patch("src.node_exporter._run", side_effect=subprocess.TimeoutExpired(cmd="snap", timeout=60)):
        assert observe() == SnapState(installed=False, enabled=False)


def test_observe_reports_enabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_ENABLED)):
        assert observe() == SnapState(installed=True, enabled=True)


def test_observe_reports_disabled_snap():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_DISABLED)):
        assert observe() == SnapState(installed=True, enabled=False)


def test_get_version_reads_the_version_column():
    with patch("src.node_exporter._run", return_value=_completed(SNAP_LIST_ENABLED)):
        assert get_version() == "v1.10.2"


def test_get_version_is_none_when_snap_absent():
    with patch("src.node_exporter._run", side_effect=subprocess.CalledProcessError(1, "snap")):
        assert get_version() is None


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

    job = scrape_job(topology_labels=labels, scrape_interval="30s", scrape_timeout="5s")

    assert job.job_name == "node-exporter"
    assert job.metrics_path == "/metrics"
    assert job.scheme == "http"
    assert job.scrape_interval == "30s"
    assert job.scrape_timeout == "5s"
    assert len(job.targets) == 1
    assert job.targets[0].address == "localhost:9100"
    assert job.targets[0].labels == labels


def test_scrape_job_copies_the_labels_it_is_given():
    labels = {"juju_unit": "polkadot/0"}

    job = scrape_job(topology_labels=labels, scrape_interval="1m", scrape_timeout="10s")
    labels["juju_unit"] = "mutated"

    assert job.targets[0].labels == {"juju_unit": "polkadot/0"}
