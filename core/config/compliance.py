"""AI-governance configuration (EU AI Act, GDPR accountability artefacts).

Gates the AI system registry and the compliance document registries (Annex IV
technical documentation, Art. 27 FRIA, GDPR Art. 30 ROPA). Opt-in and
default-off: with ``COMPLIANCE_ENABLED`` unset nothing is registered and no
storage is touched.

Every store defaults to in-memory. That is the right default for a framework
(tests, single-process development) and the wrong one for production, where
these records must outlive the process by years — set the DB paths.
"""

from __future__ import annotations

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ComplianceConfig(BaseSettings):
    """Configuration for the AI-governance subsystem."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="COMPLIANCE_ENABLED")

    #: Refuse to register a system that declares an Art. 5 prohibited practice,
    #: instead of recording it as PROHIBITED and continuing. Off by default so
    #: an operator can inventory a system *before* deciding to retire it.
    block_prohibited_practices: bool = Field(
        default=False, alias="COMPLIANCE_BLOCK_PROHIBITED_PRACTICES"
    )

    #: Durable stores. Unset keeps the non-durable in-memory reference stores,
    #: so behaviour is unchanged unless a path is provided.
    registry_db_path: str | None = Field(
        default=None, alias="COMPLIANCE_REGISTRY_DB_PATH"
    )
    documents_db_path: str | None = Field(
        default=None, alias="COMPLIANCE_DOCUMENTS_DB_PATH"
    )
    fria_db_path: str | None = Field(default=None, alias="COMPLIANCE_FRIA_DB_PATH")
    ropa_db_path: str | None = Field(default=None, alias="COMPLIANCE_ROPA_DB_PATH")
    post_market_db_path: str | None = Field(
        default=None, alias="COMPLIANCE_POST_MARKET_DB_PATH"
    )
    risk_db_path: str | None = Field(default=None, alias="COMPLIANCE_RISK_DB_PATH")
    instructions_db_path: str | None = Field(
        default=None, alias="COMPLIANCE_INSTRUCTIONS_DB_PATH"
    )
    dpia_db_path: str | None = Field(default=None, alias="COMPLIANCE_DPIA_DB_PATH")

    #: Run the daily Art. 72 review sweep, which surfaces plans past their
    #: review cadence. Off by default like every other background loop.
    post_market_sweep_enabled: bool = Field(
        default=False, alias="COMPLIANCE_POST_MARKET_SWEEP_ENABLED"
    )

    #: Identity of the operator, used to pre-fill generated documentation.
    provider_name: str | None = Field(default=None, alias="COMPLIANCE_PROVIDER_NAME")
    provider_contact: str | None = Field(
        default=None, alias="COMPLIANCE_PROVIDER_CONTACT"
    )
    dpo_contact: str | None = Field(default=None, alias="COMPLIANCE_DPO_CONTACT")


_compliance_config: ComplianceConfig | None = None


def get_compliance_config() -> ComplianceConfig:
    """Get or create the global compliance configuration instance."""
    global _compliance_config
    if _compliance_config is None:
        _compliance_config = ComplianceConfig()
    return _compliance_config


def reset_compliance_config() -> None:
    """Drop the cached configuration (tests re-read the environment)."""
    global _compliance_config
    _compliance_config = None


__all__ = [
    "ComplianceConfig",
    "get_compliance_config",
    "reset_compliance_config",
]
