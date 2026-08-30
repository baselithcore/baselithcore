"""Anthropic SDK client construction for the provider's serving backends.

Split from ``anthropic_provider`` for the module size cap. The ``api``
backend authenticates with an Anthropic key; ``bedrock``/``vertex`` use the
SDK's native cloud clients, which authenticate through the cloud's own
credential chain (AWS SigV4 / Google ADC) and take no Anthropic key.
"""

from __future__ import annotations

from typing import Any


def build_async_client(
    anthropic_module: Any,
    *,
    backend: str,
    api_key: str | None,
    request_timeout: float,
    connect_timeout: float,
    aws_region: str | None,
    vertex_project: str | None,
    vertex_region: str | None,
) -> Any:
    """Build the AsyncAnthropic/AsyncAnthropicBedrock/AsyncAnthropicVertex client.

    ``max_retries=0``: LLMService owns retries; SDK-internal retries would
    stack with them. Explicit timeout: the SDK default is 600s.
    """
    import httpx

    shared_kwargs: dict[str, Any] = {
        "max_retries": 0,
        "timeout": httpx.Timeout(request_timeout, connect=connect_timeout),
    }
    if backend == "bedrock":
        if aws_region:
            shared_kwargs["aws_region"] = aws_region
        return anthropic_module.AsyncAnthropicBedrock(**shared_kwargs)
    if backend == "vertex":
        if vertex_project:
            shared_kwargs["project_id"] = vertex_project
        if vertex_region:
            shared_kwargs["region"] = vertex_region
        return anthropic_module.AsyncAnthropicVertex(**shared_kwargs)
    return anthropic_module.AsyncAnthropic(api_key=api_key, **shared_kwargs)


__all__ = ["build_async_client"]
