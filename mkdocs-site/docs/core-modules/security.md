# Security & Encryption

The `core.security` package provides three domain-agnostic infrastructure
primitives for enterprise deployments:

1. **Encryption at rest** — authenticated, versioned field encryption
   (`core.security.encryption`).
2. **Pluggable secret resolution** — decouples *where* credentials come from
   (environment, mounted files, external managers) from *how* they are consumed
   (`core.security.secrets`).
3. **Unified SSRF protection** — a single fail-closed guard
   (`core.security.ssrf`) and hardened `httpx` client factory
   (`core.security.http`) that every outbound-URL call site in the framework
   builds on.

Encryption and secrets are **opt-in**: with no configuration, the framework
behaves exactly as before (`get_field_encryptor()` returns `None`, secrets
resolve from the process environment). The SSRF guard is **on by default**
everywhere it is wired in — see below.

---

## Encryption at rest

`FieldEncryptor` turns a `str` into an opaque, self-describing token and back,
using **AES-256-GCM** (authenticated encryption — confidentiality *and*
tamper detection). Use it to protect PII columns, cached payloads, or any
sensitive value before it touches durable storage.

### Token format

```text
enc:v1:<key_id>:<urlsafe_b64(nonce(12) || ciphertext || tag)>
```

The `enc:` prefix makes encryption idempotent (already-encrypted values are
returned unchanged) and decryption tolerant of plaintext during rollout.

### Configuration

| Env var | Meaning |
|---|---|
| `DATA_ENCRYPTION_KEYS` | `id:secret,id2:secret2`. A value without `:` loads under id `default`. A *secret* is either a base64-encoded 32-byte key or a passphrase (stretched with HKDF-SHA256). |
| `DATA_ENCRYPTION_ACTIVE_KEY_ID` | Id of the key used for **new** encryptions. Required when more than one key is loaded. |

Generate a strong raw key:

```bash
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

### Usage

```python
from core.security import get_field_encryptor

enc = get_field_encryptor()           # None if not configured
if enc:
    token = enc.encrypt("user@example.com")
    plain = enc.decrypt(token)         # "user@example.com"
```

Optional **associated data (AAD)** binds ciphertext to a context (e.g. tenant
id), so a token cannot be replayed into another tenant's row:

```python
token = enc.encrypt_bytes(payload, aad=b"tenant-42")
enc.decrypt_bytes(token, aad=b"tenant-42")   # wrong AAD -> DecryptionError
```

### Key rotation

Keys are versioned and a token embeds the id of the key that produced it.

1. Add the new key and make it active:
   `DATA_ENCRYPTION_KEYS=v1:<old>,v2:<new>` and
   `DATA_ENCRYPTION_ACTIVE_KEY_ID=v2`.
2. Old ciphertext keeps decrypting (its `v1` key is still loaded).
3. Re-encrypt lazily — `encryptor.needs_rotation(token)` flags ciphertext
   produced by a non-active key; decrypt then re-encrypt to migrate it.
4. Once nothing reports `needs_rotation`, drop the old key.

Tampering, an unknown key id, or a malformed/unsupported-version token raise
`DecryptionError`; the message never contains plaintext or key material.

---

## SSRF Protection

Any URL the framework fetches on the server's behalf — a webhook target, an
OIDC issuer, an A2A peer endpoint, an MCP server, a scraped page, a
marketplace registry — is potentially attacker-influenced (config, user
input, LLM output, or a remote document) and is a **Server-Side Request
Forgery** vector: without a guard, the attacker can make the server issue
requests to `169.254.169.254` (cloud metadata), `127.0.0.1`, or an internal
service.

`core.security.ssrf` merges what used to be two separate implementations —
the browser agent's literal/offline hostname check and the webhook
dispatcher's DNS-resolve-and-pin strategy — into one module every other call
site now builds on. `core.security.http` wraps it in an `httpx.AsyncClient`
factory so a call site cannot forget the guard.

### `core.security.ssrf`

| Symbol | Purpose |
|---|---|
| `SsrfError(ValueError)` | Raised for any rejected URL. |
| `SsrfPolicy` | Frozen Pydantic model: `allow_internal: bool = False`, `allowed_schemes: frozenset[str] = {"http", "https"}`, `allowed_hosts: frozenset[str] \| None = None`. |
| `ip_is_internal(ip: str) -> bool` | Loopback/private/link-local/multicast/reserved/unspecified, plus RFC 6598 CGNAT (`100.64.0.0/10`) and the deprecated 6to4 relay anycast range (`192.88.99.0/24`), which stdlib `ipaddress` predicates miss. IPv4-mapped IPv6 (`::ffff:169.254.169.254`) is judged on the embedded IPv4 address. Unparseable input is treated as internal (fail-closed). |
| `hostname_is_blocked_literal(hostname: str) -> bool` | Cheap, offline, non-DNS-resolving check: `localhost`/`broadcasthost`/`*.localhost` and literal internal IPs. Pair with the DNS-resolving functions below for the authoritative check — this alone does **not** catch a hostname that merely *resolves* to an internal address. |
| `assert_url_safe(url, policy=None) -> None` | Scheme + literal + DNS check; raises `SsrfError` on any resolved address being internal. Blocking (does a DNS lookup) — call off the event loop. |
| `assert_url_safe_async(url, policy=None) -> None` | Same, via `asyncio.to_thread`. |
| `resolve_pinned_target(url, policy=None) -> tuple[str, str]` | Validates and returns `(pinned_url, original_host)`: the URL rewritten to the verified IP. Callers connect to `pinned_url` while sending `original_host` as the `Host` header and TLS SNI, so the address validated is exactly the address connected to (defeats DNS-rebinding TOCTOU). With `policy.allow_internal=True` the URL is returned unchanged. |

`SsrfPolicy.allow_internal=True` skips the private/loopback blocking and IP
pinning entirely — scheme enforcement still applies. It exists for
components that legitimately talk to internal hosts by design (an A2A mesh
of peer agents, a local Ollama instance) and is threaded through from an
environment variable in every such call site (see the table below).

### `core.security.http`

```python
from core.security.http import create_hardened_async_client
from core.security.ssrf import SsrfPolicy

client = create_hardened_async_client(policy=SsrfPolicy(), timeout=15.0)
resp = await client.get("https://example.com/resource")
await client.aclose()
```

`create_hardened_async_client(policy=None, **httpx_kwargs) -> httpx.AsyncClient`
wraps the (real, or a test `MockTransport`) transport in
`SsrfBlockingTransport(httpx.AsyncBaseTransport)`, which calls
`resolve_pinned_target` before **every** request that reaches the wire —
including every redirect hop `httpx` follows internally, which a one-shot
pre-request check would miss. When the target needs pinning, the transport
builds a *new* `httpx.Request` with the pinned URL, `Host` header, and
`extensions={"sni_hostname": host}` (TLS SNI + certificate verification stay
on the original hostname) rather than mutating the request in place, so
relative redirects — which `httpx` joins against the original
`request.url` — still resolve correctly.

Hardening details baked into the factory:

- **`mounts`/`proxy`/`proxies` kwargs are rejected** (`ValueError`) — any of
  them would let a caller route requests around the guard via a per-host
  transport or proxy. Configure a proxy on the *inner* transport passed via
  `transport=...` instead.
- **Keep-alive is off by default** (`httpx.Limits(max_keepalive_connections=0)`
  on the inner `AsyncHTTPTransport`) unless an explicit `limits=` is passed.
  Two different validated hostnames can pin to the same IP (shared
  infrastructure, a CDN, or coincidence); a pooled keep-alive connection
  reused across hostnames would let a second, unvalidated `Host` ride the
  first request's already-verified TCP/TLS connection. Disabling pooling by
  default trades a bit of latency for closing that connection-coalescing
  gap; pass `limits=httpx.Limits(...)` explicitly to opt back into pooling
  when the caller is a single fixed host.

### Adopted call sites

Every framework component that fetches an attacker-influenced URL routes
through `assert_url_safe`/`assert_url_safe_async` or
`create_hardened_async_client`:

| Call site | Module | Internal hosts |
|---|---|---|
| Webhook dispatch | `core.webhooks.dispatcher` (via the `core.webhooks.ssrf` shim) | Rejected by default; `WEBHOOK_ALLOW_INTERNAL=true` to allow. |
| OIDC discovery + JWKS | `core.auth.oidc` | Always rejected — issuer and the JWKS URI read back from the discovery document (attacker-influenced if the issuer/its DNS is compromised) are both screened before any HTTP call. No opt-out. |
| A2A client | `core.a2a.client.A2AClient` | **Environment-aware default** for `A2AClientConfig.allow_internal_endpoints`: allowed outside production (A2A meshes commonly run peer agents on private networks), **denied in production** — the same deny-by-default as the MCP transport and the webhook guard. Setting `A2A_ALLOW_INTERNAL_ENDPOINTS` explicitly (`1`/`true`/`yes`/`on` is truthy, anything else falsy) overrides in both directions, so a private-mesh production deployment opts back in with `A2A_ALLOW_INTERNAL_ENDPOINTS=true`. Production is resolved by the [runtime-environment resolver](config.md#runtime-environment). See [A2A Protocol](a2a.md). |
| MCP Streamable HTTP transport | `core.mcp.http_client_transport` | Rejected by default; `MCP_ALLOW_INTERNAL_ENDPOINTS=true` for a local/internal MCP server. See [MCP Integration](mcp.md#ssrf-guard-streamable-http-transport). |
| Fine-tuning providers | `core.finetuning.providers.TogetherProvider` | Rejected; hardcoded public SaaS endpoints, so the guard is defense-in-depth with no functional impact. No opt-out. |
| Plugin exporters (marketplace JWT exchange) | `core.plugins.exporters.router` | Rejected; always targets `OFFICIAL_MARKETPLACE_URL`. No opt-out. |
| Browser agent navigation + sub-resources | `plugins.browser_agent` | Rejected by default; `BASELITH_BROWSER_ALLOW_INTERNAL=true`. See [Runtime hardening controls](../advanced/security.md#ssrf-connection-pinning). |
| Web scraper (HTTP + Playwright fetchers) | `plugins.web_scraper` | Governed by `ScraperConfig.block_private_ips` (default `True`). See [Web Scraper](scraper.md#security-ssrf-protection). |
| Baselithbot channels/integrations/skills | `plugins.baselithbot.http.hardened_client` | Rejected by default; `BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS=true`. |

### Deprecated shims

`core/webhooks/ssrf.py` is a thin backward-compatible shim over
`core.security.ssrf`: `WebhookSSRFError` is now an alias of `SsrfError`, and
`resolve_pinned_target`/`validate_webhook_url` delegate to the new module
with a `SsrfPolicy(allow_internal=...)` built from their `allow_internal`
kwarg. It is kept only so existing imports keep working (`core.webhooks.dispatcher`,
`core.webhooks.service`, the marketplace registry, and third-party plugins)
and will be removed in a future major release — new code should import
`core.security.ssrf` directly. `core.scraper.utils` and
`plugins.web_scraper.utils.is_private_ip`/`resolve_safe_ips` are similar
compatibility re-exports (see [Web Scraper](scraper.md#security-ssrf-protection)).

---

## Secret resolution

`SecretsProvider` resolves named secrets to `pydantic.SecretStr`, so
credentials never leak via `repr()`, logs, or Sentry frames.

| Backend (`SECRETS_BACKEND`) | Source |
|---|---|
| `env` (default) | Process environment variables. Identical to current behaviour. |
| `file` | Per-secret files under `SECRETS_DIR` (the Docker/Kubernetes secrets pattern, e.g. `/run/secrets`). Also honours the `<NAME>_FILE` indirection and falls back to the environment. |

```python
from core.security import get_secret

password = get_secret("DB_PASSWORD")   # SecretStr | None
```

### File backend & the `_FILE` convention

With `SECRETS_BACKEND=file` and `SECRETS_DIR=/run/secrets`, a lookup for
`DB_PASSWORD` resolves in order:

1. `/run/secrets/DB_PASSWORD`, then `/run/secrets/db_password`.
2. The path in `DB_PASSWORD_FILE`, if set.
3. The plain `DB_PASSWORD` environment variable (fallback).

This keeps plaintext secrets out of environment variables and image layers.

### Registering an external backend (Vault, cloud KMS)

Heavy or environment-specific providers stay **out of `core`** (Sacred Core
rule). Register them at startup and select via `SECRETS_BACKEND`:

```python
from core.security import register_secrets_provider

register_secrets_provider("vault", lambda: MyVaultProvider(addr=..., token=...))
# then run with SECRETS_BACKEND=vault
```

A provider only needs `get_secret(name) -> SecretStr | None`.
