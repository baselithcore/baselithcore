"""``baselith plugin sign`` — stamp the supply-chain fields into a manifest.

Computes the SHA-256 over the plugin's executable surface — source, build and
packaging files, ``SKILL.md`` bodies, native modules and shipped front-end
assets, plus (from hash surface V5) the canonicalised manifest itself — and
writes it into the top-level manifest along with the surface version. Pair with
``BASELITH_REQUIRE_SIGNED_PLUGINS=true`` to refuse unsigned plugins at load
time.

The manifest is rewritten line-by-line (see
:mod:`core.plugins.manifest_rewrite`), so the comments that explain a plugin's
declared permissions survive — an earlier version round-tripped the YAML and
silently deleted every one of them.

Publisher signature: when ``BASELITH_PLUGIN_SIGNING_KEY`` holds a hex Ed25519
private key the digest is signed and ``signature_ed25519`` is refreshed.
Without it, any existing signature is *blanked*, because a signature over a
hash the manifest no longer declares reads as signed and verifies as nothing.
``scripts/sign_changed_plugins.py`` applies the same policy from pre-commit.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from core.plugins.integrity import (
    CURRENT_HASH_SURFACE,
    MANIFEST_FILENAMES,
    compute_plugin_hash,
    read_declared_surface,
)
from core.plugins.manifest_rewrite import (
    HASH_KEY,
    SIGNATURE_KEY,
    set_manifest_fields,
    stale_signature_present,
    supply_chain_fields,
)

console = Console()

#: Env var holding the hex Ed25519 private key. Never an argument: a key on
#: argv leaks into shell history and every process listing on the box.
SIGNING_KEY_ENV = "BASELITH_PLUGIN_SIGNING_KEY"


def _locate_manifest(plugin_dir: Path) -> Path | None:
    for name in MANIFEST_FILENAMES:
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
    return None


def _read_manifest(manifest_path: Path) -> dict[str, object]:
    raw = manifest_path.read_text(encoding="utf-8")
    data = (
        json.loads(raw) if manifest_path.suffix == ".json" else yaml.safe_load(raw)
    ) or {}
    if not isinstance(data, dict):
        raise ValueError("manifest is not a mapping")
    return data


def _report_declared_surface(plugin_dir: Path) -> None:
    """Say what the manifest currently claims its digest was computed under.

    An operator running ``plugin sign`` on a plugin last signed at an older
    surface is about to widen what the signature covers; naming the old
    generation makes that visible instead of silent.
    """
    declared = read_declared_surface(plugin_dir)
    current = int(CURRENT_HASH_SURFACE)
    if declared is None:
        console.print(
            f"[yellow]No hash_surface_version declared[/yellow] — stamping {current}."
        )
    elif declared < current:
        console.print(
            f"[yellow]Manifest declares hash surface {declared}[/yellow]; the new "
            f"digest is computed over surface {current}, which covers strictly more."
        )


def _resolve_signature(
    digest: str, data: Mapping[str, Any], plugin_dir: Path
) -> str | None:
    """Decide what to write into ``signature_ed25519``.

    Args:
        digest: The freshly computed integrity hash.
        data: The parsed manifest as it stands on disk.
        plugin_dir: The plugin being signed, so the remediation the warning
            prints is a command that can be pasted as-is.

    Returns:
        The fresh hex signature when a signing key is configured, ``""`` to
        blank a signature the new digest has invalidated, and ``None`` to leave
        the field alone — including when the digest did not move, so re-signing
        an unchanged tree never strips a valid signature.
    """
    private_key_hex = os.environ.get(SIGNING_KEY_ENV, "").strip()
    if private_key_hex:
        from core.plugins.signing import sign_plugin_hash

        return sign_plugin_hash(digest, private_key_hex)
    if stale_signature_present(data, digest):
        # Name the *cause* and re-run this command. The earlier wording sent
        # the operator to `scripts/sign_plugin_ed25519.py`, which reads the
        # same unset key and exits 1 for the same reason — so following the
        # advice landed them exactly back here, one command poorer.
        console.print(
            f"[yellow]WARNING:[/yellow] {SIGNING_KEY_ENV} is not set, so "
            f"{SIGNATURE_KEY} was BLANKED rather than left attesting a hash "
            "this manifest no longer declares. Before publishing, set the "
            "signing key and re-run:"
        )
        # soft_wrap keeps each command on one logical line: Rich otherwise
        # hard-wraps at the terminal width and inserts a newline mid-path,
        # which is precisely what makes a "just paste this" remedy unpastable.
        console.print(
            f"  export {SIGNING_KEY_ENV}=<hex ed25519 private key>",
            soft_wrap=True,
            highlight=False,
        )
        console.print(
            f"  baselith plugin sign {plugin_dir}", soft_wrap=True, highlight=False
        )
        return ""
    return None


def sign_plugin(path: str, *, check_only: bool = False) -> int:
    """Implement the ``plugin sign`` subcommand."""
    plugin_dir = Path(path).resolve()
    if not plugin_dir.is_dir():
        console.print(f"[red]Not a directory: {plugin_dir}[/red]")
        return 1

    manifest_path = _locate_manifest(plugin_dir)
    if manifest_path is None:
        console.print(f"[red]No manifest.(yaml|yml|json) found in {plugin_dir}[/red]")
        return 1

    digest = compute_plugin_hash(plugin_dir)
    console.print(f"[cyan]Computed {HASH_KEY}:[/cyan] {digest}")
    _report_declared_surface(plugin_dir)

    if check_only:
        return 0

    try:
        data = _read_manifest(manifest_path)
        fields = supply_chain_fields(
            data,
            digest,
            signature=_resolve_signature(digest, data, plugin_dir),
            surface_version=int(CURRENT_HASH_SURFACE),
        )
        set_manifest_fields(manifest_path, fields)
    except Exception as exc:
        console.print(
            f"[red]Failed to update {manifest_path.name}: {type(exc).__name__}: {exc}[/red]"
        )
        return 1

    if not fields:
        console.print(f"[green]{manifest_path} already up to date[/green]")
        return 0
    console.print(
        f"[green]Wrote {', '.join(sorted(fields))} to {manifest_path}[/green]"
    )
    return 0
