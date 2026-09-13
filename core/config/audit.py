"""Audit-trail configuration.

Gates the durable, tamper-evident audit trail. The framework has always emitted
``AUDIT | …`` log lines; this configuration turns those into *retained,
queryable records* — the evidentiary artefact several regimes require:

* **EU AI Act Art. 12** — automatic recording of events over the system's
  lifetime, and **Art. 19 / Art. 26(6)** — provider and deployer must keep the
  automatically generated logs for **at least six months** (unless another
  Union or national law sets a longer period). Hence the 180-day default.
* **NIS2 (EU 2022/2555) Art. 21(2)(b)** — incident handling needs an evidence
  trail behind each filing.
* **GDPR Art. 5(2)** — accountability: being able to *demonstrate* compliance.

Opt-in and additive: with ``AUDIT_ENABLED`` unset nothing changes — the legacy
logger sink stays the only sink and no file or database is touched.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Statutory floor for audit-log retention (AI Act Art. 19 / Art. 26(6)):
#: six months. Configuring a shorter horizon is allowed for non-AI-Act
#: deployments but is warned about at startup.
MIN_RETENTION_DAYS = 180


class AuditConfig(BaseSettings):
    """Configuration for the durable audit trail."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="AUDIT_ENABLED")

    # Sinks. The logger sink mirrors the historical behaviour and stays on by
    # default so enabling the subsystem never *removes* an existing signal.
    log_sink_enabled: bool = Field(default=True, alias="AUDIT_LOG_SINK_ENABLED")
    file_path: str | None = Field(default=None, alias="AUDIT_FILE_PATH")
    db_path: str | None = Field(default=None, alias="AUDIT_DB_PATH")

    # Tamper evidence: each record is hash-chained to its predecessor, so a
    # deletion or edit inside the window is detectable via ``verify_chain()``.
    hash_chain: bool = Field(default=True, alias="AUDIT_HASH_CHAIN")

    # Keyed chain. An *unkeyed* SHA-256 chain detects an edit only from an
    # attacker who cannot also recompute the chain — i.e. almost none of them,
    # since the algorithm is public and the data is right there in the file.
    # HMAC-SHA256 with a key the database does not contain raises the bar to
    # "needs the key as well as write access", which is the property an
    # evidentiary trail is supposed to have.
    #
    # Unset keeps the historical plain-hash behaviour (with a startup warning)
    # so enabling the audit trail never fails on an existing deployment.
    chain_hmac_key: SecretStr | None = Field(
        default=None,
        alias="AUDIT_CHAIN_HMAC_KEY",
        description=(
            "Secret key for the HMAC-SHA256 audit hash chain. Unset falls back "
            "to a plain SHA-256 chain (detects accidents, not attackers)."
        ),
    )
    chain_require_key: bool = Field(
        default=False,
        alias="AUDIT_CHAIN_REQUIRE_KEY",
        description=(
            "Refuse to start the durable audit sink without "
            "AUDIT_CHAIN_HMAC_KEY. Turn this on where the audit trail is "
            "evidence rather than telemetry."
        ),
    )

    # Retention. 0 disables purging entirely (keep forever); any positive value
    # is enforced by a daily sweep over the durable sink.
    retention_days: int = Field(default=180, alias="AUDIT_RETENTION_DAYS", ge=0)

    # Truncation guard for free-form event details, so an audit record can
    # never grow unbounded from a caller-supplied payload.
    max_detail_chars: int = Field(default=2000, alias="AUDIT_MAX_DETAIL_CHARS", ge=64)

    @field_validator("chain_hmac_key", mode="before")
    @classmethod
    def _blank_key_is_no_key(cls, value: Any) -> Any:
        """Normalise a blank key to ``None``.

        ``AUDIT_CHAIN_HMAC_KEY=`` (or whitespace) is an operator who has not
        set a key. Without this the model validator saw "a key is present" and
        let the configuration pass, while the sink's own
        :func:`~core.observability.audit_digest.coerce_chain_key` refused it —
        two guards disagreeing about the same value, which is how a deployment
        ends up believing it has a keyed chain that it does not.
        """
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _require_chain_key(self) -> AuditConfig:
        """Fail closed when a keyed chain is mandated but no key is set.

        Only bites when the trail is both enabled and hash-chained: turning the
        requirement on in a template must not break a deployment that does not
        run the audit subsystem at all.
        """
        if (
            self.enabled
            and self.hash_chain
            and self.chain_require_key
            and self.chain_hmac_key is None
        ):
            raise ValueError(
                "AUDIT_CHAIN_REQUIRE_KEY is set but AUDIT_CHAIN_HMAC_KEY is "
                "empty: the audit hash chain would fall back to an unkeyed "
                "digest, which is not tamper-evident against an attacker with "
                "write access."
            )
        return self

    @model_validator(mode="after")
    def _warn_on_short_retention(self) -> AuditConfig:
        """Warn when the horizon falls below the AI Act six-month floor."""
        if self.enabled and 0 < self.retention_days < MIN_RETENTION_DAYS:
            logger.warning(
                "AUDIT_RETENTION_DAYS=%d is below the EU AI Act Art. 19/26(6) "
                "six-month floor (%d days); logs will be purged before the "
                "statutory minimum.",
                self.retention_days,
                MIN_RETENTION_DAYS,
            )
        return self


_audit_config: AuditConfig | None = None


def get_audit_config() -> AuditConfig:
    """Get or create the global audit configuration instance."""
    global _audit_config
    if _audit_config is None:
        _audit_config = AuditConfig()
    return _audit_config


def reset_audit_config() -> None:
    """Drop the cached configuration (tests re-read the environment)."""
    global _audit_config
    _audit_config = None


__all__ = [
    "MIN_RETENTION_DAYS",
    "AuditConfig",
    "get_audit_config",
    "reset_audit_config",
]
