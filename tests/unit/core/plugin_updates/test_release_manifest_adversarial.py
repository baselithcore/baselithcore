"""Adversarial inputs to the release manifest: each is refused, none raises oddly."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from core.plugin_updates.release_manifest import (
    SIGNATURE_KEY,
    ReleaseManifestError,
    canonical_bytes,
    file_digests,
    files_mismatch,
    load_release_json,
    parse_files,
    sign_release_manifest,
    verify_release_manifest,
)
from core.plugins.signing import generate_keypair_hex, sign_message

_D = "0" * 64


def _tree(root: Path) -> Path:
    (root / "docs").mkdir(parents=True)
    (root / "__init__.py").write_text("X = 1\n")
    (root / "docs" / "guide.md").write_text("# Guide\n")
    return root


def _signed(priv: str) -> dict[str, object]:
    meta: dict[str, object] = {"name": "demo", "version": "1.2.0", "files": {"a": _D}}
    meta[SIGNATURE_KEY] = sign_release_manifest(meta, priv)
    return meta


@pytest.mark.parametrize(
    "key",
    ["", ".", "a/./b", "a/", "./a", "a/..", "a\nb", "a\x00b", "a\tb", "a\x7fb"],
)
def test_parse_files_refuses_unsafe_keys(key: str) -> None:
    with pytest.raises(ReleaseManifestError, match="unsafe path"):
        parse_files({"files": {key: _D}}, max_entries=10)


@pytest.mark.parametrize(
    "keys",
    [
        ("Readme.md", "README.md"),  # one file on a case-insensitive filesystem
        ("café.md", "café.md"),  # one file on a normalising filesystem
    ],
)
def test_parse_files_refuses_ambiguous_keys(keys: tuple[str, str]) -> None:
    with pytest.raises(ReleaseManifestError, match="ambiguous"):
        parse_files({"files": dict.fromkeys(keys, _D)}, max_entries=10)


@pytest.mark.parametrize("digest", ["A" * 64, 0, None, ["0" * 64], "0" * 65])
def test_parse_files_refuses_malformed_digests(digest: object) -> None:
    with pytest.raises(ReleaseManifestError):
        parse_files({"files": {"a": digest}}, max_entries=10)


def test_parse_files_refuses_a_huge_key_without_echoing_it() -> None:
    key = "../" + "x" * 10_000
    with pytest.raises(ReleaseManifestError) as info:
        parse_files({"files": {key: _D}}, max_entries=10)
    assert len(str(info.value)) < 300


@pytest.mark.parametrize(
    "raw",
    [
        b'{"name": "a", "name": "b"}',  # duplicate key: parsers disagree on which wins
        b'{"files": {"a": "x", "a": "y"}}',  # duplicate nested key
        b'{"v": NaN}',
        b'{"v": Infinity}',
        b"[1, 2]",
        b"not json",
        b"\xff\xfe",
        b"",
    ],
)
def test_load_release_json_refuses(raw: bytes) -> None:
    with pytest.raises(ReleaseManifestError):
        load_release_json(raw)


def test_load_release_json_accepts_a_plain_object() -> None:
    assert load_release_json(b'{"a": {"b": [1, "\\u00e9"]}}') == {"a": {"b": [1, "é"]}}


def test_signature_over_another_canonicalisation_is_refused() -> None:
    priv, pub = generate_keypair_hex()
    meta: dict[str, object] = {"name": "demo", "version": "1.2.0", "files": {"a": _D}}
    domain = b"baselith-release-manifest-v1\n"
    for message in (
        canonical_bytes(meta),  # no domain tag
        domain + json.dumps(meta, indent=2).encode(),  # pretty, not canonical
        domain + json.dumps(meta, sort_keys=True).encode(),  # default separators
        domain
        + json.dumps(
            meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode(),
    ):
        if message == domain + canonical_bytes(meta):
            continue
        signed = {**meta, SIGNATURE_KEY: sign_message(message, priv)}
        assert not verify_release_manifest(signed, [pub])


@pytest.mark.parametrize("signature", [123, None, "00", "zz", "0" * 128, ["ab"]])
def test_malformed_signatures_are_refused(signature: object) -> None:
    priv, pub = generate_keypair_hex()
    meta = {**_signed(priv), SIGNATURE_KEY: signature}
    assert not verify_release_manifest(meta, [pub])


def test_unserialisable_meta_is_refused_not_raised() -> None:
    priv, pub = generate_keypair_hex()
    meta = _signed(priv)
    assert not verify_release_manifest({**meta, "x": {1, 2}}, [pub])
    assert not verify_release_manifest({**meta, "x": float("nan")}, [pub])
    assert not verify_release_manifest(meta, ["not-hex", ""])


def test_file_digests_refuses_a_hardlink(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    os.link(root / "docs" / "guide.md", root / "twin.md")
    with pytest.raises(ReleaseManifestError, match="hard link"):
        file_digests(root)
    assert "hard link" in (files_mismatch(root, {"x": _D}) or "")


def test_file_digests_refuses_a_symlinked_directory(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "evil.py").write_text("")
    (root / "lib").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ReleaseManifestError, match="lib"):
        file_digests(root)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_file_digests_refuses_a_fifo(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    os.mkfifo(root / "pipe")
    with pytest.raises(ReleaseManifestError, match="pipe"):
        file_digests(root)


@pytest.mark.skipif(sys.platform == "win32", reason="backslash is a separator there")
def test_file_digests_refuses_a_name_parse_files_would_refuse(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    (root / "a\\b.md").write_text("")
    with pytest.raises(ReleaseManifestError, match="unsafe path"):
        file_digests(root)


def test_file_digests_reports_an_unreadable_directory(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    root = _tree(tmp_path / "demo")
    (root / "docs").chmod(0)
    try:
        with pytest.raises(ReleaseManifestError):
            file_digests(root)
    finally:
        (root / "docs").chmod(0o755)


def test_listed_directory_is_missing(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    expected = {**file_digests(root), "docs": _D}
    assert files_mismatch(root, expected) == "missing: docs"


def test_mismatch_detail_escapes_a_control_character(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    expected = file_digests(root)
    (root / "evil\nname").write_text("")
    detail = files_mismatch(root, expected) or ""
    assert detail == "unsafe path in the release tree: 'evil\\nname'"


def test_mismatch_detail_caps_the_list(tmp_path: Path) -> None:
    root = _tree(tmp_path / "demo")
    expected = file_digests(root)
    for i in range(8):
        (root / f"x{i}").write_text("")
    assert files_mismatch(root, expected) == "extra: x0, x1, x2, x3, x4 (+3)"


@pytest.mark.parametrize("sub", ["empty", "deep/er/empty"])
def test_file_digests_refuses_an_empty_directory(tmp_path: Path, sub: str) -> None:
    root = _tree(tmp_path / "demo")
    (root / sub).mkdir(parents=True)
    with pytest.raises(ReleaseManifestError, match="empty directory"):
        file_digests(root)
    assert "empty directory" in (
        files_mismatch(root, file_digests(_tree(tmp_path / "x"))) or ""
    )


def test_file_digests_refuses_an_empty_root(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    with pytest.raises(ReleaseManifestError, match="empty directory"):
        file_digests(tmp_path / "demo")


@pytest.mark.parametrize(
    "raw", [b'{"v": 1.0}', b'{"v": 1e3}', b'{"a": {"b": [0.5]}}', b'{"v": -2E-1}']
)
def test_load_release_json_refuses_floats(raw: bytes) -> None:
    with pytest.raises(ReleaseManifestError, match="float"):
        load_release_json(raw)
