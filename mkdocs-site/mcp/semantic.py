"""Semantic (embedding) half of the documentation search.

Keyword ranking cannot cross a language: the documentation is written in
English, and an agent talking to its user in Italian asks "come firmo un
plugin?" — no English section contains ``firmo``. A multilingual embedding
model (BAAI/bge-m3, the framework's default) places the question and the
section about plugin signing next to each other whatever language either is
in, and catches synonyms keyword search misses in English too.

The layer is optional and never in the way:

* it is on only when the framework's embedding service is configured as
  ``remote`` (``BASELITH_EMBEDDING_URL``): the docs server never loads a model
  in-process, and ``DOCS_MCP_SEMANTIC=off`` switches it off outright;
* section vectors are computed in the background after start-up and cached
  on disk (``DOCS_MCP_CACHE_DIR``, default ``~/.cache/baselith-docs-mcp``),
  keyed by a hash of the embedded text, so a restart re-embeds only the
  sections that changed; an exclusive file lock makes several server
  processes started at once compute the corpus once, not once each;
* until the vectors exist, and whenever the model server fails or is slow,
  search answers from keywords alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from baselith.core.observability.logging import get_logger

from .ranking import Unit

logger = get_logger(__name__)

try:  # POSIX only; elsewhere concurrent builds simply duplicate work.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

# What one section contributes to its vector: breadcrumb, heading and the
# opening of the body. The opening says what the section is about; embedding
# whole sections would multiply the cost on a CPU model server for little gain.
_EMBED_CHARS = 700
_BATCH = 16
# Vectors are written out every this many batches, so a build interrupted by a
# restart keeps most of its work.
_SAVE_EVERY = 20
# Candidates the semantic list hands to the fusion step.
_CANDIDATES = 40
# Below this cosine similarity a section is unrelated, not merely far.
_MIN_SIMILARITY = 0.3
# A question waits this long for its own vector before keywords answer alone.
_QUERY_TIMEOUT = 5.0

_MARKUP_RE = re.compile(r"[`*_#>|\[\]{}]+|<[^>]+>|:material-[\w-]+:")


class Embedder(Protocol):
    """The two calls this layer needs from ``EmbeddingService``."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def embed_text(unit: Unit) -> str:
    """The text a section is embedded as: where it sits, then how it opens."""
    heading = unit.section.title if unit.section is not None else ""
    body = " ".join(_MARKUP_RE.sub(" ", unit.text).split())
    head = " > ".join(p for p in (unit.title, heading) if p)
    return f"{head}\n{body}"[:_EMBED_CHARS]


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def default_cache_dir() -> Path:
    configured = os.environ.get("DOCS_MCP_CACHE_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "baselith-docs-mcp"


def build_embedder() -> tuple[Embedder, str] | None:
    """The framework's embedding service and its model id, when one is usable here."""
    if os.environ.get("DOCS_MCP_SEMANTIC", "").strip().lower() in {"off", "0", "false"}:
        return None
    try:
        from baselith.core.config.inference import get_embedding_config
        from baselith.core.services.inference.embedding import EmbeddingService

        config = get_embedding_config()
        if config.backend != "remote" or not config.url:
            return None
        return EmbeddingService.from_config(config), config.model
    except Exception as exc:
        logger.warning(f"docs semantic search unavailable: {type(exc).__name__}")
        return None


class SemanticIndex:
    """Section vectors on disk and in memory, and cosine ranking over them."""

    def __init__(self, embedder: Embedder, model: str, cache_dir: Path) -> None:
        self._embedder = embedder
        slug = re.sub(r"[^\w.-]+", "_", model)
        self._path = cache_dir / f"vectors-{slug}.npz"
        self._lock_path = cache_dir / f"vectors-{slug}.lock"
        self._vectors: dict[str, np.ndarray] = {}
        # Cache key of each live section, so a query hashes nothing.
        self._keys: dict[int, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._failed_query = False

    @property
    def size(self) -> int:
        return len(self._vectors)

    # ------------------------------------------------------------------ build

    def refresh(self, units: Sequence[Unit]) -> None:
        """Start embedding whatever sections lack a vector (no-op while one runs)."""
        self._keys = {id(u): _key(embed_text(u)) for u in units}
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._build(list(units)))

    async def wait(self) -> None:
        """Until the current build is over (tests, and an orderly shutdown)."""
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _build(self, units: list[Unit]) -> None:
        texts = {_key(t): t for t in (embed_text(u) for u in units)}
        if not set(texts) - set(self._vectors):
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lock_path, "a+") as lock:
                await asyncio.to_thread(_lock, lock)
                # Another process may have filled the cache while this one waited.
                self._vectors.update(await asyncio.to_thread(_load, self._path))
                missing = [k for k in texts if k not in self._vectors]
                if not missing:
                    return
                logger.info(f"docs semantic index: embedding {len(missing)} sections")
                skipped = 0
                try:
                    for done, batch in enumerate(_batches(missing, _BATCH), start=1):
                        skipped += await self._embed_batch(batch, texts)
                        if done % _SAVE_EVERY == 0:
                            await asyncio.to_thread(
                                _save, self._path, self._current(texts)
                            )
                finally:
                    # Only the live corpus is kept: edited sections' old vectors
                    # go. Saved even when the build stops half way.
                    self._vectors = self._current(texts)
                    await asyncio.to_thread(_save, self._path, self._vectors)
                logger.info(
                    f"docs semantic index ready: {len(self._vectors)} vectors, "
                    f"{skipped} sections skipped"
                )
        except Exception as exc:
            logger.warning(
                f"docs semantic index build failed: {type(exc).__name__}: {exc}"
            )

    async def _embed_batch(self, batch: list[str], texts: dict[str, str]) -> int:
        """Embed one batch; on failure retry its sections one by one.

        One bad request (a timeout while the model server is busy, an input
        it refuses) must not throw away the corpus: the batch is retried
        section by section and only what still fails is skipped, to be tried
        again by the next build. Returns how many sections were skipped.
        """
        try:
            vectors = await self._embedder.embed_documents([texts[k] for k in batch])
        except Exception as exc:
            logger.warning(
                f"docs semantic batch failed, retrying singly: {type(exc).__name__}: {exc}"
            )
        else:
            for key, vector in zip(batch, vectors, strict=True):
                self._vectors[key] = _unit_vector(vector)
            return 0
        skipped = 0
        for key in batch:
            try:
                (vector,) = await self._embedder.embed_documents([texts[key]])
            except Exception:
                skipped += 1
                continue
            self._vectors[key] = _unit_vector(vector)
        return skipped

    def _current(self, texts: dict[str, str]) -> dict[str, np.ndarray]:
        return {k: self._vectors[k] for k in texts if k in self._vectors}

    # ------------------------------------------------------------------ query

    async def rank(
        self,
        query: str,
        units: Sequence[Unit],
        allowed: Callable[[str], bool] | None = None,
    ) -> list[Unit]:
        """Sections closest in meaning to ``query``, best first; empty on any failure."""
        candidates = [
            (unit, vector)
            for unit in units
            if (allowed is None or allowed(unit.path))
            and (vector := self._vectors.get(self._key_of(unit))) is not None
        ]
        if not candidates or not query.strip():
            return []
        try:
            raw = await asyncio.wait_for(
                self._embedder.embed_query(query), _QUERY_TIMEOUT
            )
        except Exception as exc:
            if not self._failed_query:
                logger.warning(f"docs semantic query failed: {type(exc).__name__}")
            self._failed_query = True
            return []
        self._failed_query = False
        matrix = np.stack([vector for _, vector in candidates])
        sims = matrix @ _unit_vector(raw)
        order = np.argsort(-sims)[:_CANDIDATES]
        return [candidates[i][0] for i in order if sims[i] >= _MIN_SIMILARITY]

    def _key_of(self, unit: Unit) -> str:
        key = self._keys.get(id(unit))
        return key if key is not None else _key(embed_text(unit))


def _unit_vector(vector: Sequence[float] | np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    return array / norm if norm else array


def _batches(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _lock(handle: Any) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _load(path: Path) -> dict[str, np.ndarray]:
    if not path.is_file():
        return {}
    try:
        with np.load(path, allow_pickle=False) as data:
            keys, vectors = data["keys"], data["vectors"]
            return {str(k): vectors[i] for i, k in enumerate(keys)}
    except Exception as exc:
        logger.warning(
            f"docs semantic cache unreadable, rebuilding: {type(exc).__name__}"
        )
        return {}


def _save(path: Path, vectors: dict[str, np.ndarray]) -> None:
    keys = np.array(list(vectors), dtype="<U32")
    matrix = (
        np.stack(list(vectors.values())) if vectors else np.zeros((0, 0), np.float32)
    )
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, keys=keys, vectors=matrix.astype(np.float32))
    os.replace(tmp, path)


__all__ = [
    "Embedder",
    "SemanticIndex",
    "build_embedder",
    "default_cache_dir",
    "embed_text",
]
