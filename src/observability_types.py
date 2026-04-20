"""Compatibility re-exports for the local machine-observability contract."""

try:
    from .machine_observability import (
        MACHINE_OBSERVABILITY_SCHEMA_VERSION,
        LogFileSource,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )
except ImportError:
    from machine_observability import (
        MACHINE_OBSERVABILITY_SCHEMA_VERSION,
        LogFileSource,
        MachineObservabilityPayload,
        MetricsEndpoint,
    )
