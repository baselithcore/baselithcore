import argparse


def cmd_init(args: argparse.Namespace) -> int:
    """Execute the 'init' command to scaffold a new project."""
    from core.cli.commands.init import run_init

    return run_init(args.project_name, args.template)


def cmd_plugin(args: argparse.Namespace) -> int:
    """Execute the 'plugin' command to manage Baselith-Core plugins."""
    from core.cli.commands import plugin
    from core.cli.ui import print_error

    command = getattr(args, "plugin_command", "list") or "list"

    # Main plugin command dispatch
    PLUGIN_COMMANDS = {
        "create": lambda: plugin.create_plugin(
            args.name,
            args.type,
            interactive=getattr(args, "interactive", False),
        ),
        "list": lambda: plugin.status_local_plugins(
            getattr(args, "name", None), json_output=args.format == "json"
        ),
        "status": lambda: plugin.status_local_plugins(
            getattr(args, "name", None), json_output=args.format == "json"
        ),
        "info": lambda: plugin.info_local_plugin(
            args.name, json_output=args.format == "json"
        ),
        "delete": lambda: plugin.delete_local_plugin(
            args.name, getattr(args, "force", False)
        ),
        "disable": lambda: plugin.disable_local_plugin(
            args.name,
            all_plugins=getattr(args, "all_plugins", False),
        ),
        "enable": lambda: plugin.enable_local_plugin(
            args.name,
            all_plugins=getattr(args, "all_plugins", False),
        ),
        "export-manifest": lambda: plugin.export_manifest_cmd(args.name),
        "validate": lambda: plugin.validate_local_plugin(
            args.name,
            json_output=args.format == "json",
        ),
        "logs": lambda: plugin.plugin_logs(
            args.name,
            lines=getattr(args, "lines", 50),
            level=getattr(args, "level", None),
            json_output=args.format == "json",
        ),
        "tree": lambda: plugin.plugin_tree(
            getattr(args, "name", None),
            json_output=args.format == "json",
        ),
    }

    # Handle nested subcommands: deps, config, marketplace
    if command == "deps":
        DEPS_COMMANDS = {
            "check": lambda: plugin.deps_check(
                args.name, json_output=args.format == "json"
            ),
            "install": lambda: plugin.deps_install(
                args.name, yes=getattr(args, "yes", False)
            ),
        }
        d_command = getattr(args, "deps_command", None)
        handler = DEPS_COMMANDS.get(d_command) if d_command else None
        if handler:
            return handler()
        print_error("Usage: baselith plugin deps {check|install} <name>")
        return 1

    elif command == "config":
        CONFIG_COMMANDS = {
            "show": lambda: plugin.config_show(
                getattr(args, "name", None),
                json_output=args.format == "json",
            ),
            "set": lambda: plugin.config_set(args.name, args.key, args.value),
            "get": lambda: plugin.config_get(
                args.name,
                args.key,
                json_output=args.format == "json",
            ),
            "reset": lambda: plugin.config_reset(args.name),
        }
        c_command = getattr(args, "config_command", "show") or "show"
        handler = CONFIG_COMMANDS.get(c_command)
        if handler:
            return handler()
        print_error("Usage: baselith plugin config {show|set|get|reset}")
        return 1

    elif command == "marketplace":
        MARKETPLACE_COMMANDS = {
            "search": lambda: plugin.search_plugins(getattr(args, "query", "")),
            "info": lambda: plugin.info_plugin(args.plugin_id),
            "install": lambda: plugin.install_plugin_cmd(
                args.plugin_id,
                getattr(args, "version", None),
                getattr(args, "force", False),
            ),
            "uninstall": lambda: plugin.uninstall_plugin_cmd(args.plugin_id),
            "update": lambda: plugin.update_plugin_cmd(args.plugin_id),
            "publish": lambda: plugin.publish_plugin_cmd(
                args.path, getattr(args, "key", None)
            ),
            "login": lambda: plugin.login_cmd(),
            "logout": lambda: plugin.logout_cmd(),
        }
        m_command = getattr(args, "marketplace_command", "search") or "search"
        handler = MARKETPLACE_COMMANDS.get(m_command)
        if handler:
            return handler()
        return 1

    # Main command execution
    handler = PLUGIN_COMMANDS.get(command)
    if handler:
        return handler()

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


COMMAND_HANDLERS_MAP = {
    "init": cmd_init,
    "plugin": cmd_plugin,
    "config": cmd_config,
    "verify": cmd_verify,
    "run": cmd_run,
    "shell": cmd_shell,
    "db": cmd_db,
    "cache": cmd_cache,
    "queue": cmd_queue,
    "docs": cmd_docs,
    "doctor": cmd_doctor,
    "test": cmd_test,
    "lint": cmd_lint,
    "info": cmd_info,
}
