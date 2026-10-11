"""Local (development-only) model backends.

``torch`` / ``sentence_transformers`` are imported **only inside** the loader
functions below, so importing the core — or any plugin — with the ``remote``
backend never pulls them into ``sys.modules``. Models are process singletons.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_embedders: dict[str, Any] = {}
_rerankers: dict[str, Any] = {}


def load_embedder(model: str) -> Any:
    """Return the process-wide SentenceTransformer for ``model``."""
    with _lock:
        if model not in _embedders:
            from sentence_transformers import SentenceTransformer

            _embedders[model] = SentenceTransformer(model)
        return _embedders[model]


def load_reranker(model: str) -> Any:
    """Return the process-wide CrossEncoder for ``model``."""
    with _lock:
        if model not in _rerankers:
            from sentence_transformers import CrossEncoder

            _rerankers[model] = CrossEncoder(model, max_length=1024)
        return _rerankers[model]
