"""Environment-string parsers for :class:`core.config.security.SecurityConfig`.

Credentials reach the process as flat strings — ``key=scope|scope,...``,
``kid:secret``, ``idp_role:app_role`` — and each needs coercing into the typed
structure the field declares. The coercion is pure and independently testable,
so it lives here and the settings class keeps only its declarations and the
cross-field checks that need the whole model.

Every parser is lenient on shape and strict on secrecy: a malformed entry is
skipped rather than raised, so one stray comma cannot stop a deployment, and
every credential comes back wrapped in ``SecretStr``.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr


def coerce_to_secret_set(value: Any) -> Any:
    """Coerce a comma-separated string or mixed iterable to ``set[SecretStr]``."""
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        return {SecretStr(item) for item in _split(value)}
    if isinstance(value, (list, set, tuple)):
        return {x if isinstance(x, SecretStr) else SecretStr(str(x)) for x in value}
    return value


def parse_role_map(value: Any) -> Any:
    """Parse comma-separated ``idp_role:app_role`` pairs into a dict."""
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    parsed: dict[str, str] = {}
    for entry in _split(value):
        idp_role, separator, app_role = entry.partition(":")
        if separator and idp_role.strip() and app_role.strip():
            parsed[idp_role.strip()] = app_role.strip().lower()
    return parsed


def parse_algorithms(value: Any) -> Any:
    """Allow a comma-separated string for the OIDC algorithm list."""
    if isinstance(value, str):
        return _split(value)
    return value


def parse_scoped_keys(value: Any) -> Any:
    """Parse ``key=scope|scope,...`` into ``dict[SecretStr, set[str]]``.

    Already-parsed dicts pass through with their keys wrapped and their scopes
    coerced to a set.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {
            (k if isinstance(k, SecretStr) else SecretStr(str(k))): set(v)
            for k, v in value.items()
        }
    if not isinstance(value, str):
        return value
    parsed: dict[SecretStr, set[str]] = {}
    for entry in _split(value):
        key, separator, scope_string = entry.partition("=")
        if not separator:
            continue
        scopes = {s.strip().lower() for s in scope_string.split("|") if s.strip()}
        if key.strip() and scopes:
            parsed[SecretStr(key.strip())] = scopes
    return parsed


def parse_encryption_keys(value: Any) -> Any:
    """Parse comma-separated ``kid:secret`` pairs into ``dict[str, SecretStr]``.

    A bare value without ``:`` is loaded under the id ``default``, so the common
    single-key case stays simple. Already-parsed dicts pass through.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {
            str(k): (v if isinstance(v, SecretStr) else SecretStr(str(v)))
            for k, v in value.items()
        }
    if not isinstance(value, str):
        return value
    parsed: dict[str, SecretStr] = {}
    for entry in _split(value):
        if ":" in entry:
            key_id, secret = entry.split(":", 1)
            parsed[key_id.strip()] = SecretStr(secret)
        else:
            parsed["default"] = SecretStr(entry)
    return parsed


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


__all__ = [
    "coerce_to_secret_set",
    "parse_algorithms",
    "parse_encryption_keys",
    "parse_role_map",
    "parse_scoped_keys",
]
