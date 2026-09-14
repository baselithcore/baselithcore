"""The auto-signing hook: it must refresh the signature, not just the hash.

The hook used to rewrite ``integrity_sha256`` and leave ``signature_ed25519``
alone — so after any source change the manifest carried a publisher signature
over a hash that no longer existed. It looked signed to a reader and failed
(or, before V5, silently covered the wrong bytes). It also only ever looked at
the staged git diff, which made it unusable for re-signing a tree on demand.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.plugins.integrity import CURRENT_HASH_SURFACE, compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, verify_plugin_signature
from scripts.sign_changed_plugins import SIGNING_KEY_ENV, main, sign_plugin_dir

pytestmark = [pytest.mark.unit]

#: A plausible stale digest. Not all-digits: YAML would hand that back as an int.
STALE = "de" * 32

MANIFEST = """\
name: demo
version: 1.0.0
# A comment that carries meaning and must survive re-signing.
permissions:
  network:
    egress: []
  tools: []
integrity_sha256: {stale}
"""


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def hello(): return 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(MANIFEST.format(stale=STALE), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _no_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)


def _manifest(plugin: Path) -> dict[str, object]:
    data = yaml.safe_load((plugin / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


# ── Explicit-path mode ───────────────────────────────────────────────────────


def test_signs_a_directory_without_git(plugin: Path) -> None:
    outcome = sign_plugin_dir(plugin)
    assert outcome.error is None
    assert outcome.changed is True
    data = _manifest(plugin)
    assert data["integrity_sha256"] == compute_plugin_hash(plugin)


def test_writes_the_hash_surface_version(plugin: Path) -> None:
    sign_plugin_dir(plugin)
    assert _manifest(plugin)["hash_surface_version"] == int(CURRENT_HASH_SURFACE)


def test_comments_survive(plugin: Path) -> None:
    sign_plugin_dir(plugin)
    text = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert "# A comment that carries meaning and must survive re-signing." in text


def test_is_idempotent(plugin: Path) -> None:
    sign_plugin_dir(plugin)
    before = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    second = sign_plugin_dir(plugin)
    assert second.changed is False
    assert (plugin / "manifest.yaml").read_text(encoding="utf-8") == before


def test_unsigned_plugin_is_left_alone(tmp_path: Path) -> None:
    root = tmp_path / "unsigned"
    root.mkdir()
    (root / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text("name: unsigned\n", encoding="utf-8")
    outcome = sign_plugin_dir(root)
    assert outcome.changed is False
    assert "integrity_sha256" not in (root / "manifest.yaml").read_text()


def test_main_path_mode_never_stages(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit paths must not touch the git index — it is not a hook run."""
    import scripts.sign_changed_plugins as module

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("the git index must not be touched in path mode")

    monkeypatch.setattr(module, "_git", _boom)
    monkeypatch.setattr(module, "_stage", _boom)
    assert main([str(plugin)]) == 0
    assert _manifest(plugin)["integrity_sha256"] == compute_plugin_hash(plugin)


# ── The signature ────────────────────────────────────────────────────────────


def test_signature_is_refreshed_when_a_key_is_configured(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = generate_keypair_hex()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    outcome = sign_plugin_dir(plugin)
    assert outcome.signed is True
    data = _manifest(plugin)
    assert verify_plugin_signature(
        str(data["integrity_sha256"]), str(data["signature_ed25519"]), [public]
    )


def test_stale_signature_is_blanked_with_a_loud_warning(
    plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No key configured: a signature over the old hash must not be left behind."""
    (plugin / "manifest.yaml").write_text(
        MANIFEST.format(stale=STALE) + f"signature_ed25519: {'ab' * 32}\n",
        encoding="utf-8",
    )
    outcome = sign_plugin_dir(plugin)
    assert outcome.blanked is True
    assert _manifest(plugin)["signature_ed25519"] == ""
    captured = capsys.readouterr()
    assert "WARNING" in captured.out + captured.err
    assert SIGNING_KEY_ENV in captured.out + captured.err


def test_no_signature_stays_absent_without_a_key(plugin: Path) -> None:
    outcome = sign_plugin_dir(plugin)
    assert outcome.blanked is False
    assert "signature_ed25519" not in _manifest(plugin)


def test_a_bad_key_is_reported_not_raised(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SIGNING_KEY_ENV, "not-a-hex-key")
    outcome = sign_plugin_dir(plugin)
    assert outcome.error is not None
    assert outcome.signed is False


# ── JSON manifests ───────────────────────────────────────────────────────────


def test_json_manifest_is_signed(tmp_path: Path) -> None:
    import json

    root = tmp_path / "jsonplug"
    root.mkdir()
    (root / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"name": "jsonplug", "integrity_sha256": STALE}),
        encoding="utf-8",
    )
    sign_plugin_dir(root)
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert data["integrity_sha256"] == compute_plugin_hash(root)
    assert data["hash_surface_version"] == int(CURRENT_HASH_SURFACE)


# ── Nested keys are not clobbered ────────────────────────────────────────────


def test_only_top_level_keys_are_rewritten(plugin: Path) -> None:
    (plugin / "manifest.yaml").write_text(
        "name: demo\n"
        "permissions:\n"
        "  tools: []\n"
        "config_schema_note:\n"
        "  integrity_sha256: nested-must-not-change\n"
        f"integrity_sha256: {STALE}\n",
        encoding="utf-8",
    )
    sign_plugin_dir(plugin)
    text = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert "  integrity_sha256: nested-must-not-change" in text


# ── Blanking is gated on the digest actually moving ──────────────────────────


def _sign_with_key(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Sign ``plugin`` for real and return the trust root that verifies it."""
    private, public = generate_keypair_hex()
    monkeypatch.setenv(SIGNING_KEY_ENV, private)
    sign_plugin_dir(plugin)
    monkeypatch.delenv(SIGNING_KEY_ENV)
    return public


def test_unchanged_tree_keeps_a_valid_signature(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--all` / path mode must not strip the signature off every plugin.

    The hook only ever ran on a changed file, so blanking unconditionally was
    invisible there. Path mode is what plugin authors are told to run.
    """
    public = _sign_with_key(plugin, monkeypatch)
    signature = _manifest(plugin)["signature_ed25519"]

    outcome = sign_plugin_dir(plugin)

    assert outcome.blanked is False
    assert outcome.changed is False
    data = _manifest(plugin)
    assert data["signature_ed25519"] == signature
    assert verify_plugin_signature(
        str(data["integrity_sha256"]), str(signature), [public]
    )


def test_main_all_mode_keeps_valid_signatures(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.sign_changed_plugins as module

    public = _sign_with_key(plugin, monkeypatch)
    monkeypatch.setattr(module, "_all_plugin_dirs", lambda: [plugin])
    assert main(["--all"]) == 0
    data = _manifest(plugin)
    assert verify_plugin_signature(
        str(data["integrity_sha256"]), str(data["signature_ed25519"]), [public]
    )


def test_changed_tree_still_blanks_the_signature(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_with_key(plugin, monkeypatch)
    (plugin / "plugin.py").write_text("def hello(): return 2\n", encoding="utf-8")

    outcome = sign_plugin_dir(plugin)

    assert outcome.blanked is True
    assert _manifest(plugin)["signature_ed25519"] == ""


def test_numeric_signature_is_blanked_too(plugin: Path) -> None:
    """An all-digit signature parses as an int; it must not escape blanking."""
    (plugin / "manifest.yaml").write_text(
        MANIFEST.format(stale=STALE) + "signature_ed25519: 1234567890\n",
        encoding="utf-8",
    )
    outcome = sign_plugin_dir(plugin)
    assert outcome.blanked is True
    assert _manifest(plugin)["signature_ed25519"] == ""


def test_quoted_top_level_key_is_rewritten_not_duplicated(plugin: Path) -> None:
    """A quoted key is still a top-level key — rewrite it, never append a twin."""
    (plugin / "manifest.yaml").write_text(
        f'name: demo\n"integrity_sha256": {STALE}\n', encoding="utf-8"
    )
    sign_plugin_dir(plugin)
    text = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert text.count("integrity_sha256") == 1
    assert _manifest(plugin)["integrity_sha256"] == compute_plugin_hash(plugin)


def test_blank_signature_warning_names_the_key_not_another_tool(
    plugin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same defect as the CLI's warning, same fix: name the cause.

    Sending the operator to ``scripts/sign_plugin_ed25519.py`` means running a
    tool that reads the very environment variable that is unset, so it exits 1
    with "environment variable ... is empty" and nothing is re-signed. The
    remediation has to set the key first.
    """
    (plugin / "manifest.yaml").write_text(
        MANIFEST.format(stale=STALE) + f"signature_ed25519: {'ab' * 32}\n",
        encoding="utf-8",
    )

    sign_plugin_dir(plugin)

    captured = capsys.readouterr()
    message = captured.out + captured.err
    assert f"export {SIGNING_KEY_ENV}=" in message
    assert "sign_plugin_ed25519.py" not in message
