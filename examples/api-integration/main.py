"""
API Integration Example.

Demonstrates external API integration with webhooks, rate limiting, and retry logic.
"""

import asyncio
import hashlib
import hmac
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request, Header, BackgroundTasks
from pydantic import BaseModel, Field
import uvicorn


# ============================================================================
# Configuration
# ============================================================================

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "demo-secret")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")


# ============================================================================
# Rate Limiter
# ============================================================================

class RateLimiter:
    """Simple in-memory rate limiter."""
    
    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self.requests: dict[str, list[float]] = {}
    
    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed."""
        now = time.time()
        if key not in self.requests:
            self.requests[key] = []
        
        # Remove old entries
        self.requests[key] = [t for t in self.requests[key] if now - t < 60]
        
        if len(self.requests[key]) >= self.rpm:
            return False
        
        self.requests[key].append(now)
        return True
    
    def get_wait_time(self, key: str) -> float:
        """Get seconds to wait before next request."""
        if key not in self.requests or not self.requests[key]:
            return 0
        
        oldest = min(self.requests[key])
        wait = 60 - (time.time() - oldest)
        return max(0, wait)


# ============================================================================
# Retry Logic
# ============================================================================

class RetryConfig:
    """Configuration for retry logic."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0


async def with_retry(
    func,
    *args,
    config: RetryConfig = None,
    **kwargs
) -> Any:
    """Execute function with exponential backoff retry."""
    config = config or RetryConfig()
    last_exception = None
    
    for attempt in range(config.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < config.max_retries:
                delay = min(
                    config.base_delay * (config.exponential_base ** attempt),
                    config.max_delay
                )
                await asyncio.sleep(delay)
    
    raise last_exception


# ============================================================================
# External API Client
# ============================================================================

class APIClient:
    """Generic external API client with resilience."""
    
    def __init__(self, base_url: str, headers: dict = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.rate_limiter = RateLimiter(requests_per_minute=30)
    
    async def request(
        self,
        method: str,
        endpoint: str,
        data: dict = None,
        params: dict = None,
    ) -> dict:
        """Make API request with rate limiting."""
        import httpx
        
        # Check rate limit
        if not self.rate_limiter.is_allowed(self.base_url):
            wait_time = self.rate_limiter.get_wait_time(self.base_url)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited. Retry after {wait_time:.1f} seconds"
            )
        
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        async def make_request():
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=data,
                    params=params,
                    headers=self.headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
        
        return await with_retry(make_request)
    
    async def get(self, endpoint: str, params: dict = None) -> dict:
        return await self.request("GET", endpoint, params=params)
    
    async def post(self, endpoint: str, data: dict = None) -> dict:
        return await self.request("POST", endpoint, data=data)


# ============================================================================
# Webhook Handler
# ============================================================================

@dataclass
class WebhookEvent:
    """Received webhook event."""
    id: str
    source: str
    event_type: str
    payload: dict
    received_at: str
    verified: bool


class WebhookHandler:
    """Handle incoming webhooks."""
    
    def __init__(self, secret: str):
        self.secret = secret
        self.events: list[WebhookEvent] = []
    
    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature."""
        if not signature:
            return False
        
        expected = hmac.new(
            self.secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(f"sha256={expected}", signature)
    
    async def process_event(
        self,
        event_type: str,
        payload: dict,
        verified: bool,
        source: str = "unknown"
    ) -> WebhookEvent:
        """Process incoming webhook event."""
        import uuid
        
        event = WebhookEvent(
            id=str(uuid.uuid4()),
            source=source,
            event_type=event_type,
            payload=payload,
            received_at=datetime.now().isoformat(),
            verified=verified,
        )
        self.events.append(event)
        
        # Process event based on type
        await self._handle_event(event)
        
        return event
    
    async def _handle_event(self, event: WebhookEvent):
        """Handle specific event types."""
        handlers = {
            "push": self._handle_push,
            "pull_request": self._handle_pr,
            "issue": self._handle_issue,
        }
        handler = handlers.get(event.event_type, self._handle_default)
        await handler(event)
    
    async def _handle_push(self, event: WebhookEvent):
        print(f"[WEBHOOK] Push event from {event.source}")
    
    async def _handle_pr(self, event: WebhookEvent):
        print(f"[WEBHOOK] PR event from {event.source}")
    
    async def _handle_issue(self, event: WebhookEvent):
        print(f"[WEBHOOK] Issue event from {event.source}")
    
    async def _handle_default(self, event: WebhookEvent):
        print(f"[WEBHOOK] Unknown event type: {event.event_type}")
    
    def get_events(self, limit: int = 50) -> list[WebhookEvent]:
        return self.events[-limit:]


# ============================================================================
# API Models
# ============================================================================

class FetchRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    data: dict = None


class WebhookPayload(BaseModel):
    event_type: str = "default"
    data: dict = {}


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="API Integration Example",
    description="External API integration with webhooks and resilience",
    version="1.0.0",
)

webhook_handler = WebhookHandler(WEBHOOK_SECRET)
github_client = APIClient(
    "https://api.github.com",
    headers={"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
)


@app.get("/")
async def root():
    return {
        "title": "API Integration Example",
        "features": [
            "Webhook receiver with signature verification",
            "External API client with rate limiting",
            "Exponential backoff retry logic",
            "GitHub API demo integration",
        ],
    }


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
    x_event_type: str = Header("default", alias="X-Event-Type"),
):
    """Receive and process incoming webhooks."""
    body = await request.body()
    
    # Verify signature if present
    verified = False
    if x_hub_signature_256:
        verified = webhook_handler.verify_signature(body, x_hub_signature_256)
    
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    
    # Process in background
    event = await webhook_handler.process_event(
        event_type=x_event_type,
        payload=payload,
        verified=verified,
        source=request.headers.get("User-Agent", "unknown"),
    )
    
    return {
        "status": "received",
        "event_id": event.id,
        "verified": verified,
    }


@app.get("/events")
async def list_events(limit: int = 20):
    """List received webhook events."""
    events = webhook_handler.get_events(limit)
    return {
        "total": len(webhook_handler.events),
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "source": e.source,
                "verified": e.verified,
                "received_at": e.received_at,
            }
            for e in events
        ],
    }


@app.post("/fetch")
async def fetch_external(request: FetchRequest):
    """Fetch from external API with resilience."""
    try:
        client = APIClient(request.url, request.headers)
        
        if request.method.upper() == "GET":
            result = await client.get("")
        else:
            result = await client.post("", request.data)
        
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/github/repos/{owner}")
async def github_repos(owner: str, per_page: int = 10):
    """Demo: List GitHub repos for a user."""
    if not GITHUB_TOKEN:
        return {
            "message": "GitHub token not configured",
            "demo": True,
            "repos": [
                {"name": "demo-repo-1", "stars": 100},
                {"name": "demo-repo-2", "stars": 50},
            ],
        }
    
    try:
        repos = await github_client.get(
            f"users/{owner}/repos",
            params={"per_page": per_page, "sort": "updated"}
        )
        return {
            "owner": owner,
            "repos": [
                {
                    "name": r["name"],
                    "description": r.get("description"),
                    "stars": r["stargazers_count"],
                    "url": r["html_url"],
                }
                for r in repos
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/rate-limit/status")
async def rate_limit_status():
    """Check rate limit status."""
    return {
        "github_api": {
            "requests_in_window": len(github_client.rate_limiter.requests.get("https://api.github.com", [])),
            "limit": github_client.rate_limiter.rpm,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
