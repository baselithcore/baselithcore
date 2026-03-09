#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Baselith-Core CLI.

Command-line interface for project scaffolding, plugin management,
and development utilities.
"""

import argparse
import sys
import importlib
from typing import Any, Type, Optional
from importlib.metadata import version, PackageNotFoundError

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
    subtitle.append("  •  © 2026 BaselithCore Team - gippo.xyz", style="bold yellow")
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


def cmd_init(args: argparse.Namespace) -> int:
    """Execute the 'init' command to scaffold a new project."""
    from core.cli.commands.init import run_init

    return run_init(args.project_name, args.template)


def cmd_plugin(args: argparse.Namespace) -> int:
    """Execute the 'plugin' command to manage Baselith-Core plugins."""
    from core.cli.commands import plugin

    command = getattr(args, "plugin_command", "list") or "list"
    if command == "create":
        return plugin.create_plugin(
            args.name,
            args.type,
            interactive=getattr(args, "interactive", False),
        )
    elif command in ["list", "status"]:
        return plugin.status_local_plugins(
            getattr(args, "name", None), json_output=args.format == "json"
        )
    elif command == "info":
        return plugin.info_local_plugin(args.name, json_output=args.format == "json")
    elif command == "delete":
        return plugin.delete_local_plugin(args.name, getattr(args, "force", False))
    elif command == "disable":
        return plugin.disable_local_plugin(
            args.name,
            all_plugins=getattr(args, "all_plugins", False),
        )
    elif command == "enable":
        return plugin.enable_local_plugin(
            args.name,
            all_plugins=getattr(args, "all_plugins", False),
        )
    elif command == "export-manifest":
        return plugin.export_manifest_cmd(args.name)
    elif command == "validate":
        return plugin.validate_local_plugin(
            args.name,
            json_output=args.format == "json",
        )
    elif command == "deps":
        d_command = getattr(args, "deps_command", None)
        if d_command == "check":
            return plugin.deps_check(args.name, json_output=args.format == "json")
        elif d_command == "install":
            return plugin.deps_install(args.name, yes=getattr(args, "yes", False))
        else:
            from core.cli.ui import print_error

            print_error("Usage: baselith plugin deps {check|install} <name>")
            return 1
    elif command == "config":
        c_command = getattr(args, "config_command", "show") or "show"
        if c_command == "show":
            return plugin.config_show(
                getattr(args, "name", None),
                json_output=args.format == "json",
            )
        elif c_command == "set":
            return plugin.config_set(args.name, args.key, args.value)
        elif c_command == "get":
            return plugin.config_get(
                args.name,
                args.key,
                json_output=args.format == "json",
            )
        elif c_command == "reset":
            return plugin.config_reset(args.name)
        else:
            from core.cli.ui import print_error

            print_error("Usage: baselith plugin config {show|set|get|reset}")
            return 1
    elif command == "logs":
        return plugin.plugin_logs(
            args.name,
            lines=getattr(args, "lines", 50),
            level=getattr(args, "level", None),
            json_output=args.format == "json",
        )
    elif command == "tree":
        return plugin.plugin_tree(
            getattr(args, "name", None),
            json_output=args.format == "json",
        )
    elif command == "marketplace":
        m_command = getattr(args, "marketplace_command", "search") or "search"
        if m_command == "search":
            return plugin.search_plugins(getattr(args, "query", ""))
        elif m_command == "info":
            return plugin.info_plugin(args.plugin_id)
        elif m_command == "install":
            return plugin.install_plugin_cmd(
                args.plugin_id,
                getattr(args, "version", None),
                getattr(args, "force", False),
            )
        elif m_command == "uninstall":
            return plugin.uninstall_plugin_cmd(args.plugin_id)
        elif m_command == "update":
            return plugin.update_plugin_cmd(args.plugin_id)
        elif m_command == "publish":
            return plugin.publish_plugin_cmd(args.path, getattr(args, "key", None))
    return 1


def cmd_config(args: argparse.Namespace) -> int:
    """Execute the 'config' command to inspect and modify settings."""
    from core.cli.commands.config import show_config, validate_config

    return (
        show_config()
        if (getattr(args, "config_command", "show") or "show") == "show"
        else validate_config()
    )


def cmd_verify(args: argparse.Namespace) -> int:
    """Execute the 'verify' command to check system integrity."""
    from core.cli.commands.verify import run_verify

    return run_verify(json_output=getattr(args, "json", False))


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the 'run' command to start the development server."""
    from core.cli.commands.run import run_server

    return run_server(
        host=args.host,
        port=args.port,
        reload=args.reload and not getattr(args, "no_reload", False),
        workers=args.workers,
        log_level=args.log_level,
    )


def cmd_shell(args: argparse.Namespace) -> int:
    """Execute the 'shell' command to open an interactive REPL."""
    from core.cli.commands.shell import run_shell

    return run_shell()


def cmd_db(args: argparse.Namespace) -> int:
    """Execute the 'db' command for database migrations and maintenance."""
    from core.cli.commands.db import run_db

    return run_db(
        getattr(args, "db_command", "status") or "status",
        json_output=args.format == "json",
    )


def cmd_cache(args: argparse.Namespace) -> int:
    """Execute the 'cache' command to manage system caches."""
    from core.cli.commands.cache import run_cache

    return run_cache(
        getattr(args, "cache_command", "stats") or "stats",
        json_output=args.format == "json",
    )


def cmd_queue(args: argparse.Namespace) -> int:
    """Execute the 'queue' command for task queue management."""
    from core.cli.commands.queue import run_queue

    return run_queue(getattr(args, "queue_command", "status") or "status", vars(args))


def cmd_docs(args: argparse.Namespace) -> int:
    """Execute the 'docs' command for local documentation maintenance."""
    from core.cli.commands.docs import run_docs

    return run_docs(getattr(args, "docs_command", "generate") or "generate")


def cmd_doctor(args: argparse.Namespace) -> int:
    """Execute the 'doctor' command for comprehensive system diagnostics."""
    from core.cli.commands.doctor import run_doctor

    return run_doctor(json_output=getattr(args, "json", False))


def cmd_test(args: argparse.Namespace) -> int:
    """Execute the 'test' command to run project test suites."""
    from core.cli.commands.test import run_test

    return run_test(
        path=args.path,
        coverage=not args.no_cov,
        verbose=args.verbose,
        markers=args.markers,
        parallel=args.parallel,
        fail_fast=args.fail_fast,
        json_output=args.format == "json",
    )


def cmd_lint(args: argparse.Namespace) -> int:
    """Execute the 'lint' command to perform static code analysis."""
    from core.cli.commands.lint import run_lint

    return run_lint(check=not args.fix, fix=args.fix, mypy=not args.no_mypy)


def cmd_info(args: argparse.Namespace) -> int:
    """Execute the 'info' command to display project and system details."""
    from core.cli.commands.info import run_info

    return run_info(json_output=getattr(args, "json", False))


# Explicit command handler registry (string names for dynamic lookup,
# so that unittest.mock.patch on individual cmd_* functions works correctly).
COMMAND_HANDLERS: dict[str, str] = {
    "init": "cmd_init",
    "plugin": "cmd_plugin",
    "config": "cmd_config",
    "verify": "cmd_verify",
    "run": "cmd_run",
    "shell": "cmd_shell",
    "db": "cmd_db",
    "cache": "cmd_cache",
    "queue": "cmd_queue",
    "docs": "cmd_docs",
    "doctor": "cmd_doctor",
    "test": "cmd_test",
    "lint": "cmd_lint",
    "info": "cmd_info",
}


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

    # Register all commands from their modules
    for cmd_name in COMMANDS:
        try:
            module = importlib.import_module(f"core.cli.commands.{cmd_name}")
            if hasattr(module, "register_parser"):
                module.register_parser(subparsers, formatter_class)
        except ImportError:
            pass

    # Dynamic Plugin CLI registration
    from pathlib import Path

    base_dir = Path.cwd()
    plugins_dir = base_dir / "plugins" if (base_dir / "plugins").is_dir() else base_dir

    if plugins_dir.is_dir() and plugins_dir.name == "plugins":
        for plugin_path in plugins_dir.iterdir():
            if plugin_path.is_dir() and (plugin_path / "cli.py").is_file():
                try:
                    sys.path.insert(0, str(base_dir))
                    plugin_module = importlib.import_module(
                        f"plugins.{plugin_path.name}.cli"
                    )
                    if hasattr(plugin_module, "register_parser"):
                        plugin_module.register_parser(subparsers, formatter_class)
                except Exception:
                    pass
                finally:
                    if str(base_dir) in sys.path:
                        sys.path.remove(str(base_dir))

    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    # Show help menu when invoked without arguments
    if len(sys.argv) == 1 or sys.argv[1] in ("--help", "-h"):
        print_help_menu(parser)
        return 0

    args = parser.parse_args()

    # Dispatch via explicit handler registry (dynamic lookup for mockability)
    handler_name = COMMAND_HANDLERS.get(args.command)
    if handler_name:
        import core.cli.__main__ as _self

        handler = getattr(_self, handler_name, None)
        if handler and callable(handler):
            return handler(args)

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
        from pathlib import Path

        crash_log = Path.home() / ".baselith" / "crash-report.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with open(crash_log, "w") as f:
            f.write(f"Exception: {e}\n\n")
            f.write(traceback.format_exc())

        if Console_class is not None:
            from core.cli.ui import print_error, console

            print_error("An unexpected error occurred", str(e))
            console.print(f"[dim]Details saved to {crash_log}[/dim]")
        else:
            print(f"An unexpected error occurred: {e}")
            print(f"Details saved to {crash_log}")
        sys.exit(1)
