"""Spec §3: decide whether an unpacked release may be offered and installed.

Pure: reads only the directory it is given. Order matters — cheap identity
checks first, the hash and signature next, environment fit last — and the
first failure is the reported one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path

import yaml
from packaging.requirements import InvalidRequirement, Requirement

from core.plugins._links import first_symlink
from core.plugins.integrity import compute_plugin_hash, find_manifest_file
from core.plugins.signing import verify_plugin_signature
from core.plugins.version import SemanticVersion, check_plugin_compatibility

from .models import Refusal, VerificationResult


def requirement_satisfied(req: str) -> bool:
    """Whether the running environment already satisfies a PEP 508 requirement."""
    try:
        parsed = Requirement(req)
    except InvalidRequirement:
        return False
    if parsed.marker is not None and not parsed.marker.evaluate():
        return True
    try:
        installed = dist_version(parsed.name)
    except PackageNotFoundError:
        return False
    return parsed.specifier.contains(installed, prereleases=True)


def _refuse(refusal: Refusal, detail: str = "") -> VerificationResult:
    return VerificationResult(refusal=refusal, detail=detail)


def _string_list(value: object) -> list[str] | None:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def verify_release(
    plugin_dir: Path,
    *,
    expected_name: str,
    expected_version: str,
    installed_version: str | None,
    core_version: str,
    trusted_keys: Sequence[str],
    requirement_ok: Callable[[str], bool] = requirement_satisfied,
) -> VerificationResult:
    """Apply every spec §3 rule to an unpacked release; first failure wins."""
    link = first_symlink(plugin_dir)
    if link is not None:
        return _refuse(Refusal.INTEGRITY_MISMATCH, f"symlink: {link}")
    manifest_path = find_manifest_file(plugin_dir)
    if manifest_path is None:
        return _refuse(Refusal.MANIFEST_INVALID, "no manifest")
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        return _refuse(Refusal.MANIFEST_INVALID, str(exc))
    if not isinstance(data, dict):
        return _refuse(Refusal.MANIFEST_INVALID, "manifest is not a mapping")
    if data.get("name") != expected_name:
        return _refuse(Refusal.NAME_MISMATCH, f"manifest name {data.get('name')!r}")
    declared_version = str(data.get("version", ""))
    if declared_version != expected_version:
        return _refuse(Refusal.VERSION_MISMATCH, f"manifest version {declared_version}")
    try:
        parsed_version = SemanticVersion(declared_version)
    except ValueError:
        return _refuse(
            Refusal.MANIFEST_INVALID, f"invalid version {declared_version!r}"
        )
    if parsed_version.prerelease:
        return _refuse(Refusal.MANIFEST_INVALID, "prerelease")
    min_core = data.get("min_core_version")
    max_core = data.get("max_core_version")
    for bound in (min_core, max_core):
        if bound is not None and not isinstance(bound, str):
            return _refuse(
                Refusal.MANIFEST_INVALID, "core version bound must be a string"
            )
    dependencies = _string_list(data.get("python_dependencies"))
    if dependencies is None:
        return _refuse(
            Refusal.MANIFEST_INVALID, "python_dependencies must be a list of strings"
        )
    declared_hash = str(data.get("integrity_sha256") or "")
    if not declared_hash:
        return _refuse(Refusal.SIGNATURE_INVALID, "no integrity_sha256")
    if compute_plugin_hash(plugin_dir) != declared_hash:
        return _refuse(Refusal.INTEGRITY_MISMATCH)
    if not trusted_keys:
        return _refuse(Refusal.NO_TRUSTED_KEYS)
    signature = str(data.get("signature_ed25519") or "")
    if not signature or not verify_plugin_signature(
        declared_hash, signature, list(trusted_keys)
    ):
        return _refuse(Refusal.SIGNATURE_INVALID)
    if installed_version is not None:
        try:
            installed = SemanticVersion(installed_version)
        except ValueError:
            return _refuse(
                Refusal.NOT_NEWER,
                f"unparseable installed version {installed_version!r}",
            )
        if not parsed_version > installed:
            return _refuse(Refusal.NOT_NEWER, f"installed {installed_version}")
    problems = check_plugin_compatibility(
        core_version=core_version,
        min_core_version=min_core,
        max_core_version=max_core,
    )
    if problems:
        return _refuse(Refusal.INCOMPATIBLE_CORE, "; ".join(problems))
    missing = [
        str(r)
        for r in data.get("python_dependencies") or []
        if not requirement_ok(str(r))
    ]
    if missing:
        return _refuse(Refusal.NEEDS_ENVIRONMENT_UPDATE, ", ".join(missing))
    return VerificationResult()


__all__ = ["requirement_satisfied", "verify_release"]
