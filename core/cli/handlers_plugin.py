"""Dispatch for the ``baselith plugin`` command group.

Split out of :mod:`core.cli.handlers`, which carries one thin function per
top-level command: this group alone is four dispatch tables — the flat plugin
commands plus the nested ``deps``, ``config`` and ``marketplace`` groups — and
it had grown to a third of that module, pushing it over the 500-line cap.

The lazy import of :mod:`core.cli.commands.plugin` stays inside the function
for the reason the whole handler layer resolves late: ``baselith --help`` must
not import the plugin machinery to print a usage line.
"""

from __future__ import annotations

import argparse


def dispatch_plugin(args: argparse.Namespace) -> int:
    """Route ``baselith plugin ...`` to its implementation.

    Args:
        args: The parsed namespace, whose ``plugin_command`` (and, for the
            nested groups, ``deps_command`` / ``config_command`` /
            ``marketplace_command``) selects the branch.

    Returns:
        The process exit code: ``0`` on success, ``1`` when the subcommand
        is unknown or its usage line was printed.
    """
    from core.cli.commands import plugin
    from core.cli.ui import print_error

    command = getattr(args, "plugin_command", "list") or "list"

    # Main plugin command dispatch
    PLUGIN_COMMANDS = {
        "add": lambda: plugin.add_plugin(
            args.source,
            name=getattr(args, "name", None),
            ref=getattr(args, "ref", None),
            force=getattr(args, "force", False),
            install_deps=getattr(args, "install_deps", False),
            docker=getattr(args, "docker", False),
        ),
        "sync": lambda: (
            plugin.sync_plugins_into_docker()
            if getattr(args, "docker", False)
            else plugin.status_local_plugins(json_output=args.format == "json")
        ),
        "create": lambda: plugin.create_plugin(
            args.name,
            args.type,
            interactive=getattr(args, "interactive", False),
            register=not getattr(args, "no_register", False),
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
        "sign": lambda: plugin.sign_plugin(
            args.path, check_only=getattr(args, "check", False)
        ),
        "schema-init": lambda: plugin.schema_init(
            getattr(args, "schema_plugin", None),
            json_output=args.format == "json",
        ),
    }

    # Handle nested subcommands: deps, config, marketplace
    if command == "deps":
        DEPS_COMMANDS = {
            "check": lambda: (
                plugin.deps_check_all(
                    json_output=args.format == "json",
                    python_only=getattr(args, "python_only", False),
                )
                if getattr(args, "all_plugins", False)
                else plugin.deps_check(
                    args.name,
                    json_output=args.format == "json",
                    python_only=getattr(args, "python_only", False),
                )
            ),
            "install": lambda: (
                plugin.deps_install_all(
                    yes=getattr(args, "yes", False),
                    dry_run=getattr(args, "dry_run", False),
                )
                if getattr(args, "all_plugins", False)
                else plugin.deps_install(
                    args.name,
                    yes=getattr(args, "yes", False),
                    dry_run=getattr(args, "dry_run", False),
                )
            ),
        }
        d_command = getattr(args, "deps_command", None)
        handler = DEPS_COMMANDS.get(d_command) if d_command else None
        if handler:
            return handler()
        print_error("Usage: baselith plugin deps {check|install} <name|--all>")
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
            "list": lambda: plugin.search_plugins(
                None,
                category=getattr(args, "category", "all"),
                force_refresh=getattr(args, "refresh", False),
            ),
            "search": lambda: plugin.search_plugins(
                getattr(args, "query", ""),
                category=getattr(args, "category", "all"),
            ),
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
            "login": lambda: plugin.login_cmd(getattr(args, "github_token", None)),
            "logout": lambda: plugin.logout_cmd(),
            "identity": lambda: plugin.identity_cmd(),
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
