---
title: Security
description: Authentication, authorization, and protection
---

**Security** is a fundamental pillar of the framework, integrated **by-design** into every component. This guide covers available protection mechanisms and best practices for keeping the system secure.

!!! warning "Security-First Mindset"
    Security is not optional. Every framework feature is designed with security as a primary requirement, not an afterthought.

---

## Security Model

The framework implements a **multi-layered** security model:

```mermaid
flowchart TB
    subgraph Network["Network Layer"]
        N1[HTTPS / TLS 1.3]
        N2[Rate Limiting]
        N3[IP Whitelisting]
    end
    
    subgraph Auth["Authentication Layer"]
        A1[API Key]
        A2[JWT Token]
        A3[OAuth2 / OIDC]
    end
    
    subgraph Authz["Authorization Layer"]
        Z1[Role-Based Access]
        Z2[Permission Checks]
        Z3[Tenant Isolation]
    end
    
    subgraph Input["Input Validation"]
        I1[Schema Validation]
        I2[Guardrails]
        I3[Sanitization]
    end
    
    subgraph Data["Data Protection"]
        D1[Encryption at Rest]
        D2[Secrets Management]
        D3[Audit Logging]
    end
    
    Network --> Auth --> Authz --> Input --> Data
```

---

## Authentication

Authentication verifies **who you are**. The framework supports multiple strategies.

### JWT (JSON Web Token)

JWT is the recommended method for web applications and APIs. Tokens are cryptographically signed and contain verifiable claims.

```python
from core.auth import create_access_token, verify_token
from datetime import timedelta

# Create token after login
token = create_access_token(
    user_id="user-123",
    roles=["user", "admin"],
    tenant_id="tenant-abc",  # Important for multi-tenancy
    expires_delta=timedelta(hours=1)
)
# Result: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

# Verify token in subsequent request
try:
    payload = verify_token(token)
    print(payload["user_id"])  # "user-123"
    print(payload["roles"])    # ["user", "admin"]
except TokenExpiredError:
    # Token expired, request refresh
    pass
except InvalidTokenError:
    # Invalid or tampered token
    pass
```

**JWT Token Structure:**

| Claim       | Description                 | Example             |
| ----------- | --------------------------- | ------------------- |
| `sub`       | User ID                     | `user-123`          |
| `roles`     | User roles                  | `["user", "admin"]` |
| `tenant_id` | Tenant affiliation          | `tenant-abc`        |
| `exp`       | Expiration (Unix timestamp) | `1672531200`        |
| `iat`       | Issued at                   | `1672527600`        |

### API Key

For server-to-server integrations or scripts, use API Keys. They're simpler but less flexible than JWT.

```python
from core.auth import validate_api_key, generate_api_key

# Generate API Key for a user
api_key = generate_api_key(
    user_id="user-123",
    name="Production Integration",
    scopes=["read", "write"]
)
# Result: "sk_live_xxxxxxxxxxxxxxxxxxxxx"

# Validation in an endpoint
@router.get("/api/data")
async def get_data(api_key: str = Header(..., alias="X-API-Key")):
    key_info = await validate_api_key(api_key)
    
    if not key_info:
        raise HTTPException(401, "Invalid API key")
    
    if "read" not in key_info.scopes:
        raise HTTPException(403, "Insufficient permissions")
    
    return await fetch_data(key_info.user_id)
```

!!! tip "API Key Best Practices"
    - Use prefixes to identify type: `sk_live_`, `sk_test_`
    - Never show complete API key after creation
    - Implement periodic key rotation
    - Log every use for audit

---

## Authorization

Authorization verifies **what you can do**. Use the role and permission system.

### Role-Based Access Control (RBAC)

```python
from core.auth import require_roles, AuthRole, get_current_user

# Available roles
class AuthRole:
    USER = "user"           # Base user
    ADMIN = "admin"         # Administrator
    SUPERADMIN = "superadmin"  # Global administrator
    SERVICE = "service"     # Service account

# Decorator to protect endpoints
@router.post("/admin/users")
@require_roles([AuthRole.ADMIN])
async def create_user(
    user_data: UserCreate,
    current_user = Depends(get_current_user)
):
    """Only admins can create new users."""
    # current_user contains authenticated user info
    logger.info(f"User {current_user.id} creating new user")
    return await user_service.create(user_data)
```

### Permission-Based Access

For more granular controls, use permissions:

```python
from core.auth import require_permissions

@router.delete("/documents/{doc_id}")
@require_permissions(["documents:delete"])
async def delete_document(doc_id: str):
    """Requires specific document deletion permission."""
    return await document_service.delete(doc_id)
```

### Programmatic Checks

For conditional logic:

```python
from core.auth import has_permission, has_role

async def process_request(user, request):
    if has_role(user, AuthRole.ADMIN):
        # Admin logic
        return await admin_processing(request)
    
    if has_permission(user, "premium:features"):
        # Premium logic
        return await premium_processing(request)
    
    return await standard_processing(request)
```

---

## Input Validation & Sanitization

Input validation prevents numerous attacks. **Never trust user input**.

### Schema Validation (Pydantic)

All inputs must pass through Pydantic models:

```python
from pydantic import BaseModel, Field, validator

class ChatInput(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: str | None = Field(None, pattern=r'^[a-zA-Z0-9\-]+$')
    
    @validator('message')
    def sanitize_message(cls, v):
        # Remove control characters
        return ''.join(c for c in v if c.isprintable() or c in '\n\t')
```

### Guardrails (LLM Input Protection)

For LLM inputs, use Guardrails to prevent prompt injection:

```python
from core.guardrails import InputGuard

guard = InputGuard()

async def process_user_input(user_input: str):
    result = await guard.process(user_input)
    
    if not result.is_safe:
        logger.warning(
            "Blocked malicious input",
            reason=result.block_reason,
            risk_score=result.risk_score
        )
        raise HTTPException(400, "Invalid input detected")
    
    # Sanitized input safe to pass to LLM
    safe_input = result.sanitized_content
    return await llm.generate(safe_input)
```

**What Guardrails Check:**

- **Prompt Injection**: Attempts to override system instructions
- **Jailbreak Attempts**: Known jailbreak patterns
- **PII Detection**: Sensitive personal data
- **Malicious Patterns**: SQL injection, XSS, etc.

---

## Security Configuration

`SecurityConfig` enforces safety at startup via Pydantic validators:

| Setting         | Default      | Notes                                                                                |
| --------------- | ------------ | ------------------------------------------------------------------------------------ |
| `SECRET_KEY`    | `None`       | **Required** when `AUTH_REQUIRED=true`. Must be at least 32 chars. Uses `SecretStr`. |
| `ADMIN_PASS`    | `None`       | Uses `SecretStr`. Emits a warning if left as "changeme".                             |
| `ALLOW_ORIGINS` | `[]` (empty) | Blocks all cross-origin by default. `["*"]` emits a warning.                         |
| `AUTH_REQUIRED` | `true`       | Enforced by default to prevent anonymous access in production.                       |

Generate a secure secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

!!! danger "Environment Files"
    `.env` files are **gitignored** and must never be committed. Use `.env.example` as a template.

## Secrets Management

**Never hardcode secrets in code**. Always use the configuration system.

### Correct Configuration

```python
# ✅ Correct: use config
from core.config import get_security_config

config = get_security_config()
secret = config.secret_key

# ✅ Correct: use environment variables
import os
api_key = os.environ.get("EXTERNAL_API_KEY")
```

### Anti-Patterns to Avoid

```python
# ❌ NEVER do this
JWT_SECRET = "my-super-secret-key"  # Hardcoded!

# ❌ NEVER commit .env files with real secrets
# .env in repo with: OPENAI_API_KEY=sk-xxxxx

# ❌ NEVER log secrets
logger.info(f"Using API key: {api_key}")  # NO!
```

### Secret Rotation

Implement periodic rotation:

```python
from core.security import rotate_secret

async def scheduled_rotation():
    """Run weekly."""
    await rotate_secret("jwt_secret", generate_new_secret())
    await invalidate_old_tokens()
```

---

## OWASP Top 10 Mitigations

The framework provides protections for main OWASP vulnerabilities:

| #       | Vulnerability             | Mitigation                                                       |
| ------- | ------------------------- | ---------------------------------------------------------------- |
| **A01** | Broken Access Control     | RBAC, tenant isolation, route protection                         |
| **A02** | Cryptographic Failures    | TLS 1.3, bcrypt for passwords, secrets encryption                |
| **A03** | Injection                 | Input validation, parametrized queries, Guardrails               |
| **A04** | Insecure Design           | Security by design, threat modeling                              |
| **A05** | Security Misconfiguration | Secure defaults, `doctor` CLI check                              |
| **A06** | Vulnerable Components     | Updated dependencies, automated scans                            |
| **A07** | Auth Failures             | Rate limiting, account lockout, MFA support                      |
| **A08** | Software Integrity        | Signed packages, checksum verification                           |
| **A09** | Logging Failures          | Structured logging, security events audit                        |
| **A10** | SSRF                      | URL validation, automated checks, and manual redirect validation |

---

## Security Audit Logging

Log all security events for compliance and incident response:

```python
from core.observability import security_logger

# Successful login
security_logger.info(
    "user.login.success",
    user_id=user.id,
    ip_address=request.client.host,
    user_agent=request.headers.get("user-agent")
)

# Failed attempt
security_logger.warning(
    "user.login.failed",
    username=credentials.username,
    ip_address=request.client.host,
    failure_reason="invalid_password"
)

# Administrative action
security_logger.audit(
    "admin.user.deleted",
    actor_id=admin.id,
    target_id=deleted_user.id,
    action="user_deletion"
)
```

---

## Security Checklist

Before go-live, verify every point:

### Authentication Verification

- [x] JWT secret key is at least 256 bits long
- [x] Tokens have reasonable expiration (1-24h)
- [x] Refresh token implemented for long sessions
- [x] Rate limiting on login endpoint (5 attempts/minute)

### Network

- [x] HTTPS mandatory in production
- [x] CORS configured only for authorized domains
- [x] HTTP security headers configured (CSP, HSTS, etc.)

### Input/Output

- [x] All inputs validated with Pydantic
- [x] Guardrails active for LLM inputs
- [x] Output encoding to prevent XSS

### Secrets

- [x] No hardcoded secrets in code
- [x] `.env` in `.gitignore`
- [x] Secrets manager in production (Vault, AWS SM)

---

## Incident Response

In case of suspected breach:

1. **Containment**: Immediately revoke compromised tokens/API keys
2. **Analysis**: Examine security logs
3. **Notification**: Inform affected users
4. **Remediation**: Fix the vulnerability
5. **Documentation**: Document incident for future prevention

```python
# Revoke all user tokens
await token_service.revoke_all_user_tokens(user_id)

# Force re-login for everyone
await session_service.invalidate_all_sessions()
```

---

## Next Steps

- Configure [Observability](observability.md) to monitor security events
- Implement secure backups (see [Deployment](deployment.md))
- Run periodic penetration testing
