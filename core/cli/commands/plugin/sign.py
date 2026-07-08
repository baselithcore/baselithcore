"""``baselith plugin sign`` — populate ``integrity_sha256`` in a plugin manifest.

Computes the SHA-256 over the plugin's executable surface (`*.py`/`*.pyi`
plus build/packaging files: `pyproject.toml`, `setup.cfg`, `MANIFEST.in`,
`requirements*.txt`) and writes it into the top-level manifest
(yaml/yml/json). Pair with the runtime check
``BASELITH_REQUIRE_SIGNED_PLUGINS=true`` to refuse unsigned plugins at
load time.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from rich.console import Console

from core.plugins.integrity import compute_plugin_hash

console = Console()

_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")


def _locate_manifest(plugin_dir: Path) -> Path | None:
    for name in _MANIFEST_NAMES:
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
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
    console.print(f"[cyan]Computed integrity_sha256:[/cyan] {digest}")

    if check_only:
        return 0

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        if manifest_path.suffix == ".json":
            data = json.loads(raw) or {}
            data["integrity_sha256"] = digest
            manifest_path.write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8"
            )
        else:
            data = yaml.safe_load(raw) or {}
            data["integrity_sha256"] = digest
            manifest_path.write_text(
                yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
            )
    except Exception as exc:
        console.print(
            f"[red]Failed to update {manifest_path.name}: {type(exc).__name__}: {exc}[/red]"
        )
        return 1

    console.print(f"[green]Wrote integrity_sha256 to {manifest_path}[/green]")
    return 0
