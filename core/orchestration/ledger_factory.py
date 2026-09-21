"""Choosing the tool ledger, and saying so when the durable one is unavailable.

:mod:`core.orchestration.idempotency` states the problem and
:mod:`core.orchestration.idempotency_postgres` solves it. Nothing chose between
them: every construction site called ``InMemoryToolLedger()``, whose own
docstring says it "cannot dedupe across processes or restarts; that needs the
Postgres-backed ledger, and a deployment that runs side-effecting tools should
use one". So a deployment that ran side-effecting tools did not use one — the
durable ledger existed, was migrated (``009_tool_invocations``), was tested,
and was never reached.

Worse than absent: the in-process ledger deduplicates *just enough* to look
correct in development, where one worker handles the retry it is asked to
dedupe. The failure only appears with a second replica or after a restart —
which is exactly when a redelivered task re-runs a payment or an outbound
webhook.

Two things are fixed here. The ledger is chosen from configuration rather than
hardcoded, and it is **shared across the process**: every agent used to build
its own, so two agents in one worker did not deduplicate against each other
even in the case the in-process ledger is supposed to cover.

``ORCHESTRATOR_TOOL_LEDGER`` picks:

``auto`` (default)
    Postgres when ``POSTGRES_ENABLED``, otherwise the in-process ledger with a
    warning naming the consequence. Silence would be the bug all over again.

``postgres``
    Postgres, and an error if it cannot be built. A deployment that asked for
    durable deduplication and quietly received per-worker deduplication has
    the guarantee it was promised in name only.

``memory``
    Always in-process: a single-worker deployment, or a test.

``off``
    No ledger at all, so every retry re-executes every effectful call. The
    honest way to say "this deployment has no effectful tools", and the only
    way to get the behaviour the typed agent used to have by default.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.orchestration.idempotency import ToolLedger

logger = get_logger(__name__)

__all__ = [
    "LEDGER_TIMEOUT_SECONDS",
    "BoundedLedger",
    "DurableLedgerUnavailable",
    "get_tool_ledger",
    "reset_tool_ledger",
]

#: The consequence, in the words an operator reading the log needs.
_DEGRADED = (
    "tool calls are deduplicated within this worker only; a redelivered task "
    "or a second replica can repeat an effectful call"
)

#: Wall-clock cap on one ledger round trip. A durable ledger sits in front of
#: every effectful call, so an unreachable database must cost a bounded pause
#: and then get out of the way: the connection pool's own timeout is 30 seconds,
#: which would be paid *per tool call* on a deployment whose database is down.
#: Failing open after a short wait matches what the claim path already does with
#: any other ledger error.
LEDGER_TIMEOUT_SECONDS = 5.0

_ledger: ToolLedger | None = None


class BoundedLedger:
    """A ledger whose every call is bounded, and skipped once it is failing.

    Wraps the durable ledger only. The in-process one cannot block, so it is
    left unwrapped and behaves exactly as it always did.

    Two layers, because one is not enough. The **deadline** keeps a single
    call from waiting out the connection pool's own 30-second timeout; the
    **circuit breaker** keeps a sustained outage from paying even that, on
    every effectful tool, for as long as the database is down. Both surface as
    an exception, and every caller already treats a ledger error as "proceed
    unrecorded" — the deliberate fail-open in
    :func:`core.reasoning.react_tool_gate.claim_ledger_entry`.

    Failing open is the accepted trade: a database outage costs the
    exactly-once guarantee rather than the ability to act at all. An operator
    who needs the opposite has ``ORCHESTRATOR_TOOL_LEDGER=postgres``, which
    refuses to start without it.
    """

    def __init__(self, inner: Any, timeout: float = LEDGER_TIMEOUT_SECONDS) -> None:
        self._inner = inner
        self._timeout = timeout

    async def _bounded(self, name: str, *args: Any, **kwargs: Any) -> Any:
        from core.resilience.circuit_breaker import (
            CircuitBreakerConfig,
            get_circuit_breaker,
        )

        async def _call() -> Any:
            async with asyncio.timeout(self._timeout):
                return await getattr(self._inner, name)(*args, **kwargs)

        breaker = get_circuit_breaker(
            "tool_ledger",
            # Tighter than the default 5/60. This breaker sits in front of
            # every effectful tool call, so each failure before it opens is a
            # full deadline paid by a user-visible request; two consecutive
            # failures are enough evidence, and half a minute is short enough
            # that a brief outage does not cost much deduplication.
            CircuitBreakerConfig(fail_max=2, reset_timeout=30),
        )
        return await breaker.async_call(_call)

    async def lookup(self, key: str) -> Any:
        """The recorded outcome for ``key``, or ``None`` when unseen."""
        return await self._bounded("lookup", key)

    async def begin(self, key: str, *, run_id: str, tool: str) -> Any:
        """Claim ``key``, or hand back the outcome that already holds it."""
        return await self._bounded("begin", key, run_id=run_id, tool=tool)

    async def complete(self, key: str, result: Any) -> None:
        """Record that the call succeeded, with its result."""
        await self._bounded("complete", key, result)

    async def fail(self, key: str, error: str) -> None:
        """Record that the call failed, so a retry is allowed."""
        await self._bounded("fail", key, error)

    def __getattr__(self, name: str) -> Any:
        """Pass anything else (``purge_completed_before``) to the real ledger."""
        return getattr(self._inner, name)


class DurableLedgerUnavailable(RuntimeError):
    """``ORCHESTRATOR_TOOL_LEDGER=postgres`` but Postgres cannot back it."""


def _postgres_enabled() -> bool:
    """Whether the deployment has a Postgres to put the ledger in."""
    try:
        from core.config.storage import get_storage_config

        return bool(get_storage_config().postgres_enabled)
    except Exception as exc:  # pragma: no cover - config unreadable
        logger.warning(
            "tool_ledger_storage_config_unreadable", extra={"error": str(exc)}
        )
        return False


def _build(backend: str, *, demanded: bool) -> ToolLedger:
    """Construct the ledger for a resolved backend name.

    Args:
        backend: The backend to build, after ``auto`` has been resolved.
        demanded: Whether the operator asked for Postgres by name. It is a
            separate argument because ``auto`` resolves *to* ``"postgres"``,
            so reading the resolved name would make an automatic choice fail
            as loudly as a demanded one — which is the opposite of what
            ``auto`` means.

    Raises:
        DurableLedgerUnavailable: ``postgres`` was demanded and cannot be built.
    """
    from core.orchestration.idempotency import InMemoryToolLedger

    if backend == "memory":
        return InMemoryToolLedger()

    try:
        from core.orchestration.idempotency_postgres import PostgresToolLedger

        return BoundedLedger(PostgresToolLedger())
    except Exception as exc:
        if demanded:
            raise DurableLedgerUnavailable(
                "ORCHESTRATOR_TOOL_LEDGER=postgres, but the Postgres ledger "
                f"could not be built: {exc}"
            ) from exc
        logger.warning(
            "tool_ledger_degraded_to_memory",
            extra={"error": str(exc), "consequence": _DEGRADED},
        )
        return InMemoryToolLedger()


def get_tool_ledger() -> ToolLedger | None:
    """The process-wide tool ledger, built once on first use.

    Shared deliberately. A per-agent ledger cannot deduplicate two agents in
    the same worker, and the derived key already carries the ``run_id``, so
    sharing one store cannot collapse calls from different runs.

    Returns:
        The ledger for this process, or ``None`` when it is switched off.

    Raises:
        DurableLedgerUnavailable: ``ORCHESTRATOR_TOOL_LEDGER=postgres`` and the
            durable ledger could not be constructed.
    """
    global _ledger
    if _ledger is not None:
        return _ledger

    try:
        from core.config.orchestration import get_orchestration_config

        configured = get_orchestration_config().tool_ledger
    except Exception as exc:  # pragma: no cover - config unreadable
        logger.warning(
            "tool_ledger_config_unreadable",
            extra={"error": str(exc), "consequence": _DEGRADED},
        )
        configured = "memory"

    if configured == "postgres" and not _postgres_enabled():
        raise DurableLedgerUnavailable(
            "ORCHESTRATOR_TOOL_LEDGER=postgres, but POSTGRES_ENABLED is off. "
            "Constructing the ledger opens no connection, so this is the one "
            "misconfiguration that can be caught before the first claim fails "
            "open and silently stops deduplicating."
        )

    if configured == "off":
        logger.warning(
            "tool_ledger_disabled",
            extra={"consequence": "every retry re-executes every effectful call"},
        )
        return None

    backend = configured
    if configured == "auto":
        backend = "postgres" if _postgres_enabled() else "memory"
        if backend == "memory":
            logger.warning(
                "tool_ledger_in_process_only",
                extra={"reason": "POSTGRES_ENABLED is off", "consequence": _DEGRADED},
            )

    _ledger = _build(backend, demanded=configured == "postgres")
    logger.info("tool_ledger_selected", extra={"backend": type(_ledger).__name__})
    return _ledger


def reset_tool_ledger() -> None:
    """Drop the cached ledger so the next call rebuilds it.

    For tests and for a process that reloads configuration; the ledger holds
    no connection of its own, so dropping it cancels nothing in flight.
    """
    global _ledger
    _ledger = None
