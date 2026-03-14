"""
Tests for Multi-Tenancy Implementation.
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

# Import system under test
from core.context import get_current_tenant_id, set_tenant_context, reset_tenant_context
from core.auth import AuthUser, AuthRole
from core.middleware.tenant import TenantMiddleware
from core.db.schema import ensure_schema
from core.db.feedback import insert_feedback
from core.optimization.caching import RedisCache
from core.cache.semantic_cache import SemanticLLMCache
from core.services.vectorstore.service import VectorStoreService
from core.services.indexing.service import IndexingService, IndexedDocument


class TestContext:
    def test_default_tenant_context(self):
        assert get_current_tenant_id() == "default"

    def test_context_set_reset(self):
        token = set_tenant_context("tenant-123")
        assert get_current_tenant_id() == "tenant-123"
        reset_tenant_context(token)
        assert get_current_tenant_id() == "default"

    async def test_async_context_propagation(self):
        set_tenant_context("tenant-async")

        async def inner():
            return get_current_tenant_id()

        assert await inner() == "tenant-async"
        set_tenant_context("default")


class TestTenantMiddleware:
    @pytest.mark.asyncio
    async def test_middleware_extracts_tenant(self):
        app = MagicMock()
        middleware = TenantMiddleware(app)
        request = MagicMock()
        user = AuthUser(
            user_id="u1", tenant_id="tenant-middleware", roles={AuthRole.USER}
        )
        request.user = user
        request.state.user = user

        async def call_next(req):
            return get_current_tenant_id()

        tenant_id = await middleware.dispatch(request, call_next)
        assert tenant_id == "tenant-middleware"

    @pytest.mark.asyncio
    async def test_middleware_logging_binding(self):
        app = MagicMock()
        with patch("core.middleware.tenant.structlog"):
            with patch("core.middleware.tenant.bind_contextvars") as mock_bind:
                middleware = TenantMiddleware(app)
                request = MagicMock()
                user = AuthUser(
                    user_id="u1", tenant_id="tenant-logging", roles={AuthRole.USER}
                )
                request.user = user

                async def call_next(req):
                    return "ok"

                await middleware.dispatch(request, call_next)
                mock_bind.assert_called_with(tenant_id="tenant-logging")


class TestRedisCacheIsolation:
    def test_redis_key_namespacing(self):
        with patch("core.optimization.caching.redis"):
            cache = RedisCache(prefix="test")
            cache._enabled = True
            cache._client = MagicMock()

            token = set_tenant_context("tenant-redis")
            try:
                key = cache._make_key("my-key")
                assert ":tenant-redis:" in key
            finally:
                reset_tenant_context(token)


class TestSemanticCacheIsolation:
    @pytest.mark.asyncio
    async def test_semantic_cache_isolation(self):
        # We dummy out embedder entirely
        mock_embedder = MagicMock()

        cache = SemanticLLMCache(embedder=mock_embedder)

        # We mock _compute_embedding to return a consistent vector
        cache._compute_embedding = AsyncMock(return_value=[0.1, 0.2])  # type: ignore

        # Override cosine similarity
        cache._cosine_similarity = lambda a, b: 1.0

        # Tenant A
        set_tenant_context("tenant-A")
        await cache.set("prompt", "response A")

        # Check explicit retrieval
        res = await cache.get_similar_with_score("prompt")

        assert res is not None, "res should be tuple"
        assert len(res) == 2
        val, score = res
        assert val == "response A"

        # Tenant B
        set_tenant_context("tenant-B")
        res_b = await cache.get_similar_with_score("prompt")

        # Should be no match -> (None, 0.0)
        assert res_b is not None
        val_b, score_b = res_b
        assert val_b is None

        # Tenant B sets
        await cache.set("prompt", "response B")
        res_b2 = await cache.get_similar_with_score("prompt")

        val_b2, _ = res_b2
        assert val_b2 == "response B"

        # Tenant A again
        set_tenant_context("tenant-A")
        res_a2 = await cache.get_similar_with_score("prompt")

        val_a2, _ = res_a2
        assert val_a2 == "response A"


class TestVectorStoreIsolation:
    @pytest.mark.asyncio
    async def test_search_injects_tenant_filter(self):
        # Patch configuration to return a specific provider type
        with patch(
            "core.services.vectorstore.service.get_vectorstore_config"
        ) as mock_conf:
            mock_conf.return_value.provider = "qdrant"
            mock_conf.return_value.collection_name = "test_coll"
            # Disable search cache to ensure provider is called
            mock_conf.return_value.search_cache_enabled = False

            # Patch RedisCache so we don't need real redis
            with patch("core.services.vectorstore.service.RedisCache"):
                # We need to ensure QdrantProvider is mocked when initialized
                # VectorStoreService imports QdrantProvider from provider module
                with patch(
                    "core.services.vectorstore.service.QdrantProvider"
                ) as MockProviderCls:
                    mock_provider_instance = AsyncMock()
                    MockProviderCls.return_value = mock_provider_instance

                    service = VectorStoreService()

                    set_tenant_context("tenant-vector")
                    try:
                        await service.search("test query")

                        # Verify that tenant_id was passed to one of the provider methods
                        # With AsyncMock, we can check mock_provider_instance.search.assert_called()
                        # but search calls provider.search

                        mock_provider_instance.search.assert_called()
                        args, kwargs = mock_provider_instance.search.call_args
                        assert kwargs.get("tenant_id") == "tenant-vector"

                    finally:
                        set_tenant_context("default")

    @pytest.mark.asyncio
    async def test_delete_injects_tenant_filter(self):
        with patch(
            "core.services.vectorstore.service.get_vectorstore_config"
        ) as mock_conf:
            mock_conf.return_value.provider = "qdrant"
            with (
                patch("core.services.vectorstore.service.RedisCache"),
                patch(
                    "core.services.vectorstore.service.QdrantProvider"
                ) as MockProviderCls,
            ):
                mock_provider = AsyncMock()
                # Ensure it has delete_by_filter
                mock_provider.delete_by_filter = AsyncMock()
                MockProviderCls.return_value = mock_provider

                service = VectorStoreService()
                set_tenant_context("tenant-del")
                try:
                    await service.delete_document("doc1")

                    mock_provider.delete_by_filter.assert_called()
                    args, kwargs = mock_provider.delete_by_filter.call_args
                    assert kwargs.get("tenant_id") == "tenant-del"
                finally:
                    set_tenant_context("default")


class TestIndexingServiceIsolation:
    @pytest.mark.asyncio
    async def test_indexing_payload_has_tenant(self):
        """
        Verify that IndexingService correctly delegates to VectorStoreService
        and that VectorStoreService injects the tenant_id.
        """
        import core.services.indexing.service

        with (
            patch(
                "core.services.indexing.service.get_vectorstore_service"
            ) as mock_get_vs,
            patch.object(
                core.services.indexing.service, "get_vectorstore_config"
            ) as mock_idx_conf,
            patch.object(core.services.indexing.service, "get_processing_config"),
            patch.object(
                core.services.indexing.service, "get_embedder"
            ) as mock_get_embedder,
        ):
            # Setup Indexing Config
            mock_idx_conf.return_value.collection_name = "test_coll"

            # Setup VectorStore Service (Real instance but mocked provider to catch the payload)
            with patch(
                "core.services.vectorstore.service.get_vectorstore_config"
            ) as mock_vs_conf:
                mock_vs_conf.return_value.provider = "qdrant"
                mock_vs_conf.return_value.collection_name = "test_coll"
                mock_vs_conf.return_value.search_cache_enabled = False

                with (
                    patch(
                        "core.services.vectorstore.service.RedisCache"
                    ) as MockCacheCls,
                    patch(
                        "core.services.vectorstore.service.QdrantProvider"
                    ) as MockProviderCls,
                    patch(
                        "core.services.vectorstore.service.chunk_text",
                        return_value=["chunk1"],
                    ),
                    patch(
                        "core.services.vectorstore.service.prepare_chunk_text",
                        return_value="chunk1",
                    ),
                ):
                    # Mock cache to return None (miss)
                    mock_cache = MagicMock()
                    mock_cache.get = AsyncMock(return_value=None)
                    mock_cache.set = AsyncMock()
                    MockCacheCls.return_value = mock_cache

                    mock_provider = AsyncMock()
                    MockProviderCls.return_value = mock_provider

                    real_vs = VectorStoreService()
                    # Assign the mock provider explicitly
                    real_vs.provider = mock_provider
                    mock_get_vs.return_value = real_vs

                    # Configure embedder
                    mock_embedder = MagicMock()
                    # Embedder.encode is sync in some places but used as sync in VectorStoreService._get_embeddings_cached
                    mock_embedder.encode.return_value = [[0.1, 0.2]]
                    mock_get_embedder.return_value = mock_embedder

                    svc = IndexingService()

                    token = set_tenant_context("tenant-idx")
                    try:
                        # Use a simpler doc structure
                        item = IndexedDocument(fingerprint="fp")
                        item.uid = "doc-1"
                        item.content = "text content"

                        # This will call svc._index_document -> real_vs.index -> mock_provider.upsert
                        # svc._index_document is async, so we await it
                        await svc._index_document(item)

                        # Verify that the provider received the tenant_id
                        assert mock_provider.upsert.called
                        args, kwargs = mock_provider.upsert.call_args
                        points = kwargs["points"]
                        assert len(points) == 1
                        assert points[0]["payload"]["tenant_id"] == "tenant-idx"
                    finally:
                        reset_tenant_context(token)


class TestDatabaseSchema:
    @pytest.mark.asyncio
    async def test_ensure_schema_calls_alembic(self):
        with patch("alembic.command.upgrade") as mock_upgrade:
            with patch("alembic.config.Config"):
                await ensure_schema()
                mock_upgrade.assert_called_once()


class TestFeedbackIsolation:
    @pytest.mark.asyncio
    async def test_insert_feedback_uses_tenant(self):
        from contextlib import asynccontextmanager

        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()

        @asynccontextmanager
        async def cursor_gen(*args, **kwargs):
            yield mock_cursor

        mock_conn.cursor = MagicMock(side_effect=cursor_gen)

        @asynccontextmanager
        async def get_conn_gen():
            yield mock_conn

        with patch("core.db.feedback.get_async_connection", side_effect=get_conn_gen):
            token = set_tenant_context("tenant-db")
            try:
                await insert_feedback("q", "a", "pos")
                # Verify parameters
                call = mock_cursor.execute.call_args
                assert call is not None
                sql, params = call[0]
                # Params: query, answer, feedback, conversation_id, sources, comment, tenant_id, timestamp
                # we check index 6 (0-indexed) which is tenant_id
                assert params[6] == "tenant-db"
            finally:
                reset_tenant_context(token)
