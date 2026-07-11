"""Task re-subscription and push-notification handlers for the A2A server.

These methods were declared in :class:`~core.a2a.protocol.A2AMethod` but had
no server handlers, so conformant peers got a generic ``method_not_found``
instead of the spec-shaped answer:

* ``tasks/resubscribe`` — re-attach to an existing task's event stream. This
  server processes messages to completion, so a resubscribe replays the
  current snapshot followed by a terminal ``status-update`` (``final: true``)
  — exactly the tail a reconnecting client needs. Unknown task ids get the
  spec's ``TaskNotFoundError`` (-32003).
* ``tasks/pushNotification/set`` / ``get`` — this agent does not deliver
  push notifications (the agent card advertises ``pushNotifications: false``),
  so both answer the spec's ``PushNotificationNotSupportedError`` (-32007)
  rather than ``method_not_found``.

Kept out of ``server.py`` to respect the module size cap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from core.a2a.protocol import JSONRPCError, JSONRPCRequest, JSONRPCResponse
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.a2a.server import A2AServer

logger = get_logger(__name__)


def push_notification_unsupported(request: JSONRPCRequest) -> JSONRPCResponse:
    """Answer a pushNotification set/get with the spec error (-32007)."""
    return JSONRPCResponse.failure(
        request.id, JSONRPCError.push_notification_not_supported()
    )


async def handle_tasks_resubscribe(
    server: A2AServer, request: JSONRPCRequest
) -> JSONRPCResponse:
    """Non-streaming ``tasks/resubscribe``: return the current task snapshot."""
    params = request.params or {}
    task_id = params.get("id")
    if not task_id:
        return JSONRPCResponse.failure(
            request.id, JSONRPCError.invalid_params("Missing 'id' in params")
        )
    task = await server.task_store.get(task_id)
    if task is None:
        return JSONRPCResponse.failure(request.id, JSONRPCError.task_not_found(task_id))
    return JSONRPCResponse.success(request.id, task.to_dict())


async def stream_tasks_resubscribe(
    server: A2AServer, request: JSONRPCRequest
) -> AsyncIterator[dict[str, Any]]:
    """Streaming ``tasks/resubscribe``: snapshot + terminal status-update.

    Mirrors the ``message/stream`` event shape so a client can reuse one
    stream consumer for both methods: the task object first, then a
    ``status-update`` event with ``final: true``.
    """
    params = request.params or {}
    task_id = params.get("id")
    if not task_id:
        yield JSONRPCResponse.failure(
            request.id, JSONRPCError.invalid_params("Missing 'id' in params")
        ).to_dict()
        return
    task = await server.task_store.get(task_id)
    if task is None:
        yield JSONRPCResponse.failure(
            request.id, JSONRPCError.task_not_found(task_id)
        ).to_dict()
        return

    yield JSONRPCResponse.success(request.id, task.to_dict()).to_dict()
    yield JSONRPCResponse.success(
        request.id,
        {
            "kind": "status-update",
            "taskId": task.id,
            "contextId": task.contextId,
            "status": task.status.to_dict(),
            "final": True,
        },
    ).to_dict()


__all__ = [
    "handle_tasks_resubscribe",
    "push_notification_unsupported",
    "stream_tasks_resubscribe",
]
