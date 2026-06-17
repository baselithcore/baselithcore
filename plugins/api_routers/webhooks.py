"""
Webhook Management Router.

CRUD for webhook subscriptions plus delivery inspection and replay. Access is
gated by capability scopes (``webhooks:read`` / ``webhooks:write``) enforced on
the authenticated identity, on top of the ``require_user`` auth dependency.

Mounted only when ``WEBHOOKS_ENABLED`` is set, so it adds no surface by default.
"""

from __future__ import annotations

import secrets as _secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.api.pagination import PaginationError, paginate_sequence
from core.auth.manager import AuthManager
from core.auth.types import AuthUser
from core.context import get_current_tenant_id
from core.middleware import require_user
from core.observability.logging import get_logger
from core.webhooks.service import get_webhook_service
from core.webhooks.ssrf import WebhookSSRFError

logger = get_logger(__name__)

router = APIRouter(
    prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(require_user)]
)


def _enforce(request: Request, scope: str) -> AuthUser:
    """Resolve the authenticated identity and enforce a capability scope."""
    user: Optional[AuthUser] = getattr(request.state, "user", None)
    AuthManager.enforce_scopes(user, scope)
    assert user is not None  # enforce_scopes raised otherwise
    return user


class CreateWebhookRequest(BaseModel):
    """Payload to register a webhook endpoint."""

    url: str = Field(..., description="HTTPS endpoint that will receive events")
    event_types: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Event types to subscribe to; ['*'] for all",
    )
    description: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)


class CreateWebhookResponse(BaseModel):
    """Registration result. The signing ``secret`` is returned only once."""

    endpoint: Dict[str, Any]
    secret: str


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    request: Request, payload: CreateWebhookRequest
) -> CreateWebhookResponse:
    """Register a webhook endpoint (requires ``webhooks:write``)."""
    _enforce(request, "webhooks:write")
    tenant_id = get_current_tenant_id()
    secret = f"whsec_{_secrets.token_urlsafe(32)}"
    service = get_webhook_service()
    try:
        endpoint = await service.register_endpoint(
            payload.url,
            secret,
            tenant_id=tenant_id,
            event_types=set(payload.event_types),
            description=payload.description,
            headers=payload.headers,
        )
    except WebhookSSRFError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return CreateWebhookResponse(endpoint=endpoint.redacted(), secret=secret)


@router.get("")
async def list_webhooks(request: Request) -> Dict[str, Any]:
    """List webhook endpoints for the tenant (requires ``webhooks:read``)."""
    _enforce(request, "webhooks:read")
    service = get_webhook_service()
    endpoints = await service.list_endpoints(get_current_tenant_id())
    return {"endpoints": [e.redacted() for e in endpoints]}


@router.delete("/{endpoint_id}")
async def delete_webhook(request: Request, endpoint_id: str) -> Dict[str, Any]:
    """Delete a webhook endpoint (requires ``webhooks:write``)."""
    _enforce(request, "webhooks:write")
    service = get_webhook_service()
    deleted = await service.delete_endpoint(
        endpoint_id, tenant_id=get_current_tenant_id()
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found"
        )
    return {"status": "deleted", "endpoint_id": endpoint_id}


@router.get("/deliveries")
async def list_deliveries(
    request: Request, limit: int = 50, cursor: Optional[str] = None
) -> Dict[str, Any]:
    """List delivery records for the tenant, cursor-paginated (``webhooks:read``).

    Returns ``deliveries`` plus an opaque ``next_cursor`` (and ``has_more``) to
    fetch the following page; pass it back as the ``cursor`` query parameter.
    """
    _enforce(request, "webhooks:read")
    service = get_webhook_service()
    # The store retains a bounded window; paginate over it with an opaque cursor.
    records = await service.store.list_deliveries(get_current_tenant_id(), limit=1000)
    try:
        page = paginate_sequence(
            [d.model_dump() for d in records], limit=limit, cursor=cursor
        )
    except PaginationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return {
        "deliveries": page.items,
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
    }


@router.post("/deliveries/{delivery_id}/replay")
async def replay_delivery(request: Request, delivery_id: str) -> Dict[str, Any]:
    """Re-attempt a failed delivery (requires ``webhooks:write``)."""
    _enforce(request, "webhooks:write")
    service = get_webhook_service()
    result = await service.replay_delivery(
        delivery_id, tenant_id=get_current_tenant_id()
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery or its endpoint not found",
        )
    return {"status": result.status.value, "delivery": result.model_dump()}
