"""The standalone Ed25519 signer must agree with the other two signing tools.

Three tools write the same three supply-chain keys: ``baselith plugin sign``,
the ``scripts/sign_changed_plugins.py`` pre-commit hook, and this script — the
one the other two *name* in their blank-signature warning, so it is the command
an operator is most likely to run under pressure.

It was the only one that never stamped ``hash_surface_version``. The field is
advisory (it sits outside the digest, by necessity — it is written after the
digest is computed), but it is what tooling and reviewers read to answer "was
this signed under the surface that covers the compiled UI bundle, or the older
one that did not?". A manifest signed by this script answered "unknown", and
re-signing an already-correct plugin with it silently *removed* a stamp the
other two tools had written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.plugins.integrity import CURRENT_HASH_SURFACE, compute_plugin_hash
from core.plugins.manifest_rewrite import HASH_KEY, SIGNATURE_KEY, SURFACE_KEY
from core.plugins.signing import generate_keypair_hex, verify_plugin_signature
from scripts.sign_plugin_ed25519 import main

pytestmark = [pytest.mark.unit]

KEY_ENV = "BASELITH_PLUGIN_SIGNING_KEY"

MANIFEST = """\
name: demo
version: 1.0.0
# Why this plugin reaches nothing on the network. A reviewer reads this.
permissions:
  network:
    egress: []
"""


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def hello(): return 1\n", encoding="utf-8")
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> str:
    private, public = generate_keypair_hex()
    monkeypatch.setenv(KEY_ENV, private)
    return public


def _run(plugin: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr("sys.argv", ["sign_plugin_ed25519.py", "sign", str(plugin)])
    return main()


def _manifest(plugin: Path) -> dict[str, object]:
    data = yaml.safe_load((plugin / "manifest.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_stamps_the_hash_surface_version(
    plugin: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stamp the other two signing tools write must appear here too."""
    assert _run(plugin, monkeypatch) == 0

    assert _manifest(plugin)[SURFACE_KEY] == int(CURRENT_HASH_SURFACE)


def test_writes_a_verifiable_hash_and_signature(
    plugin: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The behaviour that already worked keeps working."""
    assert _run(plugin, monkeypatch) == 0

    data = _manifest(plugin)
    assert data[HASH_KEY] == compute_plugin_hash(plugin)
    assert verify_plugin_signature(
        str(data[HASH_KEY]), str(data[SIGNATURE_KEY]), [signing_key]
    )


def test_agrees_with_the_cli_signer_byte_for_byte(
    plugin: Path, tmp_path: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both tools must leave the same manifest, or "re-sign" means two things.

    Built as a second, identical tree so the comparison is of the *written
    result*, not of one tool observing the other's output.
    """
    from core.cli.commands.plugin.sign import sign_plugin

    twin = tmp_path / "twin"
    twin.mkdir()
    (twin / "plugin.py").write_text("def hello(): return 1\n", encoding="utf-8")
    (twin / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")

    assert _run(plugin, monkeypatch) == 0
    assert sign_plugin(str(twin)) == 0

    written = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    twin_written = (twin / "manifest.yaml").read_text(encoding="utf-8")
    assert written == twin_written


def test_comments_survive(
    plugin: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manifest's comments are its permission rationale; do not eat them."""
    assert _run(plugin, monkeypatch) == 0

    text = (plugin / "manifest.yaml").read_text(encoding="utf-8")
    assert "# Why this plugin reaches nothing on the network." in text


def test_is_idempotent(
    plugin: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running over an unchanged tree must not churn the file."""
    assert _run(plugin, monkeypatch) == 0
    first = (plugin / "manifest.yaml").read_text(encoding="utf-8")

    assert _run(plugin, monkeypatch) == 0

    assert (plugin / "manifest.yaml").read_text(encoding="utf-8") == first


def test_json_manifest_gets_the_stamp_too(
    tmp_path: Path, signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON manifests are a supported spelling, not a second-class one."""
    root = tmp_path / "jsondemo"
    root.mkdir()
    (root / "plugin.py").write_text("def hello(): return 1\n", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"name": "jsondemo", "version": "1.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("sys.argv", ["sign_plugin_ed25519.py", "sign", str(root)])
    assert main() == 0

    data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert data[SURFACE_KEY] == int(CURRENT_HASH_SURFACE)
    assert data[HASH_KEY] == compute_plugin_hash(root)


def test_missing_key_is_reported_not_raised(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key configured is an operator error, reported on stderr."""
    monkeypatch.delenv(KEY_ENV, raising=False)

    assert _run(plugin, monkeypatch) == 1
    assert SURFACE_KEY not in _manifest(plugin)
