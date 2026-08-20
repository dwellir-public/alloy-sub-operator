"""Transform machine-observability artifacts into backend alert-rule state."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import re
import subprocess
import tempfile
import time
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from charms.dwellir_observability.v0.machine_observability import (
    MAX_DECODED_ARTIFACT_BYTES,
    MAX_TOTAL_DECODED_ARTIFACT_BYTES,
    ObservabilityArtifact,
)
from pydantic import ValidationError
from yaml.tokens import AliasToken, AnchorToken, TagToken


@dataclass(frozen=True)
class RuleBuildResult:
    """Hold deterministic Prometheus/Loki desired state and safe artifact errors."""

    prometheus: dict[str, list[dict[str, object]]]
    loki: dict[str, list[dict[str, object]]]
    errors: tuple[str, ...]


_TOPOLOGY_FIELDS = (
    ("juju_model", "model"),
    ("juju_model_uuid", "model_uuid"),
    ("juju_application", "application"),
    ("juju_unit", "unit"),
    ("juju_charm", "charm_name"),
)
_TOPOLOGY_LABELS = {label for label, _ in _TOPOLOGY_FIELDS}
_GROUP_FIELDS = {"name", "rules", "labels", "interval", "limit", "query_offset", "evaluation_delay"}
_RULE_FIELDS = {"alert", "record", "expr", "for", "keep_firing_for", "labels", "annotations"}
_DURATION_PATTERN = re.compile(
    r"^(?:[0-9]+y)?(?:[0-9]+w)?(?:[0-9]+d)?(?:[0-9]+h)?(?:[0-9]+m)?(?:[0-9]+s)?(?:[0-9]+ms)?$"
)
_ARTIFACT_TYPES = {"prometheus_alert_rules", "loki_alert_rules"}
_ARTIFACT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_LABEL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_METRIC_NAME_PATTERN = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
RuleValidator = Callable[[str, list[dict[str, object]]], bool]
MAX_RULE_ARTIFACTS = 32
_MAX_VALIDATION_CALLS = 32
_VALIDATION_BUDGET_SECONDS = 15.0
_VALIDATION_PROCESS_TIMEOUT_SECONDS = 3.0


class CosToolRuleValidator:
    """Validate transformed rule artifacts with the packaged canonical cos-tool."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        runner: Callable[..., Any] = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._path = path or Path(__file__).resolve().parents[1] / "cos-tool-amd64"
        self._runner = runner
        self._clock = clock
        self.begin_reconcile()

    def begin_reconcile(self) -> None:
        """Reset the shared subprocess-call and monotonic-time budget for one hook."""
        self._calls = 0
        self._deadline = self._clock() + _VALIDATION_BUDGET_SECONDS

    def __call__(self, artifact_type: str, groups: list[dict[str, object]]) -> bool:
        """Run cos-tool without exposing its potentially content-bearing output."""
        try:
            executable = self._path.is_file() and bool(self._path.stat().st_mode & 0o111)
        except OSError:
            executable = False
        if not executable or self._calls >= _MAX_VALIDATION_CALLS or self._clock() >= self._deadline:
            return False
        query_type = "promql" if artifact_type == "prometheus_alert_rules" else "logql"
        try:
            with tempfile.TemporaryDirectory() as directory:
                rules_path = Path(directory) / "rules.yaml"
                rules_path.write_text(yaml.safe_dump({"groups": groups}, sort_keys=True))
                remaining = self._deadline - self._clock()
                if remaining <= 0 or self._calls >= _MAX_VALIDATION_CALLS:
                    return False
                self._calls += 1
                result = self._runner(
                    [str(self._path), "--format", query_type, "validate", str(rules_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=min(_VALIDATION_PROCESS_TIMEOUT_SECONDS, remaining),
                )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0


class _DecodeError(ValueError):
    """Carry a safe failure category and charged decoded-byte count."""

    def __init__(self, category: str, decoded_bytes: int):
        super().__init__(category)
        self.category = category
        self.decoded_bytes = decoded_bytes


def _decode_bounded(artifact: ObservabilityArtifact, maximum_bytes: int) -> bytes:
    """Decode one artifact without exceeding its remaining aggregate allowance."""
    if maximum_bytes <= 0:
        raise _DecodeError("size", 0)
    try:
        compressed = base64.b64decode(artifact.content, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise _DecodeError("encoding", 0) from exc

    limit = min(MAX_DECODED_ARTIFACT_BYTES, maximum_bytes)
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    content = bytearray()
    try:
        for offset in range(0, len(compressed), 8192):
            pending = compressed[offset : offset + 8192]
            while pending:
                remaining = limit - len(content)
                if remaining <= 0:
                    raise _DecodeError("size", len(content))
                decoded = decompressor.decompress(pending, remaining)
                content.extend(decoded)
                pending = decompressor.unconsumed_tail
                if decompressor.unused_data:
                    raise _DecodeError("encoding", len(content))
    except zlib.error as exc:
        raise _DecodeError("encoding", len(content)) from exc

    if not decompressor.eof:
        category = "size" if len(content) >= limit else "encoding"
        raise _DecodeError(category, len(content))
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), artifact.sha256):
        raise _DecodeError("checksum", len(content))
    return bytes(content)


def _value(source: Any, field: str, default: Any = "") -> Any:
    """Read a field from a mapping or attribute-bearing payload object."""
    if isinstance(source, Mapping):
        return source.get(field, default)
    return getattr(source, field, default)


def _topology_labels(payload: Any) -> dict[str, str]:
    """Return non-empty canonical labels from the original principal topology."""
    topology = _value(payload, "source_topology", None)
    if topology is None:
        return {}
    return {label: str(value) for label, field in _TOPOLOGY_FIELDS if (value := _value(topology, field, ""))}


def _valid_ownership_component(value: object) -> bool:
    """Accept bounded printable identity components that cannot confuse ownership paths."""
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < len(value.encode("utf-8")) <= 256
        and "/" not in value
        and value.isprintable()
    )


def _matcher_value(value: str) -> str:
    """Escape one topology value for a PromQL or LogQL string matcher."""
    return json.dumps(value, ensure_ascii=False)


def _safe_name(value: str) -> str:
    """Convert a name component to a readable filesystem-safe representation."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "rules"


def _validate_rule_document(document: Any) -> list[dict[str, object]]:
    """Validate standard Prometheus/Loki rule semantics and return the groups."""
    if not isinstance(document, dict) or "groups" not in document:
        raise ValueError("schema")
    groups = document["groups"]
    if not isinstance(groups, list):
        raise ValueError("schema")
    for group in groups:
        _validate_group(group)
    return groups


def _validate_group(group: Any) -> None:
    """Validate one standard rule group and all rules it contains."""
    if not isinstance(group, dict) or not set(group).issubset(_GROUP_FIELDS):
        raise ValueError("schema")
    if not isinstance(group.get("name"), str) or not group["name"].strip():
        raise ValueError("schema")
    if not isinstance(group.get("rules"), list):
        raise ValueError("schema")
    if any(
        key in group and not _valid_duration(group[key]) for key in ("interval", "query_offset", "evaluation_delay")
    ):
        raise ValueError("schema")
    if "limit" in group and (
        not isinstance(group["limit"], int) or isinstance(group["limit"], bool) or group["limit"] < 0
    ):
        raise ValueError("schema")
    if "labels" in group and not _valid_rule_mapping(group["labels"]):
        raise ValueError("schema")
    for rule in group["rules"]:
        _validate_rule(rule)


def _validate_rule(rule: Any) -> None:
    """Validate one alerting or recording rule and its typed standard fields."""
    if not isinstance(rule, dict):
        raise ValueError("schema")
    if not set(rule).issubset(_RULE_FIELDS):
        raise ValueError("schema")
    has_alert = "alert" in rule
    has_record = "record" in rule
    if has_alert == has_record:
        raise ValueError("schema")
    identity = rule.get("alert") if has_alert else rule.get("record")
    expression = rule.get("expr")
    if not _valid_rule_identity(identity, alert=has_alert):
        raise ValueError("schema")
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("schema")
    if has_record and set(rule) - {"record", "expr", "labels"}:
        raise ValueError("schema")
    if any(key in rule and not _valid_duration(rule[key]) for key in ("for", "keep_firing_for")):
        raise ValueError("schema")
    for mapping_name in ("labels", "annotations"):
        values = rule.get(mapping_name, {})
        if not _valid_rule_mapping(values):
            raise ValueError("schema")


def _valid_rule_mapping(values: object) -> bool:
    """Return whether labels or annotations use valid string names and values."""
    return isinstance(values, dict) and all(
        isinstance(key, str) and _LABEL_NAME_PATTERN.fullmatch(key) is not None and isinstance(value, str)
        for key, value in values.items()
    )


def _valid_alert_name(value: object) -> bool:
    """Accept bounded printable nonempty alert identifiers for backend validation."""
    return isinstance(value, str) and bool(value.strip()) and len(value.encode("utf-8")) <= 256 and value.isprintable()


def _valid_rule_identity(value: object, *, alert: bool) -> bool:
    """Validate alert names locally and recording names against metric syntax."""
    if alert:
        return _valid_alert_name(value)
    return isinstance(value, str) and _METRIC_NAME_PATTERN.fullmatch(value) is not None


def _valid_duration(value: object) -> bool:
    """Return whether a value uses Prometheus's compound duration syntax."""
    return isinstance(value, str) and (value == "0" or (bool(value) and _DURATION_PATTERN.fullmatch(value) is not None))


def validate_rule_groups(groups: object) -> bool:
    """Return whether groups satisfy the same strict backend boundary as artifacts."""
    try:
        _validate_rule_document({"groups": groups})
    except ValueError:
        return False
    return True


def _load_rule_document(decoded: bytes) -> Any:
    """Safely parse JSON-compatible YAML while rejecting reference and tag features."""
    try:
        text = decoded.decode("utf-8")
        if any(isinstance(token, (AnchorToken, AliasToken, TagToken)) for token in yaml.scan(text)):
            raise ValueError("schema")
        return yaml.safe_load(text)
    except UnicodeDecodeError as exc:
        raise RuntimeError("json") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("json") from exc


def _transform_groups(
    groups: list[dict[str, object]],
    *,
    artifact_id: str,
    ownership: str,
    topology_labels: dict[str, str],
) -> list[dict[str, object]]:
    """Copy, scope, and deterministically name groups from one valid artifact."""
    matcher = ",".join(f"{key}={_matcher_value(value)}" for key, value in topology_labels.items())
    identifier = _safe_name(
        "-".join(
            value
            for key, value in topology_labels.items()
            if key in {"juju_model_uuid", "juju_application", "juju_unit"}
        )
    )
    transformed: list[tuple[str, str, dict[str, object]]] = []
    ownership_digest = hashlib.sha256(ownership.encode("utf-8")).hexdigest()[:12]
    for original_group in groups:
        group = copy.deepcopy(original_group)
        group_labels = group.get("labels")
        if isinstance(group_labels, dict):
            group["labels"] = {key: value for key, value in group_labels.items() if key not in _TOPOLOGY_LABELS}
        base_name = "-".join(
            (
                identifier,
                ownership_digest,
                _safe_name(artifact_id),
                _safe_name(str(original_group["name"])),
            )
        )
        group["name"] = base_name
        for rule in group["rules"]:  # type: ignore[index,union-attr]
            expression = rule.get("expr")
            if isinstance(expression, str):
                rule["expr"] = expression.replace("%%juju_topology%%", matcher)
            labels = {key: value for key, value in rule.get("labels", {}).items() if key not in _TOPOLOGY_LABELS}
            labels.update(topology_labels)
            rule["labels"] = labels
        original_key = json.dumps(original_group, ensure_ascii=False, sort_keys=True)
        transformed.append((base_name, original_key, group))

    name_counts: dict[str, int] = {}
    result: list[dict[str, object]] = []
    for base_name, original_key, group in sorted(transformed, key=lambda item: (item[0], item[1])):
        matching_count = sum(item[0] == base_name for item in transformed)
        if matching_count > 1:
            digest = hashlib.sha256(original_key.encode()).hexdigest()[:8]
            occurrence = name_counts.get(f"{base_name}-{digest}", 0) + 1
            name_counts[f"{base_name}-{digest}"] = occurrence
            suffix = f"-{occurrence}" if occurrence > 1 else ""
            group["name"] = f"{base_name}-{digest}{suffix}"
        result.append(group)
    return result


def _identity(raw_artifact: Any) -> tuple[str, str]:
    """Extract a safe artifact identity without including artifact content."""
    raw_type = _value(raw_artifact, "artifact_type", "unknown")
    raw_id = _value(raw_artifact, "artifact_id", "unknown")
    artifact_type = raw_type if isinstance(raw_type, str) and raw_type in _ARTIFACT_TYPES else "unknown"
    artifact_id = raw_id if isinstance(raw_id, str) and _ARTIFACT_ID_PATTERN.fullmatch(raw_id) else "unknown"
    return artifact_type, artifact_id


def _validation_category(exc: Exception) -> str:
    """Map detailed decoder failures to a non-sensitive reason category."""
    message = str(exc).lower()
    if "checksum" in message:
        return "checksum"
    if "encoding" in message or "base64" in message or "gzip" in message:
        return "encoding"
    if "size" in message or "limit" in message:
        return "size"
    return "schema"


def _ownership_topology(payload: Any) -> tuple[str, str] | None:
    """Return canonical required topology components, or none when ambiguous."""
    topology = _value(payload, "source_topology", None)
    model_uuid = _value(topology, "model_uuid", "") if topology is not None else ""
    application = _value(topology, "application", "") if topology is not None else ""
    if not _valid_ownership_component(model_uuid) or not _valid_ownership_component(application):
        return None
    return model_uuid, application


def _backend_accepts(
    validator: RuleValidator | None,
    artifact_type: str,
    groups: list[dict[str, object]],
) -> bool:
    """Call an injected backend validator and fail closed without exposing details."""
    try:
        return validator is not None and validator(artifact_type, groups)
    except Exception:  # noqa: BLE001 - validator detail may contain expressions
        return False


def build_rule_state(payload: Any, *, validator: RuleValidator | None = None) -> RuleBuildResult:
    """Decode each v3 artifact independently into deterministic backend state."""
    if _value(payload, "schema_version", 1) != 3:
        return RuleBuildResult(prometheus={}, loki={}, errors=())

    prometheus: dict[str, list[dict[str, object]]] = {}
    loki: dict[str, list[dict[str, object]]] = {}
    errors: list[str] = []
    artifacts = sorted(
        _value(payload, "artifacts", []) or [],
        key=lambda artifact: _identity(artifact),
    )
    overflow_artifacts = artifacts[MAX_RULE_ARTIFACTS:]
    artifacts = artifacts[:MAX_RULE_ARTIFACTS]
    ownership_topology = _ownership_topology(payload)
    if ownership_topology is None:
        return RuleBuildResult(
            prometheus={},
            loki={},
            errors=tuple(
                f"{artifact_type}/{artifact_id}: topology" for artifact_type, artifact_id in map(_identity, artifacts)
            ),
        )
    model_uuid, application = ownership_topology

    topology_labels = _topology_labels(payload)
    decoded_bytes = 0
    for raw_artifact in artifacts:
        artifact_type, artifact_id = _identity(raw_artifact)
        try:
            artifact = ObservabilityArtifact.model_validate(raw_artifact)
        except ValidationError as exc:
            category = _validation_category(exc)
            errors.append(f"{artifact_type}/{artifact_id}: {category}")
            continue
        try:
            decoded = _decode_bounded(
                artifact,
                MAX_TOTAL_DECODED_ARTIFACT_BYTES - decoded_bytes,
            )
        except _DecodeError as exc:
            decoded_bytes += exc.decoded_bytes
            errors.append(f"{artifact_type}/{artifact_id}: {exc.category}")
            continue
        decoded_bytes += len(decoded)
        try:
            document = _load_rule_document(decoded)
            groups = _validate_rule_document(document)
            ownership = f"{model_uuid}/{application}/{artifact.artifact_type}/{artifact.artifact_id}"
            transformed = _transform_groups(
                groups,
                artifact_id=artifact.artifact_id,
                ownership=ownership,
                topology_labels=topology_labels,
            )
        except RuntimeError:
            category = "json"
        except ValueError as exc:
            category = _validation_category(exc)
        else:
            if not _backend_accepts(validator, artifact.artifact_type, transformed):
                errors.append(f"{artifact_type}/{artifact_id}: validation")
                continue
            target = prometheus if artifact.artifact_type == "prometheus_alert_rules" else loki
            target[ownership] = transformed
            continue
        errors.append(f"{artifact_type}/{artifact_id}: {category}")

    errors.extend(
        f"{artifact_type}/{artifact_id}: limit" for artifact_type, artifact_id in map(_identity, overflow_artifacts)
    )
    return RuleBuildResult(
        prometheus=dict(sorted(prometheus.items())),
        loki=dict(sorted(loki.items())),
        errors=tuple(errors),
    )


def publish_rule_groups(charm: Any, relation_name: str, groups: list[dict[str, object]]) -> None:
    """Publish complete rule-group desired state and Alloy metadata to a relation."""
    payload = json.dumps({"groups": groups}, sort_keys=True, separators=(",", ":"))
    metadata = json.dumps(
        {
            "model": charm.model.name,
            "model_uuid": charm.model.uuid,
            "application": charm.app.name,
            "unit": charm.unit.name,
        },
        sort_keys=True,
    )
    for relation in charm.model.relations.get(relation_name, []):
        relation.data[charm.app]["alert_rules"] = payload
        relation.data[charm.app]["metadata"] = metadata
