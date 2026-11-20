"""Machine-wide record of which unit owns the Alloy installation.

The marker lives outside any unit's charm directory so every alloy-sub unit
(and any other charm using the same convention) on the machine sees it.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

MARKER_DIR = Path("/var/lib/juju-alloy")
MARKER_NAME = "owner.json"
LOCK_NAME = "lock"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarkerState:
    """The marker's raw content as a claim found it; ``None`` when there was no marker."""

    content: bytes | None


@dataclass(frozen=True)
class Claim:
    """Outcome of trying to claim the machine's Alloy.

    ``prior`` is the marker as the claim found it, for ``restore``.
    """

    state: Literal["owned", "other", "unmanaged"]
    owner: str | None = None
    prior: MarkerState | None = field(default=None, compare=False, repr=False)


def _dir(marker_dir: Path | None) -> Path:
    return MARKER_DIR if marker_dir is None else marker_dir


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / LOCK_NAME, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_raw(directory: Path) -> MarkerState:
    """Return the marker's raw content; raise ``OSError`` when it cannot be read."""
    try:
        return MarkerState((directory / MARKER_NAME).read_bytes())
    except FileNotFoundError:
        return MarkerState(None)


def _parse(raw: MarkerState) -> tuple[Literal["missing", "released", "unreadable", "owned"], str | None]:
    """Return the marker state and the owner, if any."""
    if raw.content is None:
        return "missing", None
    try:
        data = json.loads(raw.content.decode("utf-8"))
    except (ValueError, RecursionError):
        return "unreadable", None
    if not isinstance(data, dict) or "owner" not in data:
        return "unreadable", None
    owner = data["owner"]
    if owner is None:
        return "released", None
    if isinstance(owner, str) and owner:
        return "owned", owner
    return "unreadable", None


def _snapshot(directory: Path) -> tuple[MarkerState, Literal["missing", "released", "unreadable", "owned"], str | None]:
    """Return the marker's raw content with its parsed state and owner.

    A marker that exists but cannot be read is captured as empty content,
    which reads back as unreadable too.
    """
    try:
        raw = _read_raw(directory)
    except OSError:
        return MarkerState(b""), "unreadable", None
    state, owner = _parse(raw)
    return raw, state, owner


def _read(directory: Path) -> tuple[Literal["missing", "released", "unreadable", "owned"], str | None]:
    """Return the marker state and the owner, if any."""
    _raw, state, owner = _snapshot(directory)
    return state, owner


def _write_bytes(directory: Path, content: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".owner-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, directory / MARKER_NAME)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _write(directory: Path, owner: str | None) -> None:
    _write_bytes(directory, json.dumps({"owner": owner}).encode("utf-8"))


def _take(directory: Path, unit_name: str) -> Claim:
    _write(directory, unit_name)
    return Claim("owned", unit_name)


def _claim_existing(directory: Path, unit_name: str, state: str, owner: str | None, force: bool) -> Claim:
    """Decide for a marker naming an owner or unreadable."""
    if state == "owned" and owner == unit_name:
        return Claim("owned", unit_name)
    if force:
        return _take(directory, unit_name)
    return Claim("other", owner if state == "owned" else UNKNOWN)


def _claim_unowned(
    directory: Path, unit_name: str, released: bool, installed: bool, allow_existing: bool, force: bool
) -> Claim:
    """Decide for a missing or released marker."""
    if released or not installed or allow_existing or force:
        return _take(directory, unit_name)
    return Claim("unmanaged")


def _decide(
    directory: Path, unit_name: str, state: str, owner: str | None, installed: bool, allow_existing: bool, force: bool
) -> Claim:
    if state in ("owned", "unreadable"):
        return _claim_existing(directory, unit_name, state, owner, force)
    return _claim_unowned(directory, unit_name, state == "released", installed, allow_existing, force)


def claim(
    unit_name: str,
    *,
    installed: bool,
    allow_existing: bool = False,
    force: bool = False,
    marker_dir: Path | None = None,
) -> Claim:
    """Claim the machine's Alloy for ``unit_name`` if the marker allows it."""
    directory = _dir(marker_dir)
    with _locked(directory):
        prior, state, owner = _snapshot(directory)
        result = _decide(directory, unit_name, state, owner, installed, allow_existing, force)
        return Claim(result.state, result.owner, prior)


def current_owner(*, marker_dir: Path | None = None) -> str | None:
    """Return the owner, None when missing or released, "unknown" when unreadable."""
    state, owner = _read(_dir(marker_dir))
    if state == "unreadable":
        return UNKNOWN
    return owner


def release(unit_name: str, *, marker_dir: Path | None = None) -> bool:
    """Release the marker if ``unit_name`` owns it; return whether it did."""
    directory = _dir(marker_dir)
    if not directory.is_dir():
        return False
    with _locked(directory):
        state, owner = _read(directory)
        if state != "owned" or owner != unit_name:
            return False
        _write(directory, None)
        return True


def restore(unit_name: str, prior: MarkerState, *, marker_dir: Path | None = None) -> bool:
    """Put back the marker a claim by ``unit_name`` found; return whether it did.

    Only a marker that still names ``unit_name`` is restored, so a later claim
    by another unit is never undone.
    """
    directory = _dir(marker_dir)
    with _locked(directory):
        state, owner = _read(directory)
        if state != "owned" or owner != unit_name:
            return False
        if prior.content is None:
            (directory / MARKER_NAME).unlink(missing_ok=True)
        else:
            _write_bytes(directory, prior.content)
        return True
