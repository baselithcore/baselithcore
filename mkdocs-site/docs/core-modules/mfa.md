---
title: Multi-Factor Authentication (TOTP)
description: Time-based one-time-password second factor and recovery codes
---

`core/auth/mfa.py` adds a **second authentication factor** to BaselithCore:
time-based one-time passwords (TOTP, [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238),
built on HOTP [RFC 4226](https://www.rfc-editor.org/rfc/rfc4226)) plus single-use
recovery codes. It satisfies the multi-factor / continuous-authentication
control of **NIS2 Art. 21(2)(j)**.

The implementation is **standard-library only** — no new dependency — and is
compatible with off-the-shelf authenticator apps (Google Authenticator, Authy,
1Password, Microsoft Authenticator) via the `otpauth://` provisioning URI.

## Design

- **Opt-in & additive.** Disabled by default (`MFA_ENABLED=false`). Enabling it
  has no effect on existing JWT / API-key / OIDC paths — it is a step-up your
  application invokes during login, not a middleware that rewrites auth.
- **Storage-agnostic.** The framework does not own a user store, so MFA is a set
  of primitives plus a config-bound `TOTPProvider`. Your application persists
  the enrollment secret (encrypted at rest) and the recovery-code hashes.
- **Secrets stay wrapped.** The shared secret is carried in `pydantic.SecretStr`
  and the plaintext recovery codes are excluded from `repr`, so neither leaks
  via logs or Sentry frames.
- **Constant-time checks.** Both OTP and recovery-code comparisons use
  `hmac.compare_digest` to avoid timing side channels.

## Configuration

| Setting       | Env var       | Default        | Description                                        |
| ------------- | ------------- | -------------- | -------------------------------------------------- |
| `mfa_enabled` | `MFA_ENABLED` | `false`        | Master switch for the MFA step-up.                 |
| `mfa_issuer`  | `MFA_ISSUER`  | `BaselithCore` | Issuer label shown in the user's authenticator app.|

## Enrollment flow

```python
from core.auth import get_auth_manager

auth = get_auth_manager()
enrollment = auth.mfa.enroll(account_name="alice@example.com")

# Show the user ONCE — render the URI as a QR code, list the recovery codes:
qr_uri = enrollment.provisioning_uri()      # otpauth://totp/BaselithCore:alice@...
recovery_codes = enrollment.recovery_codes  # plaintext, display-once

# Persist server-side (encrypt the secret with core.security.encryption):
store.save_mfa(
    user_id="alice",
    secret_ciphertext=encryptor.encrypt(enrollment.secret.get_secret_value()),
    recovery_hashes=list(enrollment.recovery_code_hashes),
)
```

## Verification (login step-up)

```python
secret = decryptor.decrypt(store.load_secret("alice"))

# Primary path: 6-digit TOTP from the authenticator app.
if auth.mfa.verify_code(secret, submitted_code):
    grant_session()

# Fallback: a single-use recovery code.
used = auth.mfa.verify_recovery_code(submitted_code, store.recovery_hashes("alice"))
if used is not None:
    store.consume_recovery_hash("alice", used)   # recovery codes are single-use
    grant_session()
```

`verify_code` tolerates ±1 time step (±30 s) of clock skew by default so a small
client/server drift does not reject a valid code.

## API surface

| Symbol                                     | Purpose                                                |
| ------------------------------------------ | ------------------------------------------------------ |
| `TOTPProvider`                             | Config-bound facade (obtain via `AuthManager.mfa`).    |
| `MFAEnrollment`                            | Enrollment artifacts (secret, recovery codes/hashes).  |
| `generate_secret()`                        | Mint a base32 shared secret (160-bit default).         |
| `generate_totp(secret, ...)`               | Compute the current TOTP code.                         |
| `verify_totp(secret, code, ...)`           | Verify a code with clock-skew tolerance.               |
| `provisioning_uri(secret, account, issuer)`| Build the `otpauth://` QR-enrollment URI.              |
| `generate_recovery_codes(count)`           | Mint single-use recovery codes (plaintext, show once). |
| `hash_recovery_code(code)`                 | SHA-256 hash for storage at rest.                      |
| `verify_recovery_code(code, hashes)`       | Constant-time match; returns the consumed hash.        |

All symbols are re-exported from `core.auth`.

## Operational notes

- **Encrypt the secret at rest.** Use [`core/security/encryption.py`](security.md)
  (`DATA_ENCRYPTION_KEYS`) — a stored TOTP secret is equivalent to a password.
- **Recovery codes are single-use.** After `verify_recovery_code` returns a hash,
  remove it from the stored set so it cannot be replayed.
- **Enforce MFA on privileged roles first.** Require the step-up for `ADMIN`
  and any identity holding control-plane scopes (`*`, `keys:manage`, …).
