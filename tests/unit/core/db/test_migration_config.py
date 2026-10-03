"""The Alembic migrations are located through the package, never the cwd.

A wheel-installed deployment has no ``alembic.ini`` and no ``migrations/``
directory in its working directory; before the scripts moved into
``core/db/migrations`` every path that ran or inspected them resolved both from
``os.getcwd()`` and failed (or, in ``baselith doctor``, passed while unable to
find them).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from core.db import migration_config
from core.db.migration_config import (
    MigrationsNotFoundError,
    build_alembic_config,
    migration_heads,
    migrations_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_migrations_dir_is_inside_the_package() -> None:
    location = migrations_dir()

    assert location == Path(migration_config.__file__).resolve().parent / "migrations"
    assert (location / "env.py").is_file()
    assert (location / "versions").is_dir()


def test_config_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    config = build_alembic_config()

    assert config.get_main_option("script_location") == str(migrations_dir())
    assert ScriptDirectory.from_config(config).get_current_head()


def test_migration_heads_resolve_to_a_single_head() -> None:
    heads = migration_heads()

    assert len(heads) == 1


def test_missing_migrations_raise_a_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migration_config, "_package_root", lambda: tmp_path)

    with pytest.raises(MigrationsNotFoundError, match="migrations"):
        migrations_dir()


def test_percent_in_the_install_path_is_not_interpolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "100%site"
    (root / "migrations" / "versions").mkdir(parents=True)
    (root / "migrations" / "env.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(migration_config, "_package_root", lambda: root)

    config = build_alembic_config()

    assert config.get_main_option("script_location") == str(root / "migrations")


def test_repository_alembic_ini_points_at_the_packaged_scripts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from alembic.config import Config

    monkeypatch.chdir(tmp_path)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    script = ScriptDirectory.from_config(config)

    assert Path(script.dir).resolve() == migrations_dir()
    assert script.get_heads() == list(migration_heads())


def test_base_install_can_run_the_async_alembic_environment() -> None:
    """env.py migrates on an async engine, which needs greenlet at runtime.

    SQLAlchemy 2.x ships greenlet only through its ``asyncio`` extra, so a
    plain ``pip install baselith-core`` could not migrate its own database
    unless the base dependencies ask for that extra explicitly.
    """
    import tomllib

    from packaging.requirements import Requirement

    root = Path(__file__).resolve().parents[4]
    env_source = (root / "core" / "db" / "migrations" / "env.py").read_text()
    assert "create_async_engine" in env_source

    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    sqlalchemy = [Requirement(d) for d in deps if Requirement(d).name == "sqlalchemy"]
    assert sqlalchemy, "sqlalchemy must be a direct base dependency"
    assert "asyncio" in sqlalchemy[0].extras
