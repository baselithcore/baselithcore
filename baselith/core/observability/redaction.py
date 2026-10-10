"""Sensitive-data redaction for log entries.

Extracted from ``baselith.core.observability.logging`` (module size cap); all names
are re-exported there for backward compatibility. The structlog processor
:func:`redact_sensitive` runs on every log entry (structlog pipeline AND the
foreign-log pre-chain), masking secrets by key and by value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, MutableMapping
from typing import Any

from baselith.core.config import get_app_config

_REDACTED = "[REDACTED]"

# The one credential vocabulary. Every pass reads it: structured keys (log
# kwargs, nested dicts, the Sentry scrubber), key=value pairs inside log
# messages, and third-party free text (``redact_secrets``). A name added here
# is masked by all three; a name missing here leaks from all three.
_CREDENTIAL_KEYWORDS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "secret_key",
    "secret-key",
    "secretkey",
    "token",
    "api_key",
    "api-key",
    "apikey",
    "private_key",
    "private-key",
    "privatekey",
    "access_key",
    "access-key",
    "accesskey",
    "authorization",
    "bearer",
    "credential",
    "credentials",
    "session",
    "jwt",
    "assertion",
    "verifier",
)

# A structured field (or log kwarg) is a secret when its name *contains* a
# keyword anywhere; the value is replaced wholesale. Deliberately broad
# (``max_tokens`` is masked too): a key is a declared field, so over-masking
# costs one readable value, while a missed key leaks the whole secret.
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = _CREDENTIAL_KEYWORDS
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-admin-token",
        "proxy-authorization",
    }
)

# Anchored to the start of its character run, not to ``\b``: a word boundary
# recurs inside ``a.a.a.…``, and every one of them restarted a scan to the end
# of the run (quadratic; ``"a." * 50000`` cost seconds per log entry).
_EMAIL_REGEX = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# --- key=value pairs inside free text ----------------------------------------
#
# A key in running text is a credential when one of its words *ends* in a
# keyword: ``access_token``, ``clientSecret``, ``x-api-key``, ``db_pwd``,
# ``token_type`` and ``passwordHash`` all qualify, ``tokenizer``,
# ``secretary`` and ``max_tokens`` do not. The keyword is found by a lookahead confined to that word, so a key
# of any length is seen, and a match only starts at a word boundary, so the
# scan stays linear. Earlier forms either wrapped the keyword in unbounded
# ``[\w-]*`` affixes (quadratic backtracking over keyword-like text) or
# bounded them (a long key prefix skipped redaction).
_KEYWORD_ALTERNATION = "|".join(
    re.escape(k) for k in sorted(_CREDENTIAL_KEYWORDS, key=len, reverse=True)
)
# The keyword ends its word at a ``_``/``-``/non-word character, or at a
# camelCase hump (``passwordHash``), matched case-sensitively so that
# ``tokenizer`` stays readable under IGNORECASE.
_KEYWORD_END = r"(?:(?![^\W_])|(?=(?-i:[A-Z])))"
_CREDENTIAL_KEY = rf"(?=[\w-]*?(?:{_KEYWORD_ALTERNATION}){_KEYWORD_END})[\w-]+"
# An auth-scheme word belongs to the value: taken alone it would be
# "redacted" while the credential after it stayed readable.
_SCHEME = r"(?:(?:bearer|basic|token)\s+)"
# Quoted values (JSON, Python reprs): the quotes stay, the content goes. Each
# alternative consumes one character or one escape, so they never overlap; a
# missing closing quote masks to the end of the line (fail closed).
_QUOTED = (
    r'b?"(?:[^"\\\n]|\\.)*(?P<dq>")?'
    r"|b?'(?:[^'\\\n]|\\.)*(?P<sq>')?"
)
_BARE = r"[^\s&,;\"'}\]]+"
# ``password hunter2`` is masked, ``password policy`` is prose: a value that
# follows only whitespace must carry a scheme word or look like a credential
# (a digit or a symbol).
_SHAPED = r"(?=[^\s&,;\"'}\]]*?[\d@#$%^&*_+=/~])"


# ``<key>`` then ``:``/``=`` and any value, or whitespace and a
# credential-shaped value. ``{key}`` is filled per pass below.
_PAIR_TEMPLATE = (
    r"(?<![\w-])(?P<k>{key})(?P<q>[\"']?)"
    rf"(?:(?P<sep>\s*[:=]\s*)(?P<v>{_SCHEME}?(?:{_QUOTED}|{_BARE}))"
    rf"|(?P<ws>[ \t]+)(?P<wv>{_SCHEME}{_BARE}|{_SHAPED}{_BARE}))"
)


def _mask_pair(m: re.Match[str], mask: str) -> str:
    """Rebuild a matched pair with its value masked, quotes preserved."""
    if m.group("sep") is None:
        return f"{m.group('k')}{m.group('q')}{m.group('ws')}{mask}"
    value = m.group("v")
    prefix = "b" if value[:2] in ('b"', "b'") else ""
    opening = value[len(prefix)] if value[len(prefix) :][:1] in ('"', "'") else ""
    closing = m.group("dq") or m.group("sq") or ""
    head = f"{m.group('k')}{m.group('q')}{m.group('sep')}"
    return f"{head}{prefix}{opening}{mask}{closing}"


# Log messages: the credential vocabulary.
_CREDENTIALS_REGEX = re.compile(
    _PAIR_TEMPLATE.replace("{key}", _CREDENTIAL_KEY), re.IGNORECASE
)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _mask_email(match: re.Match[str]) -> str:
    email = match.group(0)
    try:
        user, domain = email.split("@")
    except ValueError:
        return "[EMAIL_REDACTED]"
    masked = (user[:3] + "***") if len(user) > 3 else (user[0] + "***")
    return f"{masked}@{domain}"


def _mask_text(value: str) -> str:
    value = _EMAIL_REGEX.sub(_mask_email, value)
    return _mask_credentials(value, _CREDENTIALS_REGEX, _REDACTED)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        return {
            k: (
                _REDACTED
                if isinstance(k, str) and _key_is_sensitive(k)
                else _redact_value(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def redact_url_credentials(url: str) -> str:
    """Return ``url`` with any embedded ``user:password@`` userinfo removed.

    Connection strings (``redis://:pass@host``, ``postgres://u:p@host``) must
    never be logged verbatim — the password would land in logs/Sentry. Logs the
    scheme/host/port/path only. Anything that is not a ``str`` comes back as
    ``[REDACTED]``: there is no telling what it would print.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not isinstance(url, str):  # nothing safe to echo
        return _REDACTED
    try:
        parts = urlsplit(url)
        if parts.username is None and parts.password is None:
            return url
        host = parts.hostname or ""
        # ``.port`` raises ValueError on a non-numeric or out-of-range port.
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:  # malformed URL (bad IPv6 netloc, bad port)
        # Fail closed: a URL urlsplit cannot parse may still carry userinfo,
        # and returning it unchanged would log the password.
        return _strip_userinfo(url)
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _strip_userinfo(url: str) -> str:
    """Drop everything up to the last ``@`` of the authority, without parsing."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        # Scheme-relative (``//user:pw@host``) still has an authority.
        sep = "//" if url.startswith("//") else ""
        scheme, rest = "", url[len(sep) :]
    authority, slash, tail = rest.partition("/")
    if "@" not in authority:
        return url
    host = authority.rsplit("@", 1)[1]
    return f"{scheme}{sep}{host}{slash}{tail}"


def redact_sensitive(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: strip secrets from every log entry.

    Two complementary passes, so both structured kwargs and raw message strings
    are covered (the previous stdlib-filter approach saw neither structlog
    kwargs nor JSON-rendered fields):

    1. **By key** — any field whose name marks it a secret (``token``,
       ``authorization``, ``password``…) has its value replaced with
       ``[REDACTED]``, recursively into nested dicts/lists.
    2. **By value** — remaining string values (including the message ``event``)
       have emails masked and inline ``key=secret``, JSON/repr
       ``"key": "secret"``, ``Bearer <t>`` and JWT-looking values redacted.

    Gated on ``log_masking_enabled`` (default on). Installed in both the
    structlog pipeline and the foreign-log pre-chain by ``configure_logging``,
    so it applies on the FastAPI/uvicorn path — not only the MCP server.
    """
    try:
        if not getattr(get_app_config(), "log_masking_enabled", True):
            return event_dict
    # Unreadable config: keep masking on (the safe default).
    except Exception:  # silent-ok: structlog processor; logging here recurses
        pass

    for key in list(event_dict.keys()):
        if isinstance(key, str) and key != "event" and _key_is_sensitive(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


# --- free text from third parties (provider error bodies, exception messages) ----
#
# The key-name pass cannot see inside a string. These patterns are for text
# that may echo a credential back: a provider's error body, a transport error
# carrying a URL with a query. Same pairs as the log pass plus the OAuth
# ``code`` (a numeric ``code`` — an HTTP/error status — is kept). A
# non-credential pair is never matched, so it cannot swallow a credential
# behind it (``status=token=…``).
_SECRET_PAIR = re.compile(
    _PAIR_TEMPLATE.replace("{key}", rf"(?:{_CREDENTIAL_KEY}|code(?![\w-]))"),
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(
    r"\beyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*|\bya29\.[\w-]{10,}|(?<![\w/])1//[\w-]{20,}"
)
_MAX_FREE_TEXT = 4096


def _mask_credentials(text: str, pairs: re.Pattern[str], mask: str) -> str:
    """Mask credential pairs, standalone bearer tokens and JWT-like strings."""

    def pair(m: re.Match[str]) -> str:
        value = m.group("v") if m.group("sep") is not None else m.group("wv")
        if m.group("k").lower() == "code" and value.strip("\"'").isdigit():
            return m.group(0)
        return _mask_pair(m, mask)

    text = pairs.sub(pair, text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {mask}", text)
    return _JWT.sub(lambda _m: mask, text)


def redact_secrets(
    text: str, secrets: Iterable[str] = (), *, mask: str = _REDACTED
) -> str:
    """Mask credentials in third-party free text (key=value, JSON, bearer, JWT).

    Covers every key the credential vocabulary names (``*_token``,
    ``*secret``, ``password``/``pwd``, ``api_key``, ``private_key``,
    ``Authorization``…) and OAuth ``code`` values, in ``key=value``,
    ``key: value``, JSON and Python-repr (``"key": "value"``, ``'key': 'v'``)
    forms, ``key <credential-shaped value>``, plus standalone
    ``Bearer <token>`` and JWT-looking strings. A numeric ``code`` (an
    HTTP/error status) is kept. ``secrets`` are masked wherever they appear,
    whatever surrounds them.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, mask)
    return _mask_credentials(text, _SECRET_PAIR, mask)


def safe_snippet(
    text: str, secrets: Iterable[str] = (), *, limit: int = 200, mask: str = _REDACTED
) -> str:
    """A bounded, single-line, redacted excerpt of untrusted ``text``.

    Redaction runs before the cut so a credential straddling the limit cannot
    leave a readable prefix; control characters (log forging) become spaces.
    """
    cleaned = redact_secrets(text[:_MAX_FREE_TEXT], secrets, mask=mask)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


__all__ = [
    "redact_secrets",
    "redact_sensitive",
    "redact_url_credentials",
    "safe_snippet",
]
