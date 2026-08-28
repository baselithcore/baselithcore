"""Tests for :func:`core.plugins.env.load_plugin_dotenv` scoping/hardening.

The public plugin ``.env`` loader must (a) refuse a symlinked ``.env`` (it must
not point outside the plugin at host secrets), and (b) load ONLY keys in the
plugin's own ``<DIRNAME>_`` namespace, so a plugin cannot flip a framework/core
key it does not own (``MCP_HTTP_REQUIRE_AUTH``, ``BASELITH_SKIP_INTEGRITY_CHECK``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.plugins.env import load_plugin_dotenv


def _plugin(tmp_path: Path, name: str, env_body: str) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / ".env").write_text(env_body, encoding="utf-8")
    return plugin_dir


def test_symlinked_env_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MYPLUGIN_TOKEN", raising=False)
    outside = tmp_path / "outside.env"
    outside.write_text("MYPLUGIN_TOKEN=via_symlink\n", encoding="utf-8")
    plugin_dir = tmp_path / "myplugin"
    plugin_dir.mkdir()
    (plugin_dir / ".env").symlink_to(outside)

    assert load_plugin_dotenv(plugin_dir) is False
    assert "MYPLUGIN_TOKEN" not in os.environ


def test_out_of_namespace_keys_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("MYPLUGIN_TOKEN", "MCP_HTTP_REQUIRE_AUTH", "ALLOW_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    plugin_dir = _plugin(
        tmp_path,
        "myplugin",
        "MYPLUGIN_TOKEN=ok\nMCP_HTTP_REQUIRE_AUTH=false\nALLOW_ORIGINS=*\n",
    )

    assert load_plugin_dotenv(plugin_dir) is True
    # In-namespace key loaded; framework keys the plugin does not own refused.
    assert os.environ.get("MYPLUGIN_TOKEN") == "ok"
    assert "MCP_HTTP_REQUIRE_AUTH" not in os.environ
    assert "ALLOW_ORIGINS" not in os.environ


def test_existing_process_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYPLUGIN_TOKEN", "from_process")
    plugin_dir = _plugin(tmp_path, "myplugin", "MYPLUGIN_TOKEN=from_dotenv\n")

    assert load_plugin_dotenv(plugin_dir) is True
    assert os.environ["MYPLUGIN_TOKEN"] == "from_process"


def test_missing_file_is_noop(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "myplugin"
    plugin_dir.mkdir()
    assert load_plugin_dotenv(plugin_dir) is False


def test_dir_name_hyphens_map_to_underscore_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCUMENT_SOURCES_ROOT", raising=False)
    # Directory ``document-sources`` → namespace ``DOCUMENT_SOURCES_``.
    plugin_dir = _plugin(tmp_path, "document-sources", "DOCUMENT_SOURCES_ROOT=/data\n")
    assert load_plugin_dotenv(plugin_dir) is True
    assert os.environ.get("DOCUMENT_SOURCES_ROOT") == "/data"


def test_explicit_allowed_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("BRAND_KEY", "MYPLUGIN_KEY"):
        monkeypatch.delenv(key, raising=False)
    plugin_dir = _plugin(tmp_path, "myplugin", "BRAND_KEY=v1\nMYPLUGIN_KEY=v2\n")
    # Override the derived namespace: only BRAND_* is the plugin's namespace.
    assert load_plugin_dotenv(plugin_dir, allowed_prefixes=("BRAND_",)) is True
    assert os.environ.get("BRAND_KEY") == "v1"
    assert "MYPLUGIN_KEY" not in os.environ


def test_explicit_prefixes_cannot_reopen_framework_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The denylist runs before the namespace check, so ``allowed_prefixes``
    cannot be used to claim a framework namespace as the plugin's own."""
    for key in ("MCP_ALLOW_INTERNAL_ENDPOINTS", "SENTRY_DSN"):
        monkeypatch.delenv(key, raising=False)
    plugin_dir = _plugin(
        tmp_path,
        "myplugin",
        "MCP_ALLOW_INTERNAL_ENDPOINTS=true\nSENTRY_DSN=https://evil.example\n",
    )

    assert load_plugin_dotenv(plugin_dir, allowed_prefixes=("MCP_", "SENTRY_")) is True
    assert "MCP_ALLOW_INTERNAL_ENDPOINTS" not in os.environ
    assert "SENTRY_DSN" not in os.environ


def test_dir_name_shadowing_framework_namespace_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin directory named ``baselith-x`` derives ``BASELITH_X_``, which is
    caught by the ``BASELITH_`` denylist prefix — the namespace is not a bypass."""
    monkeypatch.delenv("BASELITH_X_TOKEN", raising=False)
    plugin_dir = _plugin(tmp_path, "baselith-x", "BASELITH_X_TOKEN=t\n")

    assert load_plugin_dotenv(plugin_dir) is True
    assert "BASELITH_X_TOKEN" not in os.environ


def test_unlisted_out_of_namespace_key_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key on no denylist is still refused: the allowlist is the primary gate."""
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    plugin_dir = _plugin(tmp_path, "myplugin", "AWS_SECRET_ACCESS_KEY=leak\n")

    assert load_plugin_dotenv(plugin_dir) is True
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
