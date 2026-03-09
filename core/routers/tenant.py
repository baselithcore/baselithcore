"""
Tenant Management Router.

Provides API endpoints for managing multi-tenant isolating context,
such as creating and listing tenants. Protected by admin credentials.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.routers.admin import verify_credentials
from core.services.tenant import get_tenant_service, Tenant

router = APIRouter(prefix="/admin/tenants", tags=["admin", "tenants"])


class CreateTenantRequest(BaseModel):
    """Payload for creating a new tenant."""

    id: str
    name: str


@router.get("", response_model=List[Tenant])
async def list_tenants(user: str = Depends(verify_credentials)):
    """List all tenants."""
    service = get_tenant_service()
    return await service.list_tenants()


@router.post("", response_model=Tenant, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    request: CreateTenantRequest, user: str = Depends(verify_credentials)
):
    """Create a new tenant."""
    service = get_tenant_service()
    try:
        if await service.get_tenant(request.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant with ID {request.id} already exists",
            )
        return await service.create_tenant(request.id, request.name)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e
