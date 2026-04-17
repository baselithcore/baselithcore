"""Encrypted provider-secret store for Baselithbot.

Persists operator-supplied provider API keys on disk using symmetric Fernet
encryption. The dashboard UI may *set*, *rotate*, *test*, and *delete* keys;
it may never *read* them back in plaintext — snapshots return a masked
``last4`` preview and a boolean ``configured`` flag only.

Security model
--------------
- Master key lives in ``BASELITHBOT_SECRET_KEY`` (base64 Fernet key, 44
  chars). When missing, a key is generated once and persisted to
  ``<state>/.secret_key`` with mode ``0600``; the file is never logged and
  never shipped in tool responses.
- Ciphertexts are stored as JSON at ``<state>/provider_keys.enc.json`` with
  mode ``0600``; atomic write via ``.tmp`` + ``os.replace``.
- Reads decrypt on demand; plaintext is held only in the caller's stack
  frame. Redaction helpers return ``"***" + last4`` for logs + telemetry.
- Mutations run under a ``threading.Lock`` so concurrent dashboard requests
  do not race the on-disk file.
- ``provider`` values are validated against ``ALLOWED_PROVIDERS`` to prevent
  abuse of the endpoint as a general-purpose secret bucket.

Threat model (explicit non-goals)
---------------------------------
- Not a HSM. Anyone with read access to *both* the state directory *and*
  the ``BASELITHBOT_SECRET_KEY`` env (or ``.secret_key`` file) can decrypt
  all keys. Deploy with proper filesystem permissions and secret mgmt for
  the master key.
- Not MFA. The dashboard bearer token (``BASELITHBOT_DASHBOARD_TOKEN``) is
  the only gate on the write endpoints.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core.observability.logging import get_logger

logger = get_logger(__name__)


ALLOWED_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "anthropic", "google", "ollama", "huggingface"}
)

_MASTER_KEY_ENV = "BASELITHBOT_SECRET_KEY"


class SecretStoreError(RuntimeError):
    """Raised when the secret store cannot read/write keys safely."""


def _mask(value: str) -> str:
    """Return ``"***"+last4`` preview for logs / UI responses."""
    value = value.strip()
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]


def _load_or_create_master_key(state_dir: Path) -> bytes:
    env_key = os.environ.get(_MASTER_KEY_ENV, "").strip()
    if env_key:
        try:
            Fernet(env_key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise SecretStoreError(
                f"{_MASTER_KEY_ENV} is not a valid Fernet key: {exc}"
            ) from exc
        return env_key.encode("ascii")

    key_path = state_dir / ".secret_key"
    if key_path.exists():
        data = key_path.read_bytes().strip()
        if not data:
            raise SecretStoreError(f"empty master key file: {key_path}")
        return data

    state_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.warning(
        "baselithbot_secret_store_master_key_generated",
        path=str(key_path),
        hint=(
            f"Persisted new master key to {key_path}. For multi-node "
            f"deploys, lift it to the {_MASTER_KEY_ENV} env var."
        ),
    )
    return key


class ProviderSecretStore:
    """Thread-safe encrypted store for per-provider API keys."""

    def __init__(self, state_dir: Path | str) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._state_dir / "provider_keys.enc.json"
        self._lock = threading.Lock()
        self._fernet = Fernet(_load_or_create_master_key(self._state_dir))
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def _validate_provider(provider: str) -> str:
        norm = provider.strip().lower()
        if norm not in ALLOWED_PROVIDERS:
            raise SecretStoreError(
                f"unknown provider '{provider}'; "
                f"allowed={sorted(ALLOWED_PROVIDERS)}"
            )
        return norm

    def snapshot(self) -> list[dict[str, Any]]:
        """Return UI-safe list of configured keys (no plaintext)."""
        with self._lock:
            out: list[dict[str, Any]] = []
            for provider in sorted(ALLOWED_PROVIDERS):
                entry = self._entries.get(provider)
                if entry is None:
                    out.append(
                        {
                            "provider": provider,
                            "configured": False,
                            "last4": None,
                            "updated_at": None,
                        }
                    )
                else:
                    out.append(
                        {
                            "provider": provider,
                            "configured": True,
                            "last4": entry.get("last4"),
                            "updated_at": entry.get("updated_at"),
                        }
                    )
            return out

    def get_plaintext(self, provider: str) -> str | None:
        """Decrypt and return the key for ``provider`` or None if unset.

        Intended for server-side consumers (VisionService, LLM clients).
        Never expose the return value to the UI or client logs.
        """
        norm = self._validate_provider(provider)
        with self._lock:
            entry = self._entries.get(norm)
            if entry is None:
                return None
            token = entry["ciphertext"].encode("ascii")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError(
                f"cannot decrypt stored key for '{norm}' — master key rotated?"
            ) from exc

    def set(self, provider: str, api_key: str) -> dict[str, Any]:
        """Encrypt and persist ``api_key`` for ``provider``."""
        norm = self._validate_provider(provider)
        plain = api_key.strip()
        if len(plain) < 8:
            raise SecretStoreError("api_key too short (min 8 chars)")
        if len(plain) > 512:
            raise SecretStoreError("api_key too long (max 512 chars)")
        ciphertext = self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")
        with self._lock:
            self._entries[norm] = {
                "ciphertext": ciphertext,
                "last4": _mask(plain),
                "updated_at": time.time(),
            }
            self._persist_locked()
        logger.info(
            "baselithbot_secret_store_set", provider=norm, last4=_mask(plain)
        )
        return {
            "provider": norm,
            "configured": True,
            "last4": self._entries[norm]["last4"],
            "updated_at": self._entries[norm]["updated_at"],
        }

    def delete(self, provider: str) -> bool:
        norm = self._validate_provider(provider)
        with self._lock:
            if norm not in self._entries:
                return False
            del self._entries[norm]
            self._persist_locked()
        logger.info("baselithbot_secret_store_delete", provider=norm)
        return True

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError) as exc:
            logger.warning(
                "baselithbot_secret_store_load_failed",
                path=str(self._path),
                error=str(exc),
            )
            return
        if not isinstance(data, dict):
            return
        for provider, entry in data.items():
            if provider in ALLOWED_PROVIDERS and isinstance(entry, dict):
                self._entries[provider] = {
                    "ciphertext": str(entry.get("ciphertext", "")),
                    "last4": str(entry.get("last4", "***")),
                    "updated_at": float(entry.get("updated_at") or 0.0),
                }

    def _persist_locked(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = json.dumps(self._entries, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self._path)


__all__ = [
    "ALLOWED_PROVIDERS",
    "ProviderSecretStore",
    "SecretStoreError",
]
