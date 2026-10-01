"""release.json format 2: every file hashed, the manifest itself signed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.plugin_updates.release_manifest import (
    SIGNATURE_KEY,
    ReleaseManifestError,
    canonical_bytes,
    file_digests,
    files_mismatch,
    is_legacy,
    parse_files,
    sign_release_manifest,
    verify_release_manifest,
)
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


def _tree(root: Path) -> Path:
    (root / "docs").mkdir(parents=True)
    (root / "__init__.py").write_text("X = 1\n")
    (root / "docs" / "guide.md").write_text("# Guide\n")
    (root / "wheelhouse").mkdir()
    (root / "wheelhouse" / "pkg-1.0-py3-none-any.whl").write_bytes(b"PK\x03\x04")
    return root


def _meta() -> dict[str, object]:
    return {
        "name": "demo",
        "version": "1.2.0",
        "files": {"a": "0" * 64},
        "min_core_version": None,
    }


def test_file_digests_covers_every_file_including_a_wheelhouse(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    digests = file_digests(root)
    assert sorted(digests) == [
        "__init__.py",
        "docs/guide.md",
        "wheelhouse/pkg-1.0-py3-none-any.whl",
    ]
    assert digests["docs/guide.md"] == hashlib.sha256(b"# Guide\n").hexdigest()


def test_file_digests_refuses_links(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    (root / "link.md").symlink_to(root / "docs" / "guide.md")
    with pytest.raises(ReleaseManifestError, match="link.md"):
        file_digests(root)


def test_canonical_bytes_sorted_compact_without_signature() -> None:
    meta = {"b": 1, "a": [2, "é"], SIGNATURE_KEY: "ff"}
    assert canonical_bytes(meta) == b'{"a":[2,"\\u00e9"],"b":1}'


def test_sign_and_verify_round_trip_and_tamper() -> None:
    priv, pub = generate_keypair_hex()
    meta = _meta()
    meta[SIGNATURE_KEY] = sign_release_manifest(meta, priv)
    assert verify_release_manifest(meta, [pub])
    for key, value in (("version", "9.9.9"), ("files", {"a": "1" * 64}), ("extra", 1)):
        tampered = {**meta, key: value}
        assert not verify_release_manifest(tampered, [pub])
    assert not verify_release_manifest({**meta, SIGNATURE_KEY: ""}, [pub])
    assert not verify_release_manifest(meta, [generate_keypair_hex()[1]])


def test_plugin_hash_signature_is_not_a_manifest_signature() -> None:
    priv, pub = generate_keypair_hex()
    meta = _meta()
    digest = hashlib.sha256(canonical_bytes(meta)).hexdigest()
    meta[SIGNATURE_KEY] = sign_plugin_hash(digest, priv)
    assert not verify_release_manifest(meta, [pub])


def test_is_legacy() -> None:
    assert is_legacy({"name": "demo"})
    assert is_legacy({"files": {}})
    assert not is_legacy({"files": {}, SIGNATURE_KEY: "x"})


@pytest.mark.parametrize(
    "files",
    [
        {},
        [],
        {"../x": "0" * 64},
        {"/abs": "0" * 64},
        {"a//b": "0" * 64},
        {"a\\b": "0" * 64},
        {"a": "XYZ"},
        {"a": "0" * 63},
    ],
)
def test_parse_files_refuses(files: object) -> None:
    with pytest.raises(ReleaseManifestError):
        parse_files({"files": files}, max_entries=10)


def test_parse_files_caps_entries() -> None:
    files = {f"f{i}": "0" * 64 for i in range(3)}
    assert parse_files({"files": files}, max_entries=3) == files
    with pytest.raises(ReleaseManifestError, match="more than 2"):
        parse_files({"files": files}, max_entries=2)


def test_files_mismatch_reports_missing_extra_changed(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    expected = file_digests(root)
    assert files_mismatch(root, expected) is None
    (root / "docs" / "guide.md").write_text("changed\n")
    (root / "._guide.md").write_bytes(b"\x00\x05\x16\x07")
    (root / "__init__.py").unlink()
    detail = files_mismatch(root, expected)
    assert detail == "missing: __init__.py; extra: ._guide.md; changed: docs/guide.md"


def test_release_json_round_trips_through_json() -> None:
    priv, pub = generate_keypair_hex()
    meta = _meta()
    meta[SIGNATURE_KEY] = sign_release_manifest(meta, priv)
    assert verify_release_manifest(json.loads(json.dumps(meta, indent=2)), [pub])
