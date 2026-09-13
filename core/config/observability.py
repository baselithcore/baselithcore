"""Observability configuration (metric cardinality and trace exemplars).

Kept apart from :class:`~core.config.app.AppConfig` — which owns the telemetry
*transport* settings (``TELEMETRY_ENABLED``, the OTLP endpoint, the sample
rate) — because these two knobs govern what the Prometheus **registry** looks
like, not whether a collector is attached:

* ``METRICS_TENANT_LABEL_ALLOWLIST`` bounds the cardinality of the ``tenant``
  label. Per-tenant cost attribution is only useful for the handful of tenants
  an operator actually tracks; letting every tenant id through would multiply
  every series by the tenant count and is the classic way to melt a Prometheus.
  The default is the empty set — nothing is broken out — so enabling the label
  can never surprise an existing deployment with a cardinality explosion.
* ``METRICS_EXEMPLARS_ENABLED`` attaches the current ``trace_id`` to histogram
  observations (OpenMetrics exemplars), which is what turns a latency spike in
  Grafana into one click through to the trace that caused it.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list

logger = logging.getLogger(__name__)


class ObservabilityConfig(BaseSettings):
    """Configuration for Prometheus metric enrichment."""

    model_config = SettingsConfigDict(
        env_prefix="OBSERVABILITY_",
        case_sensitive=False,
        extra="ignore",
    )

    # ``NoDecode``: pydantic-settings JSON-decodes complex fields before any
    # validator sees them, so a plain ``acme,globex`` would raise SettingsError
    # — which ``resolve_tenant_label`` swallows, silently collapsing every
    # tenant to "other" and doing the opposite of what was configured.
    metrics_tenant_label_allowlist: Annotated[set[str], NoDecode] = Field(
        default_factory=set,
        alias="METRICS_TENANT_LABEL_ALLOWLIST",
        description=(
            "Comma-separated tenant ids broken out individually on tenant-labelled "
            "metrics. Every other tenant collapses to 'other'. Empty (default) "
            "collapses all of them, keeping cardinality at one extra series."
        ),
    )

    metrics_exemplars_enabled: bool = Field(
        default=True,
        alias="METRICS_EXEMPLARS_ENABLED",
        description=(
            "Attach the current trace_id to histogram observations as an "
            "OpenMetrics exemplar, linking a latency bucket to its trace."
        ),
    )

    @field_validator("metrics_tenant_label_allowlist", mode="before")
    @classmethod
    def _parse_csv_lists(cls, value: Any) -> Any:
        """Accept ``acme,globex`` and a blank value, as well as a JSON array.

        Paired with ``NoDecode`` on the field — see
        :mod:`core.config._collections` for why both halves are needed.
        """
        return csv_list(value)


_observability_config: ObservabilityConfig | None = None


def get_observability_config() -> ObservabilityConfig:
    """Get or create the global observability configuration instance."""
    global _observability_config
    if _observability_config is None:
        _observability_config = ObservabilityConfig()
    return _observability_config


def reset_observability_config() -> None:
    """Drop the cached configuration (tests re-read the environment)."""
    global _observability_config
    _observability_config = None


__all__ = [
    "ObservabilityConfig",
    "get_observability_config",
    "reset_observability_config",
]
