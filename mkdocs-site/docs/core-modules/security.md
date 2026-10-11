# Security & Encryption

The `baselith.core.security` package provides three domain-agnostic infrastructure
primitives for enterprise deployments:

1. **Encryption at rest** — authenticated, versioned field encryption
   (`baselith.core.security.encryption`).
2. **Pluggable secret resolution** — decouples *where* credentials come from
   (environment, mounted files, external managers) from *how* they are consumed
   (`baselith.core.security.secrets`).
3. **Unified SSRF protection** — a single fail-closed guard
   (`baselith.core.security.ssrf`) and hardened `httpx` client factory
   (`baselith.core.security.http`) that every outbound-URL call site in the framework
   builds on.

Encryption and secrets are **opt-in**: with no configuration, the framework
behaves exactly as before (`get_field_encryptor()` returns `None`, secrets
resolve from the process environment). The SSRF guard is **on by default**
everywhere it is wired in — see below.

Field encryption is a primitive, not a blanket policy: the only framework
component that calls `get_field_encryptor()` is the Postgres webhook store,
which encrypts endpoint secrets and headers (see
[Webhooks › Durable store](webhooks.md#durable-store-webhook_storepostgres)).
Every other table the framework writes is stored as written; encrypt your own
columns with the encryptor as shown below.

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
from baselith.core.security import get_field_encryptor

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

## Credential digests

`baselith.core.security.digest.credential_digest(data)` returns the hex SHA-256 of an
opaque credential. API keys, JWTs and session tokens are indexed and cached by
this digest, so the raw value never becomes a dictionary key, a cache entry or
a rate-limit bucket name. SHA-256 fits because the inputs are random tokens,
not human-chosen passwords, and the lookup runs on every authenticated request.

It is not password storage. The operator credential goes through
PBKDF2-SHA256 in `baselith/core/middleware/_admin_credentials.py`.

## SSRF Protection

Any URL the framework fetches on the server's behalf — a webhook target, an
OIDC issuer, an A2A peer endpoint, an MCP server, a scraped page, a
marketplace registry — is potentially attacker-influenced (config, user
input, LLM output, or a remote document) and is a **Server-Side Request
Forgery** vector: without a guard, the attacker can make the server issue
requests to `169.254.169.254` (cloud metadata), `127.0.0.1`, or an internal
service.

`baselith.core.security.ssrf` merges what used to be two separate implementations —
the browser agent's literal/offline hostname check and the webhook
dispatcher's DNS-resolve-and-pin strategy — into one module every other call
site now builds on. `baselith.core.security.http` wraps it in an `httpx.AsyncClient`
factory so a call site cannot forget the guard.

### `baselith.core.security.ssrf`

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

### `baselith.core.security.http`

```python
from baselith.core.security.http import create_hardened_async_client
from baselith.core.security.ssrf import SsrfPolicy

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

### `baselith.core.security.bounded_body` {#bounded-bodies}

A byte counter on `httpx.Response.aiter_bytes()` does not bound memory for a
compressed body: httpx inflates each network chunk whole before yielding it,
so a few KiB of gzip on the wire become gigabytes before the counter is
consulted. `aiter_bounded(response, limit)` / `read_bounded(response, limit)`
read the raw wire bytes and inflate them under the cap instead:

- `identity` (or no `Content-Encoding`) passes through, and an identity
  `Content-Length` over the cap is refused before the body is read;
- `gzip` and `deflate` are inflated with `zlib` under a `max_length`, so the
  decoder never produces more than `limit + 1` bytes;
- any other encoding (`br`, `zstd`, stacked encodings) raises
  `UnsupportedEncodingError` before the body is read.

Over the cap raises `BodyTooLargeError`; both derive from `BodyLimitError`
(a `ValueError`). `read_truncated(response, limit)` keeps the first `limit`
decoded bytes instead of refusing — for bodies where a prefix is still useful,
such as `robots.txt`. Requests read this way send
`Accept-Encoding: BOUNDED_ACCEPT_ENCODING` (`gzip, deflate`), so a server never
picks an encoding the reader refuses; `decoded_headers(response)` drops
`Content-Encoding`/`Content-Length` when a buffered response is rebuilt from
the decoded body.

Every capped reader goes through it: the connector client, the plugin-update
source, the inference HTTP client, the voice providers, and the `web_scraper`
and `document_sources` web fetchers. The MCP client keeps its stricter
identity-only rule (`baselith.core.mcp.bounded_http`).

### Adopted call sites

Every framework component that fetches an attacker-influenced URL routes
through `assert_url_safe`/`assert_url_safe_async` or
`create_hardened_async_client`:

| Call site | Module | Internal hosts |
|---|---|---|
| Webhook registration + dispatch | `baselith.core.webhooks.service` (`assert_url_safe`), `baselith.core.webhooks.dispatcher` (`resolve_pinned_target`) | Rejected by default; `WEBHOOK_ALLOW_INTERNAL=true` to allow. |
| Marketplace registry URL | `baselith.core.marketplace.registry` | Rejected by default; loopback (local testing) or `BASELITH_MARKETPLACE_ALLOW_INTERNAL=true` for a trusted internal registry. |
| OIDC discovery + JWKS | `baselith.core.auth.oidc` | Always rejected — issuer and the JWKS URI read back from the discovery document (attacker-influenced if the issuer/its DNS is compromised) are both screened before any HTTP call. No opt-out. |
| A2A client | `baselith.core.a2a.client.A2AClient` | **Environment-aware default** for `A2AClientConfig.allow_internal_endpoints`: allowed outside production (A2A meshes commonly run peer agents on private networks), **denied in production** — the same deny-by-default as the MCP transport and the webhook guard. Setting `A2A_ALLOW_INTERNAL_ENDPOINTS` explicitly (`1`/`true`/`yes`/`on` is truthy, anything else falsy) overrides in both directions, so a private-mesh production deployment opts back in with `A2A_ALLOW_INTERNAL_ENDPOINTS=true`. Production is resolved by the [runtime-environment resolver](config.md#runtime-environment). See [A2A Protocol](a2a.md). |
| MCP Streamable HTTP transport | `baselith.core.mcp.http_client_transport` | Rejected by default; `MCP_ALLOW_INTERNAL_ENDPOINTS=true` for a local/internal MCP server. See [MCP Integration](mcp.md#ssrf-guard-streamable-http-transport). |
| Fine-tuning providers | `baselith.core.finetuning.providers.TogetherProvider` | Rejected; hardcoded public SaaS endpoints, so the guard is defense-in-depth with no functional impact. No opt-out. |
| Plugin exporters (marketplace JWT exchange) | `baselith.core.plugins.exporters.router` | Rejected; always targets `OFFICIAL_MARKETPLACE_URL`. No opt-out. |
| Browser agent navigation + sub-resources | `baselith.plugins.browser_agent` | Rejected by default; `BASELITH_BROWSER_ALLOW_INTERNAL=true`. See [Runtime hardening controls](../advanced/security.md#ssrf-connection-pinning). |
| Web scraper (HTTP + Playwright fetchers) | `baselith.plugins.web_scraper` | Governed by `ScraperConfig.block_private_ips` (default `True`). See [Web Scraper](scraper.md#security-ssrf-protection). |

### Per-caller egress guard

The checks above answer "is this host safe to reach at all". They cannot answer
"is *this caller* allowed to reach it" — that is a policy about plugins, and
`baselith.core.security` must not know what a plugin is (Sacred Core rule). The decision
therefore arrives as a callable:

```python
from baselith.core.security.ssrf import set_egress_guard

set_egress_guard(lambda host: policy.assert_allowed(host))  # raises to refuse
set_egress_guard(None)                                      # remove it
```

The guard is invoked with the target **host** on every screened URL, before the
connection is made, and refuses by raising. Unset by default, which is exactly
the behaviour of the module before it existed: the guard adds a decision point,
never a new default. It composes with — and does not replace — the
loopback/private/link-local screening and the IP pinning described above.

### Deprecated shims

`baselith/core/webhooks/ssrf.py` is a thin backward-compatible shim over
`baselith.core.security.ssrf`, **deprecated since 1.0 and removed in 2.0**.
No framework module imports it any more — the webhook service and dispatcher,
the marketplace registry and the webhook management router all call
`baselith.core.security.ssrf` directly; it exists only for third-party code.

| Deprecated (`baselith.core.webhooks.ssrf`, also re-exported by `baselith.core.webhooks`) | Replacement (`baselith.core.security.ssrf`) |
| --- | --- |
| `validate_webhook_url(url, allow_internal=x)` | `assert_url_safe(url, SsrfPolicy(allow_internal=x))` |
| `resolve_pinned_target(url, allow_internal=x)` | `resolve_pinned_target(url, SsrfPolicy(allow_internal=x))` |
| `WebhookSSRFError` | `SsrfError` |

The two functions are wrapped in `@deprecated(since="1.0", removed_in="2.0", …)`
and emit a `DeprecationWarning` on every call. `WebhookSSRFError` is an
**alias**, not a wrapper — the same class object as `SsrfError` — so
`except WebhookSSRFError` keeps catching what the new API raises; for the same
reason it cannot warn (a warning on the class would fire for every `SsrfError`
the framework raises), and it is deprecated by documentation only. `baselith.core.scraper.utils` and
`baselith.plugins.web_scraper.utils.is_private_ip`/`resolve_safe_ips` are similar
compatibility re-exports (see [Web Scraper](scraper.md#security-ssrf-protection)).

---

## Secret resolution

`SecretsProvider` resolves named secrets to `pydantic.SecretStr`, so
credentials never leak via `repr()`, logs, or Sentry frames.

| Backend (`SECRETS_BACKEND`) | Source |
|---|---|
| `env` (default) | Process environment variables. Identical to current behaviour. |
| `file` | Per-secret files under `SECRETS_DIR` (the Docker/Kubernetes secrets pattern, e.g. `/run/secrets`). Also honours the `<NAME>_FILE` indirection and falls back to the environment. |
| any other name | A provider an installed package declares under the `baselith.secrets_providers` entry point, or one registered in-process (see [below](#external-backends-vault-cloud-secret-managers)). |

```python
from baselith.core.security import get_secret

password = get_secret("DB_PASSWORD")   # SecretStr | None
```

### File backend & the `_FILE` convention

With `SECRETS_BACKEND=file` and `SECRETS_DIR=/run/secrets`, a lookup for
`DB_PASSWORD` resolves in order:

1. `/run/secrets/DB_PASSWORD`, then `/run/secrets/db_password`.
2. The path in `DB_PASSWORD_FILE`, if set.
3. The plain `DB_PASSWORD` environment variable (fallback).

This keeps plaintext secrets out of environment variables and image layers.

Both settings are fields of `SecurityConfig` (`secrets_backend`,
`secrets_dir`), and `get_secrets_provider()` reads them from
`get_security_config()` rather than straight from `os.environ`, so a value
supplied through any settings source applies. The provider and the security
config are both cached for the process: a test that changes `SECRETS_BACKEND`
must reset both (`reset_secrets_provider()` and
`baselith.core.config.security._security_config = None`).

### External backends (Vault, cloud secret managers)

Heavy or environment-specific providers stay **out of `core`** (Sacred Core
rule): `core` ships no vendor SDK and no Vault client. A provider only needs
`get_secret(name) -> SecretStr | None`, and it reaches the runtime one of two
ways — both selected the same way, with `SECRETS_BACKEND=<name>`.

**Ship it as a package (recommended).** Declare a zero-argument factory under
the `baselith.secrets_providers` entry-point group. The operator installs the
package and sets `SECRETS_BACKEND=vault`; no startup code changes. This works
even though the first secret read happens during bootstrap, before any plugin
loads, because discovery is part of building the provider itself.

```toml
# pyproject.toml of your provider package
[project.entry-points."baselith.secrets_providers"]
vault = "acme_vault_secrets:VaultSecretsProvider"
```

```python
# acme_vault_secrets.py — EXAMPLE ONLY, not part of BaselithCore.
# A minimal HashiCorp Vault KV v2 provider. Each secret lives at
# <mount>/data/<path> under a key equal to the secret name.
import os

import httpx
from pydantic import SecretStr


class VaultSecretsProvider:
    def __init__(self) -> None:
        self._addr = os.environ["VAULT_ADDR"].rstrip("/")
        self._token = SecretStr(os.environ["VAULT_TOKEN"])  # never a plain str
        self._mount = os.environ.get("VAULT_KV_MOUNT", "secret")
        self._path = os.environ.get("VAULT_KV_PATH", "baselith")
        self._client = httpx.Client(timeout=5.0)

    def get_secret(self, name: str) -> SecretStr | None:
        response = self._client.get(
            f"{self._addr}/v1/{self._mount}/data/{self._path}",
            headers={"X-Vault-Token": self._token.get_secret_value()},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()["data"]["data"].get(name)
        return SecretStr(value) if value is not None else None
```

A production provider would add caching, token renewal (or Kubernetes/AppRole
auth) and TLS settings; the contract above is all the framework requires.

**Register it in-process** (tests, or an application embedding the framework):

```python
from baselith.core.security import register_secrets_provider

register_secrets_provider("vault", lambda: MyVaultProvider(addr=..., token=...))
# then run with SECRETS_BACKEND=vault
```

**Precedence.** The built-ins `env` and `file` always win and cannot be
shadowed (registering either raises `ValueError`; an entry point with either
name is ignored). An explicit registration beats an entry point of the same
name. The entry-point group is read lazily, only when the selected backend is
neither a built-in nor registered, so the default `env` path never scans
package metadata.

**Fail closed.** A misconfigured backend stops the first secret read rather
than silently reading credentials from the environment:

| Situation | Error |
|---|---|
| Name is not a built-in, registered, or installed | `ValueError` listing the built-in, registered and discovered names |
| Two installed packages declare the same name | `ValueError` ("Ambiguous …") naming both distributions |
| The entry point fails to import, or its factory raises | `RuntimeError` naming the entry point; the original exception is chained, its text is not echoed |
| The factory returns something without `get_secret` | `TypeError` naming the entry point (or registered backend) |

A failed build is not cached, and no secret value ever appears in these
messages or in the provider's log line (which records only the provider class).

### Per-caller secret guard

Which backend answers a lookup is one question; **who may ask** is another, and
it is again a policy about plugins that `baselith.core.security` cannot hold. The same
seam as the egress guard applies:

```python
from baselith.core.security.secrets import set_secret_guard

set_secret_guard(lambda name: policy.assert_may_read(name))  # raises to refuse
set_secret_guard(None)                                       # remove it
```

The guard runs on every `get_secret` call, before the provider is consulted, and
receives the secret's **name only — never its value**, so installing a policy
hook can never become a way to observe credentials. Unset by default. A guard
that raises propagates that exception to the caller unchanged: refusing a read
is deliberately louder than returning `None`, which would be indistinguishable
from an unset secret.
