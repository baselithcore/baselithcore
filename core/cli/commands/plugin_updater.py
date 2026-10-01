"""``baselith plugin-updater``: run the one-click plugin updater and operate its runs.

``serve`` is the systemd service; the other subcommands are for the operator
on the host (``request`` and ``rollback`` create runs that need no second
approval: whoever holds a shell on the host is already trusted with it).
Exit codes: 0 ok, 1 refused or failed, 2 invalid input or a configuration the
updater cannot start with (the unit does not restart on 2).

The command never imports plugin code and never starts an error reporter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pwd
import re
import signal
import sys
from pathlib import Path

from core.config.plugin_update_apply import get_update_apply_config
from core.plugin_updates.apply.models import RunKind, RunState
from core.plugin_updates.apply.store import RunConflict, RunStateConflict, RunStore
from core.plugin_updates.apply.updater import UpdaterRefused, build_executor, serve

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

logger = logging.getLogger(__name__)


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register the ``plugin-updater`` command and its five subcommands."""
    parser = subparsers.add_parser(
        "plugin-updater",
        help="Run and operate the one-click plugin updater",
        description="The host service that installs signed plugin updates, "
        "restarts the API, health-checks it and rolls back.",
        formatter_class=formatter_class,
    )
    sub = parser.add_subparsers(dest="plugin_updater_command", title="Operations")
    sub.add_parser(
        "serve", help="Run the updater (systemd)", formatter_class=formatter_class
    )
    sub.add_parser(
        "status",
        help="Heartbeat and unfinished runs (--format json for JSON)",
        formatter_class=formatter_class,
    )
    request = sub.add_parser(
        "request", help="Queue an update run", formatter_class=formatter_class
    )
    request.add_argument("plugin")
    request.add_argument("--version", required=True)
    request.add_argument("--sha256", required=True, help="Pinned tarball SHA-256")
    rollback = sub.add_parser(
        "rollback",
        help="Roll back to the previous version",
        formatter_class=formatter_class,
    )
    rollback.add_argument("plugin")
    resolve = sub.add_parser(
        "resolve", help="Release a rollback_failed run", formatter_class=formatter_class
    )
    resolve.add_argument("run_id")
    return parser


def _err(message: str) -> None:
    print(f"baselith plugin-updater: {message}", file=sys.stderr)


def _operator() -> str:
    """The OS account running the command (not ``$USER``, which the caller sets)."""
    try:
        return f"cli:{pwd.getpwuid(os.getuid()).pw_name}"
    except KeyError:
        return f"cli:uid-{os.getuid()}"


def _audit(op: str, run_id: str, plugin: str, actor: str) -> None:
    logger.warning(
        "AUDIT | PLUGIN_UPDATE | %s run=%s plugin=%s by=%s", op, run_id, plugin, actor
    )


def _store() -> RunStore:
    return RunStore(get_update_apply_config().state_dir)


def _live_target(plugin: str) -> str | None:
    """The store entry the overlay link points at (None = bundled).

    Raises:
        ValueError: The overlay path is not a store link.
    """
    from core.plugin_updates.apply.swap import current_target
    from core.plugins.overlay import overlay_root

    root = overlay_root()
    return current_target(root, plugin) if root is not None else None


def _installed_version(plugin: str) -> str | None:
    """Version the API loads for ``plugin``: the overlay link's entry, else bundled."""
    from core.config.plugins import get_plugin_config
    from core.plugins._overlay_guard import bundled_version, read_manifest_mapping
    from core.plugins.overlay import STORE_DIRNAME, overlay_root

    root = overlay_root()
    try:
        target = _live_target(plugin)
    except ValueError:
        target = None
    if root is not None and target is not None:
        manifest = read_manifest_mapping(root / STORE_DIRNAME / target) or {}
        version = manifest.get("version")
        return str(version) if version else None
    return bundled_version(Path(get_plugin_config().plugins_path), plugin)


def _create(
    plugin: str,
    kind: RunKind,
    *,
    to_version: str | None,
    tarball_sha256: str | None,
    target: str | None = None,
) -> int:
    """Create an already-approved run (a host shell is trusted; D1 for rollbacks)."""
    actor = _operator()
    try:
        run = _store().create(
            kind=kind,
            plugin=plugin,
            from_version=_installed_version(plugin),
            requested_by=actor,
            approval_required=False,
            approval_ttl_seconds=get_update_apply_config().approval_ttl_seconds,
            to_version=to_version,
            tarball_sha256=tarball_sha256,
            target=target,
        )
    except ValueError as exc:
        _err(str(exc))
        return 2
    except RunConflict as exc:
        _err(f"run {exc.active_run_id} is active for {plugin}")
        return 1
    _audit(kind.value if kind is RunKind.ROLLBACK else "request", run.id, plugin, actor)
    print(run.id)
    return 0


def _request(kwargs: dict[str, object]) -> int:
    plugin, version = str(kwargs.get("plugin", "")), str(kwargs.get("version", ""))
    sha = str(kwargs.get("sha256", "")).lower()
    if not _SHA256.match(sha) or not version:
        _err("--sha256 must be 64 hex characters and --version is required")
        return 2
    return _create(plugin, RunKind.UPDATE, to_version=version, tarball_sha256=sha)


def _rollback(kwargs: dict[str, object]) -> int:
    plugin = str(kwargs.get("plugin", ""))
    try:
        last = _store().last_succeeded_update(plugin)
        live = _live_target(plugin)
    except ValueError as exc:
        _err(str(exc))
        return 2
    if last is None:
        _err(f"{plugin} has no successful update to roll back")
        return 1
    if live == last.previous_target:  # no restart for nothing
        print(f"{plugin} already runs its pre-update version; nothing to roll back")
        return 0
    return _create(
        plugin,
        RunKind.ROLLBACK,
        to_version=last.from_version,
        tarball_sha256=None,
        target=last.previous_target,
    )


def _resolve(kwargs: dict[str, object]) -> int:
    store = _store()
    run = store.get(str(kwargs.get("run_id", "")))
    if run is None:
        _err("no such run")
        return 1
    actor = _operator()
    try:
        store.transition(
            run.id,
            RunState.FAILED,
            actor=actor,
            message="resolved by operator",
            expect=RunState.ROLLBACK_FAILED,
        )
    except RunStateConflict as exc:
        _err(f"run {run.id} is {exc.actual.value}, not rollback_failed")
        return 1
    store.clear_expectation(run.plugin, run.id)
    _audit("resolve", run.id, run.plugin, actor)
    print(f"{run.id} resolved; {run.plugin} is free for new runs")
    return 0


def _status(fmt: str) -> int:
    store = _store()
    heartbeat = store.read_heartbeat()
    runs = store.unfinished()
    if fmt == "json":
        payload = {
            "heartbeat": heartbeat.model_dump(mode="json") if heartbeat else None,
            "unfinished": [r.model_dump(mode="json") for r in runs],
        }
        print(json.dumps(payload, indent=2))
        return 0
    if heartbeat is None:
        print("heartbeat: none (the updater has never run here)")
    else:
        print(
            f"heartbeat: {heartbeat.at.isoformat()} pid={heartbeat.pid} "
            f"core={heartbeat.core_version} enabled={heartbeat.enabled} "
            f"overlay_writable={heartbeat.overlay_writable} "
            f"restart_configured={heartbeat.restart_configured}"
        )
    print(f"unfinished runs: {len(runs)}")
    for r in runs:
        print(f"  {r.id} {r.plugin} {r.kind.value} {r.state.value} by={r.requested_by}")
    return 0


def _serve() -> int:
    from core._version import __version__

    config = get_update_apply_config()

    async def main() -> None:
        executor, root = build_executor(config)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(
            config=config,
            store=RunStore(config.state_dir),
            executor=executor,
            overlay_root=root,
            core_version=__version__,
            stop=stop,
        )

    try:
        asyncio.run(main())
    except UpdaterRefused as exc:
        _err(str(exc))
        return 2
    except Exception as exc:
        # Type name only: never a traceback (frames may hold the schema
        # owner's credentials). systemd restarts the unit; the next start
        # reconciles the interrupted run.
        _err(f"stopped on an unexpected {type(exc).__name__}; reconciling at restart")
        return 1
    return 0


def run_plugin_updater(command: str, kwargs: dict[str, object]) -> int:
    """Dispatch one ``plugin-updater`` subcommand; its exit code."""
    if command == "serve":
        return _serve()
    if command == "request":
        return _request(kwargs)
    if command == "rollback":
        return _rollback(kwargs)
    if command == "resolve":
        return _resolve(kwargs)
    return _status("json" if kwargs.get("format") == "json" else "text")


__all__ = ["register_parser", "run_plugin_updater"]
