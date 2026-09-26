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

    key = derive_call_key(run_id, tool, args, occurrence, tenant_id=tenant)
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

* **The key is derived, not generated — and from content, not position.** Two
  spellings of the same call — an argument dict in a different order — must
  produce one key, or the ledger dedupes nothing. And a resumed run that
  reaches the same effect by a different path must land on the same key, so
  the key hashes the call and its *occurrence* within the run, never its
  ordinal (:mod:`core.orchestration.call_keys`).
* **The key carries no payload.** It lands in logs and a database column, so it
  is a SHA-256 digest, never the arguments.
* **``in_flight`` is not ``completed``.** A crash between ``begin`` and
  ``complete`` leaves a row that says "this may have happened". Replaying it is
  a decision for the caller, not something the ledger silently allows.
* **A ``failed`` row is re-claimable.** The effect did not land, so the retry
  that follows must be allowed to run — only ``in_flight`` and ``completed``
  hold the key.

**Key schemes.** :func:`derive_call_key` (content-addressed, ``v2``) is what
every writer uses. :func:`derive_idempotency_key` is the positional scheme it
replaced, kept so rows written before the upgrade stay derivable:
:func:`claim_call` accepts the legacy key of the call and, when the ``v2`` key
is fresh, consults the legacy row before executing — a run that was in flight
across the upgrade and resumes along its original path still replays. One that
resumes along a *different* path re-executes a pre-upgrade effect at most once
(the positional key cannot match it); rows written after the upgrade no longer
have that failure mode. The legacy read is one extra lookup per first-time
effectful call and can be retired once the ledger's retention window has
passed.

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

from core.observability.logging import get_logger
from core.orchestration.autonomy import READ_ONLY
from core.orchestration.call_keys import CallOccurrences, derive_call_key

__all__ = [
    "CallOccurrences",
    "InMemoryToolLedger",
    "ToolCallInFlight",
    "ToolLedger",
    "ToolOutcome",
    "claim_call",
    "derive_call_key",
    "derive_idempotency_key",
    "requires_idempotency",
]

logger = get_logger(__name__)

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
    """Derive the **legacy, positional** key identifying one tool call.

    Superseded by :func:`derive_call_key`: ``step`` is a position in the run,
    so a resumed run that reached the same effect by a different path derived
    a fresh key and executed it again. Kept so rows written under this scheme
    stay derivable (see :func:`claim_call`) and the operator surface can still
    look them up. New writers must not use it.

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


async def claim_call(
    ledger: ToolLedger,
    key: str,
    *,
    run_id: str,
    tool: str,
    legacy_key: str | None = None,
) -> ToolOutcome | None:
    """Claim ``key`` for a call, honouring a row written under the old scheme.

    Behaves exactly like :meth:`ToolLedger.begin`, plus one compatibility read:
    when the content-addressed ``key`` is fresh and the caller can still name
    the call's positional ``legacy_key`` (:func:`derive_idempotency_key`), the
    legacy row decides —

    * ``completed``: its result is copied onto ``key`` and returned, so the
      effect is replayed rather than repeated;
    * ``in_flight``: returned as held (the effect may have landed), and the
      claim just taken on ``key`` is released as ``failed`` so a later retry,
      once the legacy row resolves, is not locked out;
    * ``failed`` or absent: the caller owns the call.

    A failed compatibility read is logged and treated as "absent": the claim on
    ``key`` already succeeded, and the legacy row is a courtesy to runs that
    were in flight across the upgrade, not a precondition.

    Args:
        ledger: The ledger.
        key: The content-addressed key (:func:`derive_call_key`).
        run_id: The run this call belongs to.
        tool: Tool name.
        legacy_key: The positional key the same call had before the upgrade,
            or ``None`` to skip the compatibility read.

    Returns:
        ``None`` when the caller owns the call, else the outcome holding it.
    """
    held = await ledger.begin(key, run_id=run_id, tool=tool)
    if held is not None or legacy_key is None:
        return held
    try:
        legacy = await ledger.lookup(legacy_key)
    except Exception as exc:  # the compatibility read must not block the call
        logger.warning(f"tool ledger legacy lookup failed for {tool}: {exc}")
        return None
    if legacy is None or legacy.status == "failed":
        return None
    try:
        # Best effort: whether or not ``key`` records it, the legacy row has
        # already answered, and answering differently would repeat the effect.
        if legacy.is_replayable:
            await ledger.complete(key, legacy.result)
        else:
            await ledger.fail(key, "legacy-scheme call still in flight")
    except Exception as exc:
        logger.warning(f"tool ledger could not migrate legacy row for {tool}: {exc}")
    return legacy
