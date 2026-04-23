"""Helpers for building charm-managed Alloy service arguments."""

from __future__ import annotations

import shlex

DEFAULT_LISTEN_ADDRESS = "--server.http.listen-addr=0.0.0.0:6987"
_FORBIDDEN_FLAGS = (
    "--server.http.listen-addr",
    "--config.file",
)


def build_effective_custom_args(custom_args: str | None) -> str:
    """Return required Alloy args plus validated user-provided args."""
    user_tokens = shlex.split(custom_args or "")
    _validate_user_tokens(user_tokens)
    if not user_tokens:
        return DEFAULT_LISTEN_ADDRESS
    return shlex.join([DEFAULT_LISTEN_ADDRESS, *user_tokens])


def _validate_user_tokens(tokens: list[str]) -> None:
    """Reject user arguments that would override charm-owned settings."""
    for token in tokens:
        for forbidden_flag in _FORBIDDEN_FLAGS:
            if token == forbidden_flag or token.startswith(f"{forbidden_flag}="):
                raise ValueError(f"{forbidden_flag} must not be set in custom-args.")
