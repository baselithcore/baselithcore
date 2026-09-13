"""A CLI command is out-of-request work and has to say so under RLS.

Nothing upstream of ``baselith <command>`` binds a tenant, so with
``DB_RLS_ENABLED=true`` the first connection checkout raised
``TenantContextError`` — the pool refuses to invent a tenant rather than bind
``default`` and hand the operator another tenant's rows.

The declaration is made **at the dispatch point** (``core.cli.handlers.
run_command``, called from ``core.cli.__main__.main``), not by decorating the
handler functions. That distinction is the point of half this module: the real
dispatch table lives in ``__main__`` and plugin CLIs inject into it at runtime
(``plugins/baselithbot/diagnostics/cli.py``), or attach a handler with
``parser.set_defaults(handler=...)``. A per-function decorator covered neither,
so every plugin-registered command ran unscoped while a coverage test that
enumerated the *unused* map in ``handlers`` reported full classification.

Exemptions are the *long-lived processes* plus the ones that open no connection
(:data:`handlers.UNSCOPED_COMMANDS`), and one ``(command, subcommand)`` pair
(:data:`handlers.UNSCOPED_SUBCOMMANDS`):

* ``run`` hands the process to the API server and ``shell`` is an interactive
  REPL; a process-wide ``system`` binding would make the pool's fail-closed
  check a no-op for every unbound thing that happens inside them;
* ``queue worker`` is the same argument one level down — it blocks in
  ``start_worker`` for the life of the process and already binds an identity per
  job — while ``queue status`` is an ordinary short command and stays scoped, so
  the exemption cannot be keyed on the top-level name alone;
* ``init``/``test``/``lint`` never open a connection.

What these tests cannot cover: the actual psycopg round-trip under a live
``DB_RLS_ENABLED`` deployment. They assert the tenant bound around the handler,
which is precisely the input ``_current_tenant_for_session`` reads.
"""

from __future__ import annotations

import argparse
import sys
import types
from contextlib import contextmanager

import pytest
from pydantic import BaseModel, ValidationError

from core import context as core_context
from core.cli import __main__ as cli_main
from core.cli import handlers
from core.db.session_setup import SYSTEM_TENANT_ID

pytestmark = [pytest.mark.unit]


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


@pytest.fixture
def rls_on(monkeypatch):
    monkeypatch.setattr(handlers, "_rls_enabled", lambda: True)


@pytest.fixture
def rls_off(monkeypatch):
    monkeypatch.setattr(handlers, "_rls_enabled", lambda: False)


def _probe() -> tuple[handlers.CommandHandler, list[str | None]]:
    """A handler that records the tenant bound while it runs."""
    seen: list[str | None] = []

    def _handler(_args: argparse.Namespace) -> int:
        seen.append(core_context._tenant_context.get())
        return 0

    return _handler, seen


class TestRunCommand:
    """The chokepoint itself."""

    def test_a_scoped_command_runs_as_the_system_tenant(self, rls_on):
        handler, seen = _probe()

        with no_tenant():
            assert handlers.run_command("db", handler, argparse.Namespace()) == 0

        assert seen == [SYSTEM_TENANT_ID]

    def test_the_scope_is_released_afterwards(self, rls_on):
        handler, _ = _probe()

        with no_tenant():
            handlers.run_command("db", handler, argparse.Namespace())
            assert core_context._tenant_context.get() is None

    def test_nothing_is_bound_when_rls_is_off(self, rls_off):
        """Unchanged behaviour: binding a tenant would move cache/memory
        namespaces for a deployment that never asked for RLS."""
        handler, seen = _probe()

        with no_tenant():
            handlers.run_command("db", handler, argparse.Namespace())

        assert seen == [None]

    @pytest.mark.parametrize("command", sorted(handlers.UNSCOPED_COMMANDS))
    def test_the_exempt_commands_bind_nothing(self, rls_on, command):
        handler, seen = _probe()

        with no_tenant():
            handlers.run_command(command, handler, argparse.Namespace())

        assert seen == [None]


class TestLongLivedProcessesAreNotScoped:
    """The exemption rationale applied consistently.

    ``run`` is exempt because binding ``system`` for a server's lifetime disarms
    the pool's fail-closed check for everything inside it. That sentence is true
    verbatim of ``baselith queue worker`` — a blocking loop in the calling
    process — and of ``shell``. The worker already binds per unit of work
    (``core.task_queue.worker._tenant_for``), so an outer binding buys nothing
    and costs the check for RQ's bookkeeping, the scheduler poll and startup.
    """

    def test_queue_worker_is_not_scoped(self, rls_on):
        handler, seen = _probe()
        args = argparse.Namespace(queue_command="worker", concurrency=1)

        with no_tenant():
            handlers.run_command("queue", handler, args)

        assert seen == [None]

    def test_queue_status_is_still_scoped(self, rls_on):
        """The exemption is on the pair, not the command."""
        handler, seen = _probe()
        args = argparse.Namespace(queue_command="status")

        with no_tenant():
            handlers.run_command("queue", handler, args)

        assert seen == [SYSTEM_TENANT_ID]

    def test_queue_with_no_subcommand_is_scoped(self, rls_on):
        handler, seen = _probe()

        with no_tenant():
            handlers.run_command("queue", handler, argparse.Namespace())

        assert seen == [SYSTEM_TENANT_ID]

    def test_shell_is_not_scoped(self, rls_on):
        handler, seen = _probe()

        with no_tenant():
            handlers.run_command("shell", handler, argparse.Namespace())

        assert seen == [None]

    def test_the_worker_scopes_itself_per_job(self):
        """Why the outer binding is not merely unnecessary but wrong: the worker
        has its own, finer-grained answer for every job."""
        import inspect

        from core.task_queue import worker

        assert "system_tenant_scope" in inspect.getsource(worker._tenant_for)

    def test_is_scoped_command_reads_the_nested_dest(self):
        """Every nested parser in this CLI names its dest ``<command>_command``;
        one rule covers db/queue/cache/config/docs/plugin."""
        assert (
            handlers.is_scoped_command(
                "queue", argparse.Namespace(queue_command="worker")
            )
            is False
        )
        assert (
            handlers.is_scoped_command("db", argparse.Namespace(db_command="worker"))
            is True
        )

    def test_the_exit_code_is_passed_through(self, rls_on):
        with no_tenant():
            assert handlers.run_command("db", lambda _a: 3, argparse.Namespace()) == 3

    def test_an_unknown_command_is_scoped_not_exempt(self, rls_on):
        """Fail-closed by default: the exemption list is an explicit opt-out, so
        a command nobody classified still declares an identity."""
        handler, seen = _probe()

        with no_tenant():
            handlers.run_command("something-brand-new", handler, argparse.Namespace())

        assert seen == [SYSTEM_TENANT_ID]


class TestPluginRegisteredCommands:
    """The hole the decorator left open.

    ``plugins/baselithbot/diagnostics/cli.py`` writes straight into
    ``core.cli.__main__.COMMAND_HANDLERS_MAP`` at parser-registration time, and
    plugin parsers also use ``set_defaults(handler=...)``. Neither passes
    through anything ``core.cli.handlers`` can decorate, so both have to be
    covered by the dispatch itself.
    """

    @staticmethod
    def _dispatch(args: argparse.Namespace) -> int:
        """The dispatch tail of ``core.cli.__main__.main``, verbatim."""
        handler = cli_main.COMMAND_HANDLERS_MAP.get(args.command) or getattr(
            args, "handler", None
        )
        assert handler is not None and callable(handler)
        result = handlers.run_command(args.command, handler, args)
        return int(result) if isinstance(result, int) else 0

    def test_a_command_injected_into_the_dispatch_map_is_scoped(
        self, monkeypatch, rls_on
    ):
        handler, seen = _probe()
        monkeypatch.setitem(cli_main.COMMAND_HANDLERS_MAP, "baselithbot", handler)

        with no_tenant():
            self._dispatch(argparse.Namespace(command="baselithbot"))

        assert seen == [SYSTEM_TENANT_ID]

    def test_a_set_defaults_handler_is_scoped_too(self, rls_on):
        handler, seen = _probe()

        with no_tenant():
            self._dispatch(argparse.Namespace(command="plugin-owned", handler=handler))

        assert seen == [SYSTEM_TENANT_ID]

    def test_main_dispatches_through_run_command(self):
        """Guard the wiring itself: a future edit that calls ``handler(args)``
        directly would silently reopen the hole."""
        import inspect

        source = inspect.getsource(cli_main.main)
        assert "run_command(args.command, handler, args)" in source
        assert "result: Any = handler(args)" not in source


class TestCoverage:
    """Asserted against the map that actually dispatches."""

    def test_every_exemption_names_a_real_command(self):
        assert handlers.UNSCOPED_COMMANDS <= set(cli_main.COMMAND_HANDLERS_MAP)

    def test_the_core_commands_that_touch_persistence_are_scoped(self):
        scoped = set(cli_main.COMMAND_HANDLERS_MAP) - handlers.UNSCOPED_COMMANDS
        assert scoped == {
            "plugin",
            "config",
            "verify",
            "db",
            "cache",
            "queue",
            "docs",
            "doctor",
            "info",
        }

    def test_every_subcommand_exemption_names_a_scoped_command(self):
        """A pair exemption on an already-exempt command would be dead weight,
        and one on a command that does not exist would be a typo nobody sees."""
        for command, _subcommand in handlers.UNSCOPED_SUBCOMMANDS:
            assert command in cli_main.COMMAND_HANDLERS_MAP
            assert command not in handlers.UNSCOPED_COMMANDS

    def test_the_legacy_map_is_a_different_object(self):
        """``core.cli.handlers.COMMAND_HANDLERS_MAP`` is a convenience view that
        nothing dispatches through, so a plugin injecting into the real map never
        appears in it. Enumerating it was how a command could be 'classified' and
        unscoped at the same time."""
        assert handlers.COMMAND_HANDLERS_MAP is not cli_main.COMMAND_HANDLERS_MAP


class TestABrokenConfigDoesNotBreakTheCli:
    """The commands you run *because* the configuration is broken must run.

    ``_rls_enabled`` imports ``core.db.connection``, which evaluates
    ``get_storage_config()`` at module scope. A malformed ``DB_*`` variable
    therefore raised a pydantic ``ValidationError`` out of the dispatcher — from
    ``baselith doctor``, ``info`` and ``config validate`` among others, which is
    exactly backwards. It degrades to "RLS off" instead: a storage config that
    will not parse describes no reachable pool, so there is no session to bind a
    tenant on, and the first real database touch re-raises the same error where
    the operator can see it.
    """

    @staticmethod
    def _config_error() -> ValidationError:
        class _Storage(BaseModel):
            db_pool_min_size: int

        try:
            _Storage(db_pool_min_size="notanint")
        except ValidationError as exc:
            return exc
        raise AssertionError("expected a ValidationError")  # pragma: no cover

    @pytest.fixture
    def unloadable_db_module(self, monkeypatch):
        """Stand in for ``core.db.connection`` failing to evaluate its config."""
        error = self._config_error()
        module = types.ModuleType("core.db.connection")

        def _raise(_name: str):
            raise error

        module.__getattr__ = _raise  # type: ignore[method-assign]
        monkeypatch.setitem(sys.modules, "core.db.connection", module)
        return error

    def test_the_probe_degrades_instead_of_raising(self, unloadable_db_module):
        assert handlers._rls_enabled() is False

    def test_a_dispatched_command_still_runs(self, unloadable_db_module):
        handler, seen = _probe()

        with no_tenant():
            assert handlers.run_command("info", handler, argparse.Namespace()) == 0

        assert seen == [None]

    def test_the_probe_logs_the_message_only(self, unloadable_db_module):
        """A traceback here renders with *frame locals* under ``LOG_LEVEL=debug``
        — the whole ``AppConfig`` repr to stderr — for a probe whose failure the
        command itself reports properly a moment later. One quiet line."""
        from unittest.mock import patch

        with patch.object(handlers, "logger") as log:
            assert handlers._rls_enabled() is False

        log.debug.assert_called_once()
        assert "exc_info" not in log.debug.call_args.kwargs
        assert log.debug.call_args.kwargs.get("stack_info") is None
        log.error.assert_not_called()
        log.warning.assert_not_called()

    def test_a_missing_module_still_degrades(self, monkeypatch):
        """The original ``ImportError`` path is unchanged."""

        def _raise(_name: str):
            raise ImportError("no core.db.connection")

        module = types.ModuleType("core.db.connection")
        module.__getattr__ = _raise  # type: ignore[method-assign]
        monkeypatch.setitem(sys.modules, "core.db.connection", module)

        assert handlers._rls_enabled() is False
