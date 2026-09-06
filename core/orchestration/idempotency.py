"""Exactly-once *effects* for tool calls, on top of at-least-once delivery.

Nothing in the runtime promises a tool runs once. The task queue redelivers, the
agent loop resumes from a checkpoint, an operator replays a dead-lettered job —
each of those can re-enter the same call. For a read the cost is a wasted round
trip. For a payment (``core.world_model.mandates``), an outbound webhook or an
email-sending skill, it is a defect the end user sees.

The fix is the standard one, and it is a *ledger*, not a lock: record the
intent before the call and the outcome after it, keyed by a value derived from
the call itself. A replay finds the recorded outcome and returns it instead of
executing again.

    key = derive_idempotency_key(run_id, step, tool, args)
    held = await ledger.begin(key, run_id=run_id, tool=tool)
    if held is not None:
        if held.is_replayable:
            return held.result          # already happened, do not repeat it
        raise ToolCallInFlight(tool)    # someone else owns it *right now*
    ...
    await ledger.complete(key, result)

``begin`` is the whole hot path: one round trip that both claims the key and
reports the row already holding it. A ``lookup``-then-``begin`` pair would be
two round trips *and* a race — two replicas can both miss the lookup.
:meth:`ToolLedger.lookup` exists for the operator surface ("what did run X
do?"), not for the loop.

Four properties are load-bearing:

* **The key is derived, not generated.** Two spellings of the same call — an
  argument dict in a different order — must produce one key, or the ledger
  dedupes nothing.
* **The key carries no payload.** It lands in logs and a database column, so it
  is a SHA-256 digest, never the arguments.
* **``in_flight`` is not ``completed``.** A crash between ``begin`` and
  ``complete`` leaves a row that says "this may have happened". Replaying it is
  a decision for the caller, not something the ledger silently allows.
* **A ``failed`` row is re-claimable.** The effect did not land, so the retry
  that follows must be allowed to run — only ``in_flight`` and ``completed``
  hold the key.

Only ``read_only`` tools skip the ledger; every other autonomy category —
including an unrecognised one — is treated as effectful, matching the
fail-closed default in :mod:`core.orchestration.autonomy`.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from core.orchestration.autonomy import READ_ONLY

__all__ = [
    "InMemoryToolLedger",
    "ToolCallInFlight",
    "ToolLedger",
    "ToolOutcome",
    "derive_idempotency_key",
    "requires_idempotency",
]

OutcomeStatus = Literal["in_flight", "completed", "failed"]

#: Default cap for the in-process ledger. A long-lived worker must not grow an
#: unbounded dict of every tool call it has ever made.
DEFAULT_LEDGER_MAXSIZE = 4096


class ToolCallInFlight(RuntimeError):
    """Another worker holds the claim on this call and has not finished it.

    Not a failure of the tool: the effect may or may not have landed, and the
    only safe answer for an effectful call is to neither repeat it nor report
    a result. Callers surface it to the model or the operator; a run that
    resumes after the holder finishes reads the recorded outcome instead.
    """

    def __init__(self, tool: str, key: str = "") -> None:
        self.tool = tool
        self.key = key
        super().__init__(
            f"tool '{tool}' is already in flight for this run; not re-executed"
        )


def requires_idempotency(category: str) -> bool:
    """Whether a tool in ``category`` needs a ledger entry.

    Args:
        category: The tool's autonomy category.

    Returns:
        ``False`` only for ``read_only``. An unknown category is effectful:
        an undeclared tool must not slip past the ledger, the same fail-closed
        default the approval gate uses.
    """
    return category != READ_ONLY


def derive_idempotency_key(
    run_id: str, step: int, tool: str, args: dict[str, Any] | None
) -> str:
    """Derive the stable key identifying one tool call.

    Args:
        run_id: The run this call belongs to. Supplying a stable ``run_id``
            across retries is what makes deduplication possible at all — a
            fresh id per attempt is a different call by definition.
        step: Position in the run, so a loop that legitimately calls the same
            tool twice with the same arguments is not collapsed into one.
        tool: Tool name.
        args: Call arguments.

    Returns:
        A 64-character hex digest. Arguments that cannot be serialised fall
        back to their ``repr``: the key stays stable within a process but not
        necessarily across them, which is the honest outcome for a value the
        runtime cannot canonicalise.
    """
    try:
        canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(sorted((args or {}).items(), key=lambda kv: kv[0]))
    payload = "\x1f".join((run_id, str(step), tool, canonical))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolOutcome:
    """What the ledger recorded for one call."""

    status: OutcomeStatus
    result: Any = None
    error: str | None = None
    recorded_at: float = 0.0

    @property
    def is_replayable(self) -> bool:
        """Whether the recorded result may be returned instead of re-executing.

        Only a completed call qualifies. ``in_flight`` means the process died
        mid-call and nobody knows whether the effect landed; ``failed`` means it
        did not, so a retry is the correct behaviour.
        """
        return self.status == "completed"


class ToolLedger(Protocol):
    """Records tool-call intent and outcome, keyed by an idempotency key."""

    async def lookup(self, key: str) -> ToolOutcome | None:
        """The recorded outcome for ``key``, or ``None`` when unseen."""
        ...

    async def begin(self, key: str, *, run_id: str, tool: str) -> ToolOutcome | None:
        """Claim ``key`` for a call that is about to be made.

        Returns:
            ``None`` when the claim succeeded and the caller owns the call.
            The **existing** outcome when it did not — another worker got
            there first, and the caller must treat it exactly like a
            :meth:`lookup` hit rather than executing. A ``failed`` row is
            re-claimed: a call that did not land is meant to be retried.
        """
        ...

    async def complete(self, key: str, result: Any) -> None:
        """Record that the call succeeded, with its result."""
        ...

    async def fail(self, key: str, error: str) -> None:
        """Record that the call failed, so a retry is allowed."""
        ...


class InMemoryToolLedger:
    """Bounded in-process ledger.

    Deduplicates within one process — a retry inside the same worker, a resumed
    loop that has not crossed a restart. It cannot dedupe across processes or
    restarts; that needs the Postgres-backed ledger, and a deployment that runs
    side-effecting tools should use one.
    """

    def __init__(self, maxsize: int = DEFAULT_LEDGER_MAXSIZE) -> None:
        self._entries: OrderedDict[str, ToolOutcome] = OrderedDict()
        self._maxsize = max(1, maxsize)

    async def lookup(self, key: str) -> ToolOutcome | None:
        """The recorded outcome for ``key``, or ``None`` when unseen."""
        return self._entries.get(key)

    async def begin(self, key: str, *, run_id: str, tool: str) -> ToolOutcome | None:
        """Claim ``key``, or hand back the outcome that already holds it."""
        existing = self._entries.get(key)
        if existing is not None and existing.status != "failed":
            return existing
        self._put(key, ToolOutcome(status="in_flight", recorded_at=time.time()))
        return None

    async def complete(self, key: str, result: Any) -> None:
        """Record that the call succeeded, with its result."""
        self._put(
            key, ToolOutcome(status="completed", result=result, recorded_at=time.time())
        )

    async def fail(self, key: str, error: str) -> None:
        """Record that the call failed, so a retry is allowed."""
        self._put(
            key, ToolOutcome(status="failed", error=error, recorded_at=time.time())
        )

    def _put(self, key: str, outcome: ToolOutcome) -> None:
        self._entries[key] = outcome
        self._entries.move_to_end(key)
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)
