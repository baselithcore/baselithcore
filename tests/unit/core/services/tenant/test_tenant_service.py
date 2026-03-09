import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.services.tenant import TenantService
from core.routers.tenant import create_tenant, CreateTenantRequest


class TestTenantService:
    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_create_tenant(self, mock_get_conn):
        # Setup mocks
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()

        # Context manager for get_async_connection
        ctx_conn = MagicMock()
        ctx_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx_conn.__aexit__ = AsyncMock(return_value=None)
        mock_get_conn.return_value = ctx_conn

        # Context manager for cursor
        mock_conn.cursor = MagicMock()
        ctx_cursor = MagicMock()
        ctx_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        ctx_cursor.__aexit__ = AsyncMock(return_value=None)
        mock_conn.cursor.return_value = ctx_cursor

        # Simulate DB return
        mock_cursor.fetchone.return_value = (
            "tenant-1",
            "My Tenant",
            "active",
            "2023-01-01T00:00:00Z",
        )

        service = TenantService()
        tenant = await service.create_tenant("tenant-1", "My Tenant")

        assert tenant.id == "tenant-1"
        assert tenant.name == "My Tenant"
        assert tenant.status == "active"

        # Verify SQL
        mock_cursor.execute.assert_called()
        assert "INSERT INTO tenants" in mock_cursor.execute.call_args[0][0]

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_list_tenants(self, mock_get_conn):
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()

        ctx_conn = MagicMock()
        ctx_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx_conn.__aexit__ = AsyncMock(return_value=None)
        mock_get_conn.return_value = ctx_conn

        mock_conn.cursor = MagicMock()
        ctx_cursor = MagicMock()
        ctx_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        ctx_cursor.__aexit__ = AsyncMock(return_value=None)
        mock_conn.cursor.return_value = ctx_cursor

        mock_cursor.fetchall.return_value = [
            ("t1", "Tenant 1", "active", "2023-01-01"),
            ("t2", "Tenant 2", "inactive", "2023-01-02"),
        ]

        service = TenantService()
        tenants = await service.list_tenants()

        assert len(tenants) == 2
        assert tenants[0].id == "t1"
        assert tenants[1].id == "t2"


class TestTenantRouter:
    @pytest.mark.asyncio
    @patch("core.routers.tenant.get_tenant_service")
    async def test_router_create_tenant(self, mock_get_service):
        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service

        # Setup mock behavior
        mock_service.get_tenant.return_value = None  # Tenant does not exist
        mock_service.create_tenant.return_value = MagicMock(id="t1", name="T1")

        # Call function directly (bypassing FastAPI Depends for unit test simplicity)
        req = CreateTenantRequest(id="t1", name="T1")
        result = await create_tenant(req, user="admin")

        assert result.id == "t1"
        mock_service.create_tenant.assert_called_with("t1", "T1")
