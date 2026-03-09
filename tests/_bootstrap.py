"""Test bootstrap utilities ensuring the app package can be imported safely."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class _StubCursor:
    def __enter__(self) -> "_StubCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, *_args, **_kwargs) -> None:
        return None


class _StubConnection:
    _app_timezone = None

    def cursor(self, *_args, **_kwargs):
        return _StubCursor()


class _StubConnectionPool:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    class _Ctx:
        def __enter__(self) -> _StubConnection:
            return _StubConnection()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def connection(self, *args, **kwargs):
        return self._Ctx()

    def close(self) -> None:
        return None


if "psycopg" not in sys.modules:
    try:
        import psycopg

        psycopg_stub = psycopg
    except ImportError:
        psycopg_stub = types.ModuleType("psycopg")
        psycopg_stub.Connection = _StubConnection  # type: ignore[attr-defined]
        psycopg_stub.Cursor = _StubCursor  # type: ignore[attr-defined]
        sys.modules["psycopg"] = psycopg_stub
else:
    psycopg_stub = sys.modules["psycopg"]

if "psycopg.rows" not in sys.modules:
    rows_module = types.ModuleType("psycopg.rows")
    rows_module.RowFactory = object  # type: ignore[attr-defined]
    rows_module.dict_row = object  # type: ignore[attr-defined]
    sys.modules["psycopg.rows"] = rows_module
else:
    rows_module = sys.modules["psycopg.rows"]

if not hasattr(psycopg_stub, "rows"):
    psycopg_stub.rows = rows_module  # type: ignore[attr-defined]

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        psycopg_pool_stub = types.ModuleType("psycopg_pool")
        psycopg_pool_stub.ConnectionPool = _StubConnectionPool  # type: ignore[attr-defined]
        sys.modules["psycopg_pool"] = psycopg_pool_stub


if "app.llm" not in sys.modules:
    llm_stub = types.ModuleType("app.llm")
    llm_stub.generate_response = lambda *args, **kwargs: ""  # type: ignore[attr-defined]
    llm_stub.generate_response_stream = (
        lambda *args, **kwargs: iter(())  # type: ignore[attr-defined]
    )
    sys.modules["app.llm"] = llm_stub


__all__ = []
