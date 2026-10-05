"""Synchronous and asynchronous clients for the BaselithCore API.

Both clients share the same surface and the same construction:

    from baselith_sdk import BaselithClient

    client = BaselithClient("https://api.example.com", api_key="sk-...")
    print(client.chat("hello").answer)

Covered: chat (plain and streamed), feedback, liveness/readiness, async agent
runs (submit, poll, ``wait_for_run``), run event streams and state history,
human-in-the-loop approvals, and webhook subscriptions and deliveries.

Auth (API key, bearer or HTTP Basic), retries with backoff on 429/5xx (per-call
policy: see :mod:`._base`), idempotency keys, timeouts and ``/v1`` routing live
in :mod:`._base`. Routes are written
as their OpenAPI templates with ``path_params`` filled in by the transport, so
the ``sdk-contract`` gate can read them from this file.
"""

from __future__ import annotations

import time  # noqa: F401  (tests patch ``client.time.sleep``)
import uuid
from typing import Any, AsyncIterator, Iterator

from . import _pagination
from ._base import (
    _DEFAULT_RESUME_TIMEOUT,
    _asse_body,
    _AsyncBase,
    _sse_body,
    _SyncBase,
)
from ._sse import (  # noqa: F401
    ChatStreamError,
    _aiter_run_events,
    _aiter_sse_chunks,
    _iter_run_events,
    _iter_sse_chunks,
)
from .models import (
    AgentRunRequest,
    AgentRunStatus,
    AgentRunSubmission,
    ApprovalDecisionRequest,
    ApprovalDecisionResult,
    ApprovalPage,
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    HealthStatus,
    ReadinessStatus,
    RunEvent,
    RunHistoryPage,
    RunResumeResult,
    WebhookCreated,
    WebhookCreateRequest,
    WebhookDeliveryPage,
    WebhookPage,
    WebhookReplay,
)


class BaselithClient(_SyncBase):
    """Synchronous client. Usable as a context manager."""

    # --- Chat, feedback, probes ---
    def chat(self, query: str, **kwargs: Any) -> ChatResponse:
        """Send a query to the agent and return the typed response."""
        req = ChatRequest(query=query, **kwargs)
        resp = self._request("POST", "/chat", json=req)
        return ChatResponse.model_validate(resp.json())

    def chat_stream(self, query: str, **kwargs: Any) -> Iterator[str]:
        """Stream the agent's answer as text chunks.

        The wire format is Server-Sent Events (see ``ChatStreamError`` for the
        mid-stream failure case); this decodes the frames and yields just the
        text.
        """
        chat_url = self._url("/chat/stream")
        kw = self._stream_kwargs(self._headers(), ChatRequest(query=query, **kwargs))
        with self._http.stream("POST", chat_url, **kw) as r:
            yield from _iter_sse_chunks(_sse_body(r))

    def submit_feedback(
        self, *, idempotency_key: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Record feedback on a generated answer."""
        req = FeedbackRequest(**kwargs)
        resp = self._request(
            "POST",
            "/feedback",
            json=req,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return resp.json()

    def health(self) -> HealthStatus:
        """Liveness probe (unauthenticated, unversioned)."""
        resp = self._request("GET", "/health", versioned=False)
        return HealthStatus.model_validate(resp.json())

    def readiness(self) -> ReadinessStatus:
        """Readiness probe (unauthenticated, unversioned)."""
        resp = self._request("GET", "/health/ready", versioned=False)
        return ReadinessStatus.model_validate(resp.json())

    # --- Async agent runs ---
    def submit_agent_run(
        self,
        query: str,
        *,
        conversation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunSubmission:
        """Queue an agent run; returns its ``task_id`` and poll URL (``202``)."""
        req = AgentRunRequest(query=query, conversation_id=conversation_id)
        resp = self._request(
            "POST",
            "/agent/async",
            json=req,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return AgentRunSubmission.model_validate(
            {**resp.json(), "location": resp.headers.get("Location")}
        )

    def get_agent_run(self, task_id: str) -> AgentRunStatus:
        """Current status of a queued run (404 for unknown or other-tenant ids)."""
        resp = self._request(
            "GET", "/agent/status/{task_id}", path_params={"task_id": task_id}
        )
        return AgentRunStatus.model_validate(resp.json())

    def wait_for_run(
        self, task_id: str, *, timeout: float = 300.0, poll_interval: float = 2.0
    ) -> AgentRunStatus:
        """Poll until the run is completed, failed or cancelled.

        Raises:
            RunTimeoutError: ``timeout`` seconds passed first.
        """
        return _pagination.wait_for_run(
            self.get_agent_run, task_id, timeout, poll_interval
        )

    # --- Runs: events and history ---
    def stream_run_events(
        self, run_id: str, *, last_event_id: str | None = None
    ) -> Iterator[RunEvent]:
        """Stream a run's structured events (SSE) until its terminal event.

        Subscribe before starting or resuming the run: the feed is fan-out
        only, not replayed (``last_event_id`` is sent but cannot rewind it).
        """
        events_url = self._url("/runs/{run_id}/events", path_params={"run_id": run_id})
        kw = self._stream_kwargs(self._stream_headers(last_event_id))
        with self._http.stream("GET", events_url, **kw) as r:
            yield from _iter_run_events(_sse_body(r))

    def get_run_history(
        self, run_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> RunHistoryPage:
        """One page of a run's version-ascending snapshot summaries."""
        resp = self._request(
            "GET",
            "/runs/{run_id}/history",
            path_params={"run_id": run_id},
            params={"limit": limit, "cursor": cursor},
        )
        return RunHistoryPage.model_validate(resp.json())

    # --- Approvals ---
    def list_approvals(
        self,
        *,
        tenant_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> ApprovalPage:
        """One page of runs paused awaiting a decision (newest first)."""
        resp = self._request(
            "GET",
            "/approvals",
            params={"tenant_id": tenant_id, "limit": limit, "cursor": cursor},
        )
        return ApprovalPage.model_validate(resp.json())

    def decide_approval(
        self,
        run_id: str,
        approved: bool,
        *,
        reason: str | None = None,
        approver: str | None = None,
        idempotency_key: str | None = None,
    ) -> ApprovalDecisionResult:
        """Record an approve/deny decision; ``approver`` is a display label only.

        Auto ``Idempotency-Key``; never re-sent after a timeout or ``5xx``.
        """
        resp = self._request(
            "POST",
            "/approvals/{run_id}/decision",
            path_params={"run_id": run_id},
            json=ApprovalDecisionRequest(
                approved=approved, reason=reason, approver=approver
            ),
            **self._unsafe_call(idempotency_key),
        )
        return ApprovalDecisionResult.model_validate(resp.json())

    def resume_run(
        self,
        run_id: str,
        *,
        timeout: float = _DEFAULT_RESUME_TIMEOUT,
        idempotency_key: str | None = None,
    ) -> RunResumeResult:
        """Resume a checkpointed run so the approval gate consumes the decision.

        The server runs the resumed loop in-request: waits up to ``timeout`` s
        (default 660). Auto ``Idempotency-Key``; never re-sent after a timeout
        or ``5xx`` (the loop may still run) — retry with the same key.
        """
        resp = self._request(
            "POST",
            "/approvals/{run_id}/resume",
            path_params={"run_id": run_id},
            **self._unsafe_call(idempotency_key),
            timeout=timeout,
        )
        return RunResumeResult.model_validate(resp.json())

    # --- Webhooks ---
    def create_webhook(
        self,
        url: str,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> WebhookCreated:
        """Register an endpoint (``webhooks:write``); the secret is returned once.

        ``kwargs``: ``event_types`` (default ``["*"]``), ``description``,
        ``headers`` (static headers sent with every delivery).
        """
        resp = self._request(
            "POST",
            "/webhooks",
            json=WebhookCreateRequest(url=url, **kwargs),
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return WebhookCreated.model_validate(resp.json())

    def list_webhooks(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> WebhookPage:
        """One page of the tenant's endpoints (``webhooks:read``)."""
        resp = self._request(
            "GET", "/webhooks", params={"limit": limit, "cursor": cursor}
        )
        return WebhookPage.model_validate(resp.json())

    def delete_webhook(self, endpoint_id: str) -> dict[str, Any]:
        """Delete an endpoint (``webhooks:write``)."""
        resp = self._request(
            "DELETE",
            "/webhooks/{endpoint_id}",
            path_params={"endpoint_id": endpoint_id},
        )
        return resp.json()

    def list_webhook_deliveries(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> WebhookDeliveryPage:
        """One page of the tenant's delivery records (``webhooks:read``)."""
        resp = self._request(
            "GET", "/webhooks/deliveries", params={"limit": limit, "cursor": cursor}
        )
        return WebhookDeliveryPage.model_validate(resp.json())

    def replay_webhook_delivery(
        self, delivery_id: str, *, idempotency_key: str | None = None
    ) -> WebhookReplay:
        """Re-attempt a delivery (``webhooks:write``); not re-sent on timeout/5xx."""
        resp = self._request(
            "POST",
            "/webhooks/deliveries/{delivery_id}/replay",
            path_params={"delivery_id": delivery_id},
            **self._unsafe_call(idempotency_key),
        )
        return WebhookReplay.model_validate(resp.json())


class AsyncBaselithClient(_AsyncBase):
    """Asynchronous client. Usable as an async context manager."""

    # --- Chat, feedback, probes ---
    async def chat(self, query: str, **kwargs: Any) -> ChatResponse:
        """Send a query to the agent and return the typed response."""
        req = ChatRequest(query=query, **kwargs)
        resp = await self._request("POST", "/chat", json=req)
        return ChatResponse.model_validate(resp.json())

    async def chat_stream(self, query: str, **kwargs: Any) -> AsyncIterator[str]:
        """Stream the agent's answer as text chunks (see the sync client)."""
        chat_url = self._url("/chat/stream")
        kw = self._stream_kwargs(self._headers(), ChatRequest(query=query, **kwargs))
        async with self._http.stream("POST", chat_url, **kw) as r:
            async for chunk in _aiter_sse_chunks(await _asse_body(r)):
                yield chunk

    async def submit_feedback(
        self, *, idempotency_key: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Record feedback on a generated answer."""
        req = FeedbackRequest(**kwargs)
        resp = await self._request(
            "POST",
            "/feedback",
            json=req,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return resp.json()

    async def health(self) -> HealthStatus:
        """Liveness probe (unauthenticated, unversioned)."""
        resp = await self._request("GET", "/health", versioned=False)
        return HealthStatus.model_validate(resp.json())

    async def readiness(self) -> ReadinessStatus:
        """Readiness probe (unauthenticated, unversioned)."""
        resp = await self._request("GET", "/health/ready", versioned=False)
        return ReadinessStatus.model_validate(resp.json())

    # --- Async agent runs (see the sync client for each method's contract) ---
    async def submit_agent_run(
        self,
        query: str,
        *,
        conversation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRunSubmission:
        req = AgentRunRequest(query=query, conversation_id=conversation_id)
        resp = await self._request(
            "POST",
            "/agent/async",
            json=req,
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return AgentRunSubmission.model_validate(
            {**resp.json(), "location": resp.headers.get("Location")}
        )

    async def get_agent_run(self, task_id: str) -> AgentRunStatus:
        resp = await self._request(
            "GET", "/agent/status/{task_id}", path_params={"task_id": task_id}
        )
        return AgentRunStatus.model_validate(resp.json())

    async def wait_for_run(
        self, task_id: str, *, timeout: float = 300.0, poll_interval: float = 2.0
    ) -> AgentRunStatus:
        return await _pagination.await_run(
            self.get_agent_run, task_id, timeout, poll_interval
        )

    # --- Runs: events and history ---
    async def stream_run_events(
        self, run_id: str, *, last_event_id: str | None = None
    ) -> AsyncIterator[RunEvent]:
        events_url = self._url("/runs/{run_id}/events", path_params={"run_id": run_id})
        kw = self._stream_kwargs(self._stream_headers(last_event_id))
        async with self._http.stream("GET", events_url, **kw) as r:
            async for event in _aiter_run_events(await _asse_body(r)):
                yield event

    async def get_run_history(
        self, run_id: str, *, limit: int | None = None, cursor: str | None = None
    ) -> RunHistoryPage:
        resp = await self._request(
            "GET",
            "/runs/{run_id}/history",
            path_params={"run_id": run_id},
            params={"limit": limit, "cursor": cursor},
        )
        return RunHistoryPage.model_validate(resp.json())

    # --- Approvals ---
    async def list_approvals(
        self,
        *,
        tenant_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> ApprovalPage:
        resp = await self._request(
            "GET",
            "/approvals",
            params={"tenant_id": tenant_id, "limit": limit, "cursor": cursor},
        )
        return ApprovalPage.model_validate(resp.json())

    async def decide_approval(
        self,
        run_id: str,
        approved: bool,
        *,
        reason: str | None = None,
        approver: str | None = None,
        idempotency_key: str | None = None,
    ) -> ApprovalDecisionResult:
        resp = await self._request(
            "POST",
            "/approvals/{run_id}/decision",
            path_params={"run_id": run_id},
            json=ApprovalDecisionRequest(
                approved=approved, reason=reason, approver=approver
            ),
            **self._unsafe_call(idempotency_key),
        )
        return ApprovalDecisionResult.model_validate(resp.json())

    async def resume_run(
        self,
        run_id: str,
        *,
        timeout: float = _DEFAULT_RESUME_TIMEOUT,
        idempotency_key: str | None = None,
    ) -> RunResumeResult:
        resp = await self._request(
            "POST",
            "/approvals/{run_id}/resume",
            path_params={"run_id": run_id},
            **self._unsafe_call(idempotency_key),
            timeout=timeout,
        )
        return RunResumeResult.model_validate(resp.json())

    # --- Webhooks ---
    async def create_webhook(
        self,
        url: str,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> WebhookCreated:
        resp = await self._request(
            "POST",
            "/webhooks",
            json=WebhookCreateRequest(url=url, **kwargs),
            idempotency_key=idempotency_key or str(uuid.uuid4()),
        )
        return WebhookCreated.model_validate(resp.json())

    async def list_webhooks(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> WebhookPage:
        resp = await self._request(
            "GET", "/webhooks", params={"limit": limit, "cursor": cursor}
        )
        return WebhookPage.model_validate(resp.json())

    async def delete_webhook(self, endpoint_id: str) -> dict[str, Any]:
        resp = await self._request(
            "DELETE",
            "/webhooks/{endpoint_id}",
            path_params={"endpoint_id": endpoint_id},
        )
        return resp.json()

    async def list_webhook_deliveries(
        self, *, limit: int | None = None, cursor: str | None = None
    ) -> WebhookDeliveryPage:
        resp = await self._request(
            "GET", "/webhooks/deliveries", params={"limit": limit, "cursor": cursor}
        )
        return WebhookDeliveryPage.model_validate(resp.json())

    async def replay_webhook_delivery(
        self, delivery_id: str, *, idempotency_key: str | None = None
    ) -> WebhookReplay:
        resp = await self._request(
            "POST",
            "/webhooks/deliveries/{delivery_id}/replay",
            path_params={"delivery_id": delivery_id},
            **self._unsafe_call(idempotency_key),
        )
        return WebhookReplay.model_validate(resp.json())
