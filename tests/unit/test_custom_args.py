import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from custom_args import DEFAULT_LISTEN_ADDRESS, build_effective_custom_args


def test_build_effective_custom_args_uses_required_default_when_empty():
    assert build_effective_custom_args("") == DEFAULT_LISTEN_ADDRESS


def test_build_effective_custom_args_appends_user_args_after_required_default():
    assert build_effective_custom_args("--log.level=debug") == (f"{DEFAULT_LISTEN_ADDRESS} --log.level=debug")


@pytest.mark.parametrize("custom_args", ["--server.http.listen-addr=127.0.0.1:12345", "--config.file=/tmp/test"])
def test_build_effective_custom_args_rejects_forbidden_overrides(custom_args: str):
    with pytest.raises(ValueError, match="must not be set"):
        build_effective_custom_args(custom_args)
