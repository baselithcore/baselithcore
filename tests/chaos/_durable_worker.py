"""Child process for the process-kill chaos tests.

``tests/chaos/test_durable_process_kill.py`` runs this module as a separate OS
process (``python -m tests.chaos._durable_worker``) so that it can be killed
with a real ``SIGKILL`` (or frozen with ``SIGSTOP``) in the middle of a durable
run. Nothing here is mocked: the run is driven through the same
``init_checkpoint`` / ``CheckpointManager.run_step`` path the orchestrator uses,
against :class:`PostgresCheckpointStore` on a real PostgreSQL, with the lease
heartbeat running.

The run is two tool steps, each with a side effect that inserts one row into
``chaos_side_effects`` (tagged with the executing process id), so "did step 1
run twice?" is a row count, not an inference.

Modes:

* ``start`` — fresh run; step 1 completes, then step 2 hangs *inside* its tool
  call until the process is killed.
* ``zombie`` — fresh run; step 1 completes, then the run waits *between* the
  two steps for a ``go`` file. The test freezes the process, lets its lease
  expire, takes the run over, and only then lets it continue.
* ``resume`` — resume an existing run (retrying while another executor's lease
  is live) and finish it.

The process reports through files in ``--workdir``: ``ready.json`` once it
reached the point the test waits for, ``result.json`` when it finishes.

Not collected by pytest (no ``test_`` prefix). The schema-scoped cursor and the
side-effect recorder are shared with the test module, which runs its own
executors in-process.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

QUERY = "chaos: charge the card, then ship the order"
CHARGE = "charge_card"
SHIP = "ship_order"

#: The side-effect ledger. One row per real execution of a step's tool call.
SIDE_EFFECTS_DDL = """
CREATE TABLE chaos_side_effects (
    id      BIGSERIAL PRIMARY KEY,
    run_id  TEXT NOT NULL,
    tool    TEXT NOT NULL,
    pid     INTEGER NOT NULL,
    at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CursorFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]


def conninfo() -> str:
    """The connection string the real-database integration tests use."""
    from tests.integration.test_tool_ledger_postgres import _conninfo

    return _conninfo()


def cursor_factory(schema: str) -> CursorFactory:
    """A ``get_async_cursor`` replacement pinned to ``schema``.

    One real connection per call, as in the store contract tests: every
    statement the store issues is its own autocommitted round trip, and a
    killed process leaves nothing half-open behind.
    """
    import psycopg
    from psycopg import sql

    info = conninfo()
    set_path = sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))

    @contextlib.asynccontextmanager
    async def _cursor(*, row_factory: Any = None) -> AsyncIterator[Any]:
        conn = await psycopg.AsyncConnection.connect(info, autocommit=True)
        try:
            await conn.execute(set_path)
            kwargs = {"row_factory": row_factory} if row_factory is not None else {}
            async with conn.cursor(**kwargs) as cur:
                yield cur
        finally:
            await conn.close()

    return _cursor


class SideEffects:
    """Records each real execution of a step's tool call (row + local list)."""

    def __init__(self, cursor: CursorFactory, run_id: str) -> None:
        self._cursor = cursor
        self._run_id = run_id
        self.ran: list[str] = []

    def call(self, tool: str) -> Callable[[], Awaitable[dict[str, Any]]]:
        async def _execute() -> dict[str, Any]:
            self.ran.append(tool)
            async with self._cursor() as cur:
                await cur.execute(
                    "INSERT INTO chaos_side_effects (run_id, tool, pid) "
                    "VALUES (%s, %s, %s)",
                    (self._run_id, tool, os.getpid()),
                )
            return {"receipt": f"{tool}:{os.getpid()}"}

        return _execute


Hook = Callable[[], Awaitable[None]]


async def drive(
    manager: Any,
    effects: SideEffects,
    *,
    between_steps: Hook | None = None,
    inside_ship: Hook | None = None,
) -> dict[str, Any]:
    """The run's handler: two durable tool steps, as a handler would write them.

    Args:
        manager: The run's ``CheckpointManager``.
        effects: Side-effect recorder.
        between_steps: Awaited after step 1 is checkpointed, before step 2.
        inside_ship: Awaited inside step 2's tool call, before its side effect.
    """
    charged = await manager.run_step(CHARGE, {"amount": 42}, effects.call(CHARGE))
    if between_steps is not None:
        await between_steps()

    ship = effects.call(SHIP)

    async def _ship() -> dict[str, Any]:
        if inside_ship is not None:
            await inside_ship()
        return await ship()

    shipped = await manager.run_step(SHIP, {"order": "o-1"}, _ship)
    return {CHARGE: charged, SHIP: shipped}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` atomically, so a poller never reads half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


async def _wait_for(path: Path) -> None:
    """Poll for a file another process creates (no shared event to wait on)."""
    while True:
        if path.exists():
            return
        await asyncio.sleep(0.02)


async def _start_run(args: argparse.Namespace, store: Any) -> Any:
    from baselith.core.orchestration.checkpoint_init import init_checkpoint
    from baselith.core.orchestration.limits import LoopBudget

    manager = await init_checkpoint(
        store,
        QUERY,
        {"tenant_id": args.tenant},
        "chaos",
        LoopBudget(),
        args.run_id,
        False,
    )
    manager.start_heartbeat()
    return manager


def _ready(args: argparse.Namespace, manager: Any, phase: str) -> None:
    write_json(
        args.workdir / "ready.json",
        {"pid": os.getpid(), "owner": manager.owner, "phase": phase},
    )


async def _mode_start(args: argparse.Namespace, store: Any, effects: Any) -> None:
    manager = await _start_run(args, store)

    async def _hang() -> None:
        _ready(args, manager, "inside_step_2")
        await asyncio.Event().wait()  # until SIGKILL

    await drive(manager, effects, inside_ship=_hang)
    raise AssertionError("step 2 was supposed to hang until the process died")


async def _mode_zombie(args: argparse.Namespace, store: Any, effects: Any) -> None:
    from baselith.core.orchestration.checkpoint import CheckpointConflictError
    from baselith.core.orchestration.checkpoint_lease import end_checkpoint_attempt

    manager = await _start_run(args, store)

    async def _pause() -> None:
        _ready(args, manager, "between_steps")
        await _wait_for(args.workdir / "go")

    outcome = "completed"
    try:
        await drive(manager, effects, between_steps=_pause)
    except CheckpointConflictError:
        outcome = "CheckpointConflictError"
    finally:
        # What the orchestrator's ``finally`` does: hand the lease back. For an
        # owner that lost it this must be a no-op.
        await end_checkpoint_attempt(manager)
    write_json(
        args.workdir / "result.json",
        {
            "pid": os.getpid(),
            "outcome": outcome,
            "effects": effects.ran,
            "lease_lost": manager.lease_lost,
        },
    )


async def _mode_resume(args: argparse.Namespace, store: Any, effects: Any) -> None:
    from baselith.core.orchestration.checkpoint import CheckpointLeaseHeldError
    from baselith.core.orchestration.checkpoint_init import init_checkpoint
    from baselith.core.orchestration.checkpoint_lease import end_checkpoint_attempt
    from baselith.core.orchestration.limits import LoopBudget

    refusals = 0
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            manager = await init_checkpoint(
                store,
                QUERY,
                {"tenant_id": args.tenant},
                None,
                LoopBudget(),
                args.run_id,
                True,
            )
            break
        except CheckpointLeaseHeldError:
            refusals += 1
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.1)
    steps_at_resume = len(manager.checkpoint.steps)
    manager.start_heartbeat()
    try:
        answer = await drive(manager, effects)
        await manager.complete(answer)
    finally:
        await end_checkpoint_attempt(manager)
    write_json(
        args.workdir / "result.json",
        {
            "pid": os.getpid(),
            "owner": manager.owner,
            "refusals": refusals,
            "steps_at_resume": steps_at_resume,
            "effects": effects.ran,
            "answer": answer,
        },
    )


_MODES = {"start": _mode_start, "zombie": _mode_zombie, "resume": _mode_resume}


async def _main(args: argparse.Namespace) -> None:
    from baselith.core.orchestration import checkpoint_postgres

    cursor = cursor_factory(args.schema)
    # The worker's own process: patching the module is the whole setup.
    checkpoint_postgres.get_async_cursor = cursor  # type: ignore[assignment]
    store = checkpoint_postgres.PostgresCheckpointStore()
    await _MODES[args.mode](args, store, SideEffects(cursor, args.run_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(_MODES), required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
