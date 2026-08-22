"""The Postgres DSN must never leak the password through model serialization.

``db_password`` is a ``SecretStr`` precisely so it stays out of ``repr()`` /
``model_dump()`` / Sentry frames. ``conninfo`` re-derives the plaintext password
into a full DSN; if it were a ``@computed_field`` that DSN would ride along in
every config dump, defeating the wrapping. It must be a plain property.
"""

from __future__ import annotations

import pytest
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


# --- connection strings with inline credentials -----------------------------
#
# database_url / db_replica_url / graph_db_url / cache_redis_url /
# queue_redis_url stay plain `str` (call sites consume them as DSNs), so the
# leak is closed at the serialization boundary instead: every dump and the repr
# strip the `user:password@` userinfo, while attribute access keeps the usable
# value.

_DSN_SECRETS = ("PGSECRET", "REDISSECRET", "QSECRET", "GSECRET", "REPLSECRET")


def _dsn_config() -> StorageConfig:
    return StorageConfig(
        DATABASE_URL="postgresql://dbuser:PGSECRET@db:5432/app",
        DB_REPLICA_URL="postgresql://ro:REPLSECRET@replica:5432/app",
        GRAPH_DB_URL="redis://:GSECRET@graph:6379",
        CACHE_REDIS_URL="redis://:REDISSECRET@cache:6379/1",
        QUEUE_REDIS_URL="redis://:QSECRET@cache:6379/2",
    )


@pytest.mark.parametrize("surface", ["repr", "model_dump", "model_dump_json"])
def test_inline_dsn_credentials_never_serialized(surface: str) -> None:
    cfg = _dsn_config()
    blob = {
        "repr": lambda: repr(cfg),
        "model_dump": lambda: str(cfg.model_dump()),
        "model_dump_json": cfg.model_dump_json,
    }[surface]()
    leaked = [s for s in _DSN_SECRETS if s in blob]
    assert not leaked, f"{surface} leaked {leaked}"


def test_redaction_keeps_the_connectable_parts() -> None:
    """Redaction must strip only the userinfo — scheme/host/port/path stay, so
    a dump is still useful for diagnosing which backend is configured."""
    dumped = _dsn_config().model_dump()
    assert dumped["cache_redis_url"] == "redis://cache:6379/1"
    assert dumped["database_url"] == "postgresql://db:5432/app"


def test_attribute_access_still_returns_the_real_dsn() -> None:
    """The redaction is a serialization concern only: consumers connect with
    the credentialed value."""
    cfg = _dsn_config()
    assert cfg.cache_redis_url == "redis://:REDISSECRET@cache:6379/1"
    assert cfg.database_url == "postgresql://dbuser:PGSECRET@db:5432/app"


def test_credential_free_urls_are_left_untouched() -> None:
    cfg = StorageConfig(CACHE_REDIS_URL="redis://cache:6379/1")
    assert cfg.model_dump()["cache_redis_url"] == "redis://cache:6379/1"
