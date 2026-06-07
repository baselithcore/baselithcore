# Security & Encryption

The `core.security` package provides two domain-agnostic infrastructure
primitives for enterprise deployments:

1. **Encryption at rest** — authenticated, versioned field encryption
   (`core.security.encryption`).
2. **Pluggable secret resolution** — decouples *where* credentials come from
   (environment, mounted files, external managers) from *how* they are consumed
   (`core.security.secrets`).

Both are **opt-in**. With no configuration, the framework behaves exactly as
before: `get_field_encryptor()` returns `None` and secrets resolve from the
process environment.

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
