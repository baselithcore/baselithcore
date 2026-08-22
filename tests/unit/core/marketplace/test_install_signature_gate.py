"""The pre-install gate must enforce the publisher signature, not only the hash.

``pip install <dir>`` runs the plugin's build backend (arbitrary code), so a
hostile registry that ships a tampered tree must be rejected *before* that. The
self-declared ``integrity_sha256`` alone is insufficient — whoever tampers with
the tree recomputes it — so with ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` enabled
the install must also require a valid Ed25519 signature over the hash.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.marketplace.installer import PluginInstaller
from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


@pytest.fixture
def signed_hash_plugin(tmp_path: Path) -> tuple[Path, str]:
    """A plugin dir whose manifest carries a matching integrity hash (unsigned)."""
    root = tmp_path / "demo_plugin"
    root.mkdir()
    (root / "plugin.py").write_text("def hello(): return 'hi'\n", encoding="utf-8")
    digest = compute_plugin_hash(root)
    (root / "manifest.yaml").write_text(
        f"name: demo\nversion: 1.0.0\nauthor: t\nintegrity_sha256: {digest}\n",
        encoding="utf-8",
    )
    return root, digest


def test_gate_passes_when_signatures_not_required(
    signed_hash_plugin: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", raising=False)
    root, _ = signed_hash_plugin
    assert PluginInstaller()._verify_integrity_pre_install(root) is True


def test_gate_refuses_unsigned_plugin_in_strict_mode(
    signed_hash_plugin: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hash matches, but there is no signature and enforcement is on: the build
    # backend must never run, so the gate fails closed.
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", generate_keypair_hex()[1])
    root, _ = signed_hash_plugin
    assert PluginInstaller()._verify_integrity_pre_install(root) is False


def test_gate_accepts_signed_plugin_in_strict_mode(
    signed_hash_plugin: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, digest = signed_hash_plugin
    private_hex, public_hex = generate_keypair_hex()
    signature = sign_plugin_hash(digest, private_hex)

    data = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    data["signature_ed25519"] = signature
    (root / "manifest.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", public_hex)
    assert PluginInstaller()._verify_integrity_pre_install(root) is True
