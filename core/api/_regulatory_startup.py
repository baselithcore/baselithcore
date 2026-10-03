"""Regulatory and retention subsystems brought up by the FastAPI lifespan.

The durable audit trail, the compliance-profile check, the governance review
sweep, the consent DSR provider and the retention scheduler. Each step is
individually opt-in and no-ops when its flag is unset. Re-exported from
:mod:`core.api.startup_checks`, where the lifespan imports them.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


def start_retention_scheduler(app: Any) -> None:
    """Start the background DSR retention sweep when configured (Art. 5(1)(e)).

    Opt-in: runs only when ``PRIVACY_ENABLED`` and ``PRIVACY_RETENTION_DAYS > 0``.
    Stores the scheduler on ``app.state.retention_scheduler`` (``None`` when not
    started) so :func:`stop_retention_scheduler` can tear it down. Best-effort —
    a failure here must never block startup.
    """
    app.state.retention_scheduler = None
    try:
        from core.config.privacy import get_privacy_config

        privacy = get_privacy_config()
        if not (privacy.enabled and privacy.retention_days > 0):
            return

        from core.privacy.scheduler import RetentionScheduler

        scheduler = RetentionScheduler(privacy.retention_days * 86400)
        scheduler.start()
        app.state.retention_scheduler = scheduler
        logger.info(
            "🗓️ Retention scheduler started (horizon=%dd).", privacy.retention_days
        )
    except Exception as exc:
        logger.warning("Retention scheduler setup failed: %s", exc)


async def stop_retention_scheduler(app: Any) -> None:
    """Stop the retention scheduler if one was started. Best-effort."""
    scheduler = getattr(app.state, "retention_scheduler", None)
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:
        logger.warning("Retention scheduler shutdown failed: %s", exc)


def start_post_market_sweep(app: Any) -> None:
    """Start the daily governance review sweep when configured.

    Covers the three recurring obligations at once: the Art. 9(1) risk-file
    review, the Art. 72(1) post-market review, and the GDPR Art. 35(11) DPIA
    review plus any Art. 36(1) prior consultation still outstanding.

    Opt-in: runs only when ``COMPLIANCE_ENABLED`` and
    ``COMPLIANCE_POST_MARKET_SWEEP_ENABLED``. Best-effort — never blocks
    startup.
    """
    app.state.post_market_scheduler = None
    try:
        from core.config.compliance import get_compliance_config

        config = get_compliance_config()
        if not (config.enabled and config.post_market_sweep_enabled):
            return

        from core.compliance.review_sweep import ComplianceReviewScheduler

        scheduler = ComplianceReviewScheduler()
        scheduler.start()
        app.state.post_market_scheduler = scheduler
        logger.info(
            "📋 Compliance review sweep started (AI Act Art. 9/72, GDPR Art. 35)."
        )
    except Exception as exc:
        logger.warning("Compliance review sweep setup failed: %s", exc)


async def stop_post_market_sweep(app: Any) -> None:
    """Stop the governance review sweep if one was started. Best-effort."""
    scheduler = getattr(app.state, "post_market_scheduler", None)
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:
        logger.warning("Compliance review sweep shutdown failed: %s", exc)


def check_compliance_profile(app: Any) -> None:
    """Check the declared regulatory posture against the running configuration.

    Reports every gap (or fails startup when
    ``BASELITH_COMPLIANCE_PROFILE_STRICT`` is set) but never flips a setting on
    by itself — see :mod:`core.compliance.profile`. No-op unless
    ``BASELITH_COMPLIANCE_PROFILE`` names a profile.
    """
    app.state.compliance_profile = None
    try:
        from core.compliance.profile import enforce_profile

        app.state.compliance_profile = enforce_profile()
    except Exception as exc:
        # A strict-mode violation must stop startup; anything else is
        # best-effort and must not block it.
        if type(exc).__name__ == "ComplianceProfileError":
            raise
        logger.warning("Compliance profile check skipped: %s", exc)


def register_consent_provider() -> None:
    """Attach the Art. 7 consent log to the data-subject registry.

    Consent records *are* personal data. Without this registration a
    subject-access export (Art. 15/20) or an erasure (Art. 17) would silently
    omit them — the request would look complete while leaving a store
    untouched, which is the failure mode the DSR framework exists to prevent.

    Opt-in with the rest of the privacy subsystem (``PRIVACY_ENABLED``).
    Idempotent because the registry is keyed by provider name: a second call
    replaces the entry with the *current* consent service rather than leaving a
    stale one behind after a reconfiguration. Best-effort — a failure here must
    never block startup.
    """
    try:
        from core.config.privacy import get_privacy_config

        if not get_privacy_config().enabled:
            return

        from core.privacy import get_consent_service, register_data_provider

        register_data_provider(get_consent_service())
        logger.info("🔏 Consent log registered as a DSR provider (GDPR Art. 7).")
    except Exception as exc:
        logger.warning("Consent DSR provider registration failed: %s", exc)


def start_regulatory_subsystems(app: Any) -> None:
    """Bring up the regulatory subsystems, in the order they depend on.

    1. the durable audit trail — first, so every later startup step is already
       covered by it (AI Act Art. 12/19, NIS2 Art. 21(2)(b), GDPR Art. 5(2));
    2. the compliance-profile check, which may fail startup in strict mode;
    3. the Art. 72 post-market review sweep;
    4. the consent log, attached to the DSR registry so Art. 15/17 requests
       actually reach it.

    Each step is individually opt-in and no-ops when its flag is unset.
    """
    from core.observability.audit_setup import start_audit_trail

    start_audit_trail(app)
    check_compliance_profile(app)
    start_post_market_sweep(app)
    register_consent_provider()


async def stop_regulatory_subsystems(app: Any) -> None:
    """Tear the regulatory subsystems down, in reverse order. Best-effort."""
    from core.observability.audit_setup import stop_audit_trail

    await stop_post_market_sweep(app)
    await stop_audit_trail(app)


__all__ = [
    "check_compliance_profile",
    "register_consent_provider",
    "start_post_market_sweep",
    "start_regulatory_subsystems",
    "start_retention_scheduler",
    "stop_post_market_sweep",
    "stop_regulatory_subsystems",
    "stop_retention_scheduler",
]
