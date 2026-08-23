from typing import Any, cast

from pydantic import BaseModel

from core.db.connection import get_async_connection
from core.resilience.retry import retry

#: Rows returned by :meth:`TenantService.list_tenants` when the caller does not
#: ask for a specific page. Large enough that every small/medium deployment
#: still sees its whole directory in one call, so the added bound is invisible
#: in practice.
DEFAULT_TENANT_PAGE_SIZE = 100

#: Hard ceiling on a single page. A caller asking for more is clamped rather
#: than allowed to reinstate the unbounded scan.
MAX_TENANT_PAGE_SIZE = 500


class Tenant(BaseModel):
    """
    Data model representing a system tenant.

    Attributes:
        id: Unique tenant identifier (slug).
        name: Human-readable display name.
        status: Operational status (active, suspended, etc.).
        created_at: ISO timestamp of creation.
    """

    id: str
    name: str
    status: str
    created_at: str


class TenantService:
    """
    Service for managing multi-tenant isolation and metadata.

    Handles CRUD operations for tenants using the primary SQL database.
    """

    @retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
    async def create_tenant(self, tenant_id: str, name: str) -> Tenant:
        """
        Register a new tenant in the system.

        Args:
            tenant_id: Unique string identifier for the tenant.
            name: Descriptive name for the tenant.

        Returns:
            Tenant: The newly created tenant record.

        Raises:
            ValueError: If creation fails or ID is taken.
        """
        async with get_async_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO tenants (id, name) VALUES (%s, %s) RETURNING id, name, status, created_at",
                    (tenant_id, name),
                )
                row: tuple[Any, ...] | None = cast(
                    tuple[Any, ...] | None, await cursor.fetchone()
                )
                # Autocommit is enabled in pool config
                if row:
                    return Tenant(
                        id=row[0], name=row[1], status=row[2], created_at=str(row[3])
                    )
                raise ValueError("Failed to create tenant")

    @retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        """
        Retrieve a tenant by its ID.

        Args:
            tenant_id: The ID to look up.

        Returns:
            Optional[Tenant]: The tenant object if found, else None.
        """
        async with get_async_connection() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, name, status, created_at FROM tenants WHERE id = %s",
                (tenant_id,),
            )
            row: tuple[Any, ...] | None = cast(
                tuple[Any, ...] | None, await cursor.fetchone()
            )
            if row:
                return Tenant(
                    id=row[0], name=row[1], status=row[2], created_at=str(row[3])
                )
            return None

    @retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
    async def list_tenants(
        self, limit: int = DEFAULT_TENANT_PAGE_SIZE, offset: int = 0
    ) -> list[Tenant]:
        """
        Retrieve registered tenants, newest first.

        The query is always bounded: an unbounded ``SELECT`` over a table that
        grows with every onboarding eventually materializes the whole tenant
        directory into memory on a single request. Callers that genuinely need
        every row page through with ``offset``.

        Args:
            limit: Maximum number of tenants to return. Clamped to
                ``[1, MAX_TENANT_PAGE_SIZE]``.
            offset: Number of rows to skip (pagination cursor).

        Returns:
            List[Tenant]: Tenant records ordered by creation date (descending).
        """
        page_size = max(1, min(limit, MAX_TENANT_PAGE_SIZE))
        async with get_async_connection() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT id, name, status, created_at FROM tenants "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (page_size, max(0, offset)),
            )
            rows: list[tuple[Any, ...]] = cast(
                list[tuple[Any, ...]], await cursor.fetchall()
            )
            return [
                Tenant(id=r[0], name=r[1], status=r[2], created_at=str(r[3]))
                for r in rows
            ]


_tenant_service = TenantService()


def get_tenant_service() -> TenantService:
    """
    Get the global TenantService singleton.

    Returns:
        TenantService: The shared service instance.
    """
    return _tenant_service
