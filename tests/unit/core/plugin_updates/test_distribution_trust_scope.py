"""Distribution keys live in their own trust scope, apart from plugin names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from baselith.core.plugin_updates._core_state import distribution_keys
from baselith.core.plugin_updates.distribution_manifest import (
    PUBLIC_CORE_NAME,
    distribution_trust_scope,
)
from baselith.core.plugins.signing import generate_keypair_hex


def _store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **scopes: list[str] | None
) -> dict[str, str]:
    keys: dict[str, str] = {}
    entries = []
    for key_id, plugins in scopes.items():
        _, pub = generate_keypair_hex()
        keys[key_id] = pub
        entry: dict[str, object] = {"key_id": key_id, "public_key_hex": pub}
        if plugins is not None:
            entry["plugins"] = plugins
        entries.append(entry)
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"keys": entries}))
    path.chmod(0o600)
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(path))
    monkeypatch.delenv("BASELITH_PLUGIN_TRUST_ROOTS", raising=False)
    return keys


def test_scope_cannot_be_a_plugin_name() -> None:
    scope = distribution_trust_scope(PUBLIC_CORE_NAME)
    assert scope == "distribution:baselithcore"
    assert ":" in scope  # plugin names are [a-z0-9_-]: never this string


def test_a_plugin_key_named_like_the_core_does_not_sign_the_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = _store(
        tmp_path,
        monkeypatch,
        plugin_publisher=[PUBLIC_CORE_NAME],  # a plugin called "baselithcore"
        distribution=[distribution_trust_scope(PUBLIC_CORE_NAME)],
        unscoped=None,
    )
    found = distribution_keys(PUBLIC_CORE_NAME)
    assert keys["plugin_publisher"] not in found
    assert keys["distribution"] in found and keys["unscoped"] in found


def test_a_distribution_key_does_not_sign_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baselith.core.plugins.signing import load_trust_roots

    keys = _store(
        tmp_path,
        monkeypatch,
        distribution=[distribution_trust_scope("baselithcore-enterprise")],
    )
    assert keys["distribution"] not in load_trust_roots("baselithcore-enterprise")
    assert keys["distribution"] in distribution_keys("baselithcore-enterprise")
