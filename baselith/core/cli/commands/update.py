"""``baselith update``: the update report, a check, a pre-flight, and core installs.

``status`` prints the last saved report as the server serves it (exit 0 even
when none exists yet); ``check`` runs a check now and prints the result (exit 1
when the check itself failed: see :func:`check_failed`); neither installs,
removes or restarts anything, and the background checker is never started;
``check`` does contact GitHub, save the update cache and may announce a new
update on the event bus. ``preflight <subject>`` prints whether an update can
be installed now, one ``[level] code: message`` line per item (exit 1 when any
item blocks). ``apply core --version V`` and ``rollback core`` request a
one-click core install or rollback from the host updater (see
:mod:`._update_apply`); plugins keep ``baselith plugin-updater request``.
``policy get|set`` shows or writes the auto-apply policy, ``runs`` lists the
runs of every subject and ``resolve`` frees a ``rollback_failed`` one (see
:mod:`._update_policy`).
Exit codes: 0 ok, 1 refused or operational failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import asyncio

from baselith.core.cli.commands._update_policy import (
    add_policy_parsers,
    run_policy_command,
)
from baselith.core.cli.commands._update_preflight import run_preflight
from baselith.core.cli.commands._update_report import (
    check_failed,
    load_served_report,
    release_error,
)
from baselith.core.config.plugin_updates import get_plugin_update_config
from baselith.core.plugin_updates.models import (
    CheckReport,
    Refusal,
    SystemUpdate,
    UpdateCandidate,
)
from baselith.core.plugin_updates.service import PluginUpdateService


def register_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register ``update`` and its operations (status to resolve)."""
    parser = subparsers.add_parser(
        "update",
        help="Update report, pre-flight, core installs and the auto-apply policy",
        description="Report which core and plugin updates are available, check "
        "whether one can be installed, request a core install or rollback from "
        "the host updater, set the policy that installs patch releases on its "
        "own in a maintenance window, and list or resolve runs. status, "
        "preflight, policy get and runs are read-only; check contacts GitHub "
        "and refreshes the cache; apply and rollback create a run the updater "
        "executes (it restarts the API); policy set changes what the updater "
        "installs by itself.",
        formatter_class=formatter_class,
    )
    sub = parser.add_subparsers(dest="update_command", title="Operations")
    for name, text in (
        ("status", "Print the report of the last check (read-only)"),
        ("check", "Run a check now (contacts GitHub) and print the report"),
    ):
        # --format comes from the global flag, injected on every subcommand.
        sub.add_parser(name, help=text, formatter_class=formatter_class)
    pre = sub.add_parser(
        "preflight",
        help="Whether an update can be installed now (read-only; exit 1 on a block)",
        formatter_class=formatter_class,
    )
    pre.add_argument(
        "subject", help="core, or a plugin name (plugin:<name> also works)"
    )
    pre.add_argument(
        "--version",
        dest="target_version",
        default=None,
        help="Target release (default: the latest known one)",
    )
    _add_install_parsers(sub, formatter_class)
    add_policy_parsers(sub, formatter_class)
    return parser


def _add_install_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    formatter_class: type[argparse.HelpFormatter],
) -> None:
    apply = sub.add_parser(
        "apply",
        help="Install the latest signed core release (the updater runs it)",
        formatter_class=formatter_class,
    )
    apply.add_argument(
        "subject", help="core (plugins: baselith plugin-updater request)"
    )
    apply.add_argument("--version", dest="target_version", required=True)
    apply.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    back = sub.add_parser(
        "rollback",
        help="Return the core to the commit its last one-click update replaced",
        formatter_class=formatter_class,
    )
    back.add_argument(
        "subject", help="core (plugins: baselith plugin-updater rollback)"
    )
    back.add_argument("--yes", action="store_true", help="Do not ask for confirmation")


def _load_cached() -> CheckReport | None:
    """The last saved report as served, or None when absent or unreadable."""
    return load_served_report()


async def _check_now() -> CheckReport:
    """Run one check (never ``start()``: no background loop)."""
    return await PluginUpdateService(get_plugin_update_config()).check_now()


def _core_lines(system: SystemUpdate | None, label: str = "core") -> list[str]:
    if system is None:
        return [f"{label}: not checked"]
    failure = release_error(system)
    if failure and system.latest is None:
        line = f"{label} {system.installed_version}: check failed ({failure})"
    elif system.available and system.latest is not None:
        line = f"{label} {system.installed_version} -> {system.latest.version}"
    else:
        line = f"{label} {system.installed_version}: up to date"
    if system.security:  # an advisory can outlive, or precede, a fixed release
        line += f"  SECURITY ({system.severity or 'unknown'})"
    lines = [line]
    if failure and system.latest is not None:
        lines.append(f"  last check failed: {system.error}; showing last known state")
    elif system.error and not failure:
        lines.append(f"  note: {system.error}")
    return lines


def _candidate_line(candidate: UpdateCandidate) -> str:
    latest = candidate.latest.version if candidate.latest else "?"
    head = f"{candidate.plugin} {candidate.installed_version or '-'} -> {latest}"
    assets = candidate.signed_assets
    if assets is not None and assets.verified and assets.tarball_sha256:
        return f"{head}  sha256={assets.tarball_sha256}"
    return f"{head}  notice only (no signed package)"


def render_report(report: CheckReport) -> list[str]:
    """Return the text lines of a report (pure, no I/O)."""
    lines = [f"checked at {report.checked_at.isoformat()}"]
    if report.error:
        lines.append(f"error: {report.error}")
    if report.distribution is not None:  # absent in the public core
        lines.extend(_core_lines(report.distribution, "distribution"))
    lines.extend(_core_lines(report.system))
    available = [c for c in report.candidates if c.available]
    refused = [c for c in report.candidates if not c.available and _is_refused(c)]
    current = len(report.candidates) - len(available) - len(refused)
    lines.extend(f"  {_candidate_line(c)}" for c in available)
    for c in refused:
        detail = f" {c.detail}" if c.detail else ""
        lines.append(f"  {c.plugin}: refused: {c.refusal}{detail}")
    lines.append(f"{current} plugin(s) up to date")
    return lines


def _is_refused(candidate: UpdateCandidate) -> bool:
    return candidate.refusal not in (None, Refusal.NOT_NEWER)


def _emit(report: CheckReport, fmt: str) -> None:
    if fmt == "json":
        print(report.model_dump_json(indent=2))
    else:
        print("\n".join(render_report(report)))


def _run_check(fmt: str) -> int:
    if not get_plugin_update_config().enabled:
        print(
            "update checks are off: set CORE_UPDATE_REPO (core), "
            "DISTRIBUTION_UPDATE_REPO (distribution) and/or "
            "PLUGIN_UPDATE_SOURCES_FILE (plugins) to turn them on"
        )
        return 0
    try:
        report = asyncio.run(_check_now())
    except Exception as exc:
        print(f"update check failed: {type(exc).__name__}")
        return 1
    _emit(report, fmt)
    return 1 if check_failed(report) else 0


def run_update(command: str, kwargs: dict[str, object]) -> int:
    """Dispatch one ``update`` subcommand; its exit code."""
    fmt = "json" if kwargs.get("format") == "json" else "text"
    if command == "status":
        report = _load_cached()
        if report is None:
            if fmt == "json":
                print("null")
            else:
                print("no update report yet; run `baselith update check`")
            return 0
        _emit(report, fmt)
        return 0
    if command == "check":
        return _run_check(fmt)
    if command == "preflight":
        subject = str(kwargs.get("subject") or "").strip()
        if not subject:
            print("usage: baselith update preflight <subject> [--version V]")
            return 2
        target = kwargs.get("target_version")
        return run_preflight(subject, str(target) if target else None, fmt)
    if command in ("apply", "rollback"):
        return _run_install(command, kwargs)
    if command in ("policy", "runs", "resolve"):
        return run_policy_command(command, kwargs, fmt)
    print(f"unknown update command: {command}")
    return 2


def _run_install(command: str, kwargs: dict[str, object]) -> int:
    from baselith.core.cli.commands._update_apply import (
        run_apply_core,
        run_rollback_core,
    )

    subject = str(kwargs.get("subject") or "").strip()
    if subject != "core":
        print(
            f"update {command} handles the core only; for a plugin use "
            f"`baselith plugin-updater {'request' if command == 'apply' else 'rollback'}`"
        )
        return 2
    yes = bool(kwargs.get("yes"))
    if command == "rollback":
        return run_rollback_core(yes=yes)
    return run_apply_core(str(kwargs.get("target_version") or ""), yes=yes)


__all__ = ["register_parser", "render_report", "run_update"]
