"""In-process checkpoint store.

Split out of :mod:`core.orchestration.checkpoint` so that module keeps only the
contract (``Checkpoint``/``CheckpointStore``) and the replay manager, while the
concrete in-memory backend lives beside its Postgres counterpart
(``checkpoint_postgres``). ``checkpoint`` re-exports
:class:`InMemoryCheckpointStore`, so existing imports keep working.
"""

from __future__ import annotations

import copy
import time
from typing import Any

from core.orchestration.checkpoint import (
    DEFAULT_RESUMABLE_LIMIT,
    MAX_RESUMABLE_LIMIT,
    RESUMABLE_STATUSES,
    Checkpoint,
)

_deepcopy = copy.deepcopy


def _copy_json_shaped(value: Any) -> Any:
    """Deep-copy JSON-shaped data, delegating anything else to ``deepcopy``.

    ``copy.deepcopy`` runs on every ``save``/``load`` — i.e. once per agent
    step — and its generic machinery (memo table, ``__deepcopy__``/
    ``__reduce_ex__`` dispatch per node) costs ~4x a specialized walk over the
    plain JSON container types. A checkpoint is a JSON snapshot by contract, so
    in practice the walk covers the whole payload; **any** other value (tuple,
    set, bytes, ``datetime``, ``UUID``, non-``str`` dict key, custom object,
    dict/list subclass) is handed to ``deepcopy`` untouched, which keeps the
    result value- and type-identical to a plain ``deepcopy`` of the input.

    A JSON round-trip (``orjson.loads(orjson.dumps(x))``) is ~1.5x faster still
    but is *not* a deepcopy substitute: orjson silently rewrites tuples to
    lists, ``datetime``/``UUID`` to strings and NaN/Infinity to ``null``, which
    would corrupt run state instead of failing loudly.
    """
    kind = type(value)
    if kind is dict:
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                # Outside the JSON contract: don't reproduce key semantics
                # by hand, just deepcopy the whole mapping.
                return _deepcopy(value)
            out[key] = _copy_json_shaped(item)
        return out
    if kind is list:
        return [_copy_json_shaped(item) for item in value]
    if kind is str or kind is int or kind is float or kind is bool or value is None:
        # Immutable atoms: deepcopy returns them as-is too.
        return value
    return _deepcopy(value)


def _copy_state(data: dict[str, Any]) -> dict[str, Any]:
    """``_copy_json_shaped`` with a safety net for self-referential state.

    A cycle would recurse forever in the fast walk, while ``deepcopy``'s memo
    table handles it. Cyclic state is out of contract (it cannot be persisted
    by the Postgres backend), but the in-memory store must not become the only
    place that crashes on it.
    """
    try:
        copied: dict[str, Any] = _copy_json_shaped(data)
        return copied
    except RecursionError:
        return _deepcopy(data)


class InMemoryCheckpointStore:
    """In-process checkpoint store for tests and single-process use.

    Deep-copies on save and load so callers can't mutate stored state through a
    retained reference — matching the isolation a real datastore provides.

    With ``history_enabled`` every save also appends an immutable snapshot of
    the checkpoint at that version (state history / time-travel; see
    :mod:`core.orchestration.checkpoint_history`), trimmed to the newest
    ``history_limit`` snapshots per run (0 = unlimited).

    ``max_entries`` bounds the number of retained runs: once exceeded, the
    oldest *finished* (non-resumable) run is evicted first; only when every
    retained run is still resumable does the hard cap evict the oldest
    resumable one. ``None`` (the default) keeps the store unbounded for
    backward compatibility — but the default checkpoint-store factory always
    passes a bound, because with checkpointing on by default an unbounded
    per-run dict would leak for the process lifetime.
    """

    def __init__(
        self,
        history_enabled: bool = False,
        history_limit: int = 200,
        max_entries: int | None = None,
    ) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._history_enabled = history_enabled
        self._history_limit = history_limit
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._max_entries = max_entries

    def _evict_over_cap(self) -> None:
        """Drop the oldest runs beyond ``max_entries`` (finished ones first)."""
        cap = self._max_entries
        if cap is None:
            return
        while len(self._store) > cap:
            victim = next(
                (
                    rid
                    for rid, data in self._store.items()
                    if data.get("status") not in RESUMABLE_STATUSES
                ),
                None,
            )
            if victim is None:
                # Every retained run is resumable: the hard cap still wins —
                # an in-memory store that OOMs the process loses them all.
                victim = next(iter(self._store))
            self._store.pop(victim, None)
            self._history.pop(victim, None)

    async def save(self, checkpoint: Checkpoint) -> None:
        checkpoint.updated_at = time.time()
        checkpoint.version += 1
        data = _copy_state(checkpoint.to_dict())
        self._store[checkpoint.run_id] = data
        self._evict_over_cap()
        if self._history_enabled:
            snapshots = self._history.setdefault(checkpoint.run_id, [])
            snapshots.append(_copy_state(data))
            if 0 < self._history_limit < len(snapshots):
                del snapshots[: len(snapshots) - self._history_limit]

    async def save_step(
        self,
        checkpoint: Checkpoint,
        key: str,
        entry: dict[str, Any],
        trajectory_entry: dict[str, Any],
    ) -> None:
        """Persist ONE new step without re-copying the whole checkpoint.

        Same contract as the Postgres fast path: called after
        ``CheckpointManager.run_step`` mutated the in-memory checkpoint;
        version/updated_at bookkeeping stays in lock-step with :meth:`save`.
        Cumulative copy work over an n-step run drops from O(n²) to O(n).
        Falls back to a full :meth:`save` when the run is not stored yet.
        """
        stored = self._store.get(checkpoint.run_id)
        if stored is None:
            await self.save(checkpoint)
            return
        checkpoint.updated_at = time.time()
        checkpoint.version += 1
        stored["steps"][key] = _copy_state(entry)
        stored["trajectory"].append(_copy_json_shaped(trajectory_entry))
        stored["step"] = checkpoint.step
        stored["status"] = checkpoint.status
        stored["version"] = checkpoint.version
        stored["updated_at"] = checkpoint.updated_at
        if self._history_enabled:
            snapshots = self._history.setdefault(checkpoint.run_id, [])
            snapshots.append(_copy_state(stored))
            if 0 < self._history_limit < len(snapshots):
                del snapshots[: len(snapshots) - self._history_limit]

    async def load(self, run_id: str) -> Checkpoint | None:
        data = self._store.get(run_id)
        return Checkpoint.from_dict(_copy_state(data)) if data is not None else None

    async def delete(self, run_id: str) -> None:
        self._store.pop(run_id, None)
        self._history.pop(run_id, None)

    async def list_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        """Version-ascending summaries of the run's recorded snapshots."""
        return [
            {
                "version": d["version"],
                "status": d["status"],
                "step": d["step"],
                "updated_at": d["updated_at"],
            }
            for d in self._history.get(run_id, [])
        ]

    async def load_snapshot(self, run_id: str, version: int) -> Checkpoint | None:
        """Full checkpoint state as recorded at ``version``, or None."""
        for d in self._history.get(run_id, []):
            if d["version"] == version:
                return Checkpoint.from_dict(_copy_state(d))
        return None

    async def list_resumable(
        self, tenant_id: str | None = None, *, limit: int | None = None
    ) -> list[str]:
        """Resumable ``run_id``s, bounded like the Postgres backend.

        Args:
            tenant_id: Optional tenant scope.
            limit: Page size; ``None`` uses
                :data:`~core.orchestration.checkpoint.DEFAULT_RESUMABLE_LIMIT`.
        """
        page_size = DEFAULT_RESUMABLE_LIMIT if limit is None else limit
        page_size = max(1, min(page_size, MAX_RESUMABLE_LIMIT))
        run_ids = [
            rid
            for rid, d in self._store.items()
            if d.get("status") in RESUMABLE_STATUSES
            and (tenant_id is None or d.get("tenant_id") == tenant_id)
        ]
        return run_ids[:page_size]

    async def list_runs(
        self,
        *,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Recent run summaries, newest first (the run-explorer read path).

        Unlike :meth:`list_resumable` — which answers "what must crash recovery
        pick up" — this lists runs in *any* state so an operator can inspect a
        completed or failed run after the fact.
        """
        rows = [
            summarize_run(d)
            for d in self._store.values()
            # An unset tenant belongs to the default one — the Postgres column
            # defaults to 'default', so both backends filter identically.
            if (tenant_id is None or (d.get("tenant_id") or "default") == tenant_id)
            and (status is None or d.get("status") == status)
        ]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        return rows[: max(0, limit)] if limit else rows


def summarize_run(data: dict[str, Any]) -> dict[str, Any]:
    """Project a persisted checkpoint dict onto a listing summary.

    Deliberately omits the heavy fields (``trajectory``, ``steps``,
    ``plugin_data``, ``answer``): a list of runs must stay cheap to serve, and
    the detail endpoint returns the full state.
    """
    trajectory = data.get("trajectory") or []
    return {
        "run_id": data.get("run_id", ""),
        "tenant_id": data.get("tenant_id"),
        "query": data.get("query", ""),
        "intent": data.get("intent"),
        "status": data.get("status", ""),
        "step": int(data.get("step", 0) or 0),
        "version": int(data.get("version", 0) or 0),
        "trajectory_length": len(trajectory),
        "error": data.get("error"),
        "awaiting_approval": bool(data.get("pending_approval")),
        "created_at": float(data.get("created_at", 0.0) or 0.0),
        "updated_at": float(data.get("updated_at", 0.0) or 0.0),
    }


__all__ = ["InMemoryCheckpointStore", "summarize_run"]
