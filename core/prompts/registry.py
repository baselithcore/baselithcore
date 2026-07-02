"""
Prompt registry — versioned storage, label resolution, A/B selection.

The registry holds many versions per prompt name. Callers resolve a prompt by
explicit version, by label (``production`` etc.), or by "latest", then render it
with variables. For experimentation, :meth:`PromptRegistry.select_variant`
deterministically buckets a stable subject (tenant/user/session) across weighted
versions so the same subject always sees the same variant.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.prompts.rendering import render_template
from core.prompts.types import (
    PromptNotFoundError,
    PromptVersion,
    RenderedPrompt,
)

logger = get_logger(__name__)

# Resolution buckets are taken over this space; mirrors the feature-flag rollout
# hashing so A/B bucketing is consistent across the codebase.
_BUCKET_SPACE = 10_000


@runtime_checkable
class PromptStore(Protocol):
    """Storage interface for prompt versions and labels."""

    def put(self, version: PromptVersion) -> None: ...

    def get(self, name: str, version: str) -> PromptVersion | None: ...

    def versions(self, name: str) -> list[PromptVersion]: ...

    def resolve_label(self, name: str, label: str) -> str | None: ...

    def set_label(self, name: str, label: str, version: str) -> None: ...


class InMemoryPromptStore:
    """Process-local, thread-safe prompt store (insertion-ordered versions)."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, PromptVersion]] = {}
        self._order: dict[str, list[str]] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._lock = threading.RLock()

    def put(self, version: PromptVersion) -> None:
        with self._lock:
            byver = self._versions.setdefault(version.name, {})
            if version.version not in byver:
                self._order.setdefault(version.name, []).append(version.version)
            byver[version.version] = version
            for label in version.labels:
                self._labels.setdefault(version.name, {})[label] = version.version

    def get(self, name: str, version: str) -> PromptVersion | None:
        return self._versions.get(name, {}).get(version)

    def versions(self, name: str) -> list[PromptVersion]:
        order = self._order.get(name, [])
        byver = self._versions.get(name, {})
        return [byver[v] for v in order if v in byver]

    def resolve_label(self, name: str, label: str) -> str | None:
        return self._labels.get(name, {}).get(label)

    def set_label(self, name: str, label: str, version: str) -> None:
        with self._lock:
            self._labels.setdefault(name, {})[label] = version


def _bucket(name: str, subject: str) -> int:
    """Deterministic bucket in ``[0, _BUCKET_SPACE)`` for (name, subject)."""
    digest = hashlib.sha256(f"{name}:{subject}".encode()).hexdigest()
    return int(digest[:8], 16) % _BUCKET_SPACE


class PromptRegistry:
    """Facade over a :class:`PromptStore` with resolution and rendering."""

    def __init__(self, store: PromptStore | None = None) -> None:
        self._store: PromptStore = store or InMemoryPromptStore()

    @property
    def store(self) -> PromptStore:
        return self._store

    def register(
        self,
        name: str,
        template: str,
        *,
        version: str = "1",
        labels: set[str] | None = None,
        description: str | None = None,
        variables: list[str] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> PromptVersion:
        """Register a prompt version (idempotent for identical content)."""
        pv = PromptVersion(
            name=name,
            version=version,
            template=template,
            labels=set(labels or set()),
            description=description,
            variables=list(variables or []),
            metadata=dict(metadata or {}),
        )
        self._store.put(pv)
        logger.info(
            "prompt_registered",
            extra={"prompt": pv.key(), "labels": sorted(pv.labels)},
        )
        return pv

    def get(
        self,
        name: str,
        *,
        version: str | None = None,
        label: str | None = None,
    ) -> PromptVersion:
        """Resolve a prompt by explicit version, label, or latest.

        Precedence: ``version`` > ``label`` > latest registered.
        """
        if version is not None:
            pv = self._store.get(name, version)
            if pv is None:
                raise PromptNotFoundError(f"{name}@{version} not found")
            return pv
        if label is not None:
            resolved = self._store.resolve_label(name, label)
            if resolved is None:
                raise PromptNotFoundError(f"{name} has no version labelled {label!r}")
            pv = self._store.get(name, resolved)
            if pv is None:  # pragma: no cover - label points at a missing version
                raise PromptNotFoundError(
                    f"{name}@{resolved} (label {label!r}) missing"
                )
            return pv
        versions = self._store.versions(name)
        if not versions:
            raise PromptNotFoundError(f"No versions registered for {name!r}")
        return versions[-1]

    def promote(self, name: str, version: str, label: str) -> None:
        """Point ``label`` at ``version`` (e.g. promote to ``production``)."""
        if self._store.get(name, version) is None:
            raise PromptNotFoundError(f"{name}@{version} not found")
        self._store.set_label(name, label, version)
        logger.info(
            "prompt_promoted",
            extra={"prompt": f"{name}@{version}", "label": label},
        )

    def list_versions(self, name: str) -> list[PromptVersion]:
        return self._store.versions(name)

    def render(
        self,
        name: str,
        variables: Mapping[str, object] | None = None,
        *,
        version: str | None = None,
        label: str | None = None,
        strict: bool = True,
    ) -> RenderedPrompt:
        """Resolve a prompt and render it with ``variables``."""
        pv = self.get(name, version=version, label=label)
        var_map = dict(variables or {})
        text = render_template(pv.template, var_map, strict=strict)
        return RenderedPrompt(
            text=text,
            name=pv.name,
            version=pv.version,
            checksum=pv.checksum,
            variables=var_map,
        )

    def select_variant(
        self,
        name: str,
        subject: str,
        weights: Mapping[str, int],
    ) -> PromptVersion:
        """Deterministically choose a version for ``subject`` by weight.

        ``weights`` maps version → relative weight. The same ``(name, subject)``
        always resolves to the same version, so an A/B experiment is stable per
        subject. Raises if no weighted version exists in the store.
        """
        items = [(v, w) for v, w in weights.items() if w > 0]
        if not items:
            raise PromptNotFoundError(f"No weighted variants supplied for {name!r}")
        total = sum(w for _, w in items)
        point = _bucket(name, subject) % total
        cumulative = 0
        chosen = items[-1][0]
        for ver, weight in items:
            cumulative += weight
            if point < cumulative:
                chosen = ver
                break
        pv = self._store.get(name, chosen)
        if pv is None:
            raise PromptNotFoundError(f"{name}@{chosen} (variant) not found")
        return pv


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    """Get or create the global prompt registry."""
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry
