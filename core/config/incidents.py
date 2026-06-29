"""Security-incident reporting configuration.

Gates the NIS2 (EU 2022/2555) Art. 23 incident-reporting subsystem and sets the
regulatory deadline horizons. Opt-in and default-off so it adds no behaviour
until enabled. The defaults encode the NIS2 timeline: early warning within 24h,
incident notification within 72h, and a final report within one month.
"""

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class IncidentReportingConfig(BaseSettings):
    """Configuration for the NIS2 incident-reporting subsystem."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="INCIDENT_REPORTING_ENABLED")
    # NIS2 Art. 23 deadlines, expressed relative to the moment the entity
    # became aware of a significant incident ("detected_at"). Configurable for
    # stricter internal SLAs, but never relax past the regulatory maxima.
    early_warning_hours: int = Field(
        default=24, alias="INCIDENT_EARLY_WARNING_HOURS", ge=1
    )
    notification_hours: int = Field(
        default=72, alias="INCIDENT_NOTIFICATION_HOURS", ge=1
    )
    # Final report is due within one month of the incident notification.
    final_report_days: int = Field(default=30, alias="INCIDENT_FINAL_REPORT_DAYS", ge=1)


_incident_config: Optional[IncidentReportingConfig] = None


def get_incident_config() -> IncidentReportingConfig:
    """Get or create the global incident-reporting configuration instance."""
    global _incident_config
    if _incident_config is None:
        _incident_config = IncidentReportingConfig()
    return _incident_config
