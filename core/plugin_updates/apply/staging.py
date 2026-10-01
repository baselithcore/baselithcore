"""Stage a verified release into the overlay store.

The tarball is read through ONE ``O_NOFOLLOW`` descriptor into a private copy
whose SHA-256 must equal the pin taken when the run was requested; everything
after reads that copy, so replacing the cached tarball mid-run changes
nothing. The signed ``release.json`` is re-checked, the tree is unpacked
(bounded, no links, no escapes) and verified exactly as the checker verifies
it, the old tree's ``.env`` and declared runtime state are carried over (never
over a shipped file, never through a link), and the result is re-verified as
an overlay entry before one atomic rename promotes it to
``.store/<plugin>-<version>``. The staging directory never outlives the call.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess  # nosec B404 - fixed argv, no shell
import tarfile
import tempfile
import zlib
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.plugins._overlay_guard import overlay_refusal
from core.plugins.overlay import STORE_DIRNAME, verify_overlay_entry
from core.plugins.runtime_state import (
    declared_runtime_state_paths,
    undeclared_runtime_files,
)

from ..archive import MAX_ARCHIVE_MEMBERS, unpack_release
from ..release_manifest import ReleaseManifestError, file_digests, parse_files
from ..signed_assets import manifest_refusal
from ..verifier import verify_release
from ._carry import CarryError, carry_state
from ._private_copy import StagingError, private_copy
from .store import _RUN_ID
from .swap import current_target


@dataclass(frozen=True)
class StagedRelease:
    """A verified release promoted to ``.store/<name>``."""

    name: str
    path: Path
    files: dict[str, str]


def _git_ls_files(plugin_dir: Path) -> list[str]:
    git = shutil.which("git")
    if git is None:
        raise FileNotFoundError("git")
    out = subprocess.run(  # nosec B603 - fixed argv, no shell
        [git, "-C", str(plugin_dir), "ls-files", "-z", "--", "."],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return [p for p in out.stdout.decode("utf-8").split("\0") if p]


def known_files_of(
    current_dir: Path,
    *,
    overlay_root: Path | None,
    git_ls_files: Callable[[Path], list[str]] | None = None,
) -> set[str]:
    """Files the currently installed tree shipped.

    An overlay store entry answers from its sidecar
    ``.store/<entry>.release.json``; a source checkout from ``git ls-files``;
    with neither (a pip host) every file present counts as shipped, so
    undeclared state cannot be detected there (documented limitation).

    Raises:
        StagingError: ``undeclared_runtime_state`` when a store entry has no
            readable release record (fail closed: its state cannot be told
            apart from what it shipped).
    """
    resolved = current_dir.resolve()
    if overlay_root is not None and resolved.parent == (
        (overlay_root / STORE_DIRNAME).resolve()
    ):
        sidecar = resolved.parent / f"{resolved.name}.release.json"
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            return set(parse_files(meta, max_entries=MAX_ARCHIVE_MEMBERS))
        except (OSError, ValueError) as exc:
            raise StagingError(
                "undeclared_runtime_state", f"no release record for {resolved.name}"
            ) from exc
    try:
        return set((git_ls_files or _git_ls_files)(current_dir))
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return set(file_digests(current_dir))


def _writable_then_retry(
    func: Callable[..., Any], path: str, _exc: BaseException
) -> None:
    """``rmtree`` hook: a read-only directory from the tarball still goes."""
    parent = os.path.dirname(path)
    try:
        os.chmod(parent, stat.S_IRWXU)
        if os.path.isdir(path) and not os.path.islink(path):
            os.chmod(path, stat.S_IRWXU)
        func(path)
    except OSError:
        pass


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, onexc=_writable_then_retry)


def _check_meta(
    meta: Mapping[str, Any], plugin: str, version: str, pinned: str, keys: Sequence[str]
) -> dict[str, str]:
    if meta.get("name") != plugin or str(meta.get("version")) != version:
        raise StagingError("verification_failed", "release.json names another release")
    refused = manifest_refusal(dict(meta), keys)
    if refused is not None:
        raise StagingError("verification_failed", f"{refused[0].value}: {refused[1]}")
    if str(meta.get("tarball_sha256", "")).lower() != pinned.lower():
        raise StagingError("artifact_checksum", "release.json pins another tarball")
    try:
        return parse_files(meta, max_entries=MAX_ARCHIVE_MEMBERS)
    except ReleaseManifestError as exc:  # pragma: no cover - manifest_refusal parses
        raise StagingError("verification_failed", str(exc)) from exc


def stage_release(
    *,
    plugin: str,
    version: str,
    tarball: Path,
    meta: Mapping[str, Any],
    pinned_sha256: str,
    overlay_root: Path,
    current_dir: Path | None,
    known_files: Collection[str],
    installed_version: str | None,
    bundled_root: Path | None,
    core_version: str,
    trusted_keys: Sequence[str],
    run_id: str,
    max_unpacked_bytes: int,
) -> StagedRelease:
    """Verify ``tarball`` and promote it to ``.store/<plugin>-<version>``.

    Raises:
        StagingError: ``code`` is one of ``artifact_checksum``,
            ``verification_failed``, ``undeclared_runtime_state`` or
            ``overlay_refused``; nothing is promoted and the staging
            directory is removed.
    """
    if not _RUN_ID.fullmatch(run_id):
        raise StagingError("overlay_refused", f"invalid run id {run_id[:80]!r}")
    store = overlay_root / STORE_DIRNAME
    if store.is_symlink() or overlay_root.is_symlink():
        raise StagingError("overlay_refused", f"{STORE_DIRNAME} is a symbolic link")
    store.mkdir(parents=True, exist_ok=True)
    staging = store / f".staging-{run_id}"
    _remove(staging)
    staging.mkdir()
    try:
        return _stage(
            plugin,
            version,
            tarball,
            meta,
            pinned_sha256,
            overlay_root,
            store,
            staging,
            current_dir,
            known_files,
            installed_version,
            bundled_root,
            core_version,
            trusted_keys,
            run_id,
            max_unpacked_bytes,
        )
    except StagingError as exc:
        detail = _relative(exc.detail, overlay_root, current_dir)
        raise StagingError(exc.code, detail) from exc.__cause__
    finally:
        _remove(staging)


def _relative(detail: str, overlay_root: Path, current_dir: Path | None) -> str:
    """Name paths relative to the overlay root, never absolute.

    An installed tree outside the overlay (the bundled copy) shows as
    ``<installed>``.
    """
    subs = {str(r): "" for r in (overlay_root, overlay_root.resolve())}
    if current_dir is not None:
        for r in (current_dir, current_dir.resolve()):
            subs.setdefault(str(r), "<installed>/")
    for root in sorted(subs, key=len, reverse=True):
        detail = detail.replace(root + os.sep, subs[root])
        detail = detail.replace(root, subs[root].rstrip("/") or ".")
    return detail


def _stage(
    plugin: str,
    version: str,
    tarball: Path,
    meta: Mapping[str, Any],
    pinned_sha256: str,
    overlay_root: Path,
    store: Path,
    staging: Path,
    current_dir: Path | None,
    known_files: Collection[str],
    installed_version: str | None,
    bundled_root: Path | None,
    core_version: str,
    trusted_keys: Sequence[str],
    run_id: str,
    max_unpacked_bytes: int,
) -> StagedRelease:
    files = _check_meta(meta, plugin, version, pinned_sha256, trusted_keys)
    copy = staging / "release.tar.gz"
    private_copy(tarball, copy, pinned_sha256)
    new = _unpack_and_verify(
        copy,
        staging,
        plugin,
        version,
        files,
        installed_version,
        core_version,
        trusted_keys,
        max_unpacked_bytes,
    )
    if current_dir is not None and current_dir.is_dir():
        _carry_over(current_dir, new, known_files, files)
    reason = verify_overlay_entry(new, trusted_keys) or overlay_refusal(
        new, bundled_root, core_version
    )
    if reason is not None:
        raise StagingError("overlay_refused", reason)
    return _promote(overlay_root, store, plugin, version, new, meta, files, run_id)


def _unpack_and_verify(
    copy: Path,
    staging: Path,
    plugin: str,
    version: str,
    files: Mapping[str, str],
    installed: str | None,
    core_version: str,
    keys: Sequence[str],
    max_bytes: int,
) -> Path:
    unpacked = staging / "unpacked"
    try:
        unpack_release(copy, unpacked, max_bytes)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise StagingError(
            "verification_failed", f"unpack: {type(exc).__name__}"
        ) from exc
    entries = list(unpacked.iterdir())
    new = unpacked / plugin
    if len(entries) != 1 or new.is_symlink() or not new.is_dir():
        raise StagingError(
            "verification_failed", "tarball must hold one plugin directory"
        )
    result = verify_release(
        new,
        expected_name=plugin,
        expected_version=version,
        installed_version=installed,
        core_version=core_version,
        trusted_keys=keys,
        expected_files=files,
    )
    if not result.ok:
        refusal = result.refusal.value if result.refusal else "refused"
        raise StagingError("verification_failed", f"{refusal}: {result.detail}")
    return new


def _carry_over(
    current: Path, new: Path, known: Collection[str], shipped: Collection[str]
) -> None:
    try:
        declared = declared_runtime_state_paths(current)
    except (OSError, ValueError) as exc:  # the installed manifest is unreadable
        raise StagingError("verification_failed", f"installed manifest: {exc}") from exc
    try:
        undeclared = undeclared_runtime_files(current, known, declared)
    except OSError as exc:
        raise StagingError("undeclared_runtime_state", str(exc)) from exc
    if undeclared:
        more = f" (+{len(undeclared) - 10})" if len(undeclared) > 10 else ""
        raise StagingError(
            "undeclared_runtime_state", ", ".join(undeclared[:10]) + more
        )
    try:
        carry_state(current, new, shipped, declared)
    except CarryError as exc:
        raise StagingError("overlay_refused", str(exc)) from exc


def _set_aside(store: Path, final: Path, sidecar: Path, run_id: str) -> None:
    """Move an entry (and its sidecar) into a fresh ``.trash-<run_id>-*`` dir."""
    trash = Path(tempfile.mkdtemp(prefix=f".trash-{run_id}-", dir=store))
    os.rename(final, trash / final.name)
    if sidecar.exists() or sidecar.is_symlink():
        os.rename(sidecar, trash / sidecar.name)


def _promote(
    overlay_root: Path,
    store: Path,
    plugin: str,
    version: str,
    new: Path,
    meta: Mapping[str, Any],
    files: dict[str, str],
    run_id: str,
) -> StagedRelease:
    """Rename ``new`` to ``.store/<plugin>-<version>``, then write its sidecar.

    The caller holds the per-plugin run lock (``RunStore``), so no other run
    stages this plugin concurrently; the existence re-check right before the
    rename still refuses an entry that appeared meanwhile, because
    ``os.rename`` over an empty directory would succeed silently. A sidecar
    is only ever written next to an entry that exists: a failed rename leaves
    none, and a failed sidecar write moves the entry back into staging.
    """
    name = f"{plugin}-{version}"
    final = store / name
    sidecar = store / f"{name}.release.json"
    if final.exists() or final.is_symlink():
        try:
            live = current_target(overlay_root, plugin)
        except ValueError as exc:
            raise StagingError("overlay_refused", str(exc)) from exc
        if live == name:
            raise StagingError("overlay_refused", f"{name} is the live entry")
        # Never reused, never overwritten: set aside for prune_store to clear.
        _set_aside(store, final, sidecar, run_id)
    elif sidecar.exists() or sidecar.is_symlink():
        _set_aside_sidecar(store, sidecar, run_id)
    if final.exists() or final.is_symlink():
        raise StagingError("overlay_refused", f"{name} appeared while staging")
    os.rename(new, final)
    tmp = store / f".{name}.release.json.tmp-{run_id}"
    try:
        tmp.unlink(missing_ok=True)
        tmp.write_text(json.dumps(dict(meta), sort_keys=True), encoding="utf-8")
        os.replace(tmp, sidecar)
    except BaseException:
        tmp.unlink(missing_ok=True)
        os.rename(final, new)  # back into staging, which the caller removes
        raise
    _fsync_dir(store)
    return StagedRelease(name=name, path=final, files=files)


def _set_aside_sidecar(store: Path, sidecar: Path, run_id: str) -> None:
    trash = Path(tempfile.mkdtemp(prefix=f".trash-{run_id}-", dir=store))
    os.rename(sidecar, trash / sidecar.name)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = [
    "StagedRelease",
    "StagingError",
    "known_files_of",
    "private_copy",
    "stage_release",
]
