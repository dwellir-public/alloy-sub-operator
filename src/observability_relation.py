"""Compatibility re-export for machine-observability relation loading."""

try:
    from .machine_observability import load_machine_observability_payload
except ImportError:
    from machine_observability import load_machine_observability_payload
