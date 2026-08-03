"""
Data providers and their registry.

Each subsystem that stores personal data registers a :class:`DataProvider` so
the DSR service can export and erase that data without knowing the subsystem's
internals. A provider may also implement ``purge_expired`` to participate in
retention sweeps (checked at runtime — it is optional).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class DataProvider(Protocol):
    """A source of personal data that supports export and erasure."""

    @property
    def name(self) -> str: ...

    async def export(self, subject_id: str) -> Any:
        """Return this provider's data for ``subject_id`` (JSON-serializable)."""
        ...

    async def erase(self, subject_id: str) -> int:
        """Delete this provider's data for ``subject_id``; return records removed."""
        ...


@runtime_checkable
class RetentionProvider(Protocol):
    """A provider that can purge records older than a cutoff (retention)."""

    @property
    def name(self) -> str: ...

    async def purge_expired(self, older_than_seconds: int) -> int: ...


@runtime_checkable
class RectificationProvider(Protocol):
    """A provider that supports the Art. 16 right to rectification.

    ``corrections`` maps field name to the corrected value; the provider decides
    which of its fields are rectifiable and returns how many records it changed.
    """

    @property
    def name(self) -> str: ...

    async def rectify(self, subject_id: str, corrections: dict[str, Any]) -> int: ...


@runtime_checkable
class RestrictionProvider(Protocol):
    """A provider that supports the Art. 18 right to restriction of processing.

    Restriction is *not* erasure: the data is retained but may only be stored —
    a provider typically implements this as a flag its read paths honour. It is
    also the mechanism behind an upheld Art. 21 objection.
    """

    @property
    def name(self) -> str: ...

    async def restrict(self, subject_id: str, restricted: bool) -> int: ...


class DataProviderRegistry:
    """Holds the registered :class:`DataProvider` instances."""

    def __init__(self) -> None:
        self._providers: dict[str, DataProvider] = {}

    def register(self, provider: DataProvider) -> None:
        if provider.name in self._providers:
            logger.info("data_provider_replaced", extra={"provider": provider.name})
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> bool:
        return self._providers.pop(name, None) is not None

    def get(self, name: str) -> DataProvider | None:
        return self._providers.get(name)

    def all(self) -> list[DataProvider]:
        return list(self._providers.values())


class DictDataProvider:
    """A simple in-memory provider backed by ``{subject_id: list[records]}``.

    Useful as a reference implementation and in tests. Records carry a
    ``created_at`` epoch float so retention purging works.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._restricted: set[str] = set()

    @property
    def name(self) -> str:
        return self._name

    def add(self, subject_id: str, record: dict[str, Any]) -> None:
        self._data.setdefault(subject_id, []).append(record)

    def is_restricted(self, subject_id: str) -> bool:
        """Whether processing for this subject is restricted (Art. 18)."""
        return subject_id in self._restricted

    async def rectify(self, subject_id: str, corrections: dict[str, Any]) -> int:
        """Apply Art. 16 corrections to every record held for the subject."""
        records = self._data.get(subject_id, [])
        for record in records:
            record.update(corrections)
        return len(records)

    async def restrict(self, subject_id: str, restricted: bool) -> int:
        """Flag or unflag the subject's records as restricted (Art. 18)."""
        if restricted:
            self._restricted.add(subject_id)
        else:
            self._restricted.discard(subject_id)
        return len(self._data.get(subject_id, []))

    async def export(self, subject_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(subject_id, []))

    async def erase(self, subject_id: str) -> int:
        records = self._data.pop(subject_id, [])
        return len(records)

    async def purge_expired(self, older_than_seconds: int) -> int:
        import time

        cutoff = time.time() - older_than_seconds
        removed = 0
        for subject_id, records in list(self._data.items()):
            kept = [r for r in records if r.get("created_at", 0) >= cutoff]
            removed += len(records) - len(kept)
            if kept:
                self._data[subject_id] = kept
            else:
                self._data.pop(subject_id, None)
        return removed
