"""The ``DB Migrations`` check of ``baselith doctor``.

Two questions, in order: can the packaged Alembic migrations be located and
loaded at all, and do they run at startup or explicitly. The first one fails
the check: an installation that cannot find its migration scripts cannot
create or upgrade its schema, whatever the startup mode says.
"""

from __future__ import annotations

from core.cli.commands.doctor_checks import CheckResult, env_value

_NAME = "DB Migrations"


def check_migrations_mode() -> CheckResult:
    """Verify the migrations are locatable, then explain how they run.

    Returns:
        A failing result when the scripts are missing or a revision module
        cannot be loaded; otherwise a passing one naming the head revision
        and the ``DB_MIGRATIONS_ON_STARTUP`` mode.
    """
    # Imported here: core.db pulls in the driver stack, and the doctor
    # module is loaded by every `baselith` invocation.
    from core.db import migration_config

    try:
        heads = migration_config.migration_heads()
    except Exception as exc:  # any load failure means no upgrade can run
        return CheckResult(
            _NAME,
            False,
            "Migration scripts cannot be loaded",
            f"{type(exc).__name__}: {str(exc).rstrip('.')}. Reinstall "
            "baselith-core; the wheel must ship core/db/migrations.",
        )
    head = ", ".join(heads) or "none"
    value = (env_value("DB_MIGRATIONS_ON_STARTUP", "true") or "true").lower()
    if value in {"1", "true", "yes", "on"}:
        return CheckResult(
            _NAME,
            True,
            "Run during application startup",
            f"Head {head}. For predictable startup, prefer false and run: "
            "baselith db migrate",
        )
    return CheckResult(
        _NAME,
        True,
        "Startup migrations disabled",
        f"Head {head}. Run manually after DB changes: baselith db migrate",
    )


__all__ = ["check_migrations_mode"]
