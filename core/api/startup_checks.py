"""
Startup checks and warmups for the FastAPI lifespan.

Infrastructure health pings (PostgreSQL, Redis, Alembic migration state) and
eager construction of the auth/security singletons. Extracted from
``core/api/lifespan.py`` to keep modules under the 500-line cap.
"""

from __future__ import annotations

import redis.asyncio as redis

from core.api._regulatory_startup import (
    check_compliance_profile,
    register_consent_provider,
    start_post_market_sweep,
    start_regulatory_subsystems,
    start_retention_scheduler,
    stop_post_market_sweep,
    stop_regulatory_subsystems,
    stop_retention_scheduler,
)
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
    _warn_missing_trusted_hosts()


def _warn_missing_trusted_hosts() -> None:
    """Refuse production startup when the ``Host`` header is not validated.

    ``TrustedHostMiddleware`` is mounted only when ``TRUSTED_HOSTS`` is
    non-empty, and the default is an empty list — so out of the box nothing
    validates the ``Host``/``X-Forwarded-Host`` header. An attacker who can
    reach the app then chooses the host it believes it is served from, which
    poisons absolute URLs built from the request (password-reset and
    verification links, cached responses keyed by host).

    Fail-closed in production, like the JWT trust perimeter: startup aborts
    with remediation instructions rather than serving behind a perimeter the
    framework cannot infer (the correct hostnames are deployment knowledge).
    A deployment that consciously accepts the risk — e.g. a proxy that already
    rewrites ``Host`` — opts out explicitly with
    ``BASELITH_ALLOW_UNVALIDATED_HOST=true``, an auditable escape hatch that
    downgrades the check to an ERROR log. Outside production the check is
    silent.
    """
    import os

    allow_unvalidated = os.getenv("BASELITH_ALLOW_UNVALIDATED_HOST", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    try:
        from core.config import get_security_config

        if not is_production_env():
            return
        trusted = getattr(get_security_config(), "trusted_hosts", None)
        if trusted and set(trusted) <= _LOOPBACK_HOSTS:
            # The .env.example value, carried into production unedited: every
            # request addressed to the public hostname would answer 400.
            logger.warning(
                "🛡️ TRUSTED_HOSTS lists only loopback names (%s) in production: "
                "requests addressed to this deployment's public hostname get "
                "400 Invalid host header. Set it to the hostnames your proxy "
                "serves.",
                ", ".join(sorted(trusted)),
            )
        if trusted:
            return
        message = (
            "TRUSTED_HOSTS is empty in production: the Host header is not "
            "validated, so a spoofed Host can poison absolute URLs (reset / "
            "verification links) and host-keyed caches. Set TRUSTED_HOSTS to "
            'this deployment\'s hostnames (e.g. ["api.example.com"]), or set '
            "BASELITH_ALLOW_UNVALIDATED_HOST=true to accept the risk explicitly."
        )
        if not allow_unvalidated:
            raise UnvalidatedHostConfigError(f"🛡️ {message}")
        logger.error("🛡️ %s", message)
    except UnvalidatedHostConfigError:
        raise
    except Exception:  # pragma: no cover - advisory only
        logger.debug("Trusted-host check skipped", exc_info=True)


#: Hostnames the shipped ``.env.example`` allows so a local copy works as-is.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class UnvalidatedHostConfigError(RuntimeError):
    """Production refused to start without a validated Host perimeter."""


async def warm_memory_embedder(resources: set[str]) -> None:
    """Warm the sentence-transformer embedder off the event loop.

    When a memory tier is in play, the embedder's lazy first-load is a
    multi-second synchronous model load that would otherwise stall *every*
    in-flight request the first time a recall/RAG path touches it after
    boot. Best-effort: a failure keeps the lazy path as fallback.
    """
    if not {"memory", "hierarchical_memory"} & resources:
        return
    try:
        import asyncio

        from core.nlp.models import get_embedder

        await asyncio.to_thread(get_embedder)
        logger.info("🧠 Embedder warmed at startup")
    except Exception as exc:
        logger.warning("Embedder warmup skipped: %s", exc)


def _warn_unbound_jwt_claims() -> None:
    """Refuse production startup when JWTs carry no ``aud``/``iss`` binding.

    Without an issuer/audience claim, any two deployments that share a
    ``SECRET_KEY`` (e.g. a staging value copy-pasted to prod) mint tokens the
    other happily accepts — a cross-environment replay that the verification
    machinery already knows how to prevent. In production with auth enabled
    this is now fail-closed: startup aborts with remediation instructions
    rather than serving with an unbound token perimeter. Deployments that
    consciously accept the risk (single environment, unique secret) can opt
    out explicitly with ``BASELITH_ALLOW_UNBOUND_JWT=true`` — an auditable
    escape hatch instead of a silent default. Outside production, or with
    auth disabled, the check only warns.
    """
    import os

    allow_unbound = os.getenv("BASELITH_ALLOW_UNBOUND_JWT", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
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
        if not missing:
            return
        message = (
            f"{' and '.join(missing)} unset in production: tokens are not "
            "bound to this deployment, so any service sharing this SECRET_KEY "
            "accepts them. Set APP_BASE_URL (or JWT_ISSUER + JWT_AUDIENCE "
            "explicitly, and JWT_STRICT_VALIDATION=true once all live tokens "
            "carry the claims), or set BASELITH_ALLOW_UNBOUND_JWT=true to "
            "accept the risk explicitly."
        )
        auth_required = bool(getattr(config, "auth_required", False))
        if auth_required and not allow_unbound:
            raise UnboundJWTConfigError(f"🔐 {message}")
        logger.warning("🔐 %s", message)
    except UnboundJWTConfigError:
        raise
    except Exception:  # pragma: no cover - advisory only
        logger.debug("JWT claim-binding check skipped", exc_info=True)


class UnboundJWTConfigError(RuntimeError):
    """Production refused to start with an unbound JWT trust perimeter."""


#: Upper bound on each startup database wait (health probe, RLS posture read
#: after a failed probe). Without it every step waited a full DB_POOL_TIMEOUT
#: in series, so a boot with PostgreSQL down took minutes, not seconds.
STARTUP_DB_PROBE_TIMEOUT_S = 10.0


async def warm_db_pool() -> bool:
    """Open the async DB pool during startup instead of on the first request.

    Without this the first caller after a deploy pays TCP+TLS+auth for
    ``min_size`` connections inline. Fail-soft: on failure the lazy open on
    first use still covers requests. No-op when PostgreSQL is disabled.

    Returns:
        Whether the pool reports itself warm. ``True`` does not prove the
        database answers (a pool opened lazily earlier counts as warm), which
        is why :func:`run_startup_health_checks` probes before trusting it.
    """
    from core.db.connection import warm_async_pool

    if await warm_async_pool():
        logger.info("🔌 DB pool warmed (min_size connections ready).")
        return True
    return False


async def _probe_postgres() -> bool:
    """Warm, ping and size-check PostgreSQL; ``True`` when it answered.

    Startup has no request and therefore no tenant. With ``DB_RLS_ENABLED``
    the session binding refuses to invent one, so every checkout here declares
    itself as system work — otherwise a healthy database is reported
    unreachable, and the connection-budget read was silently refused. Each
    wait is bounded by :data:`STARTUP_DB_PROBE_TIMEOUT_S`; the budget read only
    runs once the ping proved the database answers.
    """
    import asyncio

    from core.db import reachability
    from core.db.connection import get_async_connection, system_tenant_scope
    from core.db.pool_budget import check_connection_budget

    # The boot probe already saw the database down: one cheap direct re-probe
    # instead of a pool warm-up plus a checkout that each wait out a timeout.
    if reachability.postgres_known_unreachable():
        if not await reachability.probe_postgres():
            raise ConnectionError("PostgreSQL unreachable (startup probe)")

    # Warm the pool first: the health-check checkout then reuses a warmed
    # connection, and the first real request after a deploy doesn't pay
    # TCP+TLS+auth for min_size connections inline.
    await warm_db_pool()
    with system_tenant_scope():
        async with asyncio.timeout(STARTUP_DB_PROBE_TIMEOUT_S):
            async with get_async_connection() as conn:
                await conn.execute("SELECT 1")
        logger.info("✅ Startup health check: PostgreSQL OK")
        # Pool size x workers vs the server's max_connections; warns only.
        await check_connection_budget()
    return True


async def _enforce_rls_posture(postgres_reachable: bool) -> None:
    """Run the RLS posture check; bounded when the database did not answer.

    A posture that defeats RLS still raises (it is a misconfiguration, not an
    outage). When the probe already failed, the read is capped so an
    unreachable database costs one more bounded wait, not a DB_POOL_TIMEOUT.
    """
    import asyncio

    from core.db.rls_posture import enforce_rls_posture

    if postgres_reachable:
        await enforce_rls_posture()
        return
    try:
        async with asyncio.timeout(STARTUP_DB_PROBE_TIMEOUT_S):
            await enforce_rls_posture()
    except TimeoutError:
        logger.warning(
            "Row-level-security posture check skipped (database unreachable); "
            "tenant isolation at the database has not been verified."
        )


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

    postgres_reachable = False
    if POSTGRES_ENABLED:
        try:
            postgres_reachable = await _probe_postgres()
        except Exception as exc:
            log_fn(
                "Startup health check FAILED — PostgreSQL unreachable: %s",
                type(exc).__name__,
            )

        # Deliberately outside the try above: a role that silently bypasses
        # row-level security is a security misconfiguration, not an
        # infrastructure blip, and must not be swallowed by the handler that
        # reports an unreachable database.
        await _enforce_rls_posture(postgres_reachable)

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

    if is_production and POSTGRES_ENABLED and not postgres_reachable:
        logger.warning("Could not verify migration status: PostgreSQL unreachable")
    elif is_production and POSTGRES_ENABLED:
        try:
            import asyncio as _asyncio

            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            from core.db.migration_config import build_alembic_config

            def _check_migrations() -> tuple[str, str]:
                from sqlalchemy import create_engine

                alembic_cfg = build_alembic_config()
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

    # Which provider will actually serve, and can it. Unlike the probes above
    # this one can *stop* a rollout (strict mode), because the failure it
    # catches has no runtime symptom: a deployment that never named a provider
    # inherits a local default and serves from whatever is installed on the
    # host, successfully, at a cost that appears in no ledger.
    from core.services.llm.preflight import run_llm_preflight

    await run_llm_preflight()


__all__ = [
    "check_compliance_profile",
    "register_consent_provider",
    "run_startup_health_checks",
    "start_post_market_sweep",
    "start_regulatory_subsystems",
    "stop_post_market_sweep",
    "stop_regulatory_subsystems",
    "start_retention_scheduler",
    "stop_retention_scheduler",
    "warm_auth_singletons",
    "warm_db_pool",
    "warm_memory_embedder",
]
