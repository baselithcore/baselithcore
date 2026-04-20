"""Manifest & packaging invariants.

Marketplace publish (see ``plugins/baselithbot/docs/publishing.md``) expects
specific fields to be present and well-formed. This test pins them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "plugins" / "baselithbot"


def _load_manifest() -> dict:
    manifest_path = PLUGIN_ROOT / "manifest.yaml"
    assert manifest_path.exists(), f"manifest.yaml missing: {manifest_path}"
    with manifest_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict), "manifest.yaml must be a mapping"
    return data


class TestManifestFields:
    def test_required_identity_fields(self) -> None:
        manifest = _load_manifest()
        assert manifest["id"] == "baselithbot"
        assert manifest["name"] == "baselithbot"
        assert manifest["entry_point"] == "plugin:BaselithbotPlugin"

    def test_semver_version(self) -> None:
        manifest = _load_manifest()
        version = manifest["version"]
        parts = version.split(".")
        assert len(parts) >= 3, f"version must be semver: {version}"
        assert all(p.split("-")[0].isdigit() for p in parts[:3])

    def test_license_declared(self) -> None:
        manifest = _load_manifest()
        assert manifest["license"] == "AGPL-3.0-only"

    def test_min_core_version(self) -> None:
        manifest = _load_manifest()
        assert manifest.get("min_core_version"), "min_core_version is required"

    def test_readiness_is_supported(self) -> None:
        manifest = _load_manifest()
        assert manifest["readiness"] in {"alpha", "beta", "stable"}

    def test_dependencies_pinned(self) -> None:
        manifest = _load_manifest()
        for dep in manifest.get("python_dependencies", []):
            assert any(op in dep for op in ("==", ">=", "~=", ">", "<")), (
                f"runtime dep without a version constraint: {dep}"
            )


class TestReleaseHygiene:
    """Required files for marketplace publish (docs/publishing.md §1)."""

    @pytest.mark.parametrize(
        "filename",
        [
            "LICENSE",
            "README.md",
            "CHANGELOG.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "requirements.txt",
            "pyproject.toml",
            "manifest.yaml",
            "plugin.py",
        ],
    )
    def test_file_present(self, filename: str) -> None:
        assert (PLUGIN_ROOT / filename).exists(), f"missing release file: {filename}"

    def test_requirements_mirrors_manifest(self) -> None:
        manifest = _load_manifest()
        manifest_deps = {
            dep.split(">=", 1)[0].split("==", 1)[0].split("~=", 1)[0].strip().lower()
            for dep in manifest.get("python_dependencies", [])
        }
        req_text = (PLUGIN_ROOT / "requirements.txt").read_text(encoding="utf-8")
        req_pkgs = {
            line.split(">=", 1)[0].split("==", 1)[0].strip().lower()
            for line in req_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        missing = manifest_deps - req_pkgs
        assert not missing, f"requirements.txt missing deps: {missing}"
        assert "baselith-core" in req_pkgs, "requirements.txt must pin baselith-core"
