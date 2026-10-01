from __future__ import annotations

from pathlib import Path

import pytest

from core.plugin_updates.models import Refusal
from core.plugin_updates.verifier import requirement_satisfied, verify_release
from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


def _release(
    tmp: Path,
    *,
    name: str = "demo",
    version: str = "1.2.0",
    extra: str = "",
    sign_with: str | None = None,
) -> Path:
    d = tmp / f"{name}-{version}"
    d.mkdir()
    (d / "__init__.py").write_text("X = 1\n")
    (d / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\nhash_surface_version: 5\n{extra}"
    )
    h = compute_plugin_hash(d)
    tail = f"integrity_sha256: {h}\n"
    if sign_with:
        tail += f"signature_ed25519: {sign_plugin_hash(h, sign_with)}\n"
    (d / "manifest.yaml").write_text((d / "manifest.yaml").read_text() + tail)
    return d


@pytest.fixture()
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


def _verify(d: Path, public: str, **kw: object):
    args = dict(
        expected_name="demo",
        expected_version="1.2.0",
        installed_version="1.1.0",
        core_version="1.50.0",
        trusted_keys=[public],
    )
    args.update(kw)
    return verify_release(d, **args)  # type: ignore[arg-type]


def test_valid_release_passes(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    assert _verify(_release(tmp_path, sign_with=priv), pub).ok


def test_unsigned_is_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    _, pub = keys
    assert _verify(_release(tmp_path), pub).refusal is Refusal.SIGNATURE_INVALID


def test_foreign_key_is_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    other_priv, _ = generate_keypair_hex()
    _, pub = keys
    assert (
        _verify(_release(tmp_path, sign_with=other_priv), pub).refusal
        is Refusal.SIGNATURE_INVALID
    )


def test_no_trusted_keys(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, _ = keys
    r = verify_release(
        _release(tmp_path, sign_with=priv),
        expected_name="demo",
        expected_version="1.2.0",
        installed_version="1.1.0",
        core_version="1.50.0",
        trusted_keys=[],
    )
    assert r.refusal is Refusal.NO_TRUSTED_KEYS


def test_tampered_file_is_integrity_mismatch(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    (d / "__init__.py").write_text("X = 2\n")
    assert _verify(d, pub).refusal is Refusal.INTEGRITY_MISMATCH


def test_name_and_version_must_match(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    assert _verify(d, pub, expected_name="other").refusal is Refusal.NAME_MISMATCH
    assert _verify(d, pub, expected_version="1.3.0").refusal is Refusal.VERSION_MISMATCH


@pytest.mark.parametrize("installed", ["1.2.0", "1.3.0"])
def test_not_newer(tmp_path: Path, keys: tuple[str, str], installed: str) -> None:
    priv, pub = keys
    assert (
        _verify(
            _release(tmp_path, sign_with=priv), pub, installed_version=installed
        ).refusal
        is Refusal.NOT_NEWER
    )


def test_incompatible_core(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv, extra="min_core_version: 9.0.0\n")
    assert _verify(d, pub).refusal is Refusal.INCOMPATIBLE_CORE


def test_unsatisfied_python_dependency(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(
        tmp_path,
        sign_with=priv,
        extra="python_dependencies:\n- not-a-real-package-xyz>=1\n",
    )
    r = _verify(d, pub)
    assert r.refusal is Refusal.NEEDS_ENVIRONMENT_UPDATE
    assert "not-a-real-package-xyz" in r.detail


def test_requirement_satisfied() -> None:
    assert requirement_satisfied("pydantic>=1")
    assert not requirement_satisfied("pydantic>=999")
    assert not requirement_satisfied("not-a-real-package-xyz")


def test_non_semver_manifest_version_is_manifest_invalid(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    priv, pub = keys
    d = _release(tmp_path, version="1.2", sign_with=priv)
    r = _verify(d, pub, expected_version="1.2")
    assert r.refusal is Refusal.MANIFEST_INVALID


def test_prerelease_is_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(tmp_path, version="1.3.0-rc1", sign_with=priv)
    r = _verify(d, pub, expected_version="1.3.0-rc1")
    assert r.refusal is Refusal.MANIFEST_INVALID
    assert r.detail == "prerelease"


def test_unparseable_installed_version(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    r = _verify(_release(tmp_path, sign_with=priv), pub, installed_version="abc")
    assert r.refusal is Refusal.NOT_NEWER
    assert "abc" in r.detail


@pytest.mark.parametrize(
    "extra",
    [
        "min_core_version:\n- 1.0.0\n",
        "max_core_version: 2.5\n",
        "python_dependencies: 5\n",
        "python_dependencies: pydantic>=1\n",
    ],
)
def test_wrong_typed_fields_are_manifest_invalid(
    tmp_path: Path, keys: tuple[str, str], extra: str
) -> None:
    priv, pub = keys
    d = _release(tmp_path, extra=extra, sign_with=priv)
    assert _verify(d, pub).refusal is Refusal.MANIFEST_INVALID


def test_non_utf8_manifest_is_manifest_invalid(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    _, pub = keys
    d = tmp_path / "bad"
    d.mkdir()
    (d / "manifest.yaml").write_bytes(b"name: demo\n\xff\xfe\n")
    assert _verify(d, pub).refusal is Refusal.MANIFEST_INVALID


def test_dangling_symlink_file_is_refused(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    (d / "evil.py").symlink_to("/nonexistent/evil")
    r = _verify(d, pub)
    assert r.refusal is Refusal.INTEGRITY_MISMATCH
    assert "evil.py" in r.detail


def test_symlinked_dir_is_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    (d / "pkg").symlink_to(tmp_path)
    r = _verify(d, pub)
    assert r.refusal is Refusal.INTEGRITY_MISMATCH
    assert "pkg" in r.detail


def test_symlinked_manifest_is_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    real = tmp_path / "real.yaml"
    (d / "manifest.yaml").rename(real)
    (d / "manifest.yaml").symlink_to(real)
    assert _verify(d, pub).refusal is Refusal.INTEGRITY_MISMATCH


def test_expected_files_match_passes(tmp_path: Path, keys: tuple[str, str]) -> None:
    from core.plugin_updates.release_manifest import file_digests

    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    assert _verify(d, pub, expected_files=file_digests(d)).ok


def test_expected_files_mismatch_is_refused_first(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    from core.plugin_updates.release_manifest import file_digests

    priv, pub = keys
    d = _release(tmp_path, sign_with=priv)
    listed = file_digests(d)
    (d / "README.md").write_text("added after listing\n")  # outside the hash surface
    result = _verify(d, pub, expected_files=listed)
    assert (
        result.refusal is Refusal.FILES_MISMATCH and result.detail == "extra: README.md"
    )
    assert _verify(d, pub).ok  # without a list, only the hash surface is checked
