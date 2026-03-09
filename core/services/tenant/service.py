from typing import Any, List, Optional, Tuple, cast
from pydantic import BaseModel
from core.db.connection import get_async_connection


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
                row: Optional[Tuple[Any, ...]] = cast(
                    Optional[Tuple[Any, ...]], await cursor.fetchone()
                )
                # Autocommit is enabled in pool config
                if row:
                    return Tenant(
                        id=row[0], name=row[1], status=row[2], created_at=str(row[3])
                    )
                raise ValueError("Failed to create tenant")

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """
        Retrieve a tenant by its ID.

        Args:
            tenant_id: The ID to look up.

        Returns:
            Optional[Tenant]: The tenant object if found, else None.
        """
        async with get_async_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id, name, status, created_at FROM tenants WHERE id = %s",
                    (tenant_id,),
                )
                row: Optional[Tuple[Any, ...]] = cast(
                    Optional[Tuple[Any, ...]], await cursor.fetchone()
                )
                if row:
                    return Tenant(
                        id=row[0], name=row[1], status=row[2], created_at=str(row[3])
                    )
                return None

    async def list_tenants(self) -> List[Tenant]:
        """
        Retrieve all registered tenants.

        Returns:
            List[Tenant]: List of all tenant records, ordered by creation date.
        """
        async with get_async_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id, name, status, created_at FROM tenants ORDER BY created_at DESC"
                )
                rows: List[Tuple[Any, ...]] = cast(
                    List[Tuple[Any, ...]], await cursor.fetchall()
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
