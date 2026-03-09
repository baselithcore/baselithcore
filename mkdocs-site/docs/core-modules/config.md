---
title: Configuration
description: Centralized configuration system with Pydantic Settings
---

## Overview

The `core/config` module is the **foundation** of BaselithCore's runtime behavior and operational parameters. It provides a type-safe, centralized configuration management system built on Pydantic Settings, eliminating the anti-pattern of scattered environment variable access throughout the codebase.

**Key Benefits**:

- **Type Safety** - Automatic validation prevents invalid configurations from starting
- **Centralization** - Single source of truth for all runtime settings
- **Secret Protection** - `SecretStr` types prevent accidental logging of sensitive data
- **Testability** - Factory functions enable easy mocking in unit tests
- **Environment Flexibility** - Seamless switching between dev/staging/production configs

**Core Capabilities**:

1. **Lazy Loading** - Configuration objects created only when first accessed, improving startup time
2. **Singleton Pattern** - Same config instance shared across the application, ensuring consistency
3. **Startup Validation** - Pydantic catches misconfigurations before deployment, preventing runtime failures
4. **Secret Management** - Built-in protection against accidental exposure in logs or error messages

### Why Centralized Configuration?

In baselith-cores, configuration sprawl is a critical failure point. Without centralization:

- **Inconsistency**: Different modules interpret the same environment variable differently  
- **No Validation**: Type errors and missing values fail silently until production  
- **Security Risks**: Secrets appear in stack traces, logs, and monitoring tools  
- **Testing Difficulty**: Hard-coded `os.getenv()` calls are nearly impossible to mock properly

The `core/config` architecture solves these by providing **strongly-typed configuration contracts** that are validated at application startup, not at runtime failure.

## Module Structure

```text
core/config/
├── __init__.py           # Exports and factory functions
├── base.py               # Fundamental configurations
├── app.py                # Application configuration
├── services.py           # LLM, VectorStore, Vision, Voice
├── storage.py            # Database, Redis, Qdrant
├── resilience.py         # Circuit breaker, retry, rate limiting
├── security.py           # Auth, CORS, secrets
├── orchestration.py      # Orchestrator, router
├── plugins.py            # Plugin settings
└── ...                   # Other modules
```

---

---

## When to Use

Use `core/config` for defining **static application parameters** that are set at deployment time and remain constant during execution.

**When to Use Configuration For**:

| Use Case                    | Example                               | Why Config                 |
| --------------------------- | ------------------------------------- | -------------------------- |
| **Infrastructure Settings** | Database URLs, Redis connections      | External service addresses |
| **Service Behavior**        | Rate limits, timeouts, retry policies | Operational parameters     |
| **Feature Flags**           | `ENABLE_EXPERIMENTAL_FEATURE=true`    | Gradual rollout control    |
| **LLM Parameters**          | Model names, API keys, endpoints      | Model infrastructure       |
| **Plugin Settings**         | Plugin-specific API keys, thresholds  | Plugin configuration       |

**Consider Alternatives When**:

| Scenario            | Use Instead      | Reason                         |
| ------------------- | ---------------- | ------------------------------ |
| **Runtime State**   | `core/context`   | Values change during execution |
| **User Data**       | Database models  | Persistent user-specific data  |
| **Dynamic Values**  | In-memory caches | Frequently changing values     |
| **Request Context** | Middleware/DI    | Per-request information        |

**Anti-Patterns (Do NOT Use For)**:

- Session-specific data (use `ConversationContext`)
- Agent state (use orchestrator state management)
- Temporary flags (use feature flags properly, not config hacks)
- Hard-coded business logic masquerading as "configuration"

---

## Fundamental Principle

All configuration MUST be accessed via **factory functions**, never direct `os.getenv()` calls.

!!! warning "Mandatory Rule"
    **NEVER** use `os.getenv()` directly in business code. Always use factory functions.

**Rationale**:

1. **Type Safety** - Factory returns validated Pydantic models, not optional strings
2. **Lazy Loading** - Configuration loaded only when first accessed, improving cold starts
3. **Singleton Guarantee** - `@lru_cache()` ensures same instance app-wide
4. **Startup Validation** - Invalid configs fail fast with clear error messages
5. **Mockability** - Tests can easily patch factory functions

```python
# ✅ Correct
from core.config import get_services_config
config = get_services_config()
model = config.default_model

# ❌ Wrong
import os
model = os.getenv("DEFAULT_MODEL")  # NO!
```

---

## Factory Functions

Each module exposes a factory function:

```python
from core.config import (
    get_base_config,
    get_services_config,
    get_storage_config,
    get_resilience_config,
    get_security_config,
    get_orchestration_config,
    get_plugins_config,
)

# Factories guarantee:
# 1. Lazy loading (created only on first access)
# 2. Singleton (same instance always)
# 3. Startup validation
```

---

## Configuration Modules

### Base Config

```python
from core.config import get_base_config

config = get_base_config()

print(config.debug)           # bool (mapped from CORE_DEBUG)
print(config.log_level)       # "DEBUG" | "INFO" | ... (mapped from CORE_LOG_LEVEL)
print(config.app_name)        # "Baselith-Core" (mapped from CORE_APP_NAME)
```

**`.env` Variables**:

```env
CORE_DEBUG=true
CORE_LOG_LEVEL=INFO
CORE_APP_NAME=Baselith-Core

# Multi-Tenancy (Default: true)
STRICT_TENANT_ISOLATION=true
```

!!! tip "Multi-Tenancy"
    `STRICT_TENANT_ISOLATION` is enabled by default. This ensures that every database query and event automatically respects the current tenant context. Set to `false` only for legacy projects or single-tenant migrations where logical partitioning is not yet implemented.

!!! tip "Development Tip"
    The `CORE_DEBUG` flag not only enables debug logs but also toggles the **Uvicorn Hot-Reload** feature in `backend.py`. Set `CORE_DEBUG=true` during development to see changes without restarting the server.

---

### Services Config

```python
from core.config import get_services_config

# LLM
print(config.model)               # "llama3.2"
print(config.api_base)            # "http://localhost:11434"
print(config.api_key)             # SecretStr

# VectorStore
print(config.collection_name)     # "documents"
print(config.host)                # "localhost"
print(config.port)                # 6333
print(config.embedding_model)     # "all-MiniLM-L6-v2"
```

**`.env` Variables**:

```env
LLM_MODEL=llama3.2                  # Maps to config.model
LLM_API_BASE=http://localhost:11434 # Maps to config.api_base
LLM_API_KEY=sk-...                 # Maps to config.api_key (Alias: LLM_OPENAI_API_KEY)

VECTORSTORE_COLLECTION_NAME=documents           # Maps to config.collection_name
VECTORSTORE_QDRANT_HOST=localhost               # Maps to config.host (Alias: VECTORSTORE_HOST)
VECTORSTORE_PORT=6333                           # Maps to config.port
VECTORSTORE_EMBEDDING_MODEL=all-MiniLM-L6-v2    # Maps to config.embedding_model
```

---

### Storage Config

```python
from core.config import get_storage_config

config = get_storage_config()

# PostgreSQL
print(config.database_url)        # "postgresql://..."

# Redis
print(config.redis_url)           # "redis://localhost:6379"
print(config.redis_graph_db)      # 0
print(config.redis_cache_db)      # 1
print(config.redis_queue_db)      # 2
```

**`.env` Variables**:

```env
POSTGRES_ENABLED=true
DB_HOST=localhost
DB_PORT=5432
DB_NAME=baselithcore
DB_USER=baselithcore
DB_PASSWORD=baselithcore

CACHE_BACKEND=redis
CACHE_REDIS_URL=redis://localhost:6379/1
QUEUE_REDIS_URL=redis://localhost:6379/2
```

---

### Resilience Config

```python
from core.config import get_resilience_config

config = get_resilience_config()

# Circuit Breaker
print(config.cb_fail_max)             # 5
print(config.cb_reset_timeout)        # 60.0

# Rate Limiter
print(config.api_rate_limit)          # 100
print(config.api_rate_window)         # 60

# Retry
print(config.retry_max_attempts)      # 3
print(config.retry_base_delay)        # 1.0
```

---

### Security Config

```python
from core.config import get_security_config

config = get_security_config()

print(config.secret_key)             # SecretStr
print(config.jwt_algorithm)         # "HS256"
print(config.jwt_expire_minutes)    # 30

print(config.cors_origins)          # ["http://localhost:3000"]
print(config.api_key_salt)          # SecretStr
```

---

## Validation

Pydantic validates automatically at startup:

```python
from pydantic import (
    BaseSettings, 
    validator, 
    SecretStr,
    PostgresDsn
)

class StorageConfig(BaseSettings):
    database_url: PostgresDsn
    redis_url: str
    
    @validator("redis_url")
    def validate_redis_url(cls, v):
        if not v.startswith(("redis://", "rediss://")):
            raise ValueError("Invalid Redis URL")
        return v
    
    class Config:
        env_file = ".env"
```

If configuration is invalid, the app won't start:

```text
pydantic.ValidationError: 1 validation error for StorageConfig
database_url
  invalid postgres dsn (type=value_error)
```

---

## Secrets

Secrets are managed with `SecretStr`:

```python
class SecurityConfig(BaseSettings):
    jwt_secret_key: SecretStr
    openai_api_key: SecretStr
```

```python
config = get_security_config()

# Not logged accidentally
print(config.jwt_secret_key)  # SecretStr('**********')

# Explicit access to value
actual_secret = config.jwt_secret_key.get_secret_value()
```

---

## Environment Expansion

The `plugins.yaml` file supports variable expansion:

```yaml title="configs/plugins.yaml"
plugins:
  weather:
    enabled: true
    config:
      api_key: "${WEATHER_API_KEY}"  # Expanded from .env
      base_url: "https://api.weather.com"
```

---

## Environment Overrides

You can have different `.env` files:

```text
.env              # Default (development)
.env.staging      # Staging
.env.production   # Production
```

Specify with `ENV_FILE` variable:

```bash
ENV_FILE=.env.production baselith run
```

---

## How It Works

### Configuration Loading Flow

```mermaid
sequenceDiagram
    participant App
    participant Factory
    participant Pydantic
    participant EnvFile
    
    App->>Factory: get_services_config()
    Factory->>Factory: Check @lru_cache
    alt First Call
        Factory->>Pydantic: ServicesConfig()
        Pydantic->>EnvFile: Read .env
        EnvFile-->>Pydantic: Variables
        Pydantic->>Pydantic: Validate types
        Pydantic-->>Factory: Config object
        Factory->>Factory: Cache result
    else Cached
        Factory->>Factory: Return cached
    end
    Factory-->>App: ServicesConfig instance
```

### Validation Process

Pydantic performs three-stage validation:

**Stage 1: Type Coercion**

```python
DEBUG=true          → bool(True)
REDIS_PORT=6379     → int(6379)
CORS_ORIGINS=[...]  → list[str]
```

**Stage 2: Custom Validators**

```python
@validator("database_url")
def validate_db(cls, v):
    if not v.startswith("postgresql://"):
        raise ValueError("Invalid PostgreSQL URL")
    return v
```

**Stage 3: Cross-Field Validation**

```python
@root_validator
def check_dependencies(cls, values):
    if values["use_cache"] and not values["redis_url"]:
        raise ValueError("Cache requires Redis URL")
    return values
```

**Failure Behavior**: If validation fails, the application **refuses to start**:

```text
pydantic.ValidationError: 2 validation errors for ServicesConfig
openai_api_key
  field required (type=value_error.missing)
qdrant_port
  value is not a valid integer (type=type_error.integer)
```

---

## Troubleshooting

### Error: `field required`

**Symptom**:

```text
pydantic.ValidationError: 1 validation error for StorageConfig
database_url
  field required (type=value_error.missing)
```

**Cause**: Required environment variable is not set in `.env` file.

**Solution**:

```bash
# ❌ Missing in .env
# DATABASE_URL=...

# ✅ Add to .env
echo "DATABASE_URL=postgresql://user:pass@localhost:5432/baselith" >> .env
```

**Prevention**: Always copy `.env.example` and fill all required values before first run.

---

### Error: `value is not a valid integer`

**Symptom**:

```text
value is not a valid integer (type=type_error.integer)
```

**Cause**: Environment variable has non-numeric value for integer field.

**Solution**:

```bash
# ❌ Wrong
REDIS_PORT=localhost

# ✅ Correct
REDIS_PORT=6379
```

---

### Issue: Configuration changes not reflected

**Symptom**: Modified `.env` but app still uses old values.

**Cause**: Factory functions use `@lru_cache()` - config cached after first access.

**Solution** (for development/testing):

```python
from core.config import get_services_config

# Clear cache to reload config
get_services_config.cache_clear()

# Now get fresh config
config = get_services_config()
```

**Production Note**: In production, configuration changes require **app restart**. This is intentional for immutability.

---

### Security Warning: Secrets in logs

**Symptom**: API keys appear in error messages or logs.

**Cause**: Using raw strings instead of `SecretStr`.

**Solution**:

```python
# ❌ Wrong: secrets logged
class Config(BaseSettings):
    api_key: str

logger.info(f"Config: {config}")  # Logs secret!

# ✅ Correct: SecretStr protects
class Config(BaseSettings):
    api_key: SecretStr

logger.info(f"Config: {config}")  # Prints '**********'

# Explicit access when needed
actual_key = config.api_key.get_secret_value()
```

---

## Adding New Configuration

### 1. Create Module

```python title="core/config/my_feature.py"
from pydantic import BaseSettings

class MyFeatureConfig(BaseSettings):
    enabled: bool = True
    threshold: int = 10
    api_endpoint: str
    
    class Config:
        env_prefix = "MY_FEATURE_"
        env_file = ".env"
```

### 2. Add Factory

```python title="core/config/__init__.py"
from functools import lru_cache
from .my_feature import MyFeatureConfig

@lru_cache()
def get_my_feature_config() -> MyFeatureConfig:
    return MyFeatureConfig()
```

### 3. Use in Code

```python
from core.config import get_my_feature_config

config = get_my_feature_config()
if config.enabled:
    process(config.threshold)
```

---

## Testing

Mocking configurations in tests:

```python
import pytest
from unittest.mock import patch

@pytest.fixture
def mock_config():
    with patch("core.config.get_services_config") as mock:
        mock.return_value = ServicesConfig(
            default_model="test-model",
            ollama_base_url="http://test:11434"
        )
        yield mock

def test_with_config(mock_config):
    from core.config import get_services_config
    config = get_services_config()
    assert config.default_model == "test-model"
```

Or using environment variables:

```python
def test_with_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test-model")
    
    # Invalidate cache
    get_services_config.cache_clear()
    
    config = get_services_config()
    assert config.default_model == "test-model"
```
