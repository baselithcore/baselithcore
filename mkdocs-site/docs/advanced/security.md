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
| `iss`       | Issuer (optional)           | `baselith-core`     |
| `aud`       | Audience (optional)         | `api.myapp.com`     |

Issuer and audience validation defaults from `APP_BASE_URL`, and `JWT_STRICT_VALIDATION` switches on by itself once `AUTH_REQUIRED=true` and both claims resolve — at that point every token carries them, so requiring them costs nothing. Set `JWT_ISSUER`/`JWT_AUDIENCE` explicitly to override. Without the binding, any two deployments sharing a `SECRET_KEY` (e.g. a staging value copy-pasted to prod) accept each other's tokens; with no `APP_BASE_URL` to derive from, startup logs a warning naming exactly what to set.

**Refresh-token rotation with family revocation (RFC 9700 §4.14.2).** Every
refresh token carries a reserved `family` claim chaining it to its rotation
lineage (a fresh login starts a new family). Rotation consumes and blacklists
the presented token; if a **blacklisted** refresh token is ever presented
again — the signature of theft, since someone rotated it first — the whole
family is revoked, killing the thief's freshly rotated descendant too. Legacy
refresh tokens without the claim keep rotating (they start a new family on
first rotation), and access-token verification adds no extra Redis lookups.

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

!!! warning "Keys must be random tokens, not passwords"
    Configured keys are indexed by a **SHA-256** hash (`core/auth/api_keys.py`),
    not by a password KDF: the lookup runs on every authenticated request, and a
    slow KDF buys nothing for a high-entropy random token. That reasoning only
    holds while the key *is* random — `SecurityConfig` therefore warns at startup
    about any configured key shorter than 32 characters. Mint them with
    `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

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

### Indirect Prompt Injection (External Content)

`InputGuard` only sees the **user prompt**. Instructions hidden inside content
the agent fetches itself — web pages, tool output, documents — bypass it. The
`IndirectInjectionScanner` (`core/guardrails/indirect.py`) catches those:
zero-width/bidi unicode, instruction-bearing HTML comments, hidden CSS, and
agent-directed phrases.

Use `scan_external_content(...)` at every ingestion boundary. It is **log-only
by default** (returns content unchanged) and is already wired into the
framework's untrusted-content boundaries — external MCP tool results
(`MCPClient.call_tool`) and scraped pages (both web-scraper fetchers):

```python
from core.guardrails import scan_external_content

text = scan_external_content(tool_output, source=f"mcp_tool:{name}")
```

Sanitizing is **on by default**: invisibles and instruction-bearing HTML
comments are stripped from flagged content before it reaches the model. Set
`BASELITH_SANITIZE_EXTERNAL_CONTENT=false` for legacy detection-only mode. See
[Guardrails](../core-modules/guardrails.md#indirect-injection-scanning).

### Log Injection (Untrusted Values in Log Lines)

Anything that reaches a log line from outside the process — plugin manifest
fields, filenames, header values — can carry newlines or terminal escapes and
forge additional log entries. `sanitize_log_value` (`core/utils/logsafe.py`)
escapes every non-printable character (`\n` becomes the literal `\x0a`, so the
evidence survives) and caps the length, keeping one record on one line. It is
applied at every boundary where outside text meets a log line — the plugin
gates and loader, the plugin admin API, the tenant router, and the `SafeLogger`
fallback in `core/observability/logging.py`, which also replaces values whose
key marks them a secret:

```python
from core.utils import sanitize_log_value

logger.error("Refusing plugin %s: integrity check failed", sanitize_log_value(name))
```

It is stdlib-only by design — the plugin integrity and signature gates
(`core/plugins/integrity.py`, `core/plugins/signing.py`) use it before the
observability stack is available, and `PluginLoader` escapes every
manifest-supplied name it logs.

### Path Traversal (Plugin Identifiers)

A plugin identifier names a directory under the plugins root, and it arrives
from outside: an HTTP path parameter (`/api/plugins/{plugin_name}/reload`), a
CLI argument, a manifest field. `safe_plugin_path`
(`core/plugins/_resolve.py`) is the only sanctioned way to turn one into a
path — a whitelist on the identifier plus a containment check on the result:

```python
from core.plugins._resolve import safe_plugin_path

plugin_dir = safe_plugin_path(self.plugins_dir, plugin_name)  # ValueError if it escapes
```

Identifiers must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, and the joined path
is canonicalised (`os.path.realpath`) and required to sit under the root, so a
symlink pointing outside is refused too. `PluginLoader.resolve_plugin_dir` and
`ResourceAnalyzer.get_plugin_metadata` both go through it; endpoints that
package a directory (`/api/plugins/export/publish`) apply the same
realpath-and-prefix check against `PLUGIN_PUBLISH_WORKSPACE_ROOT` and pass the
**validated** path downstream rather than the raw request value.

### Agent-Initiated Commerce Replay Protection

Signed mandate chains (`core/world_model/mandates.py`) authorize autonomous
purchases. **Replay protection is on by default**: `verify_chain(...)` consumes
each intent exactly once through a process-local guard, so a valid signed chain
cannot be re-submitted within its expiry window. Multi-worker deployments
should pass a shared (Redis-backed) `replay_guard`; passing `replay_guard=None`
is the explicit opt-in to stateless verification.
See [World Model](../core-modules/world-model.md#replay-protection).

---

## Security Configuration

`SecurityConfig` enforces safety at startup via Pydantic validators:

| Setting                    | Default      | Notes                                                                                 |
| -------------------------- | ------------ | ------------------------------------------------------------------------------------- |
| `SECRET_KEY`               | `None`       | **Required** when `AUTH_REQUIRED=true`. Must be at least 32 chars. Uses `SecretStr`. |
| `ADMIN_PASS`               | `None`       | Uses `SecretStr`. Rejected at startup if set to `"password"`, `"changeme"`, or `"admin"`. |
| `ADMIN_PASS_HASHED`        | `None`       | PBKDF2-SHA256 hashed password. Preferred over `ADMIN_PASS`.                          |
| `API_KEYS_USER` / `API_KEYS_ADMIN` / `API_KEYS_JOB` | `[]` (empty) | Comma-separated keys, wrapped in `SecretStr` so they never appear in `repr()`, logs, or Sentry frames. |
| `ALLOW_ORIGINS`            | `[]` (empty) | Blocks all cross-origin by default. `["*"]` disables credentials for security. |
| `TRUSTED_HOSTS`            | `[]` (empty) | Optional allowlist for incoming `Host` headers. Recommended behind reverse proxies in production. |
| `AUTH_REQUIRED`            | `true`       | Enforced by default. Even when set to `false`, admin/job/service routes still reject anonymous traffic. |
| `JWT_ISSUER`               | `APP_BASE_URL` | `iss` claim binding tokens to this deployment.                                       |
| `JWT_KEYS`                 | `None`       | Verification key ring `kid=key,...` enabling key rotation with no session loss — see [Auth](../core-modules/auth.md#key-rotation-without-logging-everyone-out). |
| `JWT_ACTIVE_KID`           | `None`       | Ring entry that signs new tokens (required with more than one key).                   |
| `JWT_SIGNING_KEY`          | `None`       | Private key for asymmetric signing; omit on verify-only services so they cannot mint. |
| `JWT_AUDIENCE`             | `None`       | Optional `aud` claim for token scoping.                                               |
| `JWT_STRICT_VALIDATION`    | auto         | Rejects any JWT missing `aud` or `iss`. Enabled automatically once `AUTH_REQUIRED=true` and both claims resolve; set explicitly to override. |
| `SECURITY_HEADERS_ENABLED` | `true`       | Enables CSP, HSTS, Permissions-Policy. Baseline headers are always active.           |
| `ENABLE_HSTS`              | `true`       | Adds `Strict-Transport-Security` header. Enabled by default. Disable only if TLS is not terminated upstream. |
| `CONTENT_SECURITY_POLICY`  | `None`       | Custom CSP value.                                                                     |
| `MAX_REQUEST_SIZE_BYTES`   | `10485760` (10 MiB) | Hard cap on inbound request body size. Bodies that advertise or stream beyond the cap are rejected with HTTP 413. Set to `0` to disable. |

Generate a secure secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

!!! danger "Environment Files"
    `.env` files are **gitignored** and must never be committed. Use `.env.example` as a template.

### Hardening Environment Flags

These flags live outside `SecurityConfig` and harden specific subsystems. All
default to a non-breaking posture; enable the stricter ones in production.

| Variable | Default | Effect |
| -------- | ------- | ------ |
| `BASELITH_SANITIZE_EXTERNAL_CONTENT` | **on** | Strip invisibles/bidi/HTML comments from flagged fetched content (tool output, scraped pages). Set `false` for legacy detection-only mode. |
| `BASELITH_ORCHESTRATOR_GUARDRAILS` | **on** | Input validation (regex, pre-budget) + output PII/harmful-content filtering on every `Orchestrator.process` call. Set `false` to bypass for trusted internal traffic. |
| `BASELITH_REQUIRE_SIGNED_PLUGINS` | off | Strict mode (all environments): reject plugins lacking a verified `integrity_sha256`. |
| `BASELITH_ALLOW_UNSIGNED_IN_PROD` | off | **Production is fail-closed by default** — an unsigned plugin (no `integrity_sha256`) is refused at load. Set this to allow unsigned plugins in production (insecure; logs a CRITICAL). Outside production, unsigned plugins always load. |
| `BASELITH_SKIP_INTEGRITY_CHECK` | off | Dev-only escape hatch; skips hash verification. **Ignored in production** (and when strict mode is on). |
| `BASELITH_REQUIRE_PLUGIN_SIGNATURES` | off | Publisher-authenticity gate: refuse any plugin whose `integrity_sha256` is not signed (`signature_ed25519` in the manifest) by a key in the trust roots. The hash proves the tree matches the manifest; the Ed25519 signature proves **who** published it. Sign with `scripts/sign_plugin_ed25519.py`. |
| `BASELITH_PLUGIN_TRUST_ROOTS` | unset | Comma-separated hex-encoded Ed25519 public keys trusted to sign plugins (generate with `scripts/sign_plugin_ed25519.py keygen`). |
| `BASELITH_BROWSER_ALLOW_INTERNAL` | off | Allow the browser agent (navigation + sub-resource requests) to reach loopback/private hosts (trusted local dev only). |
| `WEBHOOK_ALLOW_INTERNAL` | off | Allow outbound webhook dispatch (`core.webhooks`) to target loopback/private/link-local hosts. |
| `BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS` | off | Allow every baselithbot outbound HTTP call (channels, integrations, skills, the Ollama model probe) to reach loopback/private hosts. |
| `A2A_ALLOW_INTERNAL_ENDPOINTS` | **on** | `A2AClientConfig.allow_internal_endpoints` default: A2A peer client allows loopback/private hosts because meshes commonly run peer agents internally. Set `false` for external-peers-only deployments. |
| `MCP_ALLOW_INTERNAL_ENDPOINTS` | off | Allow the MCP Streamable HTTP client transport (`core.mcp.http_client_transport`) to reach loopback/private hosts. |
| `BASELITH_A2A_SHARED_SECRET` | unset | Enable HMAC-SHA256 signing of A2A traffic: the client signs every request (timestamp + single-use nonce bound into the MAC, so captured requests cannot be replayed even within the skew window) and the A2A router rejects unsigned/invalid/replayed requests with 401. The nonce is **required**: a signed request without one is refused. Set the same value on all peers. Unset = unauthenticated (a CRITICAL log fires in production). |
| `BASELITH_A2A_ALLOW_LEGACY_NONCELESS` | off | **Deprecated compatibility window**: accept signed A2A requests without a nonce (pre-nonce peers). Their MAC is valid but replayable within the skew window, so enabling logs a CRITICAL. Turn on only while upgrading a mesh, then remove. |
| `BASELITH_LOCKOUT_FAIL_OPEN` | off | When Redis is unreachable in production, admin lockout **fails closed** (privileged auth returns 503) because per-replica in-memory counters are defeated by rotating replicas. Set true to prefer availability over the control. |
| `BASELITH_ALLOW_UNBOUND_JWT` | off | Production with `AUTH_REQUIRED=true` refuses to start when JWTs carry no `iss`/`aud` binding (cross-environment token replay). Set true to accept the risk explicitly. |
| `DOCS_ENABLED` | auto | Force `/docs`, `/redoc`, `/openapi.json` on or off. Auto = off in production, and off when auth is enforced but no `ENVIRONMENT`/`APP_ENV` was declared (a config shape that smells like a forgotten prod env var). |
| `MCP_ALLOWED_COMMANDS` | `python,python3,node,npx,uvx,uv,deno,bun,bunx` | Allowlist of executable basenames `MCPClient` may spawn for stdio servers; custom commands outside the list are rejected. |
| `BASELITH_MARKETPLACE_ALLOW_HTTP` | off | Permit a plaintext `http://` marketplace registry on non-loopback hosts (MITM risk — trusted networks only). HTTPS and `file://` are always allowed. |
| `BASELITH_MARKETPLACE_ALLOW_INTERNAL` | off | Permit a marketplace registry URL whose host resolves to a loopback/private/link-local/metadata address. Default-deny (SSRF guard) — set only for a trusted on-prem/air-gapped registry. |

!!! note "SSRF opt-out flags at a glance"
    `BASELITH_BROWSER_ALLOW_INTERNAL`, `WEBHOOK_ALLOW_INTERNAL`,
    `BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS`, `A2A_ALLOW_INTERNAL_ENDPOINTS`, and
    `MCP_ALLOW_INTERNAL_ENDPOINTS` are the five environment knobs that flip a
    component's `SsrfPolicy.allow_internal` — see [SSRF: connection
    pinning](#ssrf-connection-pinning) below for what each guards and
    `BASELITH_MARKETPLACE_ALLOW_INTERNAL` above for the plugin-registry
    equivalent. `A2A_ALLOW_INTERNAL_ENDPOINTS` is the only one of the five
    that defaults **on**.

!!! note "JWT algorithm safety"
    `JWTHandler` rejects the `none` algorithm at construction (disabled
    signature verification — the JWT downgrade attack), requires the `exp`
    claim on every verified token (a token without expiry could never be
    blacklisted), and accepts the signing key as `SecretStr` so the plaintext
    is not unwrapped until the last moment. Successful verifications are cached
    in-process for a short window (≤5s, never past the token's own `exp`) to
    skip the signature check and Redis blacklist round-trip on repeated
    requests. The cache is a bounded LRU (8192 entries) so a burst of distinct
    valid tokens — rotation or token spray — cannot grow it without limit.
    Revoking a token evicts its entry immediately in-process; the short TTL
    bounds staleness across other workers.

## Container Hardening

In production, the compose stack applies extra runtime restrictions to reduce post-compromise blast radius:

- `no-new-privileges:true` is enabled on the main application, data, and observability containers.
- Ambient Linux capabilities are dropped for non-privileged services.
- The Nginx gateway runs with a read-only root filesystem and dedicated `tmpfs` mounts for runtime state.
- Internal services are segmented across dedicated Docker networks.
- TLS termination is expected to happen upstream, so certificate lifecycle is managed outside this application stack.

The main residual risk is intentionally pushed out of this compose stack: the sandbox daemon should run on a dedicated external host or node, not inside the main production application deployment.

## Supply-Chain Security

Dependencies and source are continuously scanned in CI; findings surface under
the repository's **Security → Code scanning** tab.

| Layer | Tool | What it covers |
| ----- | ---- | -------------- |
| SAST | **CodeQL** (`.github/workflows/codeql.yml`) | Python + JavaScript/TypeScript, `security-extended` queries, on push/PR and weekly |
| SAST | **Semgrep** (`.github/workflows/semgrep.yml`) | OSS rulesets `p/python`, `p/security-audit`, `p/secrets` (no token), report-mode |
| Dependency CVEs / SBOM | **Trivy** + **CycloneDX** (in `ci.yml`) | Vulnerability scan and a generated software bill of materials |
| Image provenance | **cosign** + SLSA (`release-image.yml`) | Keyless-signed images with provenance and SBOM attestations |

CodeQL and Trivy run in **report mode** — they publish findings without failing
the build, so security signal is visible without blocking delivery. Tighten to
blocking once the baseline is clean.

!!! info "Scanner baseline: accepted findings live in config, not in comments"
    Inline markers (`# codeql[...]`, `# nosemgrep`) document a decision next to
    the code, but only Semgrep acts on them — the CodeQL CLI dropped
    `--sarif-include-alertsuppressions`, and a dismissal in the Security tab
    does not reach a local or IDE run. So the accepted findings are declared in
    the repository instead:

    | File | Role |
    | ---- | ---- |
    | [`.github/codeql/codeql-config.yml`](https://github.com/baselithcore/baselithcore/blob/main/.github/codeql/codeql-config.yml) | `paths-ignore` for code that is not part of the shipped product (`templates/`, `examples/`, `backstage-portal/`, Alembic revision modules) and the reviewed exclusions as `query-filters`. Language-agnostic: it applies to the Python, JavaScript and Actions runs alike. |
    | [`.github/codeql/baselithcore.qls`](https://github.com/baselithcore/baselithcore/blob/main/.github/codeql/baselithcore.qls) | The Python entry point for local and IDE runs — same suite, same exclusions, so a local scan matches CI instead of resurrecting accepted findings. |
    | [`.semgrepignore`](https://github.com/baselithcore/baselithcore/blob/main/.semgrepignore) | The same path set for Semgrep. **Note:** this file *replaces* Semgrep's built-in ignore list, so the defaults (tests, vendored trees, build output) are restated in it. |

    CI runs **`security-extended`**, not `security-and-quality`. The quality half
    duplicates a layer this repo already gates with a ratchet — ruff, mypy and
    the checks under `scripts/` — and produced ~400 style findings that buried
    the security signal; `security-extended` runs *more* security queries than
    the default suite, which is the half worth having. The one quality family
    ruff cannot see is import cycles: those queries are listed, commented out,
    in the `.qls` with the command to run them on demand.

    Three queries are excluded, each with a compensating control:

    | Query | Why | What still catches it |
    | ----- | --- | --------------------- |
    | `py/weak-sensitive-data-hashing` | `core/security/digest.py` indexes **random tokens** (API keys, JWTs), not passwords | operator passwords go through argon2/PBKDF2 in `core/auth`; `SecurityConfig` warns on short API keys |
    | `py/stack-trace-exposure` | the MCP spec requires a human-readable `message` on every JSON-RPC error, and the text is written by this codebase | the A2A and quota paths return fixed messages; no third-party exception text is returned anywhere |
    | `js/clear-text-storage-of-sensitive-data` | the operator console keeps the API key the operator pastes in `sessionStorage`; a client-held bearer credential has no more-secure browser store (httpOnly cookies need a server-side session the console does not have) | the key is write-only in the UI — never read back into the DOM, dropped when the tab closes — and `API_KEYS_SCOPED` bounds what a leaked key can reach |

    Adding a fourth exclusion is a review decision, not a convenience: state the
    compensating control in both files or fix the finding.

!!! note "Scan scope: the Backstage portal is excluded from the Trivy dependency scan"
    `backstage-portal/yarn.lock` is skipped by the Trivy filesystem scan
    (`--skip-files` in `ci.yml`). The developer portal is a **vendored, dev-only
    tool** — it is not part of the published `baselith-core` wheel or the release
    container image — and its transitive npm tree is authored upstream by
    Backstage. That tree carries advisories we cannot resolve without a Backstage
    release, most notably the abandoned **`vm2`** package (no patched version
    exists; it is a build-time transitive of
    `@backstage/config-loader → typescript-json-schema`). Scanning it produced
    ~70 unactionable Code-scanning alerts that drowned out real signal for the
    shipped product, so its lockfile is an accepted exclusion. Secret and
    misconfig scanning of the portal source is unaffected — only its lockfile is
    skipped.

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

!!! note "LLM provider credentials stay wrapped"
    The OpenAI, Anthropic, and HuggingFace providers store their API key as a
    `SecretStr` internally and unwrap it only at the SDK client boundary
    (`AsyncOpenAI(api_key=...)`, etc.). The plaintext never lives as a bare
    instance attribute, so a provider object captured in a traceback or Sentry
    frame does not leak the credential.

### Pluggable Secrets Backend

By default secrets resolve from environment variables (unchanged behaviour). For
production you can switch to mounted Docker/Kubernetes secrets — keeping
plaintext out of the environment and image layers — without code changes:

```bash
SECRETS_BACKEND=file
SECRETS_DIR=/run/secrets        # reads /run/secrets/DB_PASSWORD, honours DB_PASSWORD_FILE
```

```python
from core.security import get_secret

db_password = get_secret("DB_PASSWORD")   # SecretStr | None
```

External managers (HashiCorp Vault, cloud KMS) are registered at startup via
`register_secrets_provider("vault", factory)` and selected with
`SECRETS_BACKEND=vault`. See
[Security & Encryption](../core-modules/security.md#secret-resolution).

### Encryption at Rest

Protect PII columns and other sensitive values with authenticated AES-256-GCM
field encryption. Opt-in via `DATA_ENCRYPTION_KEYS`:

```python
from core.security import get_field_encryptor

enc = get_field_encryptor()               # None if not configured
if enc:
    token = enc.encrypt("user@example.com")
    plain = enc.decrypt(token)
```

### Secret / Key Rotation

Encryption keys are **versioned**; a token embeds the id of the key that
produced it, so rotation is lossless:

1. Add the new key and make it active:
   `DATA_ENCRYPTION_KEYS=v1:<old>,v2:<new>`, `DATA_ENCRYPTION_ACTIVE_KEY_ID=v2`.
2. Old ciphertext keeps decrypting (the `v1` key stays loaded).
3. Re-encrypt lazily — `encryptor.needs_rotation(token)` flags ciphertext made
   by a non-active key; decrypt then re-encrypt to migrate.
4. Drop the old key once nothing reports `needs_rotation`.

For `SECRET_KEY` / JWT signing rotation, roll the env value and force re-login
(short token TTLs minimise the window). Full details:
[Security & Encryption](../core-modules/security.md).

---

## Rate Limiting

The distributed rate limiter uses Redis to count requests per identifier (role + user/key/IP). The counter is initialised with `SET NX EX` before being incremented with `INCR`, making the TTL-assignment atomic and eliminating the race condition that previously allowed unlimited requests under high concurrency.

Per-scope limits default to `RATE_LIMIT_USER_PER_MINUTE=60` and `RATE_LIMIT_ADMIN_PER_MINUTE=120` (admin is **no longer unlimited** by default — a `None` limit no-ops the limiter, which left admin endpoints unthrottled). `RATE_LIMIT_JOB_PER_MINUTE` remains unset (trusted server-to-server jobs) unless configured. Set any of these to a high value to widen a scope.

**Anonymous traffic is metered too.** On deployments that opt out of
authentication (`AUTH_REQUIRED=false` with no API keys configured), requests
that pass the anonymous gate are still rate-limited per client IP
(`default:anonymous:{ip}`) before reaching the route — disabling auth no
longer hands out unmetered LLM invocation to anyone who can reach the port.

A per-request **cost budget** breach (`BudgetExceededError` — token, graph- or
SQL-query limits) is rendered as a `429` RFC 9457 problem document
(`urn:baselith:error:budget_exceeded`) wherever it is raised: a dedicated
exception handler covers errors thrown deep inside application code, which
Starlette's `ExceptionMiddleware` would otherwise convert to a generic 500
before the cost-control middleware could see them.

---

## Admin Account Lockout

After **5 failed** HTTP Basic Auth attempts within **60 seconds**, further attempts are locked out for **15 minutes**. The counter is keyed on the **client IP**, not the (guessable) admin username — so an attacker cannot lock the legitimate admin out by hammering the login. The counter is stored in Redis (in-memory fallback) and cleared on successful login.

!!! warning "Behind a reverse proxy: run uvicorn with `--proxy-headers`"
    IP-keyed protections (this lockout, anonymous rate limiting) key on
    `request.client.host`. Behind a load balancer without `--proxy-headers`
    (plus `--forwarded-allow-ips` pinned to the proxy address) every client
    shares the LB's IP — 5 bad attempts from **anyone** would lock **every**
    admin out. `scripts/prod-preflight.sh` reminds you about this.

**PBKDF2 iteration floor.** When `ADMIN_PASS_HASHED` is used, hashes with
fewer than **100 000** iterations are rejected outright (the log message shows
how to regenerate at the OWASP-recommended 600 000) — a hand-rolled
`pbkdf2_sha256$1$…` value can no longer masquerade as a real KDF.

**TOTP step-up (MFA).** `TOTPProvider.verify_code(..., identity=...)` enforces
single-use codes (an accepted OTP cannot be replayed within the clock-skew
window, RFC 6238 §5.2) and throttles failed attempts per identity (RFC 4226
§7.3). See [MFA](../core-modules/mfa.md) for the pluggable guard.

---

## CORS (Cross-Origin Resource Sharing)

The framework implements a strict CORS policy to prevent unauthorized cross-origin requests, especially for authenticated endpoints.

### Wildcard Origins vs Credentials

Following security best practices and the CORS specification, **credentials (cookies, Authorization headers, Basic Auth) cannot be used with a wildcard origin (`*`)**.

- **If `ALLOW_ORIGINS=["*"]`**: The framework automatically sets `allow_credentials=False`. This is safe for public APIs but will break the Admin Console and other authenticated cross-origin tools if accessed from a different origin.
- **If credentials are required**: You **MUST** explicitly list the allowed origins in `ALLOW_ORIGINS` (e.g., `["https://admin.myapp.com", "https://myapp.com"]`).
- **Startup guard**: configuring `*` together with admin credentials — `ADMIN_PASS` **or** `ADMIN_PASS_HASHED` — fails startup: the CSRF Origin check is a no-op under wildcard, while browsers replay cached Basic-auth credentials on cross-site form POSTs against the admin endpoints.

!!! critical "Security Footgun Prevented"
    Previous versions allowed `allow_credentials=True` with a regex-based wildcard bypass. This has been removed. The framework now enforces a hard-fail or credential disablement when `*` is used, protecting the Admin Console from CSRF-like data theft.

---

## CSRF Protection

A middleware validates the `Origin` header on all state-changing requests (`POST`, `PUT`, `DELETE`, `PATCH`).

1. **Origin Validation**: If an `Origin` header is present, it must match one of the entries in `ALLOW_ORIGINS`.
2. **Wildcard Handle**: If `ALLOW_ORIGINS` contains `*`, CSRF protection is relaxed for public endpoints, but credentials remain disabled (see [CORS](#cors-cross-origin-resource-sharing)).
3. **No-Origin Requests**: Requests without an `Origin` header (e.g., direct `curl` calls) are permitted, as they cannot be forged by a browser.

Bearer-token and API-key authentication are inherently immune to CSRF because they require an explicit header that browsers won't add automatically to cross-origin requests.

---

## Host Header Validation

When `TRUSTED_HOSTS` is configured, FastAPI enables `TrustedHostMiddleware` and rejects requests whose `Host` header is not in the allowlist.

Recommended production setup:

- Set `TRUSTED_HOSTS` to the public domains actually served by your reverse proxy.
- Keep `localhost` only if you really expose local health checks through that host.
- Do not use `*` in production unless you intentionally want to disable host validation.

Example:

```env
TRUSTED_HOSTS=["api.example.com","admin.example.com"]
```

---

## Security Headers

Four baseline headers are emitted on **every response**, regardless of configuration:

| Header                    | Value                  |
| ------------------------- | ---------------------- |
| `X-Content-Type-Options`  | `nosniff`              |
| `X-Frame-Options`         | `DENY` (configurable)  |
| `Referrer-Policy`         | `same-origin`          |
| `X-XSS-Protection`        | `0` (disabled)         |

!!! note "`X-XSS-Protection: 0`"
    The legacy XSS auditor header is deprecated: modern browsers ignore it and
    `1; mode=block` could itself introduce a side channel in older ones. Per
    current OWASP guidance it is set to `0` and protection relies on the CSP.

`Content-Security-Policy` ships a strict default (operator value via
`CONTENT_SECURITY_POLICY` always wins) whose `connect-src` contains **no bare
`ws:`/`wss:` sources** — a scheme-only source matches *every* host, which
would hand an XSS foothold a free WebSocket exfiltration channel. CSP3
browsers already allow same-origin sockets under `'self'`; deployments needing
cross-origin sockets set the policy explicitly. Its `img-src` accepts
`'self' data: blob: https:` — `blob:` is there because a bundled SPA that
fetches an image over the authenticated API can only render it through
`URL.createObjectURL`, and a `blob:` URL is minted by the page from bytes it
already holds, so it opens no exfiltration path of its own. The default policy
also states `base-uri 'self'`, `form-action 'self'` and `object-src 'none'` —
all three are *permissive* when omitted, and leaving them unset would let a
`<base>` injection rebase every relative script URL, an injected form post
credentials to a foreign origin, and legacy plugin embedding stay open. `Permissions-Policy` ships a **restrictive default** — it denies `geolocation`, `camera`, `microphone`, `payment`, `usb`, and the motion sensors — and is emitted by default; override it via `PERMISSIONS_POLICY`, or set it empty to omit the header. `Strict-Transport-Security` is **enabled by default** (`ENABLE_HSTS=true`) and requires TLS termination upstream — set `ENABLE_HSTS=false` only in environments without TLS. All are emitted only when `SECURITY_HEADERS_ENABLED=true`.

`SecurityHeadersMiddleware` is implemented as pure ASGI — `BaseHTTPMiddleware` is **forbidden** by the architecture rules because it wraps every request in an extra anyio task and breaks streaming/cancellation semantics. Any new HTTP middleware **must** follow the same pattern.

---

## Request Body Size Limit

`RequestSizeLimitMiddleware` (pure ASGI, registered immediately after the request-id middleware) protects the application from memory-exhaustion DoS via oversized POST/PUT bodies. Enforcement is two-stage:

1. **Fast reject** when the `Content-Length` header exceeds `MAX_REQUEST_SIZE_BYTES` (no body read).
2. **Streaming counter** on the receive channel that aborts the request as soon as the cumulative body size crosses the cap — defends against chunked-encoding bypass and missing `Content-Length`.

Rejected requests receive `HTTP 413 Request Entity Too Large` and increment the Prometheus counter `security_events_total{reason="request_too_large"}`. WebSocket and lifespan scopes are passed through unchanged. Set `MAX_REQUEST_SIZE_BYTES=0` to disable the check (not recommended outside dev).

For large file uploads beyond ~100 MiB, prefer a dedicated streaming-upload endpoint that pipes directly to object storage rather than raising the global cap.

---

## SSRF Protection

The scraper validates all outgoing URLs against a private-IP blocklist. Hostname resolution happens at validation time and the HTTP client connects directly to the **verified IP** (IP pinning), preventing DNS rebinding attacks where a second resolution at connection time could return a private address.

```python
# Internal helper — used automatically by HttpxFetcher
from core.scraper.utils import get_pinned_url_for_host

result = get_pinned_url_for_host("https://example.com/page")
if result is None:
    raise ValueError("SSRF check failed")
pinned_url, original_host = result
# HTTP client connects to the pinned IP; Host header preserved
```

The Playwright fetcher additionally routes **every** request the rendered
page issues — not just the initial navigation — through the same guard, so a
scraped page cannot smuggle an SSRF probe through a same-origin
`<img>`/`fetch` sub-resource load. See [Web Scraper §Security &
SSRF Protection](../core-modules/scraper.md#security-ssrf-protection).

!!! note "Canonical location"
    `core.scraper.utils` is a compatibility shim that re-exports from the canonical
    module `plugins.web_scraper.utils`, whose SSRF checks now delegate to the
    unified `core.security.ssrf` module (see [Security & Encryption
    §SSRF Protection](../core-modules/security.md#ssrf-protection)). New code
    should import from `plugins.web_scraper.utils` or `core.security.ssrf`
    directly.

---

## OWASP Top 10 Mitigations

The framework provides protections for main OWASP vulnerabilities:

| #       | Vulnerability             | Mitigation                                                                              |
| ------- | ------------------------- | --------------------------------------------------------------------------------------- |
| **A01** | Broken Access Control     | RBAC, tenant isolation, route protection, plugin API requires `admin` role              |
| **A02** | Cryptographic Failures    | TLS 1.3, PBKDF2-SHA256 for admin passwords, secrets via `SecretStr`                    |
| **A03** | Injection                 | Input validation, parametrized queries, path traversal protection on file ingest        |
| **A04** | Insecure Design           | Security by design, CSRF middleware, atomic rate limiter, request body size limit         |
| **A05** | Security Misconfiguration | Secure defaults, startup validation, baseline security headers always active, pure-ASGI middleware only (no `BaseHTTPMiddleware`) |
| **A06** | Vulnerable Components     | Updated dependencies, `pip-audit` CVE scan in CI, Bandit static analysis; JSON used for all cache serialization |
| **A07** | Auth Failures             | Atomic rate limiting, admin account lockout (5 attempts / 15 min lock)                 |
| **A08** | Software Integrity        | Signed packages, checksum verification                                                  |
| **A09** | Logging Failures          | Structured audit logging; plugin management actions fully audited                       |
| **A10** | SSRF                      | URL validation, DNS resolution at validation time, IP pinning to prevent DNS rebinding  |

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
- [x] Admin account lockout after 5 failed attempts
- [x] `APP_BASE_URL` set, so `JWT_ISSUER`/`JWT_AUDIENCE` bind tokens to this deployment
- [x] `JWT_STRICT_VALIDATION` on (automatic when `AUTH_REQUIRED=true` and both claims resolve)
- [x] `JWT_KEYS` + `JWT_ACTIVE_KID` configured, so the signing key can be rotated without ending every session

### Network

- [x] HTTPS mandatory in production
- [x] CORS configured only for authorized domains
- [x] HTTP security headers configured (CSP, HSTS, etc.)
- [x] CSRF origin validation active for state-changing endpoints

### Input/Output

- [x] All inputs validated with Pydantic
- [x] Guardrails active for LLM inputs
- [x] Output encoding to prevent XSS

### Secrets

- [x] No hardcoded secrets in code
- [x] `.env` in `.gitignore`
- [x] Secrets manager in production (Vault, AWS SM) — `SECRETS_BACKEND=file` or a registered backend
- [x] Encryption at rest for PII/sensitive fields (`DATA_ENCRYPTION_KEYS`)
- [x] Documented key-rotation procedure

---

## Runtime hardening controls

These framework-level controls are on by default; the notes below cover the
knobs and the production posture to verify.

### Agentic loop enforcement

The orchestration safety primitives are enforced on the hot path, not merely
declared. `core.orchestration.enforcement` exposes two chokepoint helpers that
handlers call around each loop step and tool invocation:

- `enforce_iteration(context)` — advances the per-request `LoopBudget` (iteration
  cap) and raises `BudgetExceededError` at the limit.
- `enforce_tool_invocation(context, tool, category, cost_usd=...)` — fail-closed
  order: **contract** (`ContractValidator.check_tool_call`) → **autonomy**
  (`enforce_approval`) → **budget** (tool-call + USD cap).

`ParallelToolExecutor` accepts `loop_budget` and `contract_validator` and
enforces all three (it already gated on `autonomy_policy`). Each helper is a
no-op when its primitive is absent, so custom handlers can call them
unconditionally. Defaults: 25 iterations, 50 tool calls, $0.50 per request.

### SSRF: connection pinning

Outbound fetch guards resolve DNS and **pin the connection to the validated IP**
so the address checked is the address connected to (defeats DNS rebinding):

- **Webhooks** — `resolve_pinned_target()` returns a `(pinned_url, host)` pair;
  the dispatcher POSTs to the pinned IP with `Host` + TLS `sni_hostname` set to
  the original hostname, and `follow_redirects=False` so a 3xx cannot redirect
  to an internal host. Override for local dev only with `WEBHOOK_ALLOW_INTERNAL`.
- **BrowserAgent** — a Playwright route interceptor re-validates **every**
  request the page issues, not just navigation: sub-resource loads (scripts,
  images, fetch/XHR) and server-driven redirects are all checked, so a page
  cannot smuggle an SSRF probe through a same-origin asset request. DNS
  resolution runs off-loop and fails closed; decimal/octal/hex and
  IPv4-mapped-IPv6 encodings are normalized/blocked. A per-host verdict cache
  avoids a DNS lookup per sub-resource and is cleared on every top-level
  navigation, so the residual DNS-rebinding window is bounded to "within the
  currently loaded page" rather than eliminated outright. Override with
  `BASELITH_BROWSER_ALLOW_INTERNAL=true`.
- **Web scraper (Playwright fetcher)** — the same all-requests pattern as
  BrowserAgent (no per-host cache: scraper page loads are typically
  shorter-lived), gated by `ScraperConfig.block_private_ips` (default `True`).
  See [Web Scraper](../core-modules/scraper.md#security-ssrf-protection).

`core.security.ssrf` (`assert_url_safe`/`assert_url_safe_async`) and
`core.security.http.create_hardened_async_client` are the unified guard other
outbound call sites build on — full API reference in [Security & Encryption
§SSRF Protection](../core-modules/security.md#ssrf-protection):

- **OIDC discovery** (`core.auth.oidc`) — the issuer and the `jwks_uri` read
  back from its discovery document (attacker-influenced if the issuer or its
  DNS is ever compromised) are both screened via `_assert_issuer_safe()`
  before any HTTP call. Every OIDC HTTP call goes through the pinning path —
  PyJWT's own `PyJWKClient` (which fetches via `urllib.request.urlopen`,
  invisible to this guard) is not used. No opt-out; a self-hosted IdP on an
  internal network needs an externally reachable JWKS/discovery endpoint.
- **A2A client** (`core.a2a.client.A2AClient`) — see [A2A Client](a2a.md);
  internal hosts stay allowed by default for peer meshes, gated by
  `A2AClientConfig.allow_internal_endpoints` (env `A2A_ALLOW_INTERNAL_ENDPOINTS`,
  default `true`).
- **MCP Streamable HTTP transport** (`core.mcp.http_client_transport`) — see
  [MCP](mcp.md#ssrf-guard-streamable-http-transport); gated by
  `MCP_ALLOW_INTERNAL_ENDPOINTS` (default off).
- **Fine-tuning providers** (`core.finetuning.providers.TogetherProvider`) —
  hardcoded public SaaS endpoints; the guard is defense-in-depth with no
  functional impact.
- **Plugin exporters** (`core.plugins.exporters.router`) — the GitHub →
  marketplace JWT exchange always targets `OFFICIAL_MARKETPLACE_URL`, now
  additionally hardened against a compromised/misconfigured marketplace URL.
- **Baselithbot** (`plugins.baselithbot.http.hardened_client`) — every channel
  webhook, integration, and skill HTTP call (Slack, Discord, Matrix, the
  ClawHub skill, dashboard security checks, the Ollama model probe, …) routes
  through this factory. Gated by `BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS` (default
  off) — see [baselithbot Security](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/docs/security.md).

### SSRF unification — migration notes

This release replaced two independent, partially-overlapping SSRF
implementations (browser agent, webhook dispatcher) with the single
`core.security.ssrf`/`core.security.http` module documented above, and
adopted it on every outbound-URL call site the audit found. Practical
consequences when upgrading:

- **New default-deny on previously-unguarded call sites.** OIDC discovery/
  JWKS, the A2A-adjacent fine-tuning providers, the plugin exporters'
  marketplace JWT exchange, and every baselithbot outbound HTTP call now
  reject internal/loopback/link-local/metadata destinations by default. If
  any of these legitimately need to reach an internal host in your
  deployment (a self-hosted fine-tuning mirror, an internal marketplace, a
  LAN Ollama instance, an internal webhook receiver), set the corresponding
  opt-out env var (see below) — OIDC and the fine-tuning/exporter call
  sites have no opt-out (see bullets above).
- **Malformed URLs are now rejected even with `block_private_ips=False`.**
  The web-scraper's `check_ssrf_safe`/`get_pinned_url_for_host` route through
  `resolve_pinned_target`, whose scheme/host parsing (`_parse_and_screen`)
  runs unconditionally — before the `allow_internal` policy check. Disabling
  private-IP blocking no longer implies "accept anything parseable or not".
- **`is_private_ip()` narrowed.** The web-scraper's legacy `is_private_ip()`
  (kept for backward compatibility) now delegates to
  `hostname_is_blocked_literal()`, which recognizes literal blocked names
  (`localhost`, `broadcasthost`, `*.localhost`) and literal internal IPs, but
  no longer pattern-matches hostname suffixes like `.local`/`.internal`. The
  enforced SSRF path (`check_ssrf_safe`/`get_pinned_url_for_host`) is
  unaffected — it still resolves DNS and blocks on the resolved IP — so this
  only matters for code calling `is_private_ip()` directly as an offline
  heuristic.
- **`core/webhooks/ssrf.py` is now a deprecated shim** over
  `core.security.ssrf` (see [Security & Encryption §Deprecated
  shims](../core-modules/security.md#deprecated-shims)); existing imports
  keep working unchanged.
- **Ollama model discovery** (`plugins.baselithbot.diagnostics.ollama_probe`)
  requires `BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS=true` to reach a
  localhost/LAN Ollama instance — without it the probe fails closed and the
  dashboard falls back to its static model catalog (it never raises).
- **Hardened clients no longer honor `HTTP_PROXY`/`HTTPS_PROXY` from the
  environment.** `create_hardened_async_client` always builds (or receives) an
  explicit `transport`, and httpx only auto-reads env proxies (`trust_env`)
  when it constructs its own default transport — so an explicit transport
  disables `allow_env_proxies` implicitly. This is intentional: a proxy and
  IP pinning are incompatible (the pinned connection would be handed to the
  proxy instead of the validated address). If you need a proxy, configure it
  on the inner transport you pass in: `create_hardened_async_client(transport=httpx.AsyncHTTPTransport(proxy="http://proxy:3128"))`.

Opt-out for a development environment that legitimately needs one of these
call sites to reach an internal host: see the five `*_ALLOW_INTERNAL*` /
`*_ALLOW_INTERNAL_ENDPOINTS` rows in [Hardening Environment
Flags](#hardening-environment-flags) above for the full list and defaults.

### Log redaction

Sensitive-data redaction is a **structlog processor** (`redact_sensitive`)
installed in both the main pipeline and the foreign-log pre-chain, so it applies
on the FastAPI/uvicorn path — not only the MCP server. It redacts secrets by key
name (recursively) and masks emails / inline `key=secret` patterns in message
strings. Gated by `LOG_MASKING_ENABLED` (default on). Connection strings are
scrubbed with `redact_url_credentials()` before logging.

### Transport (production)

`StorageConfig` emits a **warning** (non-fatal) in production when transport is
unencrypted, so upgrades never break but operators get a clear signal:

- Set `DB_SSL_MODE=require` (or `verify-full`) — the default falls back to
  libpq `prefer` (plaintext accepted).
- Use `rediss://` with AUTH/ACL for `CACHE_REDIS_URL`, `GRAPH_DB_URL`,
  `QUEUE_REDIS_URL`. The task queue serializes jobs with pickle, so an
  unauthenticated Redis reachable by an attacker is an RCE path — lock it down.

### Multi-tenant cache isolation

The GraphDB read cache is keyed on the **tenant-scoped** parameters (including
`tenant_id`), so an identical query from a different tenant never collides on a
cached entry.

### API surface

- Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are **disabled in
  production** (enabled otherwise for DX).
- Refresh tokens cannot be replayed as access tokens: `verify_token` enforces a
  `type` claim (`expected_type="access"` by default).
- `API_KEY_ENABLED=false` now actually disables API-key authentication.
- The feedback endpoint caps all persisted fields (query/answer/comment/
  conversation_id) on both the model and the legacy fallback path, and ignores
  unexpected fields (`extra="ignore"`) instead of storing them.

### Agent-to-agent (A2A) and A2UI

- **A2A dispatch fails closed in production.** With no `BASELITH_A2A_SHARED_SECRET`
  configured, unsigned requests are rejected (`401`) in production unless the
  operator explicitly opts in with `BASELITH_A2A_ALLOW_UNAUTHENTICATED=true`.
  Outside production the previous unauthenticated behavior is preserved for
  trusted-mesh / local use. When a secret is set, HMAC signing is required.
- **A2UI blueprints allow-list URL schemes.** `Link.href` / `Image.src` accept
  only `http`/`https`/`mailto` or relative URLs; `javascript:` / `data:` /
  `vbscript:` (including whitespace/control-char obfuscation) are rejected at
  schema validation, closing the agent-driven XSS path.

### Plugin install

The marketplace installer verifies plugin integrity **before** running
`pip install` (whose build backend executes arbitrary code), and uses
`sys.executable -m pip` for both install and uninstall (no PATH hijack). In
production an unsigned plugin is **refused by default** (fail-closed); set
`BASELITH_ALLOW_UNSIGNED_IN_PROD=true` to override, or
`BASELITH_REQUIRE_SIGNED_PLUGINS=true` to reject unsigned plugins in every
environment. The registry URL is additionally run through the SSRF guard —
a host resolving to a loopback/private/metadata address is rejected unless
`BASELITH_MARKETPLACE_ALLOW_INTERNAL=true` (trusted internal registry).

!!! note "Follow-ups not yet shipped"
    A signed marketplace registry (per-plugin Ed25519 publisher signatures
    shipped in 0.27 — `BASELITH_REQUIRE_PLUGIN_SIGNATURES` +
    `BASELITH_PLUGIN_TRUST_ROOTS`; the registry index itself is not yet
    signed), a JSON job serializer for the task queue, native Qdrant
    auth/TLS config, per-tenant scoping of the privacy DSR endpoints, converting
    the CSRF/plugin-activation `BaseHTTPMiddleware` to pure ASGI, and a pre-auth
    IP rate limiter remain planned. Treat them as operational compensating
    controls until delivered.

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
