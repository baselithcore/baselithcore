"""
Persistent scratchpad memory layer.

Agents externalize internal dialogue to a section-organized Markdown
document, then re-read sections to refocus on the goal mid-loop. The
scratchpad is distinct from STM/MTM/LTM because it is *written by the
agent* and bounded per-section.

The backend is pluggable via the ``ScratchpadBackend`` protocol so the same
``Scratchpad`` facade can be wired to in-memory, file, Redis, or Postgres
storage. Default backend is in-memory and thread-isolated by ``thread_id``.

Every operation exists in both shapes: the synchronous methods for scripts and
non-async callers, and ``a``-prefixed coroutines for the agent loop. Reaching a
network-backed scratchpad (Redis, Postgres) from an event loop through the
synchronous methods blocks that loop for a full round-trip per section, which
is why the async pair is the one the runtime should call. A backend that
implements only the synchronous protocol still works — its calls are offloaded
with :func:`asyncio.to_thread` — so third-party backends keep functioning.
"""

from __future__ import annotations

import asyncio
from threading import RLock
from typing import Any, Final, Protocol

DEFAULT_MAX_SECTION_BYTES: Final[int] = 8 * 1024
DEFAULT_MAX_SECTIONS: Final[int] = 32


class ScratchpadOverflowError(RuntimeError):
    """Raised when a section payload exceeds the configured byte cap."""


class ScratchpadBackend(Protocol):
    """Storage contract for scratchpad sections.

    The ``a``-prefixed coroutines are the ones the agent loop calls; the
    synchronous methods remain for non-async callers. A backend over a network
    store should implement the async methods natively (async client) and keep
    the synchronous ones as thin wrappers over its sync client.
    """

    def get(self, thread_id: str, section: str) -> str | None: ...

    def set(self, thread_id: str, section: str, content: str) -> None: ...

    def delete(self, thread_id: str, section: str) -> None: ...

    def list_sections(self, thread_id: str) -> list[str]: ...

    def clear(self, thread_id: str) -> None: ...

    async def aget(self, thread_id: str, section: str) -> str | None: ...

    async def aset(self, thread_id: str, section: str, content: str) -> None: ...

    async def adelete(self, thread_id: str, section: str) -> None: ...

    async def alist(self, thread_id: str) -> list[str]: ...

    async def aclear(self, thread_id: str) -> None: ...


class InMemoryScratchpadBackend:
    """Thread-isolated in-memory backend. Process-local; not durable."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}
        self._lock = RLock()

    def get(self, thread_id: str, section: str) -> str | None:
        with self._lock:
            return self._store.get(thread_id, {}).get(section)

    def set(self, thread_id: str, section: str, content: str) -> None:
        with self._lock:
            self._store.setdefault(thread_id, {})[section] = content

    def delete(self, thread_id: str, section: str) -> None:
        with self._lock:
            sections = self._store.get(thread_id)
            if sections is not None:
                sections.pop(section, None)

    def list_sections(self, thread_id: str) -> list[str]:
        with self._lock:
            return sorted(self._store.get(thread_id, {}).keys())

    def clear(self, thread_id: str) -> None:
        with self._lock:
            self._store.pop(thread_id, None)

    def get_all(self, thread_id: str) -> dict[str, str]:
        """Every section for the thread in one call."""
        with self._lock:
            return dict(self._store.get(thread_id, {}))

    # -- async surface -----------------------------------------------------
    # Process-local dict access under an RLock: there is no I/O to offload, so
    # these are genuinely free wrappers rather than thread hops.

    async def aget(self, thread_id: str, section: str) -> str | None:
        return self.get(thread_id, section)

    async def aset(self, thread_id: str, section: str, content: str) -> None:
        self.set(thread_id, section, content)

    async def adelete(self, thread_id: str, section: str) -> None:
        self.delete(thread_id, section)

    async def alist(self, thread_id: str) -> list[str]:
        return self.list_sections(thread_id)

    async def aclear(self, thread_id: str) -> None:
        self.clear(thread_id)

    async def aget_all(self, thread_id: str) -> dict[str, str]:
        return self.get_all(thread_id)


class Scratchpad:
    """
    Agent-facing scratchpad facade.

    Each agent run uses a single ``thread_id`` to namespace its sections,
    preventing cross-session leakage. Section content is treated as Markdown
    by convention but stored opaquely.
    """

    def __init__(
        self,
        backend: ScratchpadBackend | None = None,
        *,
        max_section_bytes: int = DEFAULT_MAX_SECTION_BYTES,
        max_sections: int = DEFAULT_MAX_SECTIONS,
    ) -> None:
        self._backend: ScratchpadBackend = backend or InMemoryScratchpadBackend()
        self._max_section_bytes = max_section_bytes
        self._max_sections = max_sections

    def update_section(self, thread_id: str, section: str, content: str) -> None:
        """Overwrite ``section`` for ``thread_id`` with ``content``."""
        self._validate(section, content)
        self._check_section_budget(
            thread_id, section, self._backend.list_sections(thread_id)
        )
        self._backend.set(thread_id, section, content)

    def read_section(self, thread_id: str, section: str) -> str | None:
        """Return the content of ``section`` or ``None`` if unset."""
        return self._backend.get(thread_id, section)

    def clear_section(self, thread_id: str, section: str) -> None:
        """Remove a single section."""
        self._backend.delete(thread_id, section)

    def list_sections(self, thread_id: str) -> list[str]:
        """Return the sorted list of section names for the thread."""
        return self._backend.list_sections(thread_id)

    def read_all(self, thread_id: str) -> str:
        """Return a Markdown document concatenating all sections.

        Backends exposing ``get_all`` serve the whole read in one round-trip
        (Redis HGETALL); others pay one ``get`` per section.
        """
        get_all = getattr(self._backend, "get_all", None)
        if callable(get_all):
            return self._render(dict(get_all(thread_id)))
        return self._render(
            {
                name: self._backend.get(thread_id, name) or ""
                for name in self.list_sections(thread_id)
            }
        )

    def clear(self, thread_id: str) -> None:
        """Drop every section for the thread."""
        self._backend.clear(thread_id)

    # -- async surface -----------------------------------------------------

    async def _call(self, name: str, *args: Any) -> Any:
        """Invoke the backend's ``a<name>`` coroutine, or offload the sync one.

        A backend written before the async protocol existed only has the
        synchronous methods; running those inline would block the loop for the
        whole round-trip, so they go to a worker thread instead.
        """
        method = getattr(self._backend, f"a{name}", None)
        if method is not None:
            return await method(*args)
        sync_name = "list_sections" if name == "list" else name
        return await asyncio.to_thread(getattr(self._backend, sync_name), *args)

    async def aupdate_section(self, thread_id: str, section: str, content: str) -> None:
        """Async :meth:`update_section` — same validation and caps."""
        self._validate(section, content)
        existing = await self._call("list", thread_id)
        self._check_section_budget(thread_id, section, existing)
        await self._call("set", thread_id, section, content)

    async def aread_section(self, thread_id: str, section: str) -> str | None:
        """Async :meth:`read_section`."""
        result = await self._call("get", thread_id, section)
        return None if result is None else str(result)

    async def aclear_section(self, thread_id: str, section: str) -> None:
        """Async :meth:`clear_section`."""
        await self._call("delete", thread_id, section)

    async def alist_sections(self, thread_id: str) -> list[str]:
        """Async :meth:`list_sections`."""
        return list(await self._call("list", thread_id))

    async def aread_all(self, thread_id: str) -> str:
        """Async :meth:`read_all`, preferring the backend's bulk read.

        ``aget_all`` (one HGETALL) wins; a backend offering only the
        synchronous ``get_all`` is offloaded to a thread rather than called
        inline, since that is a full round-trip on the loop. Without either,
        the read falls back to one ``get`` per section.
        """
        aget_all = getattr(self._backend, "aget_all", None)
        if aget_all is not None:
            return self._render(dict(await aget_all(thread_id)))
        get_all = getattr(self._backend, "get_all", None)
        if callable(get_all):
            return self._render(dict(await asyncio.to_thread(get_all, thread_id)))
        names = await self.alist_sections(thread_id)
        bodies = {
            name: (await self.aread_section(thread_id, name)) or "" for name in names
        }
        return self._render(bodies)

    async def aclear(self, thread_id: str) -> None:
        """Async :meth:`clear`."""
        await self._call("clear", thread_id)

    # -- shared internals --------------------------------------------------

    def _validate(self, section: str, content: str) -> None:
        """Section-name and byte-cap validation shared by both surfaces."""
        if not section:
            raise ValueError("section name must be non-empty")
        size = len(content.encode("utf-8"))
        if size > self._max_section_bytes:
            raise ScratchpadOverflowError(
                f"section '{section}' is {size} bytes (cap {self._max_section_bytes})"
            )

    def _check_section_budget(
        self, thread_id: str, section: str, existing: list[str]
    ) -> None:
        """Refuse a *new* section once the per-thread count cap is reached."""
        if section not in existing and len(existing) >= self._max_sections:
            raise ScratchpadOverflowError(
                f"thread '{thread_id}' already has {len(existing)} sections "
                f"(cap {self._max_sections})"
            )

    @staticmethod
    def _render(sections: dict[str, str]) -> str:
        """Concatenate sections into the Markdown document, sorted by name."""
        if not sections:
            return ""
        return "\n\n".join(
            f"## {name}\n\n{sections[name]}" for name in sorted(sections)
        )


__all__ = [
    "DEFAULT_MAX_SECTION_BYTES",
    "DEFAULT_MAX_SECTIONS",
    "InMemoryScratchpadBackend",
    "Scratchpad",
    "ScratchpadBackend",
    "ScratchpadOverflowError",
]
