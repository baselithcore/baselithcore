"""Rate-limit enforcement + provider-key probe utilities."""

from __future__ import annotations

from fastapi import HTTPException, Request

from plugins.baselithbot.policies import RateLimiter


def _client_key(request: Request, prefix: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{prefix}:{host}"


def enforce(limiter: RateLimiter, request: Request, prefix: str) -> None:
    if not limiter.consume(_client_key(request, prefix)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


async def probe_provider(provider: str, api_key: str) -> tuple[bool, str]:
    """Issue a minimal authenticated request to validate ``api_key``.

    The probe is provider-specific and cheap:
        - openai:    GET /v1/models
        - anthropic: POST /v1/messages with a 1-token prompt
        - google:    GET /v1beta/models
        - ollama:    no-op (local, no key)

    Returns ``(ok, short_detail)``. Never returns any bytes of ``api_key``.
    """
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed"

    provider = provider.strip().lower()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            if provider == "anthropic":
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-5-haiku-20241022",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
                return resp.status_code in (200, 400), f"status={resp.status_code}"
            if provider == "google":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": api_key},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            if provider == "ollama":
                return True, "ollama is local; no remote auth"
            if provider == "huggingface":
                resp = await client.get(
                    "https://huggingface.co/api/whoami-v2",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return resp.status_code == 200, f"status={resp.status_code}"
            return False, f"unsupported provider: {provider}"
    except httpx.HTTPError as exc:
        return False, f"network error: {type(exc).__name__}"


__all__ = ["enforce", "probe_provider"]
