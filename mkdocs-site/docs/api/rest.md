---
title: REST API
description: HTTP endpoints of the system
---



The system exposes a **REST API** based on FastAPI that provides programmatic access to the framework's functionality. Each plugin can extend the API with its custom endpoints.

---

## API Architecture

```mermaid
graph LR
    Client[HTTP Client] --> Router[FastAPI Router]
    Router --> Core[Core Endpoints]
    Router --> Plugins[Plugin Endpoints]
    
    Core --> Auth[Authentication]
    Core --> Chat[Chat/Completion]
    Core --> Admin[Admin]
    
    Plugins --> Weather[Weather Plugin]
    Plugins --> Swarm[Swarm Plugin]
    Plugins --> Custom[Custom Plugins]
```

**Base URL**: `http://localhost:8000` (configurable via `API_HOST` and `API_PORT`)

---

## Authentication

### API Key

Most endpoints require authentication via API Key in the header.

```bash
curl -H "X-API-Key: your-api-key-here" http://localhost:8000/api/chat
```

**Generate an API Key**:

```python
from core.security import generate_api_key

api_key = generate_api_key(user_id="user123")
print(api_key)  # "sk_live_xxxxxxxxxx"
```

### JWT Token (Admin)

Administrative endpoints require a JWT token.

```bash
# 1. Login
curl -X POST http://localhost:8000/api/auth/login \
  -d '{"username": "admin", "password": "secret"}'

# Response: {"access_token": "eyJ0eX AiOiJKV1QiLCJhbGc..."}

# 2. Use the token
curl -H "Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGc..." \
  http://localhost:8000/api/admin/stats
```

---

## Chat & Completion

### `POST /api/chat` - Send Message

Main endpoint to interact with the system. Send a message and receive a response processed by the orchestrator.

**Request**:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "message": "What is the capital of France?",
    "session_id": "user123-session",
    "stream": false
  }'
```

**Request Body**:

```json
{
  "message": "string",           // User message (required)
  "session_id": "string",        // Session ID for context (optional)
  "stream": false,               // If true, use SSE streaming
  "context": {},                 // Additional context (optional)
  "metadata": {}                 // Custom metadata (optional)
}
```

**Response** (200 OK):

```json
{
  "response": "The capital of France is Paris.",
  "session_id": "user123-session",
  "intent": "knowledge_query",
  "agent_used": "research-agent",
  "tokens_used": 127,
  "response_time_ms": 1234
}
```

---

### `POST /api/chat/stream` - SSE Streaming

Streaming response using Server-Sent Events (SSE). Useful for long responses to display progressively.

**Request**:

```bash
curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"message": "Tell me a long story"}'
```

**Response** (SSE Stream):

```text
data: {"type": "start", "session_id": "abc123"}

data: {"type": "token", "content": "Once"}

data: {"type": "token", "content": " upon"}

data: {"type": "token", "content": " a time..."}

data: {"type": "end", "tokens_used": 450}
```

---

## Health & Monitoring

### `GET /health` - Health Check

Check if the system is up and running.

**Response** (200 OK):

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "plugins_loaded": 5,
  "uptime_seconds": 123456,
  "dependencies": {
    "redis": "connected",
    "postgres": "connected",
    "qdrant": "connected",
    "llm": "ready"
  }
}
```

**Possible Status**:

- `healthy`: Everything working
- `degraded`: Some services unavailable
- `unhealthy`: System not operational

---

### `GET /metrics` - Prometheus Metrics

Endpoint for exporting metrics in Prometheus format.

!!! warning "Authentication Required"
    This endpoint is protected and requires Administrator HTTP Basic Authentication to prevent unauthorized scraping of system metrics.

```bash
curl -u admin:password http://localhost:8000/metrics
```

---

## Admin

!!! warning "Authentication Required"
    All `/api/admin/*` endpoints require a JWT token with ADMIN role.

### `GET /api/admin/plugins` - List Plugins

Show all plugins loaded in the system.

**Response** (200 OK):

```json
{
  "plugins": [
    {
      "name": "swarm",
      "version": "1.0.0",
      "enabled": true,
      "agents": ["SwarmOrchestrator"],
      "endpoints": 3,
      "health": "healthy"
    }
  ],
  "total": 5
}
```

---

### `GET /api/admin/stats` - System Statistics

Global system statistics.

**Response** (200 OK):

```json
{
  "requests": {
    "total": 124567,
    "success": 121234,
    "errors": 3333
  },
  "cache": {
    "hits": 45678,
    "misses": 12345,
    "hit_rate": 0.787
  },
  "llm": {
    "tokens_used": 12456789,
    "requests": 34567,
    "avg_latency_ms": 1234.5
  }
}
```

---

## Plugin Endpoints

Each plugin can expose its endpoints under `/api/<plugin-name>/`.

### Example: Weather Plugin

```bash
# Current weather
GET /api/weather/current/{city}

# Forecast
POST /api/weather/forecast
  Body: {"city": "Milan", "days": 7}
```

Consult each plugin's documentation for available endpoints.

---

## Response Codes

| Code  | Meaning               | When                                |
| ----- | --------------------- | ----------------------------------- |
| `200` | OK                    | Request completed successfully      |
| `201` | Created               | Resource created (e.g. new session) |
| `400` | Bad Request           | Invalid parameters                  |
| `401` | Unauthorized          | Missing or invalid API Key          |
| `403` | Forbidden             | Insufficient permissions            |
| `404` | Not Found             | Endpoint or resource not found      |
| `429` | Too Many Requests     | Rate limit exceeded                 |
| `500` | Internal Server Error | Server error                        |
| `503` | Service Unavailable   | System temporarily unavailable      |

---

## Errors

Error responses follow a standard format:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Missing required field: message",
    "details": {
      "field": "message",
      "expected": "string"
    }
  },
  "request_id": "req_abc123xyz"
}
```

**Common Error Codes**:

- `INVALID_REQUEST`: Missing or invalid parameters
- `AUTHENTICATION_FAILED`: Invalid API Key
- `RATE_LIMIT_EXCEEDED`: Too many requests
- `LLM_ERROR`: Error in LLM service
- `INTERNAL_ERROR`: Generic internal error

---

## Rate Limiting

The API applies rate limiting to prevent abuse.

**Default Limits**:

- `100 requests / minute` per API Key
- `1000 requests / hour` per API Key
- `10 requests / minute` for admin endpoints

**Response Headers**:

```http
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1672531200
```

Configure custom limits in `core/config/resilience.py`.

---

## Complete Examples

### Simple Chat

```python
import requests

response = requests.post(
    "http://localhost:8000/api/chat",
    headers={"X-API-Key": "your-api-key"},
    json={
        "message": "Hello, how are you?",
        "session_id": "user123"
    }
)

data = response.json()
print(data["response"])
```

---

### SSE Streaming

```python
import requests
import json

response = requests.post(
    "http://localhost:8000/api/chat/stream",
    headers={"X-API-Key": "your-api-key"},
    json={"message": "Tell me a story"},
    stream=True
)

for line in response.iter_lines():
    if line.startswith(b"data: "):
        data = json.loads(line[6:])
        if data["type"] == "token":
            print(data["content"], end="", flush=True)
```

---

## Interactive Documentation

Access interactive Swagger/OpenAPI documentation:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

From here you can test endpoints directly from the browser.

---

## Best Practices

!!! tip "Use Session ID"
    Always pass the same `session_id` to maintain conversational context across multiple requests.

!!! tip "Handle Rate Limiting"
    Implement retry with exponential backoff when receiving 429.

!!! warning "Secure API Keys"
    Don't commit API keys in code. Use environment variables.

!!! tip "Streaming for UX"
    Use `/chat/stream` for long responses to improve user experience.
