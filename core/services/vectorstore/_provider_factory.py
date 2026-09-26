"""Construct the concrete vector store provider from configuration.

Split out of ``service.py`` (module size cap).
"""

from typing import Any

from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.interfaces import VectorStoreProtocol


def build_provider(config: Any) -> VectorStoreProtocol:
    """Instantiate the provider ``config.provider`` names.

    Args:
        config: A ``VectorStoreConfig`` (or duck-compatible object).

    Returns:
        VectorStoreProtocol: The active provider (e.g., QdrantProvider).

    Raises:
        VectorStoreError: Unsupported provider, or qdrant-client missing.
    """
    if config.provider == "qdrant":
        # Lazy import: qdrant-client is an optional extra since pgvector
        # became an alternative backend.
        try:
            from core.services.vectorstore.providers.qdrant_provider import (
                QdrantProvider,
            )
        except ImportError as exc:
            raise VectorStoreError(
                "The 'qdrant' vector store backend requires qdrant-client: "
                "pip install 'baselith-core[qdrant]' — or set "
                "VECTORSTORE_PROVIDER=pgvector to use PostgreSQL instead."
            ) from exc
        return QdrantProvider(
            host=config.host,
            port=config.port,
            grpc_port=config.grpc_port,
            mode=config.qdrant_mode,
            path=config.qdrant_path,
            api_key=(
                config.qdrant_api_key.get_secret_value()
                if config.qdrant_api_key
                else None
            ),
            https=config.qdrant_https,
            timeout=config.request_timeout_seconds,
        )
    elif config.provider == "pgvector":
        from core.services.vectorstore.providers.pgvector_provider import (
            PgVectorProvider,
        )

        return PgVectorProvider()
    else:
        raise VectorStoreError(f"Unsupported provider: {config.provider}")


__all__ = ["build_provider"]
