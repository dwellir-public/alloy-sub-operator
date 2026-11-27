"""Helpers for deriving principal-unit context from subordinate relations."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PrincipalContext:
    """Juju topology of the attached principal unit, plus this collector unit's own address."""

    application: str
    unit: str
    address: str = ""
    model: str = ""
    model_uuid: str = ""
    charm_name: str = ""

    @classmethod
    def from_relation(
        cls,
        relation: Any,
        *,
        model_name: str = "",
        model_uuid: str = "",
        charm_name: str = "",
        address: str = "",
    ) -> "PrincipalContext":
        """Build principal context from a subordinate attachment relation.

        address is the collector unit's own address; the principal's relayed
        address is deliberately not read from the relation.
        """
        if hasattr(relation, "remote_unit_name"):
            application = relation.remote_app_name
            unit = relation.remote_unit_name
            return cls(
                application=application,
                unit=unit,
                address=address,
                model=model_name,
                model_uuid=model_uuid,
                charm_name=charm_name,
            )

        app = getattr(relation, "app", None)
        units = sorted(getattr(relation, "units", ()), key=lambda unit: unit.name)
        if app is None or not units:
            raise ValueError("relation does not expose an attached principal unit")

        principal_unit = units[0]
        return cls(
            application=app.name,
            unit=principal_unit.name,
            address=address,
            model=model_name,
            model_uuid=model_uuid,
            charm_name=charm_name,
        )

    @classmethod
    def from_source_topology(
        cls,
        topology: Any,
        *,
        model_name: str = "",
        model_uuid: str = "",
        address: str = "",
    ) -> "PrincipalContext":
        """Build principal context from an explicit v2 source topology block."""
        return cls(
            application=topology.application,
            unit=topology.unit,
            address=address,
            model=getattr(topology, "model", "") or model_name,
            model_uuid=getattr(topology, "model_uuid", "") or model_uuid,
            charm_name=getattr(topology, "charm_name", "") or "",
        )

    def juju_labels(self, *, charm_name: str | None = None) -> dict[str, str]:
        """Render the principal context as Juju label key-value pairs."""
        labels = {
            "juju_model": self.model,
            "juju_model_uuid": self.model_uuid,
            "juju_application": self.application,
            "juju_unit": self.unit,
        }
        effective_charm_name = charm_name if charm_name is not None else self.charm_name
        if effective_charm_name:
            labels["juju_charm"] = effective_charm_name
        if self.address:
            labels["juju_unit_ip"] = self.address
        return labels
