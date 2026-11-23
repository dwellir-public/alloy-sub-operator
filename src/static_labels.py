"""Parse and validate the static-labels charm option."""

import re

MAX_STATIC_LABELS = 16
MAX_LABEL_LENGTH = 128
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_NAMES = frozenset({"job", "instance"})
_RESERVED_PREFIXES = ("juju_", "__")


MAX_ECHO_LENGTH = 64


def _clip(text: str) -> str:
    """Bound operator input echoed in an error message."""
    return text if len(text) <= MAX_ECHO_LENGTH else text[:MAX_ECHO_LENGTH] + "..."


class StaticLabelsError(ValueError):
    """Raised when the static-labels option is invalid."""


def parse_static_labels(raw: str) -> dict[str, str]:
    """Return the labels in a comma-separated ``name=value`` string, in the order written."""
    labels: dict[str, str] = {}
    for segment in raw.split(","):
        entry = segment.strip()
        if not entry:
            continue
        name, separator, value = entry.partition("=")
        name, value = name.strip(), value.strip()
        if not separator:
            raise StaticLabelsError(f"static-labels entry {_clip(entry)!r} must be name=value")
        _validate(entry, name, value)
        if name in labels:
            raise StaticLabelsError(f"static-labels name {_clip(name)!r} is repeated")
        labels[name] = value
    if len(labels) > MAX_STATIC_LABELS:
        raise StaticLabelsError(f"static-labels allows at most {MAX_STATIC_LABELS} labels")
    return labels


def _validate(entry: str, name: str, value: str) -> None:
    if not _NAME_PATTERN.match(name):
        raise StaticLabelsError(f"static-labels entry {_clip(entry)!r} has an invalid label name")
    if name in _RESERVED_NAMES or name.startswith(_RESERVED_PREFIXES):
        raise StaticLabelsError(f"static-labels name {_clip(name)!r} is reserved")
    if not value:
        raise StaticLabelsError(f"static-labels entry {_clip(entry)!r} has an empty value")
    if len(name) > MAX_LABEL_LENGTH or len(value) > MAX_LABEL_LENGTH:
        raise StaticLabelsError(f"static-labels entry for {_clip(name)!r} exceeds {MAX_LABEL_LENGTH} characters")
