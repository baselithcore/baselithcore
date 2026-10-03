"""Publisher keys and the on-disk trust store that names them.

Split out of :mod:`core.plugins.signing`, which re-exports every public name
here; ``BASELITH_PLUGIN_TRUST_STORE`` points at the JSON file.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUST_STORE_ENV = "BASELITH_PLUGIN_TRUST_STORE"

#: Raw Ed25519 public keys are 32 bytes — 64 hex characters.
_PUBLIC_KEY_HEX_LEN = 64


@dataclass(frozen=True, slots=True)
class TrustedKey:
    """One publisher key a deployment is willing to accept.

    Attributes:
        key_id: Operator-facing label for the key, used in refusal logs so a
            rejection names *which* publisher was involved. Defaults to a
            fingerprint (the first 16 hex characters) when the store omits it.
        public_key_hex: Hex-encoded 32-byte raw Ed25519 public key.
        not_after: Instant the key stops being accepted, or ``None`` for a key
            with no expiry. Naive values are read as UTC.
        revoked: Set by an operator when a key is compromised or retired. A
            revoked key is refused immediately, regardless of ``not_after``.
        plugins: Plugin names this key may sign for, or ``None`` for any
            plugin. A scoped key limits the blast radius of one compromised
            publisher key to the plugins it publishes; an empty tuple signs
            for nothing (fail closed on an emptied scope).
    """

    key_id: str
    public_key_hex: str
    not_after: datetime | None = None
    revoked: bool = False
    plugins: tuple[str, ...] | None = None

    def covers(self, plugin_name: str | None) -> bool:
        """Whether this key may vouch for ``plugin_name``.

        ``None`` (caller does not know the plugin) matches every key, so a
        scope can only narrow a check that names the plugin.
        """
        if self.plugins is None or plugin_name is None:
            return True
        return plugin_name in self.plugins

    def rejection_reason(self, now: datetime | None = None) -> str | None:
        """Why this key may not be used, or ``None`` when it is usable.

        Args:
            now: Instant to evaluate expiry against; defaults to the current
                UTC time. Injected by tests rather than by callers.

        Returns:
            ``"revoked"``, ``"expired on <timestamp>"``, or ``None``.
        """
        if self.revoked:
            return "revoked"
        if self.not_after is not None and (now or datetime.now(UTC)) >= self.not_after:
            return f"expired on {self.not_after.isoformat()}"
        return None

    @property
    def is_usable(self) -> bool:
        """Whether the key counts as a trust root right now."""
        return self.rejection_reason() is None


#: Stand-in expiry for an entry whose ``not_after`` will not parse. Safely in
#: the past, so an unreadable date refuses the key instead of quietly reading
#: as "never expires" — fail closed.
_ALREADY_EXPIRED = datetime.min.replace(tzinfo=UTC)


def _parse_not_after(raw: object, key_id: str) -> datetime | None:
    """Parse a store entry's ``not_after``.

    Args:
        raw: The value as it appears in the store.
        key_id: Label for the log line when the value is unreadable.

    Returns:
        ``None`` when the entry declares no expiry, a timezone-aware
        ``datetime`` when it parses, and :data:`_ALREADY_EXPIRED` when it does
        not.
    """
    if raw is None or raw == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        logger.error(
            "Plugin trust store: key %s has an unparseable not_after (%r); "
            "treating the key as expired.",
            key_id,
            raw,
        )
        return _ALREADY_EXPIRED
    # A store written by hand is far more likely to mean UTC than local time,
    # and an aware value is required for the comparison in rejection_reason.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_store_entry(entry: object) -> TrustedKey | None:
    """Turn one JSON object from the trust store into a :class:`TrustedKey`."""
    if not isinstance(entry, dict):
        logger.error("Plugin trust store: skipping non-object entry %r.", entry)
        return None
    public_hex = str(entry.get("public_key_hex", "")).strip().lower()
    key_id = str(entry.get("key_id") or public_hex[:16] or "<unnamed>")
    if len(public_hex) != _PUBLIC_KEY_HEX_LEN:
        logger.error(
            "Plugin trust store: skipping key %s — public_key_hex must be %d "
            "hex characters (a raw 32-byte Ed25519 key).",
            key_id,
            _PUBLIC_KEY_HEX_LEN,
        )
        return None
    try:
        bytes.fromhex(public_hex)
    except ValueError:
        logger.error(
            "Plugin trust store: skipping key %s — public_key_hex is not hex.",
            key_id,
        )
        return None
    return TrustedKey(
        key_id=key_id,
        public_key_hex=public_hex,
        not_after=_parse_not_after(entry.get("not_after"), key_id),
        revoked=bool(entry.get("revoked", False)),
        plugins=_parse_plugins(entry.get("plugins"), key_id),
    )


def _parse_plugins(raw: object, key_id: str) -> tuple[str, ...] | None:
    """Parse a store entry's optional ``plugins`` scope.

    Absent or ``null`` means unscoped. Anything else must be a list of
    non-empty strings; a malformed scope is read as *empty* — the key then
    signs for nothing — rather than as unscoped, so a typo cannot widen trust.
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(
        isinstance(name, str) and name.strip() for name in raw
    ):
        logger.error(
            "Plugin trust store: key %s has a malformed plugins scope (%r); "
            "the key signs for no plugin until it is fixed.",
            key_id,
            raw,
        )
        return ()
    return tuple(dict.fromkeys(name.strip() for name in raw))


def load_trust_store(path: str | Path | None = None) -> list[TrustedKey]:
    """Read the trust store file, if one is configured.

    The file is a JSON object ``{"keys": [...]}`` (a bare list is also
    accepted) whose entries are ``{key_id, public_key_hex, not_after,
    revoked}``. ``not_after`` and ``revoked`` are optional.

    Args:
        path: Store location; defaults to ``BASELITH_PLUGIN_TRUST_STORE``.

    Returns:
        Every well-formed entry, revoked and expired ones included — callers
        that want only usable keys should use :func:`load_trust_roots`. A
        missing, unreadable or malformed store yields an empty list and an
        ERROR log: with signature enforcement on, no trust roots means every
        plugin is refused, which is the fail-closed outcome.
    """
    raw_path = str(path) if path is not None else os.environ.get(_TRUST_STORE_ENV, "")
    if not raw_path.strip():
        return []
    store = Path(raw_path.strip())
    try:
        if store.stat().st_mode & 0o022:
            logger.error(
                "Plugin trust store %s is group- or world-writable; no keys "
                "loaded until it is owner-writable only (chmod 0644 or 0600).",
                store,
            )
            return []
        payload = json.loads(store.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("Plugin trust store %s does not exist; no keys loaded.", store)
        return []
    except (OSError, ValueError) as exc:
        logger.error(
            "Plugin trust store %s could not be read (%s); no keys loaded.",
            store,
            type(exc).__name__,
        )
        return []

    entries = payload.get("keys") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        logger.error(
            "Plugin trust store %s must hold a list of keys (or a 'keys' "
            "array); no keys loaded.",
            store,
        )
        return []
    return [key for key in map(_parse_store_entry, entries) if key is not None]


__all__ = ["TrustedKey", "load_trust_store"]
