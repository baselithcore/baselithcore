"""Create every enabled plugin's schema, at deploy time, as the table owner.

The serving process must not hold DDL. A deployment that isolates tenants at
the database connects as a least-privilege role — ``NOSUPERUSER NOBYPASSRLS``,
owning nothing — because PostgreSQL exempts a superuser, a ``BYPASSRLS`` role
and a table's *owner* from that table's own row-level-security policy. A plugin
that builds its schema from the serving process cannot run there: it fails on
``permission denied for schema public``, or, once granted that, on ``must be
owner of table …``, which no grant fixes.

So the schema work runs here instead, with the owner credential, in the same
slot the Alembic migration Job occupies — and the application starts afterwards
holding nothing but DML.

    baselith plugin schema-init                # every enabled plugin
    baselith plugin schema-init --plugin auth  # just one

The exit code is the number of plugins that failed, so a deploy Job stops on it
rather than starting an application against a half-built schema.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.cli.ui import print_error, print_info, print_success, print_warning

__all__ = ["schema_init"]


def schema_init(
    plugin_name: str | None = None,
    *,
    json_output: bool = False,
) -> int:
    """Run ``init_schema()`` on the enabled plugins.

    Args:
        plugin_name: Restrict the run to one plugin, by registry name.
        json_output: Emit a machine-readable summary instead of prose.

    Returns:
        The number of plugins whose schema step raised.
    """
    return asyncio.run(_run(plugin_name, json_output=json_output))


async def _run(plugin_name: str | None, *, json_output: bool) -> int:
    from core.db.connection import system_tenant_scope

    candidates = await _load_enabled(plugin_name)
    if not candidates:
        target = f" matching {plugin_name!r}" if plugin_name else ""
        print_warning(f"No enabled plugin{target} to initialise.")
        return 0

    done: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []

    for name, plugin, config in candidates:
        if not _declares_schema(plugin):
            skipped.append(name)
            continue
        try:
            # Deploy-time work has no request behind it, so no tenant is bound;
            # under DB_RLS_ENABLED the pool refuses such a checkout unless the
            # caller declares itself system work, which this is.
            with system_tenant_scope():
                await plugin.init_schema(config)
            done.append(name)
        except Exception as exc:
            failed.append((name, f"{type(exc).__name__}: {exc}"))

    _report(done, skipped, failed, json_output=json_output)
    return len(failed)


def _declares_schema(plugin: Any) -> bool:
    """Whether the plugin overrides the no-op :meth:`Plugin.init_schema`."""
    from core.plugins.interface import Plugin

    return type(plugin).init_schema is not Plugin.init_schema


async def _load_enabled(
    plugin_name: str | None,
) -> list[tuple[str, Any, dict[str, Any]]]:
    """Instantiate the enabled plugins without initialising them.

    ``initialize=False`` is the point: this is a deploy step, not a boot. The
    plugin's runtime resources are never opened, so a schema run cannot be
    derailed by a client it would have connected to.
    """
    from core.plugins.config_file import (
        match_config_key,
        plugin_enabled,
        read_plugin_configs,
    )
    from core.plugins.loader import PluginLoader
    from core.plugins.registry import PluginRegistry

    loader = PluginLoader(Path("plugins"), PluginRegistry())
    configs = read_plugin_configs()
    found: list[tuple[str, Any, dict[str, Any]]] = []
    for plugin_dir in loader.discover_plugins():
        plugin = await _load_cold(loader, plugin_dir)
        if plugin is None:
            continue
        name = plugin.metadata.name
        if plugin_name and name != plugin_name:
            continue
        # The enable-list matches on the directory name too, which is not
        # always the registry name (`coding_agent` registers as `coding-agent`).
        if not plugin_enabled(configs, plugin_dir.name, name):
            continue
        # An empty enable-list enables everything and names no block, so there
        # is simply no config to pass — not a reason to skip the plugin.
        key = match_config_key(configs, plugin_dir.name, name) if configs else None
        found.append((name, plugin, dict(configs.get(key, {})) if key else {}))
    return sorted(found, key=lambda item: item[0])


async def _load_cold(loader: Any, plugin_dir: Path) -> Any | None:
    """Load one plugin without initialising it, tolerating a bad directory."""
    try:
        return await loader.load_plugin(plugin_dir, initialize=False)
    except Exception as exc:
        print_warning(f"{plugin_dir.name}: could not load ({exc})")
        return None


def _report(
    done: list[str],
    skipped: list[str],
    failed: list[tuple[str, str]],
    *,
    json_output: bool,
) -> None:
    """Print the outcome, in prose or as JSON."""
    if json_output:
        print(
            json.dumps(
                {
                    "initialised": done,
                    "no_schema": skipped,
                    "failed": [{"plugin": n, "error": e} for n, e in failed],
                },
                indent=2,
            )
        )
        return

    for name in done:
        print_success(f"{name}: schema ready")
    if skipped:
        print_info(f"{len(skipped)} plugin(s) declare no schema of their own.")
    for name, error in failed:
        print_error(f"{name}: {error}")
    if failed:
        print_error(
            f"{len(failed)} plugin(s) failed — the application must not start "
            "against a half-built schema."
        )
    elif done:
        print_success(f"{len(done)} plugin schema(s) ready.")
