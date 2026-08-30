"""Prompt-catalog admin API.

Operator surface for durable prompt management: list the registry's names,
versions and labels; register a new prompt version; promote a label. Writes
go through the process-wide :class:`~core.prompts.sync.PromptSynchronizer`,
so they persist to the durable backend and reach every replica on its next
refresh — without a configured synchronizer (``BASELITH_PROMPT_SYNC``) the
write endpoints refuse with 503, because a promotion that silently stays
replica-local is a footgun. Reads are always served from the local registry.

Protected by the same admin Basic Auth as the admin router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.observability.logging import get_logger
from core.prompts.registry import get_prompt_registry
from core.prompts.sync import PromptSynchronizer, get_prompt_synchronizer
from core.prompts.types import PromptNotFoundError, PromptVersion
from plugins.api_routers.admin import verify_credentials

logger = get_logger(__name__)

router = APIRouter(
    prefix="/prompts",
    tags=["prompts"],
    dependencies=[Depends(verify_credentials)],
)


class PromptVersionIn(BaseModel):
    """Payload for registering one prompt version."""

    version: str = Field(min_length=1, max_length=100)
    template: str = Field(min_length=1, max_length=200_000)
    description: str | None = Field(default=None, max_length=2000)
    labels: list[str] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)


class LabelIn(BaseModel):
    """Payload for promoting a label to a version."""

    version: str = Field(min_length=1, max_length=100)


def _require_synchronizer() -> PromptSynchronizer:
    synchronizer = get_prompt_synchronizer()
    if synchronizer is None:
        raise HTTPException(
            status_code=503,
            detail="Prompt sync is disabled (set BASELITH_PROMPT_SYNC=postgres); "
            "writes would stay replica-local.",
        )
    return synchronizer


@router.get("")
async def list_prompts() -> dict[str, Any]:
    """List registered prompts with their versions and labels."""
    registry = get_prompt_registry()
    store = registry.store
    prompts = [
        {
            "name": name,
            "versions": [pv.version for pv in store.versions(name)],
            "labels": store.labels(name),
        }
        for name in store.names()
    ]
    return {"prompts": prompts, "total": len(prompts)}


@router.post("/{name}/versions", status_code=201)
async def register_version(name: str, payload: PromptVersionIn) -> dict[str, Any]:
    """Register (and persist) a new version of ``name``."""
    synchronizer = _require_synchronizer()
    version = PromptVersion(
        name=name,
        version=payload.version,
        template=payload.template,
        description=payload.description,
        labels=set(payload.labels),
        variables=payload.variables,
    )
    await synchronizer.push_version(version)
    logger.info(
        "prompt_version_registered",
        extra={"prompt": version.key(), "labels": sorted(version.labels)},
    )
    return {"name": name, "version": payload.version, "checksum": version.checksum}


@router.post("/{name}/labels/{label}")
async def promote_label(name: str, label: str, payload: LabelIn) -> dict[str, Any]:
    """Point ``label`` at an existing version of ``name`` (durable promote)."""
    synchronizer = _require_synchronizer()
    try:
        await synchronizer.push_label(name, label, payload.version)
    except PromptNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.info(
        "prompt_label_promoted",
        extra={"prompt": f"{name}@{payload.version}", "label": label},
    )
    return {"name": name, "label": label, "version": payload.version}


__all__ = ["router"]
