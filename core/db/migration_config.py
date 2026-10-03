"""Locate the packaged Alembic migrations and build their configuration.

The migration scripts ship inside the package (``core/db/migrations``), so a
wheel-installed deployment can run them from any working directory. Every code
path that runs or inspects migrations — the startup upgrade, the startup
revision check, ``baselith db migrate`` and ``baselith doctor`` — goes through
:func:`build_alembic_config` instead of reading ``alembic.ini`` from the cwd.

The repository-root ``alembic.ini`` remains for developers running the
``alembic`` CLI directly; it points at the same directory.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alembic.config import Config

MIGRATIONS_DIRNAME = "migrations"
"""Directory, relative to the ``core.db`` package, holding ``env.py``."""


class MigrationsNotFoundError(RuntimeError):
    """The packaged migration scripts are missing from the installation."""


def _package_root() -> Path:
    """Return the filesystem directory of the ``core.db`` package."""
    return Path(str(files(__package__ or "core.db"))).resolve()


def migrations_dir() -> Path:
    """Return the directory holding the packaged Alembic environment.

    Returns:
        Absolute path of the directory containing ``env.py`` and ``versions/``.

    Raises:
        MigrationsNotFoundError: The installation does not carry the scripts —
            typically a wheel built without their package-data entry.
    """
    location = _package_root() / MIGRATIONS_DIRNAME
    if not (location / "env.py").is_file() or not (location / "versions").is_dir():
        raise MigrationsNotFoundError(
            f"Alembic migrations not found at {location}: the installed "
            "baselith-core package is missing core/db/migrations "
            "(env.py and versions/)."
        )
    return location


def build_alembic_config() -> Config:
    """Build an Alembic ``Config`` bound to the packaged migrations.

    No ``alembic.ini`` is read, so the result is independent of the working
    directory. The database URL comes from the storage settings inside
    ``env.py``, as it always has.

    Returns:
        A configuration whose ``script_location`` is :func:`migrations_dir`.

    Raises:
        MigrationsNotFoundError: The packaged scripts are missing.
    """
    from alembic.config import Config

    config = Config()
    # ConfigParser interpolates ``%``; an install path containing one must
    # reach Alembic verbatim.
    config.set_main_option("script_location", str(migrations_dir()).replace("%", "%%"))
    return config


def migration_heads() -> tuple[str, ...]:
    """Return the head revision(s) of the packaged migration graph.

    Loading every revision module is the cheapest proof that the scripts are
    not only present but importable.

    Returns:
        The head revision identifiers.

    Raises:
        MigrationsNotFoundError: The packaged scripts are missing.
    """
    from alembic.script import ScriptDirectory

    return tuple(ScriptDirectory.from_config(build_alembic_config()).get_heads())


__all__ = [
    "MIGRATIONS_DIRNAME",
    "MigrationsNotFoundError",
    "build_alembic_config",
    "migration_heads",
    "migrations_dir",
]
