import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import alloy


@pytest.mark.parametrize(
    ("dpkg", "which", "unit", "expected"),
    [
        ("install ok installed", None, False, "package"),
        ("install ok installed", "/usr/bin/alloy", True, "package"),
        ("deinstall ok config-files", None, False, "none"),
        (None, None, False, "none"),
        (None, "/usr/local/bin/alloy", False, "other"),
        (None, None, True, "other"),
        ("deinstall ok config-files", "/usr/local/bin/alloy", False, "other"),
    ],
)
def test_installation(dpkg, which, unit, expected):
    def fake_run(cmd, **kwargs):
        command = list(cmd)
        if command[0] == "dpkg-query":
            if dpkg is None:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout=dpkg, stderr="", returncode=0)
        if command[0] == "systemctl":
            if not unit:
                raise subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout="[Unit]", stderr="", returncode=0)
        raise AssertionError(f"unexpected command {command}")

    with patch("alloy._run", side_effect=fake_run), patch("alloy.shutil.which", return_value=which):
        assert alloy.installation() == expected


def test_installation_reports_none_when_tools_are_missing():
    with (
        patch("alloy._run", side_effect=FileNotFoundError("dpkg-query")),
        patch("alloy.shutil.which", return_value=None),
    ):
        assert alloy.installation() == "none"


@pytest.mark.parametrize(
    ("which", "unit", "expected"),
    [(None, False, "none"), ("/usr/local/bin/alloy", False, "other"), (None, True, "other")],
)
def test_installation_treats_a_missing_dpkg_as_absent(which, unit, expected):
    def fake_run(cmd, **kwargs):
        command = list(cmd)
        if command[0] == "dpkg-query":
            raise FileNotFoundError("dpkg-query")
        if not unit:
            raise FileNotFoundError("systemctl")
        return SimpleNamespace(stdout="[Unit]", stderr="", returncode=0)

    with patch("alloy._run", side_effect=fake_run), patch("alloy.shutil.which", return_value=which):
        assert alloy.installation() == expected


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(["dpkg-query"], 10), PermissionError("dpkg-query"), OSError("too many open files")],
)
def test_installation_raises_when_the_probe_cannot_answer(error):
    with (
        patch("alloy._run", side_effect=error),
        patch("alloy.shutil.which", return_value=None),
        pytest.raises(type(error)),
    ):
        alloy.installation()


def test_installation_raises_when_the_systemd_probe_times_out():
    def fake_run(cmd, **kwargs):
        if list(cmd)[0] == "dpkg-query":
            raise subprocess.CalledProcessError(1, cmd)
        raise subprocess.TimeoutExpired(cmd, 10)

    with (
        patch("alloy._run", side_effect=fake_run),
        patch("alloy.shutil.which", return_value=None),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        alloy.installation()
