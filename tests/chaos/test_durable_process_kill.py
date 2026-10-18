"""Chaos: durable execution survives a real process death on a real Postgres.

The unit suite proves the checkpoint machinery with in-process stores and
simulated cancellation. What it cannot prove is the property durability exists
for: an OS process that dies mid-step — no ``finally``, no lease release, no
last write — and a different process that picks the run up. These cases do
exactly that. A child process (:mod:`tests.chaos._durable_worker`) runs a
two-step durable run against ``PostgresCheckpointStore``; the test kills it
with ``SIGKILL`` (or freezes it with ``SIGSTOP``) while it holds the run's
lease, and then checks, against the database, that:

* the dead owner's lease still fences the run until it expires;
* another process resumes it once it has, replaying step 1 from the checkpoint
  without re-running its side effect (exactly one row), and running step 2;
* an owner that was only frozen, and wakes after the takeover, cannot run its
  next side effect or land a write, and neither can its stale state;
* the recovery sweep leaves the abandoned run alone while the lease is live and
  resumes it after.

Runs only against a real server, with the real-database opt-in (see
``tests/integration/test_tool_ledger_postgres.py``)::

    BASELITH_TEST_REAL_DB=1 python -m pytest tests/chaos/test_durable_process_kill.py --no-cov

Each case works in a throwaway schema, with the table the store's own runtime
DDL creates. The CI ``integration_test`` job runs this module with
``BASELITH_TEST_INTEGRATION_STRICT=1``, under which a missing database fails
the case instead of skipping it.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from baselith.core.orchestration.checkpoint import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    Checkpoint,
    CheckpointConflictError,
    CheckpointLeaseHeldError,
    CheckpointManager,
)
from baselith.core.orchestration.checkpoint_lease import (
    end_checkpoint_attempt,
    lease_is_live,
)
from baselith.core.orchestration.limits import LoopBudget
from baselith.core.orchestration.recovery import resume_interrupted_runs
from tests.chaos._durable_worker import (
    CHARGE,
    SHIP,
    SIDE_EFFECTS_DDL,
    SideEffects,
    cursor_factory,
    drive,
)

pytestmark = [pytest.mark.chaos, pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The workers' lease. Short, so expiry is quick, but at least three heartbeat
#: periods long, so a live owner is never mistaken for a dead one.
LEASE_SECONDS = 3.0

#: Upper bound on any single wait; the cases finish in a few seconds each.
DEADLINE_SECONDS = 25.0


def _skip_unless_strict(reason: str) -> None:
    """Skip — or fail, in the integration job, where Postgres must be there."""
    strict = os.environ.get("BASELITH_TEST_INTEGRATION_STRICT", "").strip().lower()
    if strict in {"1", "true", "yes"}:
        pytest.fail(f"skipped under BASELITH_TEST_INTEGRATION_STRICT=1: {reason}")
    pytest.skip(reason)


@pytest.fixture
def schema() -> Iterator[str]:
    """A throwaway schema holding the checkpoint and side-effect tables."""
    from tests.integration.test_tool_ledger_postgres import (
        _conninfo,
        _pg_available,
        _real_db_enabled,
    )

    if not _real_db_enabled():
        _skip_unless_strict(
            "set BASELITH_TEST_REAL_DB=1 to run against a real Postgres"
        )
    if not _pg_available():
        _skip_unless_strict("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    from baselith.core.orchestration.checkpoint_postgres import _DDL

    name = f"chaos_ckpt_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {name}")
        cur.execute(f"SET search_path TO {name}")
        cur.execute(_DDL)
        cur.execute(SIDE_EFFECTS_DDL)
    try:
        yield name
    finally:
        with (
            psycopg.connect(_conninfo(), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, schema: str) -> Any:
    """This process's view of the same store the workers write to."""
    from baselith.core.orchestration.checkpoint_postgres import (
        PostgresCheckpointStore,
    )

    monkeypatch.setattr(
        "baselith.core.orchestration.checkpoint_postgres.get_async_cursor",
        cursor_factory(schema),
    )
    return PostgresCheckpointStore()


@dataclass
class Worker:
    """One ``_durable_worker`` child process."""

    proc: subprocess.Popen[bytes]
    workdir: Path
    log: Path

    @property
    def pid(self) -> int:
        return self.proc.pid

    def _died(self, waiting_for: str) -> str:
        output = self.log.read_text(encoding="utf-8", errors="replace")
        return (
            f"worker {self.pid} exited with {self.proc.returncode} before "
            f"{waiting_for}:\n{output[-4000:]}"
        )

    async def ready(self) -> dict[str, Any]:
        """Wait until the worker reached the point the test acts on."""
        path = self.workdir / "ready.json"
        deadline = time.monotonic() + DEADLINE_SECONDS
        while not path.exists():
            if self.proc.poll() is not None:
                pytest.fail(self._died("it was ready"))
            if time.monotonic() > deadline:
                pytest.fail(f"worker {self.pid} not ready in {DEADLINE_SECONDS}s")
            await asyncio.sleep(0.02)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def result(self) -> dict[str, Any]:
        """Wait for the worker to finish and return what it reported."""
        try:
            self.proc.wait(timeout=DEADLINE_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"worker {self.pid} still running after {DEADLINE_SECONDS}s")
        path = self.workdir / "result.json"
        if self.proc.returncode != 0 or not path.exists():
            pytest.fail(self._died("it reported a result"))
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def kill(self) -> None:
        """SIGKILL: no ``finally``, no lease release, no last write."""
        os.kill(self.pid, signal.SIGKILL)
        assert self.proc.wait(timeout=DEADLINE_SECONDS) == -signal.SIGKILL


@pytest.fixture
def spawn(tmp_path: Path, schema: str) -> Iterator[Any]:
    """Start ``_durable_worker`` processes; none outlives the test."""
    workers: list[Worker] = []

    def _spawn(mode: str, run_id: str, tenant: str) -> Worker:
        workdir = tmp_path / f"{mode}-{len(workers)}"
        workdir.mkdir()
        log = workdir / "worker.log"
        env = {
            **os.environ,
            "ORCHESTRATOR_CHECKPOINT_LEASE_SECONDS": str(LEASE_SECONDS),
        }
        argv = [
            sys.executable,
            "-m",
            "tests.chaos._durable_worker",
            f"--mode={mode}",
            f"--schema={schema}",
            f"--run-id={run_id}",
            f"--tenant={tenant}",
            f"--workdir={workdir}",
            f"--timeout={DEADLINE_SECONDS}",
        ]
        with log.open("wb") as sink:
            proc = subprocess.Popen(  # list argv, no shell
                argv, cwd=REPO_ROOT, env=env, stdout=sink, stderr=subprocess.STDOUT
            )
        worker = Worker(proc, workdir, log)
        workers.append(worker)
        return worker

    yield _spawn
    for worker in workers:
        if worker.proc.poll() is None:
            worker.proc.kill()  # SIGKILL also ends a SIGSTOPped process
            worker.proc.wait(timeout=DEADLINE_SECONDS)


def _side_effects(schema: str, run_id: str) -> list[tuple[str, int]]:
    """Every real execution of a step's tool call, in order: ``(tool, pid)``."""
    import psycopg

    from tests.integration.test_tool_ledger_postgres import _conninfo

    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"SET search_path TO {schema}")
        cur.execute(
            "SELECT tool, pid FROM chaos_side_effects WHERE run_id = %s ORDER BY id",
            (run_id,),
        )
        return [(str(tool), int(pid)) for tool, pid in cur.fetchall()]


def _tools(checkpoint: Checkpoint) -> list[str]:
    return sorted(entry["tool_name"] for entry in checkpoint.steps.values())


def _ids() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:10]
    return f"chaos-run-{suffix}", f"chaos-tenant-{suffix}"


async def _killed_mid_step(store: Any, spawn: Any, run_id: str, tenant: str) -> Any:
    """Start a run, let step 1 land, SIGKILL the owner inside step 2."""
    worker = spawn("start", run_id, tenant)
    ready = await worker.ready()

    held = await store.load(run_id)
    assert held is not None
    assert held.status == STATUS_RUNNING
    assert _tools(held) == [CHARGE], "step 1 is checkpointed before step 2 starts"
    assert held.owner == ready["owner"]
    assert lease_is_live(held), "the worker holds a live lease while it executes"

    worker.kill()
    return worker, ready


async def test_sigkill_mid_step_resumes_elsewhere_without_repeating_step_one(
    store: Any, spawn: Any, schema: str
) -> None:
    run_id, tenant = _ids()
    dead, ready = await _killed_mid_step(store, spawn, run_id, tenant)

    # The owner is gone, its lease is not: nobody may take the run yet.
    with pytest.raises(CheckpointLeaseHeldError) as refused:
        await store.claim(run_id, "chaos-intruder", LEASE_SECONDS)
    assert refused.value.owner == ready["owner"]

    # A different process resumes it, retrying until the lease runs out.
    resumer = spawn("resume", run_id, tenant)
    report = resumer.result()

    assert report["owner"] != ready["owner"]
    assert report["steps_at_resume"] == 1
    assert report["effects"] == [SHIP], "step 1 replayed, only step 2 executed"
    # The replayed result is the one the dead process computed.
    assert report["answer"][CHARGE] == {"receipt": f"{CHARGE}:{dead.pid}"}
    assert report["answer"][SHIP] == {"receipt": f"{SHIP}:{resumer.pid}"}
    assert _side_effects(schema, run_id) == [(CHARGE, dead.pid), (SHIP, resumer.pid)]

    final = await store.load(run_id)
    assert final is not None
    assert final.status == STATUS_COMPLETED
    assert _tools(final) == sorted([CHARGE, SHIP])
    assert final.owner is None, "the resumer released the lease when it finished"


async def test_frozen_owner_that_wakes_after_takeover_is_fenced(
    store: Any, spawn: Any, schema: str
) -> None:
    run_id, tenant = _ids()
    zombie = spawn("zombie", run_id, tenant)
    ready = await zombie.ready()
    stale = await store.load(run_id)
    assert stale is not None and stale.owner == ready["owner"]

    # Freeze it between steps: its heartbeat stops, the lease runs out.
    os.kill(zombie.pid, signal.SIGSTOP)
    successor = f"chaos-successor-{uuid.uuid4().hex[:6]}"
    refusals = 0
    deadline = time.monotonic() + DEADLINE_SECONDS
    while True:
        try:
            taken = await store.claim(run_id, successor, 60.0)
            break
        except CheckpointLeaseHeldError:
            refusals += 1
            assert time.monotonic() < deadline, "the frozen owner's lease never expired"
            await asyncio.sleep(0.1)
    assert refusals >= 1, "the frozen owner's lease was still live when frozen"
    assert taken.owner == successor
    assert taken.version > stale.version, "a takeover bumps the version (fencing)"

    # Wake it: it goes straight for step 2.
    (zombie.workdir / "go").touch()
    os.kill(zombie.pid, signal.SIGCONT)
    report = zombie.result()
    assert report["outcome"] == "CheckpointConflictError"
    assert report["effects"] == [CHARGE], "step 2's side effect never ran"
    assert report["lease_lost"] is True

    after = await store.load(run_id)
    assert after is not None
    assert after.owner == successor, "the woken owner neither wrote nor released"
    assert after.version == taken.version
    assert _tools(after) == [CHARGE]
    assert lease_is_live(after)

    # Nothing carrying the old owner's identity lands either: not its stale
    # copy, not a fresh copy re-stamped with its name, not a lease renewal.
    stale.status = STATUS_COMPLETED
    with pytest.raises(CheckpointConflictError):
        await store.save(stale)
    restamped = await store.load(run_id)
    assert restamped is not None
    restamped.owner = ready["owner"]
    with pytest.raises(CheckpointConflictError):
        await store.save(restamped)
    assert not await store.renew_lease(run_id, ready["owner"], 60.0)

    # The successor finishes: step 1 replayed, step 2 executed once, here.
    manager = CheckpointManager(store, taken, owner=successor, lease_seconds=60.0)
    effects = SideEffects(cursor_factory(schema), run_id)
    try:
        await manager.complete(await drive(manager, effects))
    finally:
        await manager.end_attempt()
    assert effects.ran == [SHIP]
    assert _side_effects(schema, run_id) == [
        (CHARGE, zombie.pid),
        (SHIP, os.getpid()),
    ]
    final = await store.load(run_id)
    assert final is not None and final.status == STATUS_COMPLETED


class _SweepOrchestrator:
    """The slice of the orchestrator the recovery sweep drives.

    ``process(..., resume=True)`` does what the real one does around a
    handler: claim/resume the checkpoint, start the heartbeat, run, complete,
    and hand the lease back.
    """

    def __init__(self, store: Any, effects: SideEffects) -> None:
        self.store = store
        self.effects = effects

    async def process(
        self, query: str, *, context: dict[str, Any], run_id: str, resume: bool
    ) -> dict[str, Any]:
        from baselith.core.orchestration.checkpoint_init import init_checkpoint

        manager = await init_checkpoint(
            self.store, query, context, None, LoopBudget(), run_id, resume
        )
        manager.start_heartbeat()
        try:
            answer = await drive(manager, self.effects)
            await manager.complete(answer)
            return {"response": answer}
        finally:
            await end_checkpoint_attempt(manager)


async def test_recovery_sweep_resumes_the_killed_run_after_its_lease_expires(
    store: Any, spawn: Any, schema: str
) -> None:
    run_id, tenant = _ids()
    dead, _ = await _killed_mid_step(store, spawn, run_id, tenant)
    effects = SideEffects(cursor_factory(schema), run_id)
    orchestrator = _SweepOrchestrator(store, effects)

    first = await resume_interrupted_runs(orchestrator, store, tenant_id=tenant)
    assert first.skipped == [run_id], "a live lease means someone may be running it"
    assert not first.resumed and not first.failed
    assert effects.ran == []

    deadline = time.monotonic() + DEADLINE_SECONDS
    while True:
        report = await resume_interrupted_runs(orchestrator, store, tenant_id=tenant)
        assert not report.failed, report.failed
        if report.resumed:
            break
        assert time.monotonic() < deadline, "the sweep never picked the run up"
        await asyncio.sleep(0.2)

    assert report.resumed == [run_id]
    assert effects.ran == [SHIP]
    assert _side_effects(schema, run_id) == [(CHARGE, dead.pid), (SHIP, os.getpid())]
    final = await store.load(run_id)
    assert final is not None
    assert final.status == STATUS_COMPLETED
    assert final.owner is None
    # Completed runs leave the resumable set: the next sweep has nothing to do.
    again = await resume_interrupted_runs(orchestrator, store, tenant_id=tenant)
    assert again.resumed == [] and again.skipped == []
