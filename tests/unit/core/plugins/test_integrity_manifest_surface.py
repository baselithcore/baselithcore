"""V5 hash surface: the manifest is signed, not just the code beside it.

Until V5 the manifest was deliberately excluded from the digest so a publisher
could inject ``integrity_sha256`` after hashing. The cost was that
``permissions`` (network egress, tool and secret grants),
``python_dependencies``, ``min_core_version`` and ``name`` were unsigned:
anyone able to edit ``manifest.yaml`` could widen a signed plugin's egress
without breaking the hash *or* the Ed25519 signature over it.

V5 folds a *canonicalised* projection of the manifest into the digest — the
parsed mapping with the three self-referential supply-chain keys blanked,
dumped as sorted JSON — so injection still works while every other key is
covered.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from core.plugins.integrity import (
    CURRENT_HASH_SURFACE,
    HashSurface,
    compute_plugin_hash,
    verify_plugin_integrity,
)

MANIFEST_BODY = """\
name: demo
version: 1.0.0
min_core_version: 0.30.0
python_dependencies:
- requests
permissions:
  network:
    egress: []
  tools: []
  secrets: []
"""


@pytest.fixture
def signed_plugin(plugin_dir: Path) -> Path:
    """A plugin tree whose manifest declares a real (empty) permission set."""
    (plugin_dir / "manifest.yaml").write_text(MANIFEST_BODY, encoding="utf-8")
    return plugin_dir


# ── The surface itself ───────────────────────────────────────────────────────


def test_current_surface_is_v5(signed_plugin: Path) -> None:
    assert CURRENT_HASH_SURFACE is HashSurface.V5_MANIFEST
    assert compute_plugin_hash(signed_plugin) == compute_plugin_hash(
        signed_plugin, surface=HashSurface.V5_MANIFEST
    )


def test_widening_egress_changes_the_hash(signed_plugin: Path) -> None:
    """The headline finding: a signed plugin's egress could be widened for free."""
    before = compute_plugin_hash(signed_plugin)
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY.replace("egress: []", "egress:\n    - evil.example.com"),
        encoding="utf-8",
    )
    assert compute_plugin_hash(signed_plugin) != before


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("name: demo", "name: something-else"),
        ("min_core_version: 0.30.0", "min_core_version: 0.1.0"),
        ("- requests", "- requests\n- evil-package"),
        ("tools: []", "tools:\n  - shell"),
        ("secrets: []", "secrets:\n  - ANTHROPIC_API_KEY"),
    ],
)
def test_manifest_keys_are_covered(signed_plugin: Path, old: str, new: str) -> None:
    before = compute_plugin_hash(signed_plugin)
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY.replace(old, new), encoding="utf-8"
    )
    assert compute_plugin_hash(signed_plugin) != before


def test_tampered_manifest_fails_verification(signed_plugin: Path) -> None:
    signed = compute_plugin_hash(signed_plugin)
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY.replace("egress: []", "egress:\n    - exfil.example.com"),
        encoding="utf-8",
    )
    assert verify_plugin_integrity(signed_plugin, signed, strict=False) is False
    assert verify_plugin_integrity(signed_plugin, signed, strict=True) is False


# ── Injection still works ────────────────────────────────────────────────────


def test_injecting_supply_chain_fields_keeps_the_hash(signed_plugin: Path) -> None:
    """The publisher workflow survives: the three self-referential keys are blanked."""
    digest = compute_plugin_hash(signed_plugin)
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY
        + f"integrity_sha256: {digest}\n"
        + f"signature_ed25519: {'ab' * 32}\n"
        + "hash_surface_version: 5\n",
        encoding="utf-8",
    )
    assert compute_plugin_hash(signed_plugin) == digest
    assert verify_plugin_integrity(signed_plugin, digest, strict=True) is True


def test_comments_and_key_order_do_not_change_the_hash(signed_plugin: Path) -> None:
    """Canonicalisation is over the parsed mapping, not the YAML text."""
    digest = compute_plugin_hash(signed_plugin)
    (signed_plugin / "manifest.yaml").write_text(
        "# A comment that must not move the digest.\n"
        "version: 1.0.0\n"
        "python_dependencies: [requests]\n"
        "permissions: {network: {egress: []}, tools: [], secrets: []}\n"
        "name: demo\n"
        "min_core_version: 0.30.0\n",
        encoding="utf-8",
    )
    assert compute_plugin_hash(signed_plugin) == digest


def test_json_manifest_canonicalises_like_yaml(tmp_path: Path) -> None:
    """Equal content in either spelling yields an equal digest."""
    import yaml

    data = yaml.safe_load(MANIFEST_BODY)
    yaml_dir = tmp_path / "as_yaml"
    json_dir = tmp_path / "as_json"
    for target in (yaml_dir, json_dir):
        target.mkdir()
        (target / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    (yaml_dir / "manifest.yaml").write_text(MANIFEST_BODY, encoding="utf-8")
    (json_dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert compute_plugin_hash(yaml_dir) == compute_plugin_hash(json_dir)


# ── Degenerate manifests ─────────────────────────────────────────────────────


def test_missing_manifest_hashes_like_v4(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    assert compute_plugin_hash(bare) == compute_plugin_hash(
        bare, surface=HashSurface.V4_UI_EXPORT
    )


def test_unparseable_manifest_is_still_covered(plugin_dir: Path) -> None:
    """A manifest that will not parse contributes its raw bytes, deterministically."""
    (plugin_dir / "manifest.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    first = compute_plugin_hash(plugin_dir)
    assert first == compute_plugin_hash(plugin_dir)
    (plugin_dir / "manifest.yaml").write_text("name: [other\n", encoding="utf-8")
    assert compute_plugin_hash(plugin_dir) != first


# ── The documented V4 legacy path ────────────────────────────────────────────


def test_v4_hash_ignores_the_manifest(signed_plugin: Path) -> None:
    """Old digests must reproduce byte-for-byte, so V4 never reads the manifest."""
    v4 = compute_plugin_hash(signed_plugin, surface=HashSurface.V4_UI_EXPORT)
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY.replace("egress: []", "egress: [evil.example.com]"),
        encoding="utf-8",
    )
    assert compute_plugin_hash(signed_plugin, surface=HashSurface.V4_UI_EXPORT) == v4


def test_v4_signature_still_verifies_with_a_named_warning(
    signed_plugin: Path, caplog: pytest.LogCaptureFixture
) -> None:
    v4 = compute_plugin_hash(signed_plugin, surface=HashSurface.V4_UI_EXPORT)
    assert v4 != compute_plugin_hash(signed_plugin)
    with caplog.at_level(logging.WARNING, logger="core.plugins.integrity_policy"):
        assert verify_plugin_integrity(signed_plugin, v4, strict=False) is True
    assert "V4_UI_EXPORT" in caplog.text
    assert "manifest" in caplog.text.lower()


def test_v4_signature_refused_in_strict_mode(signed_plugin: Path) -> None:
    v4 = compute_plugin_hash(signed_plugin, surface=HashSurface.V4_UI_EXPORT)
    assert verify_plugin_integrity(signed_plugin, v4, strict=True) is False


# ── Declared surface version ─────────────────────────────────────────────────


def test_read_declared_surface(signed_plugin: Path) -> None:
    from core.plugins.integrity import read_declared_surface

    assert read_declared_surface(signed_plugin) is None
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY + "hash_surface_version: 5\n", encoding="utf-8"
    )
    assert read_declared_surface(signed_plugin) == 5
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY + "hash_surface_version: '4'\n", encoding="utf-8"
    )
    assert read_declared_surface(signed_plugin) == 4
    (signed_plugin / "manifest.yaml").write_text(
        MANIFEST_BODY + "hash_surface_version: nonsense\n", encoding="utf-8"
    )
    assert read_declared_surface(signed_plugin) is None


def test_is_manifest_path() -> None:
    from core.plugins.integrity import is_manifest_path

    assert is_manifest_path(Path("plugins/demo/manifest.yaml")) is True
    assert is_manifest_path(Path("plugins/demo/manifest.yml")) is True
    assert is_manifest_path(Path("plugins/demo/manifest.json")) is True
    assert is_manifest_path(Path("plugins/demo/plugin.py")) is False
    assert is_manifest_path(Path("plugins/demo/manifest.toml")) is False


def test_manifest_bytes_are_not_hashed_verbatim() -> None:
    """``is_hashed_path`` reports the raw-byte surface; the manifest is canonical."""
    from core.plugins.integrity import is_hashed_path

    assert is_hashed_path(Path("plugins/demo/manifest.yaml")) is False
