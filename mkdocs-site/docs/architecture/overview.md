---
title: Architecture Overview
description: Comprehensive overview of the BaselithCore architecture
---

<!-- markdownlint-disable-file MD025 -->

BaselithCore is designed with a modular, layered architecture that cleanly separates infrastructure from business logic.

## Layer

 Architecture

```mermaid
graph TB
    subgraph Presentation["Presentation Layer"]
        WebUI[Web UI]
        RestAPI[REST API]
        CLI[CLI]
        WS[WebSocket/SSE]
    end
    
    subgraph Application["Application Layer"]
        Orch[Orchestrator]
        Intent[Intent Classifier]
        Router[Flow Router]
    end
    
    subgraph Domain["Plugin Layer"]
        P1[Plugin A]
        P2[Plugin B]
        P3[Plugin N...]
    end
    
    subgraph Core["Core Services"]
        LLM[LLM Service]
        Memory[Memory Manager]
        VS[VectorStore]
        Events[Event Bus]
    end
    
    subgraph Patterns["Agentic Patterns"]
        Reasoning[Reasoning]
        Reflection[Reflection]
        Planning[Planning]
        Swarm[Swarm]
    end
    
    subgraph Infrastructure["Infrastructure"]
        Redis[(Redis)]
        PG[(PostgreSQL)]
        Qdrant[(Qdrant)]
        LLMProv[LLM Provider]
        Sandbox[Sandbox Daemon]
    end
    
    Presentation --> Application
    Application --> Domain
    Application --> Patterns
    Domain --> Core
    Patterns --> Core
    Core --> Infrastructure
```

---

## Main Components

### 1. Presentation Layer

System access interfaces:

| Component     | Description                      | Endpoint   |
| ------------- | -------------------------------- | ---------- |
| **REST API**  | FastAPI for integrations         | `/api/*`   |
| **WebSocket** | Real-time communication          | `/ws`      |
| **SSE**       | Server-Sent Events for streaming | `/stream`  |
| **CLI**       | Command-line interface           | `baselith` |

### 2. Application Layer

The orchestration core:

```python
# Simplified flow
async def handle_request(query: str):
    # 1. Classify intent
    intent = await intent_classifier.classify(query)
    
    # 2. Find appropriate plugin/handler
    handler = plugin_registry.get_handler(intent)
    
    # 3. Execute with context
    context = await memory.get_context(session_id)
    result = await handler.execute(query, context)
    
    # 4. Update memory and return
    await memory.update(session_id, result)
    return result
```

### 3. Plugin Layer

Plugins contain domain logic:

```text
plugins/
├── auth/               # Authentication and roles
│   ├── plugin.py       # Entry point
│   ├── models.py       # Data models
│   └── router.py       # API endpoints
├── marketplace/        # Plugin marketplace
└── custom_plugin/      # Your plugin
```

!!! tip "Golden Rule"
    **NEVER** insert domain-specific logic into `core/`. If you're writing code mentioning a specific domain (e.g., "weather", "finance"), it must be a plugin.

### 4. Core Services

Domain-agnostic services:

| Service            | Module                      | Responsibility                 |
| ------------------ | --------------------------- | ------------------------------ |
| **LLM Service**    | `core/services/llm`         | LLM provider abstraction       |
| **Memory Manager** | `core/memory`               | Context and history management |
| **VectorStore**    | `core/services/vectorstore` | Semantic search                |
| **Event Bus**      | `core/events`               | Internal pub/sub               |
| **Task Queue**     | `core/task_queue`           | Asynchronous jobs              |

### 5. Infrastructure

Persistence and compute backends:

```yaml
# Redis distribution for different purposes
Database 0: Knowledge Graph (FalkorDB)
Database 1: Caching (session, results)
Database 2: Task Queue (RQ workers)
```

---

## Dependency Injection

The system uses a custom DI container (`core/di`):

```python
from core.di import Container, Lifetime

# Register services
container = Container()

# Singleton - single instance for entire app
container.register(
    LLMServiceProtocol, 
    LLMService, 
    lifetime=Lifetime.SINGLETON
)

# Transient - new instance per request
container.register(
    SessionContext, 
    lifetime=Lifetime.TRANSIENT
)

# Resolution
llm = container.resolve(LLMServiceProtocol)
```

### Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Registration: Container.register()
    Registration --> Lazy: delay_init=True
    Registration --> Eager: delay_init=False
    Lazy --> Resolution: First resolve()
    Eager --> Resolution: Container init
    Resolution --> Active
    Active --> Disposed: Container.dispose()
    Disposed --> [*]
```

---

## Centralized Configuration

All configuration is managed via Pydantic Settings in `core/config`:

```python
from core.config import get_services_config, get_resilience_config

# ✅ Correct - use factory functions
config = get_services_config()
llm_model = config.default_model

# ❌ Wrong - never use os.getenv directly
model = os.getenv("DEFAULT_MODEL")  # NO!
```

### Configuration Modules

| Module         | File            | Content                    |
| -------------- | --------------- | -------------------------- |
| **Base**       | `base.py`       | Fundamental configurations |
| **Services**   | `services.py`   | LLM, VectorStore, Vision   |
| **Resilience** | `resilience.py` | Circuit breaker, retry     |
| **Storage**    | `storage.py`    | Database connections       |
| **Security**   | `security.py`   | Auth, CORS, rate limiting  |

---

## Protocols and Interfaces

Every core service defines a `Protocol` (interface):

```python
# core/interfaces/llm.py
from typing import Protocol, AsyncGenerator

class LLMServiceProtocol(Protocol):
    """Interface for LLM services."""
    
    async def generate(
        self, 
        prompt: str, 
        **kwargs
    ) -> LLMResponse:
        """Generate a response."""
        ...
    
    async def stream(
        self, 
        prompt: str, 
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Generate in streaming mode."""
        ...
```

This enables:

- **Swapping implementations** without modifying client code
- **Easy mocking** in tests
- **Rigorous type checking** with mypy

---

## Lazy Loading

The system implements lazy loading at multiple levels to optimize startup time:

### Plugin Lazy Loading

```mermaid
sequenceDiagram
    participant Boot as System Boot
    participant Loader as PluginLoader
    participant AST as ResourceAnalyzer
    participant Plugin as Plugin Instance
    
    Boot->>Loader: Scan plugins/
    Loader->>AST: Analyze metadata (AST)
    Note over AST: No imports!
    AST-->>Loader: Static metadata
    Loader->>Loader: Register proxy
    
    Note over Loader,Plugin: Later, on first use...
    
    Boot->>Loader: Request "weather" handler
    Loader->>Plugin: Import + initialization
    Plugin-->>Boot: Handler ready
```

### Service Lazy Loading

```python
from core.di import LazyProxy

# Service not created until first access
llm_service = LazyProxy(lambda: container.resolve(LLMService))

# First access → creation
response = await llm_service.generate("Hello")
```

---

## Security

Security is integrated at all levels:

### Authentication

```python
from core.auth import require_auth, AuthRole

@router.get("/admin/stats")
@require_auth(roles=[AuthRole.ADMIN])
async def admin_stats(user: AuthenticatedUser = Depends(get_current_user)):
    return {"stats": "..."}
```

### Input Sanitization

```python
from core.guardrails import InputGuard

guard = InputGuard()

# Validate and sanitize user input
safe_input = await guard.process(user_input)
```

### Secrets Management

```python
# ✅ Secrets in .env, accessed via config
from core.config import get_security_config
jwt_secret = get_security_config().jwt_secret_key

# ❌ Never hardcoded
jwt_secret = "my-secret-key"  # NO!
```

---

## Next Steps

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-transit-connection: Request Flow

Discover the [complete flow of a request](request-flow.md).

</div>

<div class="feature-card" markdown>

### :material-brain: Agentic Patterns

Explore the [agentic patterns](agentic-patterns.md) implemented.

</div>

</div>
