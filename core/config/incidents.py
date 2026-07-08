"""Security-incident reporting configuration.

Gates the regulatory incident-reporting subsystems and sets the deadline
horizons. Opt-in and default-off so it adds no behaviour until enabled. The
NIS2 (EU 2022/2555) Art. 23 defaults encode early warning within 24h, incident
notification within 72h, and a final report within one month. The DORA
(EU 2022/2554) Art. 19 defaults encode the major-incident clock: initial
notification within 4h of classification (24h cap from awareness), intermediate
report within 72h, and a final report within one month.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class IncidentReportingConfig(BaseSettings):
    """Configuration for the regulatory incident-reporting subsystems."""

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

    # DORA (EU 2022/2554) Art. 19 major-incident reporting clock. The initial
    # notification is due within 4h of classifying the incident as major and in
    # any case no later than 24h from awareness; the intermediate report within
    # 72h of the initial notification; the final report within one month of the
    # intermediate report. Configurable for stricter internal SLAs only.
    dora_enabled: bool = Field(default=False, alias="DORA_INCIDENT_REPORTING_ENABLED")
    dora_initial_notification_hours: int = Field(
        default=4, alias="DORA_INITIAL_NOTIFICATION_HOURS", ge=1
    )
    dora_awareness_cap_hours: int = Field(
        default=24, alias="DORA_AWARENESS_CAP_HOURS", ge=1
    )
    dora_intermediate_report_hours: int = Field(
        default=72, alias="DORA_INTERMEDIATE_REPORT_HOURS", ge=1
    )
    dora_final_report_days: int = Field(
        default=30, alias="DORA_FINAL_REPORT_DAYS", ge=1
    )

    # Opt-in durable persistence. When a filesystem path is set, the singleton
    # incident service swaps its non-durable in-memory store for a SQLite store
    # at that path (records survive restarts). Unset (the default) keeps the
    # in-memory store, so behaviour is byte-for-byte unchanged unless a path is
    # provided. NIS2 (INCIDENT_DB_PATH) and DORA (DORA_DB_PATH) persist to
    # independent files.
    incident_db_path: str | None = Field(default=None, alias="INCIDENT_DB_PATH")
    dora_db_path: str | None = Field(default=None, alias="DORA_DB_PATH")


_incident_config: IncidentReportingConfig | None = None


def get_incident_config() -> IncidentReportingConfig:
    """Get or create the global incident-reporting configuration instance."""
    global _incident_config
    if _incident_config is None:
        _incident_config = IncidentReportingConfig()
    return _incident_config
