"""PostgreSQL DSNs carry connect-timeout and TCP keepalive defaults.

Without them a blackholed database stalled each connect for the OS TCP timeout
and a pooled connection to a vanished peer looked healthy until a query hung
on it. An explicit DSN keeps every parameter it already names.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from core.config._pg_conninfo import merge_conninfo, transport_params
from core.config.storage import StorageConfig

_DEFAULTS = {"connect_timeout": "10", "keepalives": "1"}


@pytest.fixture(autouse=True)
def _no_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGCONNECT_TIMEOUT", raising=False)


def _query(dsn: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(dsn).query)


def test_built_dsn_carries_production_transport_defaults() -> None:
    cfg = StorageConfig(
        DATABASE_URL=None, DB_HOST="db", DB_PASSWORD="pw", DB_SSL_MODE="require"
    )
    query = _query(cfg.conninfo)
    assert query["sslmode"] == ["require"]
    assert query["connect_timeout"] == ["10"]
    assert query["keepalives"] == ["1"]
    assert query["keepalives_idle"] == ["30"]
    assert query["keepalives_interval"] == ["10"]
    assert query["keepalives_count"] == ["3"]
    assert query["tcp_user_timeout"] == ["60000"]


def test_settings_tune_and_zero_disables_each_knob() -> None:
    cfg = StorageConfig(
        DATABASE_URL=None,
        DB_SSL_MODE=None,
        DB_CONNECT_TIMEOUT=0,
        DB_TCP_KEEPALIVES_IDLE=0,
        DB_TCP_KEEPALIVES_INTERVAL=0,
        DB_TCP_KEEPALIVES_COUNT=0,
        DB_TCP_USER_TIMEOUT_MS=0,
    )
    assert "?" not in cfg.conninfo
    cfg = StorageConfig(
        DATABASE_URL=None, DB_CONNECT_TIMEOUT=3, DB_TCP_KEEPALIVES_IDLE=5
    )
    query = _query(cfg.conninfo)
    assert query["connect_timeout"] == ["3"]
    assert query["keepalives_idle"] == ["5"]


def test_explicit_database_url_values_win() -> None:
    url = "postgresql://u:p@h:5432/db?connect_timeout=42&options=-c%20search_path%3Dx"
    cfg = StorageConfig(DATABASE_URL=url)
    dsn = cfg.conninfo
    assert dsn.startswith(url + "&")  # original query preserved byte for byte
    query = _query(dsn)
    assert query["connect_timeout"] == ["42"]
    assert query["keepalives"] == ["1"]


def test_replica_dsn_gets_the_same_defaults() -> None:
    cfg = StorageConfig(DB_REPLICA_URL="postgresql://r/db")
    assert cfg.replica_conninfo is not None
    assert _query(cfg.replica_conninfo)["connect_timeout"] == ["10"]


def test_pgconnect_timeout_env_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "4")
    params = transport_params(
        connect_timeout=10,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
        tcp_user_timeout_ms=0,
    )
    assert "connect_timeout" not in params
    assert "tcp_user_timeout" not in params


def test_keyword_value_dsn_is_extended_in_place() -> None:
    dsn = "host=db dbname=app connect_timeout=5"
    merged = merge_conninfo(dsn, _DEFAULTS)
    assert merged == "host=db dbname=app connect_timeout=5 keepalives=1"


def test_socket_url_without_host_keeps_its_slashes() -> None:
    dsn = "postgresql:///app?host=/run/postgresql"
    merged = merge_conninfo(dsn, _DEFAULTS)
    assert merged == (
        "postgresql:///app?host=/run/postgresql&connect_timeout=10&keepalives=1"
    )


def test_url_without_query_and_nothing_missing() -> None:
    assert merge_conninfo("postgresql://h/db", _DEFAULTS) == (
        "postgresql://h/db?connect_timeout=10&keepalives=1"
    )
    full = "postgresql://h/db?keepalives=0&connect_timeout=1"
    assert merge_conninfo(full, _DEFAULTS) == full
    assert merge_conninfo("", _DEFAULTS) == ""


def test_the_dsn_is_accepted_by_libpq_parsing() -> None:
    psycopg = pytest.importorskip("psycopg")
    cfg = StorageConfig(DATABASE_URL=None, DB_HOST="db", DB_PASSWORD="pw")
    parsed = psycopg.conninfo.conninfo_to_dict(cfg.conninfo)
    assert parsed["connect_timeout"] == "10"
    assert parsed["tcp_user_timeout"] == "60000"
