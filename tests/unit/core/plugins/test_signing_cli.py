"""``baselith plugin sign`` — the same signing policy as the pre-commit hook.

The command used to round-trip the manifest through ``yaml.safe_dump``, which
deleted every comment in it — and a plugin manifest's comments carry the
rationale for the permissions it declares. It also has to agree with
``scripts/sign_changed_plugins.py`` about when a signature has gone stale:
re-signing an unchanged tree must not strip a perfectly valid signature.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from core.cli.commands.plugin.sign import SIGNING_KEY_ENV, sign_plugin
from core.plugins.integrity import CURRENT_HASH_SURFACE, compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, verify_plugin_signature

pytestmark = [pytest.mark.unit]

#: Not all-digits: YAML would hand that back as an int.
STALE = "de" * 32

MANIFEST = f"""\
name: demo
version: 1.0.0
# The comment that explains why this plugin reaches nothing.
permissions:
  network:
    egress: []
integrity_sha256: {STALE}
"""


@pytest.fixture(autouse=True)
def _no_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def hello(): return 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


def _manifest(plugin: Path) -> dict[str, object]:
    data = yaml.safe_load((plugin / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _sign_with_key(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    private, public = generate_keypair_hex()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    assert sign_plugin(str(plugin)) == 0
    monkeypatch.delenv(SIGNING_KEY_ENV)
    return public


def test_writes_hash_and_surface_version(plugin: Path) -> None:
    assert sign_plugin(str(plugin)) == 0
    data = _manifest(plugin)
    assert data["integrity_sha256"] == compute_plugin_hash(plugin)
    assert data["hash_surface_version"] == int(CURRENT_HASH_SURFACE)


def test_comments_survive(plugin: Path) -> None:
    sign_plugin(str(plugin))
    text = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert "# The comment that explains why this plugin reaches nothing." in text


def test_signs_when_a_key_is_configured(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = _sign_with_key(plugin, monkeypatch)
    data = _manifest(plugin)
    assert verify_plugin_signature(
        str(data["integrity_sha256"]), str(data["signature_ed25519"]), [public]
    )
    assert os.environ.get(SIGNING_KEY_ENV) is None


def test_unchanged_tree_keeps_its_signature(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-signing without a key must not strip a signature that is still valid."""
    public = _sign_with_key(plugin, monkeypatch)
    signature = _manifest(plugin)["signature_ed25519"]

    assert sign_plugin(str(plugin)) == 0

    data = _manifest(plugin)
    assert data["signature_ed25519"] == signature
    assert verify_plugin_signature(
        str(data["integrity_sha256"]), str(signature), [public]
    )


def test_changed_tree_blanks_the_signature(
    plugin: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _sign_with_key(plugin, monkeypatch)
    (plugin / "plugin.py").write_text("def hello(): return 2\n", encoding="utf-8")

    assert sign_plugin(str(plugin)) == 0

    assert _manifest(plugin)["signature_ed25519"] == ""
    assert "BLANKED" in capsys.readouterr().out


def test_numeric_signature_is_blanked_too(plugin: Path) -> None:
    (plugin / "manifest.yaml").write_text(
        MANIFEST + "signature_ed25519: 1234567890\n", encoding="utf-8"
    )
    sign_plugin(str(plugin))
    assert _manifest(plugin)["signature_ed25519"] == ""


def test_reports_an_older_declared_surface(
    plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An operator re-signing a V4-era plugin is told what is about to widen."""
    (plugin / "manifest.yaml").write_text(
        MANIFEST + "hash_surface_version: 4\n", encoding="utf-8"
    )
    sign_plugin(str(plugin), check_only=True)
    out = capsys.readouterr().out
    assert "hash surface 4" in out
    assert str(int(CURRENT_HASH_SURFACE)) in out


def test_reports_a_missing_declared_surface(
    plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sign_plugin(str(plugin), check_only=True)
    assert "No hash_surface_version declared" in capsys.readouterr().out


def test_check_only_writes_nothing(plugin: Path) -> None:
    before = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert sign_plugin(str(plugin), check_only=True) == 0
    assert (plugin / "manifest.yaml").read_text(encoding="utf-8") == before


def test_blank_signature_warning_names_a_remediation_that_works(
    plugin: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The warning must point at the fix for the cause, not at another tool.

    It used to say "Re-sign with ``python scripts/sign_plugin_ed25519.py sign
    <path>``" — a command that exits 1 with "environment variable ... is empty"
    for the very reason the signature was blanked in the first place, and which
    carried a literal ``<path>`` placeholder instead of the plugin just signed.
    An operator who follows it lands back where they started.

    The cause is the unset key; the remediation must name it, and the command
    must be the one they already ran, with the real path in it.
    """
    _sign_with_key(plugin, monkeypatch)
    (plugin / "plugin.py").write_text("def hello(): return 2\n", encoding="utf-8")
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)

    assert sign_plugin(str(plugin)) == 0

    out = capsys.readouterr().out
    assert "BLANKED" in out
    # The remediation must set the key and re-run this command on this plugin.
    assert f"export {SIGNING_KEY_ENV}=" in out
    assert "baselith plugin sign" in out
    assert str(plugin) in out
    # ...and must not send them to a tool that fails for the same reason.
    assert "sign_plugin_ed25519.py" not in out
    assert "<path>" not in out
