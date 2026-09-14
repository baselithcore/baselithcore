from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.routers.tenant import CreateTenantRequest, create_tenant
from core.services.tenant import (
    DEFAULT_TENANT_PAGE_SIZE,
    MAX_TENANT_PAGE_SIZE,
    TenantService,
)


def _mock_cursor(mock_get_conn):
    """Wire ``get_async_connection() -> conn.cursor()`` onto a mock cursor."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = []

    ctx_conn = MagicMock()
    ctx_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx_conn.__aexit__ = AsyncMock(return_value=None)
    mock_get_conn.return_value = ctx_conn

    mock_conn.cursor = MagicMock()
    ctx_cursor = MagicMock()
    ctx_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    ctx_cursor.__aexit__ = AsyncMock(return_value=None)
    mock_conn.cursor.return_value = ctx_cursor
    return mock_cursor


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

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_list_tenants_is_always_bounded(self, mock_get_conn):
        """The listing must never issue an unbounded SELECT over `tenants`."""
        mock_cursor = _mock_cursor(mock_get_conn)

        await TenantService().list_tenants()

        sql, params = mock_cursor.execute.call_args[0]
        assert "LIMIT %s OFFSET %s" in sql
        assert params == (DEFAULT_TENANT_PAGE_SIZE, 0)

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_list_tenants_honours_pagination(self, mock_get_conn):
        mock_cursor = _mock_cursor(mock_get_conn)

        await TenantService().list_tenants(limit=10, offset=20)

        assert mock_cursor.execute.call_args[0][1] == (10, 20)

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_list_tenants_clamps_out_of_range_page(self, mock_get_conn):
        """A caller cannot reinstate the unbounded scan (or ask for 0 rows)."""
        mock_cursor = _mock_cursor(mock_get_conn)
        service = TenantService()

        await service.list_tenants(limit=10_000, offset=-5)
        assert mock_cursor.execute.call_args[0][1] == (MAX_TENANT_PAGE_SIZE, 0)

        await service.list_tenants(limit=0)
        assert mock_cursor.execute.call_args[0][1] == (1, 0)


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


class TestReservedTenantIdIsRefused:
    """``system`` cannot be provisioned.

    Migration ``010_system_tenant_rls_exemption`` grants the ``system`` tenant
    visibility of every tenant-scoped row so the framework's cross-tenant
    maintenance work can function. A tenant record with that id would let anyone
    holding a principal for it read and write the whole database, so the door
    every provisioning path goes through refuses it. The check lives in the
    service, not the router, because the router is one caller of several.
    """

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_the_service_refuses_before_touching_the_database(
        self, mock_get_conn
    ):
        from core.services.tenant import ReservedTenantIdError

        mock_cursor = _mock_cursor(mock_get_conn)

        with pytest.raises(ReservedTenantIdError, match="reserved"):
            await TenantService().create_tenant("system", "Sneaky")

        mock_cursor.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_it_is_a_value_error_for_existing_handlers(self, mock_get_conn):
        """Subclassing ``ValueError`` keeps callers that already catch it — the
        documented failure mode of ``create_tenant`` — working unchanged."""
        from core.services.tenant import ReservedTenantIdError

        _mock_cursor(mock_get_conn)

        with pytest.raises(ValueError):
            await TenantService().create_tenant("system", "Sneaky")
        assert issubclass(ReservedTenantIdError, ValueError)

    @pytest.mark.asyncio
    @patch("core.services.tenant.service.get_async_connection")
    async def test_an_ordinary_id_is_unaffected(self, mock_get_conn):
        mock_cursor = _mock_cursor(mock_get_conn)
        mock_cursor.fetchone.return_value = ("acme", "Acme", "active", "2026-01-01")

        tenant = await TenantService().create_tenant("acme", "Acme")

        assert tenant.id == "acme"
        mock_cursor.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("core.routers.tenant.get_tenant_service")
    async def test_the_admin_route_answers_400_not_500(self, mock_get_service):
        """Without the mapping it fell into the generic handler and came back as
        'An internal error occurred', which tells an operator nothing."""
        from fastapi import HTTPException

        from core.services.tenant import ReservedTenantIdError

        mock_service = AsyncMock()
        mock_get_service.return_value = mock_service
        mock_service.get_tenant.return_value = None
        mock_service.create_tenant.side_effect = ReservedTenantIdError(
            "'system' is a reserved tenant identifier and cannot be provisioned."
        )

        with pytest.raises(HTTPException) as excinfo:
            await create_tenant(
                CreateTenantRequest(id="system", name="S"), user="admin"
            )

        assert excinfo.value.status_code == 400
        assert "reserved" in excinfo.value.detail
