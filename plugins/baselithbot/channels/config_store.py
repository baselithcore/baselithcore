"""Encrypted per-channel configuration store for Baselithbot.

Persists operator-supplied channel configuration (credentials + enabled
flag) on disk using symmetric Fernet encryption with the same master key
as :mod:`plugins.baselithbot.secret_store`.

UI-safe snapshots mask every credential ending in ``token``/``key``/
``password``/``secret`` and return a boolean ``configured`` flag so the
dashboard can never read a secret back in plaintext.
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

from ..secret_store import SecretStoreError, _load_or_create_master_key

logger = get_logger(__name__)


_SENSITIVE_SUFFIXES: tuple[str, ...] = (
    "token",
    "key",
    "password",
    "secret",
    "private_key_hex",
)


def _is_sensitive(field: str) -> bool:
    lower = field.lower()
    return any(lower.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def _mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]


class ChannelConfigStore:
    """Thread-safe encrypted store for per-channel config + enabled flag."""

    def __init__(self, state_dir: Path | str) -> None:
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._state_dir / "channel_configs.enc.json"
        self._lock = threading.Lock()
        self._fernet = Fernet(_load_or_create_master_key(self._state_dir))
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def has(self, channel: str) -> bool:
        with self._lock:
            return channel in self._entries

    def is_enabled(self, channel: str) -> bool:
        with self._lock:
            entry = self._entries.get(channel)
            return bool(entry and entry.get("enabled"))

    def enabled_channels(self) -> list[str]:
        with self._lock:
            return sorted(
                name
                for name, entry in self._entries.items()
                if entry.get("enabled")
            )

    def get_config(self, channel: str) -> dict[str, Any] | None:
        """Decrypt and return the raw config dict (server-side only)."""
        with self._lock:
            entry = self._entries.get(channel)
            if entry is None:
                return None
            token = entry["ciphertext"].encode("ascii")
        try:
            raw = self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError(
                f"cannot decrypt stored config for '{channel}'"
            ) from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise SecretStoreError(
                f"stored config for '{channel}' is not valid JSON"
            ) from exc
        return parsed if isinstance(parsed, dict) else None

    def snapshot_entry(
        self, channel: str, required: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """UI-safe snapshot for a single channel (no plaintext secrets)."""
        with self._lock:
            entry = self._entries.get(channel)
            enabled = bool(entry and entry.get("enabled"))
            updated_at = float(entry.get("updated_at") or 0.0) if entry else 0.0
        config = self.get_config(channel) if entry else None
        safe: dict[str, Any] = {}
        missing: list[str] = []
        if config:
            for field, value in config.items():
                if not isinstance(value, (str, int, float, bool)):
                    continue
                sval = str(value)
                if _is_sensitive(field):
                    safe[field] = _mask(sval) if sval else ""
                else:
                    safe[field] = value
        for field in required:
            if not (config and str(config.get(field) or "").strip()):
                missing.append(field)
        return {
            "channel": channel,
            "configured": not missing,
            "enabled": enabled,
            "updated_at": updated_at or None,
            "safe_config": safe,
            "required_fields": list(required),
            "missing_fields": missing,
        }

    def set(self, channel: str, config: dict[str, Any]) -> None:
        """Encrypt and persist ``config`` for ``channel`` (preserves enabled)."""
        payload = json.dumps(config).encode("utf-8")
        if len(payload) > 16 * 1024:
            raise SecretStoreError("config too large (max 16 KiB)")
        ciphertext = self._fernet.encrypt(payload).decode("ascii")
        with self._lock:
            existing = self._entries.get(channel, {})
            self._entries[channel] = {
                "ciphertext": ciphertext,
                "enabled": bool(existing.get("enabled", False)),
                "updated_at": time.time(),
            }
            self._persist_locked()
        logger.info("baselithbot_channel_config_set", channel=channel)

    def set_enabled(self, channel: str, enabled: bool) -> None:
        with self._lock:
            entry = self._entries.get(channel)
            if entry is None:
                raise SecretStoreError(
                    f"channel '{channel}' has no stored config"
                )
            entry["enabled"] = bool(enabled)
            entry["updated_at"] = time.time()
            self._persist_locked()
        logger.info(
            "baselithbot_channel_enabled_changed",
            channel=channel,
            enabled=enabled,
        )

    def delete(self, channel: str) -> bool:
        with self._lock:
            if channel not in self._entries:
                return False
            del self._entries[channel]
            self._persist_locked()
        logger.info("baselithbot_channel_config_delete", channel=channel)
        return True

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError) as exc:
            logger.warning(
                "baselithbot_channel_config_load_failed",
                path=str(self._path),
                error=str(exc),
            )
            return
        if not isinstance(data, dict):
            return
        for channel, entry in data.items():
            if not isinstance(entry, dict):
                continue
            self._entries[channel] = {
                "ciphertext": str(entry.get("ciphertext", "")),
                "enabled": bool(entry.get("enabled", False)),
                "updated_at": float(entry.get("updated_at") or 0.0),
            }

    def _persist_locked(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = json.dumps(self._entries, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self._path)


__all__ = ["ChannelConfigStore"]
