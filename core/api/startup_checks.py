"""
Startup checks and warmups for the FastAPI lifespan.

Infrastructure health pings (PostgreSQL, Redis, Alembic migration state) and
eager construction of the auth/security singletons. Extracted from
``core/api/lifespan.py`` to keep modules under the 500-line cap.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from core.config import get_storage_config
from core.config.environment import is_production_env
from core.observability.logging import get_logger

logger = get_logger(__name__)

_storage_config = get_storage_config()

POSTGRES_ENABLED = getattr(_storage_config, "postgres_enabled", False)
CACHE_REDIS_URL = getattr(_storage_config, "cache_redis_url", "")


def warm_auth_singletons() -> None:
    """Eagerly build the auth/security singletons at boot.

    SecurityManager (rate limiter + Redis script registration) and
    AuthManager (JWT handler + API-key validator) are lazy singletons that
    would otherwise be constructed inside the first authenticated request,
    adding a one-off latency spike to it. Best-effort: a failure here must
    not block startup (e.g. minimal test apps without auth config) — the
    lazy path remains as fallback.
    """
    try:
        from core.auth.manager import get_auth_manager
        from core.middleware.security import get_security_manager

        get_security_manager()
        get_auth_manager()
        logger.info("🔐 Auth/security singletons warmed up.")
    except Exception as exc:
        logger.warning(
            "🔐 Auth/security warmup skipped (%s: %s); will initialize lazily.",
            type(exc).__name__,
            exc,
        )
    _warn_unbound_jwt_claims()


def _warn_unbound_jwt_claims() -> None:
    """Warn in production when JWTs carry no ``aud``/``iss`` binding.

    Without an issuer/audience claim, any two deployments that share a
    ``SECRET_KEY`` (e.g. a staging value copy-pasted to prod) mint tokens the
    other happily accepts. The verification machinery is already in place —
    this only nudges operators to configure it. Warning-only: never blocks
    startup.
    """
    try:
        from core.config import get_security_config

        config = get_security_config()
        if not is_production_env():
            return
        missing = [
            name
            for name, value in (
                ("JWT_ISSUER", getattr(config, "jwt_issuer", None)),
                ("JWT_AUDIENCE", getattr(config, "jwt_audience", None)),
            )
            if not value
        ]
        if missing:
            logger.warning(
                "🔐 %s unset in production: tokens are not bound to this "
                "deployment, so any service sharing this SECRET_KEY accepts "
                "them. Set the missing value(s) (and JWT_STRICT_VALIDATION=true "
                "once all live tokens carry the claims).",
                " and ".join(missing),
            )
    except Exception:  # pragma: no cover - advisory only
        logger.debug("JWT claim-binding check skipped", exc_info=True)


async def run_startup_health_checks() -> None:
    """
    Ping critical infrastructure services at startup.

    Logs a WARNING (or ERROR in production) when a required service is
    unreachable.  Does not raise — the framework uses lazy initialization
    and individual operations will surface connection errors at call time.
    In production a failed check is escalated to
    ERROR level so alerting systems can act on it.
    """
    is_production = is_production_env()
    log_fn = logger.error if is_production else logger.warning

    if POSTGRES_ENABLED:
        try:
            from core.db.connection import get_async_connection

            async with get_async_connection() as conn:
                await conn.execute("SELECT 1")
            logger.info("✅ Startup health check: PostgreSQL OK")
        except Exception as exc:
            log_fn(
                "Startup health check FAILED — PostgreSQL unreachable: %s",
                type(exc).__name__,
            )

    if CACHE_REDIS_URL:
        try:
            _redis_check = redis.from_url(CACHE_REDIS_URL)
            await _redis_check.ping()
            await _redis_check.close()
            logger.info("✅ Startup health check: Redis OK")
        except Exception as exc:
            log_fn(
                "Startup health check FAILED — Redis unreachable: %s",
                type(exc).__name__,
            )

    if is_production and POSTGRES_ENABLED:
        try:
            import asyncio as _asyncio

            from alembic.config import Config as AlembicConfig
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            def _check_migrations() -> tuple[str, str]:
                from sqlalchemy import create_engine

                alembic_cfg = AlembicConfig("alembic.ini")
                script = ScriptDirectory.from_config(alembic_cfg)
                head_rev: str = script.get_current_head() or "unknown"

                db_url = (
                    alembic_cfg.get_main_option("sqlalchemy.url")
                    or get_storage_config().conninfo
                )
                # Force the sync psycopg (v3) driver: only psycopg3 is installed,
                # so a bare ``postgresql://`` (defaults to psycopg2) or an async
                # driver (``+psycopg_async`` / ``+asyncpg``) would fail to import
                # under this sync ``create_engine``. Normalize the scheme.
                for _scheme in (
                    "postgresql+psycopg_async://",
                    "postgresql+asyncpg://",
                    "postgresql://",
                ):
                    if db_url.startswith(_scheme):
                        db_url = "postgresql+psycopg://" + db_url[len(_scheme) :]
                        break
                engine = create_engine(db_url)
                with engine.connect() as conn:
                    ctx = MigrationContext.configure(conn)
                    current_rev: str = ctx.get_current_revision() or "none"
                engine.dispose()
                return current_rev, head_rev

            current, head = await _asyncio.to_thread(_check_migrations)
            if current != head:
                logger.error(
                    "Database migrations are NOT up to date — "
                    "current: %s, head: %s. Run `alembic upgrade head` before deploying.",
                    current,
                    head,
                )
            else:
                logger.info(
                    "✅ Startup health check: DB migrations up to date (%s)", current
                )
        except Exception as exc:
            logger.warning("Could not verify migration status: %s", type(exc).__name__)


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
    """Start the daily Art. 72 post-market review sweep when configured.

    Opt-in: runs only when ``COMPLIANCE_ENABLED`` and
    ``COMPLIANCE_POST_MARKET_SWEEP_ENABLED``. Art. 72(1) requires the monitoring
    system to stay *active*; the sweep is what makes an unreviewed plan visible
    instead of silently ageing. Best-effort — never blocks startup.
    """
    app.state.post_market_scheduler = None
    try:
        from core.config.compliance import get_compliance_config

        config = get_compliance_config()
        if not (config.enabled and config.post_market_sweep_enabled):
            return

        from core.compliance.post_market_service import PostMarketReviewScheduler

        scheduler = PostMarketReviewScheduler()
        scheduler.start()
        app.state.post_market_scheduler = scheduler
        logger.info("📋 Post-market review sweep started (AI Act Art. 72).")
    except Exception as exc:
        logger.warning("Post-market sweep setup failed: %s", exc)


async def stop_post_market_sweep(app: Any) -> None:
    """Stop the post-market review sweep if one was started. Best-effort."""
    scheduler = getattr(app.state, "post_market_scheduler", None)
    if scheduler is None:
        return
    try:
        await scheduler.stop()
    except Exception as exc:
        logger.warning("Post-market sweep shutdown failed: %s", exc)


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


def start_regulatory_subsystems(app: Any) -> None:
    """Bring up the regulatory subsystems, in the order they depend on.

    1. the durable audit trail — first, so every later startup step is already
       covered by it (AI Act Art. 12/19, NIS2 Art. 21(2)(b), GDPR Art. 5(2));
    2. the compliance-profile check, which may fail startup in strict mode;
    3. the Art. 72 post-market review sweep.

    Each step is individually opt-in and no-ops when its flag is unset.
    """
    from core.observability.audit_setup import start_audit_trail

    start_audit_trail(app)
    check_compliance_profile(app)
    start_post_market_sweep(app)


async def stop_regulatory_subsystems(app: Any) -> None:
    """Tear the regulatory subsystems down, in reverse order. Best-effort."""
    from core.observability.audit_setup import stop_audit_trail

    await stop_post_market_sweep(app)
    await stop_audit_trail(app)


__all__ = [
    "check_compliance_profile",
    "run_startup_health_checks",
    "start_post_market_sweep",
    "start_regulatory_subsystems",
    "stop_post_market_sweep",
    "stop_regulatory_subsystems",
    "start_retention_scheduler",
    "stop_retention_scheduler",
    "warm_auth_singletons",
]
