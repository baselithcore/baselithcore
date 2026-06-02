#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Baselith-Core CLI.

Command-line interface for project scaffolding, plugin management,
and development utilities.
"""

import argparse
import os
import sys
import importlib
from typing import Any, Type, Optional
from importlib.metadata import version, PackageNotFoundError
from core.cli.handlers import (
    cmd_init,
    cmd_plugin,
    cmd_config,
    cmd_verify,
    cmd_run,
    cmd_shell,
    cmd_db,
    cmd_cache,
    cmd_queue,
    cmd_docs,
    cmd_doctor,
    cmd_test,
    cmd_lint,
    cmd_info,
)

try:
    from rich_argparse import RichHelpFormatter
    from rich.console import Console
    from rich.text import Text
    from rich.panel import Panel
    from rich.table import Table
    from rich.rule import Rule

    RichHelpFormatter.usage_markup = True
    RichHelpFormatter.group_name_formatter = str.upper
    RichHelpFormatter.styles["argparse.prog"] = "bold cyan"
    RichHelpFormatter.styles["argparse.args"] = "magenta"
    RichHelpFormatter.styles["argparse.metavar"] = "cyan"
    RichHelpFormatter.styles["argparse.help"] = "white"
    RichHelpFormatter.styles["argparse.text"] = "italic"
    RichHelpFormatter.styles["argparse.groups"] = "bold yellow"

    formatter_class: Type[argparse.HelpFormatter] = RichHelpFormatter
    Console_class: Optional[Type[Any]] = Console
except ImportError:
    formatter_class = argparse.HelpFormatter
    Console_class = None


# ──────────────────────────────────────────
# Command categories (display order)
# ──────────────────────────────────────────

COMMANDS_MAP = {
    "SCAFFOLDING": ["init", "plugin"],
    "DEVELOPMENT": ["run", "shell", "docs"],
    "SYSTEM & HEALTH": ["doctor", "verify", "info", "config"],
    "INFRASTRUCTURE": ["db", "cache", "queue"],
    "QUALITY & TESTS": ["test", "lint"],
}

# Flat list of all commands for imports
COMMANDS = [cmd for group in COMMANDS_MAP.values() for cmd in group]

# Mapping of commands to handlers. Using lambdas to allow patching in tests.
COMMAND_HANDLERS_MAP: dict[str, Any] = {
    "init": lambda *args, **kwargs: cmd_init(*args, **kwargs),
    "plugin": lambda *args, **kwargs: cmd_plugin(*args, **kwargs),
    "config": lambda *args, **kwargs: cmd_config(*args, **kwargs),
    "verify": lambda *args, **kwargs: cmd_verify(*args, **kwargs),
    "run": lambda *args, **kwargs: cmd_run(*args, **kwargs),
    "shell": lambda *args, **kwargs: cmd_shell(*args, **kwargs),
    "db": lambda *args, **kwargs: cmd_db(*args, **kwargs),
    "cache": lambda *args, **kwargs: cmd_cache(*args, **kwargs),
    "queue": lambda *args, **kwargs: cmd_queue(*args, **kwargs),
    "docs": lambda *args, **kwargs: cmd_docs(*args, **kwargs),
    "doctor": lambda *args, **kwargs: cmd_doctor(*args, **kwargs),
    "test": lambda *args, **kwargs: cmd_test(*args, **kwargs),
    "lint": lambda *args, **kwargs: cmd_lint(*args, **kwargs),
    "info": lambda *args, **kwargs: cmd_info(*args, **kwargs),
}


# ──────────────────────────────────────────
# ASCII Banner
# ──────────────────────────────────────────

_BANNER_ART = r"""
██████╗  █████╗ ███████╗███████╗██╗     ██╗████████╗██╗  ██╗ ██████╗  ██████╗ ██████╗ ███████╗      
 ██╔══██╗██╔══██╗██╔════╝██╔════╝██║     ██║╚══██╔══╝██║  ██║██╔════╝ ██╔═══██╗██╔══██╗██╔════╝      
 ██████╔╝███████║███████╗█████╗  ██║     ██║   ██║   ███████║██║      ██║   ██║██████╔╝█████╗        
 ██╔══██╗██╔══██║╚════██║██╔══╝  ██║     ██║   ██║   ██╔══██║██║      ██║   ██║██╔══██╗██╔══╝        
 ██████╔╝██║  ██║███████║███████╗███████╗██║   ██║   ██║  ██║╚██████╗ ╚██████╔╝██║  ██║███████╗ ██╗  
 ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚═╝
"""


def _get_version() -> str:
    """Resolve installed package version."""
    try:
        return version("baselith-core")
    except PackageNotFoundError:
        return "0.0.0-dev"


def print_banner() -> None:
    """Print the Baselith-Core CLI gradient banner."""
    if Console_class is None:
        return
    from core.cli.ui import create_gradient_text, console as ui_console

    console = ui_console or Console_class()
    console.print()

    # Render banner with gradient
    for line in _BANNER_ART.strip().splitlines():
        console.print(create_gradient_text(line))

    # Subtitle + version
    subtitle = Text(justify="center")
    subtitle.append("  Multi-Agent, Plugin-First Framework", style="dim italic")
    subtitle.append(f"  •  v{_get_version()}", style="dim cyan")
    subtitle.append("  •  https://baselithcore.xyz", style="bold yellow")
    console.print(subtitle)
    console.print()


def print_help_menu(parser: argparse.ArgumentParser) -> None:
    """Print a professional categorized help menu."""
    if Console_class is None:
        parser.print_help()
        return

    from core.cli.ui import console as ui_console

    console = ui_console or Console_class()
    print_banner()

    # Extract help text from subparsers
    helps: dict[str, str] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice_action in action._choices_actions:
                helps[choice_action.dest] = choice_action.help or ""

    # Build categorized command table
    table = Table(box=None, show_header=False, expand=False, padding=(0, 2))
    table.add_column("Category", style="bold yellow", width=22)
    table.add_column("Command", style="bold cyan", width=12)
    table.add_column("Description", style="white")

    for category, cmds in COMMANDS_MAP.items():
        is_first = True
        for cmd in cmds:
            desc = helps.get(cmd, "")
            table.add_row(category if is_first else "", cmd, desc)
            is_first = False
        table.add_section()

    console.print(
        Panel(
            table,
            title="[bold blue]Command Menu[/bold blue]",
            border_style="blue",
            expand=False,
        )
    )

    # Usage footer
    console.print()
    console.print(
        "[bold dim]Usage:[/bold dim] [bold cyan]baselith[/bold cyan] "
        "[magenta]<command>[/magenta] [cyan][options][/cyan]"
    )
    console.print(
        "[dim]Use[/dim] [bold cyan]baselith <command> --help[/bold cyan] "
        "[dim]for detailed info on any command.[/dim]"
    )

    # Quick-start tips
    console.print()
    console.print(Rule(title="Quick Start", style="dim"))
    tips = Table(box=None, show_header=False, padding=(0, 2))
    tips.add_column("Tip", style="dim", width=30)
    tips.add_column("Command", style="cyan")
    tips.add_row("Bootstrap a new project", "baselith init my-app")
    tips.add_row("Check system health", "baselith doctor")
    tips.add_row("Start dev server", "baselith run")
    tips.add_row("Run the test suite", "baselith test")
    console.print(tips)
    console.print()


# ──────────────────────────────────────────
# Command Dispatchers
# ──────────────────────────────────────────


def _debug_log(msg: str) -> None:
    """Emit a diagnostic line to stderr when ``BASELITH_CLI_DEBUG`` is set.

    Command/plugin registration must never crash the whole CLI, but silently
    swallowing failures hides broken commands. This surfaces them on demand.
    """
    if os.environ.get("BASELITH_CLI_DEBUG"):
        print(f"[baselith-cli] {msg}", file=sys.stderr)


def _inject_global_format(parser: argparse.ArgumentParser) -> None:
    """Allow ``--format`` on every (sub)command, at any nesting depth.

    The root parser already declares ``--format`` (with ``default="text"``),
    but argparse does not propagate it to subparsers, so ``baselith db status
    --format json`` would fail while ``baselith --format json db status``
    works. This walks the subparser tree and re-declares the flag on each node
    with ``default=SUPPRESS`` — present only when the user actually passes it,
    so a value parsed at an outer level is never clobbered by an inner default.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                try:
                    sub.add_argument(
                        "--format",
                        choices=["text", "json"],
                        default=argparse.SUPPRESS,
                        help="Output format (text or json)",
                    )
                except argparse.ArgumentError:
                    pass  # already declared on this subparser; leave it
                _inject_global_format(sub)


def main() -> int:
    """Main CLI entry point."""
    current_version = _get_version()

    parser = argparse.ArgumentParser(
        prog="baselith",
        description="Baselith-Core CLI - Project scaffolding and management",
        formatter_class=formatter_class,
        add_help=False,
    )

    parser.add_argument(
        "--help", "-h", action="store_true", help="Show this help message"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {current_version}"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (text or json)",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands", required=False
    )

    # Register all commands from their modules. A single broken command must
    # not take down the whole CLI; failures are surfaced via BASELITH_CLI_DEBUG.
    for cmd_name in COMMANDS:
        try:
            module = importlib.import_module(f"core.cli.commands.{cmd_name}")
            if hasattr(module, "register_parser"):
                module.register_parser(subparsers, formatter_class)
        except Exception as e:  # noqa: BLE001 - isolate per-command failures
            _debug_log(f"failed to register command '{cmd_name}': {e!r}")

    # Dynamic Plugin CLI registration
    from pathlib import Path

    base_dir = Path.cwd()
    plugins_dir = base_dir / "plugins" if (base_dir / "plugins").is_dir() else base_dir

    if plugins_dir.is_dir() and plugins_dir.name == "plugins":
        # Add cwd to sys.path once for the whole scan, and only remove it if we
        # were the ones who added it (don't clobber a pre-existing entry).
        base_dir_str = str(base_dir)
        path_added = base_dir_str not in sys.path
        if path_added:
            sys.path.insert(0, base_dir_str)
        try:
            for plugin_path in plugins_dir.iterdir():
                if plugin_path.is_dir() and (plugin_path / "cli.py").is_file():
                    try:
                        plugin_module = importlib.import_module(
                            f"plugins.{plugin_path.name}.cli"
                        )
                        if hasattr(plugin_module, "register_parser"):
                            plugin_module.register_parser(subparsers, formatter_class)
                    except Exception as e:  # noqa: BLE001 - isolate per-plugin
                        _debug_log(
                            f"failed to register plugin CLI '{plugin_path.name}': {e!r}"
                        )
        finally:
            if path_added and base_dir_str in sys.path:
                sys.path.remove(base_dir_str)

    # Make --format usable before or after any (sub)command token.
    _inject_global_format(parser)

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()

    # Show the banner help menu for `baselith`, `baselith --help`, or any
    # invocation without a subcommand. Driven by parsed args rather than raw
    # ``sys.argv`` indexing, which mishandles leading global flags.
    if getattr(args, "help", False) or args.command is None:
        print_help_menu(parser)
        return 0

    # Dispatch via explicit handler registry, with fallback to
    # parser-provided handlers (set_defaults(handler=...)) used by plugin CLIs.
    handler = COMMAND_HANDLERS_MAP.get(args.command) or getattr(args, "handler", None)
    if handler and callable(handler):
        result: Any = handler(args)
        return int(result) if isinstance(result, int) else 0

    # Unknown / unregistered command
    print_help_menu(parser)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        if Console_class is not None:
            from core.cli.ui import console

            console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        else:
            print("\nOperation cancelled by user.")
        sys.exit(130)
    except Exception as e:
        import traceback
        from datetime import datetime, timezone
        from pathlib import Path

        crash_log = Path.home() / ".baselith" / "crash-report.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        # Append (not overwrite) with a UTC timestamp so prior crashes are not
        # lost and reports can be correlated chronologically.
        ts = datetime.now(timezone.utc).isoformat()
        with open(crash_log, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 70}\n[{ts}] Exception: {e}\n\n")
            f.write(traceback.format_exc())

        if Console_class is not None:
            from core.cli.ui import print_error, console

            print_error("An unexpected error occurred", str(e))
            console.print(f"[dim]Details saved to {crash_log}[/dim]")
        else:
            print(f"An unexpected error occurred: {e}")
            print(f"Details saved to {crash_log}")
        sys.exit(1)
