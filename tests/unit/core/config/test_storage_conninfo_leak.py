"""The Postgres DSN must never leak the password through model serialization.

``db_password`` is a ``SecretStr`` precisely so it stays out of ``repr()`` /
``model_dump()`` / Sentry frames. ``conninfo`` re-derives the plaintext password
into a full DSN; if it were a ``@computed_field`` that DSN would ride along in
every config dump, defeating the wrapping. It must be a plain property.
"""

from __future__ import annotations

from pydantic import SecretStr

from core.config.storage import StorageConfig


def _config() -> StorageConfig:
    return StorageConfig(
        DB_HOST="db.internal",
        DB_PORT=5432,
        DB_NAME="app",
        DB_USER="app_user",
        DB_PASSWORD="sup3r-s3cret-pw",
    )


def test_conninfo_still_builds_dsn_by_attribute_access() -> None:
    cfg = _config()
    assert cfg.conninfo.startswith("postgresql://app_user:")
    assert "sup3r-s3cret-pw" in cfg.conninfo  # attribute access is intentional


def test_model_dump_excludes_conninfo_and_password() -> None:
    cfg = _config()
    dumped = cfg.model_dump()
    # conninfo is not a serialized field.
    assert "conninfo" not in dumped
    # db_password stays a SecretStr, so its plaintext never appears.
    assert isinstance(dumped.get("db_password"), SecretStr)
    assert "sup3r-s3cret-pw" not in str(dumped)


def test_model_dump_json_does_not_contain_password() -> None:
    cfg = _config()
    assert "sup3r-s3cret-pw" not in cfg.model_dump_json()


def test_repr_does_not_contain_password() -> None:
    assert "sup3r-s3cret-pw" not in repr(_config())
