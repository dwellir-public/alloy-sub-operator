import subprocess
from unittest.mock import Mock

import pytest
from ops import testing

import alloy_ownership
import diagnostics
from charm import AlloySubCharm


@pytest.fixture
def probes(monkeypatch, tmp_path):
    config = tmp_path / "config.alloy"
    config.write_text('logging { level = "info" }\n')
    monkeypatch.setattr(diagnostics, "DEFAULT_CONFIG_PATH", str(config))
    monkeypatch.setattr(diagnostics.alloy, "verify_config", Mock())
    commands = []

    def command(args):
        commands.append(args)
        if args[0] == "systemctl":
            return (
                "ActiveState=active\nSubState=running\nMainPID=42\nExecMainStartTimestamp=Fri 2026-11-27 07:00:00 UTC\n"
            )
        return (
            "2026-11-27T07:00:01+00:00 host alloy[42]: failed to send to "
            "https://user:secret@host/path?token=secret token=secret\n"
        )

    monkeypatch.setattr(diagnostics, "_command", command)
    return config, commands


@pytest.mark.real_ownership
@pytest.mark.parametrize("unit_status", [testing.ActiveStatus(), testing.BlockedStatus("failed")])
def test_action_is_read_only_and_available_on_unhealthy_units(monkeypatch, probes, unit_status):
    ctx = testing.Context(AlloySubCharm)
    owner = f"{ctx.app_name}/0"
    alloy_ownership.claim(owner, installed=False)
    marker = alloy_ownership.MARKER_DIR / alloy_ownership.MARKER_NAME
    before_marker = marker.read_bytes()
    before_config = probes[0].read_bytes()

    def unexpected(*args, **kwargs):
        raise AssertionError("diagnostics must not reconcile or mutate Alloy")

    for method in ("_configure", "_ensure_alloy_ownership"):
        monkeypatch.setattr(AlloySubCharm, method, unexpected)
    for method in ("start", "stop", "restart", "reload", "install", "write_config_text"):
        monkeypatch.setattr(diagnostics.alloy, method, unexpected)
    state = ctx.run(ctx.on.action("get-diagnostics"), testing.State(unit_status=unit_status))

    after = ctx.run(ctx.on.action("get-diagnostics"), state)

    assert after.unit_status == state.unit_status
    assert after.relations == state.relations
    assert {s for s in after.stored_states if s.owner_path} == {s for s in state.stored_states if s.owner_path}
    assert marker.read_bytes() == before_marker
    assert probes[0].read_bytes() == before_config
    assert ctx.action_results is not None
    assert ctx.action_results["owned-by-unit"] is True
    assert ctx.action_results["service-state"] == "active"
    assert ctx.action_results["main-pid"] == "42"
    assert ctx.action_results["config-syntax"] == "valid"
    assert "secret" not in str(ctx.action_results["recent-logs"])


@pytest.mark.real_ownership
def test_action_does_not_adopt_an_unowned_installation(probes):
    ctx = testing.Context(AlloySubCharm)
    ctx.run(ctx.on.action("get-diagnostics"), testing.State())
    assert ctx.action_results is not None
    assert ctx.action_results["owner"] == "unclaimed"
    assert ctx.action_results["owned-by-unit"] is False
    assert not alloy_ownership.MARKER_DIR.exists()


@pytest.mark.real_ownership
def test_action_reports_other_owner(probes):
    alloy_ownership.claim("other/7", installed=False)
    ctx = testing.Context(AlloySubCharm)
    ctx.run(ctx.on.action("get-diagnostics"), testing.State())
    assert ctx.action_results is not None
    assert ctx.action_results["owner"] == "other/7"
    assert ctx.action_results["owned-by-unit"] is False


@pytest.mark.parametrize("failure", [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("probe", 10)])
def test_unavailable_probes_are_not_a_pass(monkeypatch, probes, failure):
    def unavailable(*args, **kwargs):
        raise failure

    monkeypatch.setattr(diagnostics, "_command", unavailable)
    monkeypatch.setattr(diagnostics.alloy, "verify_config", unavailable)
    results = diagnostics.collect(lookback_minutes=10, log_lines=50)
    assert results["service-state"] == "unknown"
    assert results["service-query"] == "unavailable"
    assert results["config-syntax"] == "unavailable"
    assert results["logs-status"] == "unavailable"


def test_invalid_config_does_not_expose_parser_output(monkeypatch, probes):
    def invalid(**kwargs):
        raise subprocess.CalledProcessError(1, "alloy", output="secret", stderr="secret")

    monkeypatch.setattr(diagnostics.alloy, "verify_config", invalid)
    results = diagnostics.collect(lookback_minutes=5, log_lines=0)
    assert results["config-syntax"] == "invalid"
    assert results["logs-status"] == "not-requested"
    assert len(probes[1]) == 1
    assert "secret" not in str(results)


def test_missing_config_is_unavailable(monkeypatch, tmp_path, probes):
    monkeypatch.setattr(diagnostics, "DEFAULT_CONFIG_PATH", str(tmp_path / "missing"))
    results = diagnostics.collect(lookback_minutes=5, log_lines=0)
    assert results["config-syntax"] == "unavailable"
    assert results["config-sha256"] == "unavailable"


def test_inactive_service_is_reported(monkeypatch, probes):
    monkeypatch.setattr(diagnostics, "_command", lambda _: "ActiveState=inactive\nMainPID=0\n")
    results = diagnostics.collect(lookback_minutes=5, log_lines=0)
    assert results["service-state"] == "inactive"
    assert results["main-pid"] == "0"


def test_log_window_and_output_are_bounded(monkeypatch, probes):
    monkeypatch.setattr(diagnostics, "_command", lambda _: "x" * (diagnostics.MAX_LOG_BYTES + 100))
    results = diagnostics.collect(lookback_minutes=60, log_lines=100)
    assert len(str(results["recent-logs"]).encode()) == diagnostics.MAX_LOG_BYTES
    assert results["logs-truncated"] is True


@pytest.mark.parametrize("minutes,lines", [(0, 50), (61, 50), (10, -1), (10, 101)])
def test_invalid_limits_fail_before_running_commands(monkeypatch, minutes, lines):
    command = Mock()
    monkeypatch.setattr(diagnostics, "_command", command)
    with pytest.raises(ValueError, match="lookback-minutes"):
        diagnostics.collect(lookback_minutes=minutes, log_lines=lines)
    command.assert_not_called()


def test_fixed_read_only_commands_and_timeouts(monkeypatch, probes):
    diagnostics.collect(lookback_minutes=5, log_lines=7)
    service, journal = probes[1]
    assert service[:3] == ["systemctl", "show", "alloy.service"]
    assert "--unit=alloy.service" in journal
    assert not any(arg.startswith("--priority") for arg in journal)
    assert "--lines=7" in journal
    assert "--since" in journal
    check = diagnostics.alloy.verify_config
    assert isinstance(check, Mock)
    check.assert_called_once_with(config_path=probes[0])


def test_command_timeout_and_no_shell(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="ok"))
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    diagnostics._command(["systemctl", "show", "alloy.service"])
    assert run.call_args.kwargs["timeout"] == 10
    assert "shell" not in run.call_args.kwargs


def test_redacts_common_credentials():
    message = 'password="secret value" api_key=secret Bearer secret https://host/query?token=secret'
    assert "secret" not in diagnostics._redact_logs(message)
