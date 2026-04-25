"""Verify the marketplace publisher injects ``integrity_sha256`` into the
manifest that ships inside the published zip archive."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import yaml

from core.marketplace.publisher import _inject_integrity
from core.plugins.integrity import (
    compute_plugin_hash,
    verify_plugin_integrity,
)


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    root = tmp_path / "demo_plugin"
    root.mkdir()
    (root / "manifest.yaml").write_text(
        "name: demo\nversion: 1.0.0\nauthor: tester\n", encoding="utf-8"
    )
    (root / "plugin.py").write_text("def hello(): return 'hi'\n", encoding="utf-8")
    return root


def test_inject_integrity_yaml_round_trip(plugin_dir: Path) -> None:
    manifest = plugin_dir / "manifest.yaml"
    digest = compute_plugin_hash(plugin_dir)
    rewritten = _inject_integrity(manifest, digest)
    assert rewritten is not None
    parsed = yaml.safe_load(rewritten.decode("utf-8"))
    assert parsed["integrity_sha256"] == digest
    # Original keys preserved
    assert parsed["name"] == "demo"
    assert parsed["version"] == "1.0.0"


def test_round_trip_through_zip_passes_loader_verification(
    plugin_dir: Path, tmp_path: Path
) -> None:
    """Simulate publish→install: hash + manifest injection survives extraction."""
    digest = compute_plugin_hash(plugin_dir)
    rewritten = _inject_integrity(plugin_dir / "manifest.yaml", digest)
    assert rewritten is not None

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in plugin_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(plugin_dir).as_posix()
            if rel == "manifest.yaml":
                zf.writestr(rel, rewritten)
            else:
                zf.write(path, rel)
    buffer.seek(0)

    extract_root = tmp_path / "installed"
    extract_root.mkdir()
    with zipfile.ZipFile(buffer) as zf:
        zf.extractall(extract_root)

    manifest_data = yaml.safe_load(
        (extract_root / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert verify_plugin_integrity(
        extract_root, manifest_data["integrity_sha256"], strict=True
    )


def test_inject_integrity_json(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"name":"demo","version":"1.0.0"}', encoding="utf-8")
    rewritten = _inject_integrity(manifest, "deadbeef")
    assert rewritten is not None
    import json

    data = json.loads(rewritten.decode("utf-8"))
    assert data["integrity_sha256"] == "deadbeef"
    assert data["name"] == "demo"


def test_inject_returns_none_on_parse_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(":\nthis is not: valid: yaml::\n", encoding="utf-8")
    assert _inject_integrity(manifest, "deadbeef") is None
