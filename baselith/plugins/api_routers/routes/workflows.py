"""
Workflow catalog API.

Operator surface over :mod:`baselith.core.workflows`: list, validate, save and
delete workflow definitions, run them (waiting for the result or in the
background), read run history and resume a run paused at a HUMAN gate.
Protected by the same admin Basic Auth as the approvals and runs routers.

The signed inbound trigger lives on a second router
(:data:`trigger_router`) with no Basic Auth: its caller is an external system
that proves itself with an HMAC signature instead.

Mounted only when ``WORKFLOWS_ENABLED`` is set (see
``ApiRoutersPlugin.get_routers``), so it adds no surface by default.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder

from baselith.core.api.pagination import PageParams, page_params, paginated
from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint import CheckpointLeaseHeldError
from baselith.core.workflows.builder import WorkflowDefinition
from baselith.core.workflows.catalog import CatalogError, is_valid_workflow_id
from baselith.core.workflows.generator import GenerationError, generate_workflow
from baselith.core.workflows.runtime import (
    AWAITING_APPROVAL,
    RunRecord,
    RuntimeBusyError,
    UnknownRunError,
    UnknownWorkflowError,
)
from baselith.core.workflows.serialization import (
    MAX_DEFINITION_BYTES,
    DefinitionError,
    definition_from_mapping,
    load_definition,
    load_mapping,
)
from baselith.core.workflows.service import WorkflowService, get_workflow_service
from baselith.core.workflows.triggers import (
    MAX_TRIGGER_BODY_BYTES,
    SIGNATURE_HEADER,
    TriggerAuthError,
)
from baselith.plugins.api_routers.routes._workflow_models import (
    WorkflowDraft,
    WorkflowDraftRequest,
    WorkflowPage,
    WorkflowRemoved,
    WorkflowRunPage,
    WorkflowRunRequest,
    WorkflowRunView,
    WorkflowSaved,
    WorkflowValidation,
)
from baselith.plugins.api_routers.routes.admin import verify_credentials
from baselith.plugins.api_routers.schemas import problem_responses

logger = get_logger(__name__)

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(verify_credentials)],
)

#: Signed inbound trigger: authenticated by signature, not by Basic Auth.
trigger_router = APIRouter(prefix="/workflows", tags=["workflows"])

_DEFINITION_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "application/yaml": {"schema": {"type": "string"}},
        },
    }
}


# ---------------------------------------------------------------- helpers


def _service() -> WorkflowService:
    service = get_workflow_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Workflows are not enabled.")
    return service


async def _read_body(request: Request, limit: int) -> bytes:
    """Read the request body, refusing anything past ``limit`` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"Body larger than {limit} bytes.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413, detail=f"Body larger than {limit} bytes."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _definition_from(body: bytes) -> WorkflowDefinition:
    try:
        return load_definition(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Body is not UTF-8.") from exc
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _summary(workflow: WorkflowDefinition) -> dict[str, Any]:
    mcp = workflow.metadata.get("mcp")
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "nodes": len(workflow.nodes),
        "schedule": workflow.schedule,
        "webhook_trigger": workflow.webhook_trigger,
        "mcp_exposed": isinstance(mcp, dict) and mcp.get("expose") is True,
    }


def _view(record: RunRecord) -> dict[str, Any]:
    # A workflow's output is arbitrary: keep it JSON-encodable.
    return jsonable_encoder(record.to_dict())


def _run_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownWorkflowError):
        return HTTPException(status_code=404, detail=f"Workflow '{exc}' not found.")
    if isinstance(exc, UnknownRunError):
        return HTTPException(status_code=404, detail=f"Run '{exc}' not found.")
    if isinstance(exc, RuntimeBusyError):
        return HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": "5"}
        )
    if isinstance(exc, CheckpointLeaseHeldError):
        return HTTPException(
            status_code=409, detail="The run is being executed elsewhere."
        )
    raise exc


# ------------------------------------------------------------ admin routes


@router.get("", response_model=WorkflowPage, responses=problem_responses(400, 401, 503))
async def list_workflows(page: PageParams = Depends(page_params)) -> dict[str, Any]:
    """List catalog workflows (cursor-paginated, ordered by id)."""
    return paginated(
        _service().catalog.list(), page, key="workflows", serialize=_summary
    )


@router.post(
    "/validate",
    response_model=WorkflowValidation,
    responses=problem_responses(401, 413, 422, 503),
    openapi_extra=_DEFINITION_BODY,
)
async def validate_workflow(request: Request) -> dict[str, Any]:
    """Validate a definition (JSON or YAML body) without saving it."""
    _service()
    workflow = _definition_from(await _read_body(request, MAX_DEFINITION_BYTES))
    errors = workflow.validate()
    return {"valid": not errors, "errors": errors}


@router.post(
    "/generate",
    response_model=WorkflowDraft,
    responses=problem_responses(401, 404, 422, 502, 503),
)
async def generate(body: WorkflowDraftRequest) -> dict[str, Any]:
    """Draft a definition from a description. Nothing is saved or run.

    The draft is validated; when the model cannot produce a valid one within
    the allowed attempts the answer is **422** with the last errors. Review the
    result, then save it with ``PUT /workflows/{id}``. This calls the language
    model, so it costs tokens.
    """
    service = _service()
    existing = None
    if body.revise_id is not None:
        existing = service.catalog.get(body.revise_id)
        if existing is None:
            raise HTTPException(
                status_code=404, detail=f"Workflow '{body.revise_id}' not found."
            )
    try:
        result = await generate_workflow(
            body.description, existing=existing, max_attempts=body.max_attempts
        )
    except GenerationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "No valid workflow was produced.",
                "errors": exc.errors[:10],
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("workflow_generation_failed error=%s", exc)
        raise HTTPException(
            status_code=502, detail="The language model call failed."
        ) from exc
    return {
        "attempts": result.attempts,
        "warnings": result.warnings,
        "yaml": result.yaml,
        "workflow": jsonable_encoder(result.definition.to_dict()),
    }


@router.get(
    "/runs", response_model=WorkflowRunPage, responses=problem_responses(401, 503)
)
async def list_runs(
    workflow_id: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Recent runs held in memory, newest first (process-local history)."""
    runs = _service().runtime.list_runs(workflow_id, limit)
    return {"runs": [_view(r) for r in runs], "count": len(runs)}


@router.get(
    "/runs/{run_id}",
    response_model=WorkflowRunView,
    responses=problem_responses(401, 404, 503),
)
async def get_run(run_id: str) -> dict[str, Any]:
    """One run's state. History is process-local and bounded."""
    record = _service().runtime.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return _view(record)


@router.post(
    "/runs/{run_id}/resume",
    response_model=WorkflowRunView,
    responses=problem_responses(401, 404, 409, 429, 503),
)
async def resume_run(run_id: str) -> dict[str, Any]:
    """Resume a run paused at a HUMAN gate once a decision is recorded.

    Record the decision with ``POST /approvals/{run_id}/decision`` first. Nodes
    that already finished replay from the checkpoint; the gate consumes the
    decision and the run continues (approved) or fails (denied).
    """
    try:
        record = await _service().runtime.resume(run_id)
    except (
        UnknownRunError,
        UnknownWorkflowError,
        RuntimeBusyError,
        CheckpointLeaseHeldError,
    ) as exc:
        raise _run_errors(exc) from exc
    return _view(record)


@router.get(
    "/{workflow_id}",
    responses=problem_responses(401, 404, 503),
    response_model=dict[str, Any],
)
async def get_workflow(workflow_id: str) -> dict[str, Any]:
    """The full definition (graph included) as JSON."""
    workflow = _service().catalog.get(workflow_id)
    if workflow is None:
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )
    return jsonable_encoder(workflow.to_dict())


@router.put(
    "/{workflow_id}",
    response_model=WorkflowSaved,
    responses=problem_responses(401, 413, 422, 503),
    openapi_extra=_DEFINITION_BODY,
)
async def save_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Create or replace a workflow from a JSON or YAML body.

    The path decides the id. A body that names a different id is refused so a
    typo cannot overwrite another workflow. The definition is validated first
    and written to the catalog directory when one is configured.
    """
    service = _service()
    if not is_valid_workflow_id(workflow_id):
        raise HTTPException(status_code=422, detail="Invalid workflow id.")
    body = await _read_body(request, MAX_DEFINITION_BYTES)
    try:
        data = load_mapping(body.decode("utf-8"))
        named = data.get("id")
        if named is not None and named != workflow_id:
            raise HTTPException(
                status_code=422,
                detail=f"Body id {named!r} does not match the path id {workflow_id!r}.",
            )
        workflow = definition_from_mapping({**data, "id": workflow_id})
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Body is not UTF-8.") from exc
    except DefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        persisted = service.catalog.register(workflow, persist=True)
    except CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("workflow_saved id=%s persisted=%s", workflow_id, persisted)
    return {"id": workflow_id, "persisted": persisted}


@router.delete(
    "/{workflow_id}",
    response_model=WorkflowRemoved,
    responses=problem_responses(401, 404, 503),
)
async def delete_workflow(workflow_id: str) -> dict[str, Any]:
    """Remove a workflow from the catalog and delete its file."""
    if not _service().catalog.remove(workflow_id):
        raise HTTPException(
            status_code=404, detail=f"Workflow '{workflow_id}' not found."
        )
    return {"id": workflow_id, "removed": True}


@router.post(
    "/{workflow_id}/run",
    response_model=WorkflowRunView,
    responses={
        202: {"model": WorkflowRunView, "description": "Started or awaiting approval"}
    }
    | problem_responses(401, 404, 429, 503),
)
async def run_workflow(workflow_id: str, body: WorkflowRunRequest | None = None) -> Any:
    """Run a workflow.

    With ``wait`` (the default) the response carries the finished run; a run
    that stopped at a HUMAN gate answers **202** with status
    ``awaiting_approval``. With ``wait: false`` the run starts in the background
    and the response is **202**; poll ``GET /workflows/runs/{run_id}``.
    """
    request = body or WorkflowRunRequest()
    runtime = _service().runtime
    try:
        if request.wait:
            record = await runtime.run(workflow_id, request.input, source="api")
        else:
            record = runtime.submit(workflow_id, request.input, source="api")
    except (UnknownWorkflowError, RuntimeBusyError) as exc:
        raise _run_errors(exc) from exc
    return _accepted_or_ok(record)


def _accepted_or_ok(record: RunRecord) -> Any:
    from fastapi.responses import JSONResponse

    pending = record.status in ("running", AWAITING_APPROVAL)
    return JSONResponse(_view(record), status_code=202 if pending else 200)


# ----------------------------------------------------------- inbound trigger


@trigger_router.post(
    "/{workflow_id}/trigger",
    response_model=WorkflowRunView,
    responses={202: {"model": WorkflowRunView, "description": "Accepted"}}
    | problem_responses(401, 404, 409, 413, 429, 503),
)
async def trigger_workflow(
    workflow_id: str,
    request: Request,
    wait: Annotated[bool, Query()] = False,
    signature: Annotated[
        str | None,
        Header(
            alias=SIGNATURE_HEADER,
            description="t=<unix_ts>,v1=<hex HMAC-SHA256> over '<ts>.<body>' "
            "with the workflow's derived key",
        ),
    ] = None,
) -> Any:
    """Start a workflow from an external system.

    The body (JSON, at most 1 MiB) becomes the workflow's input. The request
    must carry a fresh ``X-Baselith-Signature`` made with the key printed by
    ``baselith workflow trigger-secret <id>``; that key is valid for this
    workflow only, and a signature is accepted once. The workflow must have
    ``webhook_trigger: true``. Answers **202** with the run id, or the finished
    run with ``?wait=true``.
    """
    service = _service()
    body = await _read_body(request, MAX_TRIGGER_BODY_BYTES)
    try:
        service.verifier.verify(workflow_id, body, signature)
    except TriggerAuthError as exc:
        if not service.verifier.configured:
            raise HTTPException(
                status_code=503, detail="Workflow triggers are not configured."
            ) from exc
        raise HTTPException(
            status_code=409 if exc.replay else 401,
            detail="Signature already used." if exc.replay else "Invalid signature.",
        ) from exc

    workflow = service.catalog.get(workflow_id)
    if workflow is None or not workflow.webhook_trigger:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    payload = _trigger_payload(request, body)
    try:
        if wait:
            record = await service.runtime.run(workflow_id, payload, source="trigger")
        else:
            record = service.runtime.submit(workflow_id, payload, source="trigger")
    except (UnknownWorkflowError, RuntimeBusyError) as exc:
        raise _run_errors(exc) from exc
    return _accepted_or_ok(record)


def _trigger_payload(request: Request, body: bytes) -> Any:
    """The trigger body as the workflow input: JSON when it parses, else text."""
    if not body:
        return None
    import json

    try:
        return json.loads(body)
    except ValueError:
        if "json" in request.headers.get("content-type", "").lower():
            raise HTTPException(
                status_code=422, detail="Body is not valid JSON."
            ) from None
        return body.decode("utf-8", errors="replace")
