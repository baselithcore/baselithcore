"""Top-level dispatch targets for the ``baselith`` CLI.

One function per command, each resolving its implementation lazily so that
``baselith --help`` never imports the world.

**Tenant identity.** A CLI invocation has no request behind it, so nothing
upstream binds a tenant. With ``DB_RLS_ENABLED=true`` the connection pool
refuses to invent one (``TenantContextError``) rather than silently binding
``default`` and handing the operator another tenant's rows, so a command that
can reach Postgres has to declare what it is — the same declaration the API
boot path (:mod:`core.bootstrap.lazy_init`) and the RQ worker
(:mod:`core.task_queue.worker`) already make.

That declaration is applied **at the dispatch point** by :func:`run_command`,
which :mod:`core.cli.__main__` calls for every command it resolves. It was
briefly a decorator on the functions below, which was wrong in a way worth
recording: the real dispatch table lives in ``__main__`` and plugins inject
into it at runtime (``plugins/baselithbot/diagnostics/cli.py`` does), as does
the ``set_defaults(handler=...)`` fallback for plugin-owned parsers. A
per-function decorator can only cover the handlers it can see, so every
plugin-registered command ran unscoped. Scoping the dispatch instead makes the
default fail-closed: a command is scoped unless its name is in
:data:`UNSCOPED_COMMANDS`.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Signature every command handler shares.
CommandHandler = Callable[[argparse.Namespace], int]

#: Commands that must NOT run inside the system tenant scope.
#:
#: * ``init``/``test``/``lint`` — scaffolding and subprocess wrappers that open
#:   no connection; scoping them would buy nothing and cost the psycopg import.
#: * ``run`` and ``shell`` — **long-lived processes**. ``run`` hands the process
#:   to the API server, which binds a tenant per request and scopes its own boot
#:   work; ``shell`` is an interactive REPL. Binding ``system`` for the lifetime
#:   of either turns the pool's fail-closed check into a no-op for every unbound
#:   thing that happens inside it, which is the opposite of the property this
#:   scoping exists to add.
#:
#: Everything else, *including commands a plugin registers at runtime*, is
#: scoped. The list is an explicit exemption rather than an allowlist so that
#: forgetting to classify a new command fails closed.
UNSCOPED_COMMANDS: frozenset[str] = frozenset(
    {"init", "run", "setup", "test", "lint", "shell"}
)

#: ``(command, subcommand)`` pairs exempt inside an otherwise-scoped command.
#:
#: ``baselith queue worker`` is the case that forced this: it blocks in
#: ``core.task_queue.worker.start_worker`` for the life of the process, which is
#: the same "long-lived process" argument that exempts ``run`` — and the worker
#: already binds an identity per *unit of work* (``worker._tenant_for``), so an
#: outer ``system`` binding buys nothing while costing the fail-closed check for
#: RQ's own bookkeeping, the scheduler poll, failure handlers and startup.
#: ``baselith queue status`` is an ordinary short command and stays scoped, so
#: the exemption cannot be keyed on the top-level name alone.
UNSCOPED_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset({("queue", "worker")})


def _subcommand(command: str, args: argparse.Namespace) -> str | None:
    """The parsed subcommand of *command*, if it has one.

    Every nested parser in this CLI names its dest ``<command>_command``
    (``db_command``, ``queue_command``, …), so one rule covers them all; a
    command with no subparser simply has no such attribute.
    """
    value = getattr(args, f"{command}_command", None)
    return str(value) if value else None


def is_scoped_command(command: str, args: argparse.Namespace) -> bool:
    """Whether this invocation should declare the system tenant.

    Args:
        command: The parsed top-level subcommand.
        args: The parsed namespace, read only for the nested subcommand.

    Returns:
        False for an exempt command or ``(command, subcommand)`` pair.
    """
    if command in UNSCOPED_COMMANDS:
        return False
    subcommand = _subcommand(command, args)
    return (command, subcommand) not in UNSCOPED_SUBCOMMANDS


def _rls_enabled() -> bool:
    """Whether row-level security is switched on for this deployment.

    Resolved at dispatch time rather than at import, so ``baselith --help`` and
    the exempt invocations (:func:`is_scoped_command`) never pull in the storage
    config or the psycopg pool module. Every *scoped* command does pay that
    import — including ones that only read config, such as ``info`` and
    ``docs`` — because the answer is a property of the deployment, not of the
    command.

    Degrades to ``False`` when the answer cannot be obtained: the module is
    absent (``ImportError``), or ``core.db.connection`` raises while evaluating
    ``get_storage_config()`` at import — a malformed ``DB_*`` environment
    variable, for instance. That is not a fail-open hole in the tenant
    declaration: a storage config that will not parse describes no reachable
    pool, so there is nothing to bind a tenant *on*, and the first real database
    touch re-raises the same error where the operator can see it. What it does
    buy is that ``baselith doctor`` / ``config validate`` / ``info`` — the
    commands you reach for precisely *because* the configuration is broken —
    keep answering instead of dying on a stack trace from the dispatcher.
    """
    try:
        from core.db.connection import DB_RLS_ENABLED
    except ImportError:  # pragma: no cover - depends on the installed core
        return False
    except Exception as exc:
        # The message only, and at debug: under ``LOG_LEVEL=debug`` an
        # ``exc_info`` here renders a full rich traceback with frame locals —
        # the whole AppConfig repr — to stderr. The command the operator
        # actually ran reports the configuration problem in its own format.
        logger.debug("cli_rls_probe_failed error=%s", exc)
        return False
    return bool(DB_RLS_ENABLED)


def run_command(command: str, handler: CommandHandler, args: argparse.Namespace) -> int:
    """Invoke one resolved CLI command, declaring a tenant when RLS is on.

    The single chokepoint every ``baselith`` invocation passes through — core
    commands, and the ones plugins inject into the dispatch map or attach with
    ``parser.set_defaults(handler=...)``.

    The scope is entered only when row-level security is enabled, mirroring
    :func:`core.task_queue.worker._tenant_for`: with RLS off nothing reads
    ``app.tenant_id`` for access control, and binding a new tenant id would
    silently move the command's cache and memory namespaces, so that path stays
    byte-identical to the pre-RLS behaviour.

    Args:
        command: The parsed subcommand name, passed with *args* to
            :func:`is_scoped_command`.
        handler: The resolved handler.
        args: The parsed namespace to hand it.

    Returns:
        The handler's exit code.
    """
    if not is_scoped_command(command, args) or not _rls_enabled():
        return handler(args)

    from core.db.connection import system_tenant_scope

    with system_tenant_scope():
        return handler(args)


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
        "add": lambda: plugin.add_plugin(
            args.source,
            name=getattr(args, "name", None),
            ref=getattr(args, "ref", None),
            force=getattr(args, "force", False),
            install_deps=getattr(args, "install_deps", False),
            docker=getattr(args, "docker", False),
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


def cmd_setup(args: argparse.Namespace) -> int:
    """Execute the setup orchestration command."""
    from core.cli.commands.setup import run_setup

    return run_setup(
        profile=getattr(args, "profile", "dev"),
        install_deps=getattr(args, "install_deps", False),
        include_plugins=getattr(args, "with_plugins", False),
        start_services=getattr(args, "start_services", False),
        migrate=getattr(args, "migrate", False),
        wait_timeout=getattr(args, "wait_timeout", 60),
        json_output=getattr(args, "json", False) or args.format == "json",
    )


def cmd_config(args: argparse.Namespace) -> int:
    """Execute the 'config' command to inspect and modify settings."""
    from core.cli.commands.config import (
        check_env,
        ensure_env_profile,
        show_config,
        validate_config,
    )

    command = getattr(args, "config_command", "show") or "show"
    if command == "show":
        return show_config()
    if command == "validate":
        return validate_config()
    if command == "env":
        return ensure_env_profile(
            getattr(args, "profile", "dev"),
            json_output=getattr(args, "json", False) or args.format == "json",
        )
    if command == "check-env":
        return check_env()
    return 1


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
        preflight=not getattr(args, "skip_preflight", False),
        include_plugins=getattr(args, "check_plugins", False),
        require_services=getattr(args, "require_services", False),
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
        json_output=getattr(args, "json", False) or args.format == "json",
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

    return run_doctor(
        json_output=getattr(args, "json", False),
        fix=getattr(args, "fix", False),
        include_plugins=not getattr(args, "core_only", False),
    )


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


# NOT the dispatch table. ``core.cli.__main__`` builds its own map, which is
# also what plugin CLIs inject into at runtime; this one is a convenience
# view kept for importers. Anything asserting "every command is classified"
# must read the ``__main__`` map, not this one.
COMMAND_HANDLERS_MAP = {
    "init": cmd_init,
    "setup": cmd_setup,
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
