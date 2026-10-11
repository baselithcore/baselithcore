"""Child scopes of a run's checkpoint, and the ambient checkpoint.

A run's :class:`~baselith.core.orchestration.checkpoint.CheckpointManager`
records tool steps under content-addressed keys
(:mod:`baselith.core.orchestration.call_keys`): ``(tool, args, occurrence)``.
That identity is per *run*, which is right for one agent loop and wrong for a
composite runner. A crew whose second task calls ``search({"q": "x"})`` after
the first task did the same would, on resume, see the first task's record
answer the second task's call once the first task is replayed instead of
re-run — the occurrence counter never saw the replayed call.

A :class:`CheckpointScope` fixes that by namespacing. It is a view of the root
manager whose step keys carry a path prefix
(``crew:<hash>:0/task:3/search``) and whose occurrence counter is its own, so
every unit of a composite runner — a crew task, a group-chat turn, a swarm
subtask, a ToT expansion — gets keys that depend only on *what* it is and how
many identical units came before it inside the same parent, never on what its
siblings did. Writes still go through the root manager (one checkpoint, one
lease, one version counter).

Writes are serialised. Composite runners execute units concurrently (a
parallel crew, swarm subtasks, ToT evaluations), and two concurrent saves of
the same checkpoint object would race the compare-and-swap on ``version``: the
second write would carry the version the first one just consumed and fail with
:class:`~baselith.core.orchestration.checkpoint_errors.CheckpointConflictError`.
:class:`ScopeHost` gives the manager one lock around every store write, so
units run concurrently and only their saves queue.

The **ambient** checkpoint is a ``ContextVar`` the orchestrator binds while a
flow handler runs (and composite runners re-bind to their own scope while a
unit runs), so a crew or ToT search started from inside a handler finds the
run's checkpoint without every call site threading it through.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Any

from baselith.core.orchestration.call_keys import CallOccurrences, canonical_arguments

__all__ = [
    "CheckpointScope",
    "ScopeHost",
    "bind_checkpoint",
    "current_checkpoint",
    "durable_step",
    "resolve_checkpoint",
]

_active: ContextVar[Any | None] = ContextVar("baselith_active_checkpoint", default=None)


def current_checkpoint() -> Any | None:
    """The checkpoint (manager or scope) bound for the running unit, if any."""
    return _active.get()


@contextlib.contextmanager
def bind_checkpoint(checkpoint: Any | None) -> Iterator[Any | None]:
    """Bind ``checkpoint`` as ambient for the block; ``None`` binds nothing."""
    if checkpoint is None:
        yield None
        return
    token = _active.set(checkpoint)
    try:
        yield checkpoint
    finally:
        _active.reset(token)


def resolve_checkpoint(explicit: Any | None) -> Any | None:
    """``explicit`` when given, otherwise the ambient checkpoint."""
    return explicit if explicit is not None else current_checkpoint()


def _scope_prefix(name: str, identity: Any, occurrence: int) -> str:
    digest = hashlib.sha256(canonical_arguments(identity).encode("utf-8"))
    return f"{name}:{digest.hexdigest()[:12]}:{occurrence}"


class _ScopeFactory:
    """Shared ``scope()``: one child per ``(name, identity)`` occurrence."""

    _child_scopes: CallOccurrences | None = None

    def _scope_path(self) -> str:
        raise NotImplementedError  # pragma: no cover - abstract

    def _scope_root(self) -> Any:
        raise NotImplementedError  # pragma: no cover - abstract

    def scope(self, name: str, identity: Any = None) -> CheckpointScope:
        """A child scope for one unit of work.

        Args:
            name: Kind of unit (``"crew"``, ``"task"``, ``"turn"``, …).
            identity: JSON-able description of the unit. Two units with the
                same ``name`` and ``identity`` under the same parent get
                consecutive occurrences (``…:0``, ``…:1``) in the order they
                ask, exactly like repeated tool calls.

        Returns:
            The scope; its steps never collide with a sibling's.
        """
        if self._child_scopes is None:
            self._child_scopes = CallOccurrences()
        occurrence = self._child_scopes.next(name, identity)
        segment = _scope_prefix(name, identity, occurrence)
        parent = self._scope_path()
        path = f"{parent}/{segment}" if parent else segment
        return CheckpointScope(self._scope_root(), path)


class ScopeHost(_ScopeFactory):
    """The root-manager half: child scopes plus serialised store writes."""

    store: Any
    checkpoint: Any
    _write_lock: asyncio.Lock | None = None

    def _scope_path(self) -> str:
        return ""

    def _scope_root(self) -> Any:
        return self

    async def _persist(
        self,
        step: tuple[str, dict[str, Any], dict[str, Any]] | None = None,
    ) -> None:
        """Write the checkpoint (or just one new step) under the write lock.

        Stores may expose an incremental ``save_step`` that writes only the
        new step instead of re-serialising the whole accumulated state (which
        made an n-step run O(n²) bytes over the wire). Optional by design: a
        store implementing just the ``CheckpointStore`` protocol still works.
        """
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with self._write_lock:
            save_step = getattr(self.store, "save_step", None)
            if step is not None and save_step is not None:
                key, entry, trajectory_entry = step
                await save_step(self.checkpoint, key, entry, trajectory_entry)
            else:
                await self.store.save(self.checkpoint)


class CheckpointScope(_ScopeFactory):
    """A namespaced view of a run's checkpoint manager (see module docstring).

    Duck-compatible with the manager wherever a handler or an agent takes a
    ``checkpoint``: ``run_step``, ``next_occurrence``, ``approval_decision``,
    ``await_approval``, ``ensure_lease``, ``run_id``, ``checkpoint``.

    Args:
        root: The run's :class:`CheckpointManager`.
        path: The scope's key prefix.
    """

    def __init__(self, root: Any, path: str) -> None:
        self.root = root
        self.path = path
        self._occurrences = CallOccurrences()
        self._child_scopes = None

    def _scope_path(self) -> str:
        return self.path

    def _scope_root(self) -> Any:
        return self.root

    @property
    def run_id(self) -> str:
        """The run's id — approvals and resume address the run, not the scope."""
        return str(self.root.run_id)

    @property
    def ledger_run_id(self) -> str:
        """Run id for idempotency-ledger keys of calls made inside this scope.

        The ledger keys calls by ``(run_id, tool, args, occurrence)``; with
        per-scope occurrences, two scopes sharing the run id would share keys.
        """
        return f"{self.root.run_id}/{self.path}"

    @property
    def checkpoint(self) -> Any:
        return self.root.checkpoint

    @property
    def store(self) -> Any:
        return self.root.store

    @property
    def resumed(self) -> bool:
        return bool(self.root.resumed)

    def next_occurrence(self, tool_name: str, args: Any) -> int:
        """Occurrence of the next ``(tool_name, args)`` step in this scope."""
        return self._occurrences.next(tool_name, args)

    async def run_step(
        self,
        tool_name: str,
        args: Any,
        fn: Callable[[], Awaitable[Any]],
        *,
        category: str = "tool",
        occurrence: int | None = None,
    ) -> Any:
        """Execute or replay one step under this scope's prefix."""
        if occurrence is None:
            occurrence = self._occurrences.next(tool_name, args)
        return await self.root.run_step(
            f"{self.path}/{tool_name}",
            args,
            fn,
            category=category,
            occurrence=occurrence,
        )

    def approval_decision(self, tool_name: str, category: str) -> bool | None:
        return self.root.approval_decision(tool_name, category)  # type: ignore[no-any-return]

    async def await_approval(self, tool_name: str, category: str) -> None:
        await self.root.await_approval(tool_name, category)

    async def ensure_lease(self) -> None:
        await self.root.ensure_lease()


async def durable_step(
    checkpoint: Any | None,
    name: str,
    args: Any,
    fn: Callable[[], Awaitable[Any]],
    *,
    category: str = "step",
) -> Any:
    """Run ``fn`` as a checkpoint step when ``checkpoint`` is set, else directly.

    The convenience for code that is durable only when a run has a
    checkpoint: the result must be JSON-serialisable (it is persisted).

    Args:
        checkpoint: A manager, a scope, or ``None``.
        name: Step name (the ``tool_name`` of the key).
        args: Deterministic identity of the step.
        fn: Zero-arg coroutine factory doing the work.
        category: Trajectory category.

    Returns:
        ``fn()``'s result, freshly computed or replayed.
    """
    if checkpoint is None:
        return await fn()
    return await checkpoint.run_step(name, args, fn, category=category)
