"""
Persistent scratchpad memory layer.

Agents externalize internal dialogue to a section-organized Markdown
document, then re-read sections to refocus on the goal mid-loop. The
scratchpad is distinct from STM/MTM/LTM because it is *written by the
agent* and bounded per-section.

The backend is pluggable via the ``ScratchpadBackend`` protocol so the same
``Scratchpad`` facade can be wired to in-memory, file, Redis, or Postgres
storage. Default backend is in-memory and thread-isolated by ``thread_id``.
"""

from __future__ import annotations

from threading import RLock
from typing import Final, Protocol

DEFAULT_MAX_SECTION_BYTES: Final[int] = 8 * 1024
DEFAULT_MAX_SECTIONS: Final[int] = 32


class ScratchpadOverflowError(RuntimeError):
    """Raised when a section payload exceeds the configured byte cap."""


class ScratchpadBackend(Protocol):
    """Storage contract for scratchpad sections."""

    def get(self, thread_id: str, section: str) -> str | None: ...

    def set(self, thread_id: str, section: str, content: str) -> None: ...

    def delete(self, thread_id: str, section: str) -> None: ...

    def list_sections(self, thread_id: str) -> list[str]: ...

    def clear(self, thread_id: str) -> None: ...


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
        if not section:
            raise ValueError("section name must be non-empty")
        encoded = content.encode("utf-8")
        if len(encoded) > self._max_section_bytes:
            raise ScratchpadOverflowError(
                f"section '{section}' is {len(encoded)} bytes "
                f"(cap {self._max_section_bytes})"
            )
        existing = self._backend.list_sections(thread_id)
        if section not in existing and len(existing) >= self._max_sections:
            raise ScratchpadOverflowError(
                f"thread '{thread_id}' already has {len(existing)} sections "
                f"(cap {self._max_sections})"
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
        """Return a Markdown document concatenating all sections."""
        names = self.list_sections(thread_id)
        if not names:
            return ""
        parts: list[str] = []
        for name in names:
            body = self._backend.get(thread_id, name) or ""
            parts.append(f"## {name}\n\n{body}")
        return "\n\n".join(parts)

    def clear(self, thread_id: str) -> None:
        """Drop every section for the thread."""
        self._backend.clear(thread_id)
