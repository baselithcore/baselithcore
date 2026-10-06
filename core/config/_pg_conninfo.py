"""libpq transport defaults merged into a PostgreSQL connection string.

Without ``connect_timeout`` a connect to a blackholed host waits out the OS TCP
timeout (minutes); without TCP keepalives a connection whose peer vanished
(failover, NAT/conntrack expiry, a load balancer dropping idle flows) looks
alive in the pool until a query hangs on it; without ``tcp_user_timeout`` a
write to such a peer retransmits for up to ~15 minutes on Linux.

Defaults are *merged*, never forced: a parameter already present in an
explicit ``DATABASE_URL`` / ``DB_REPLICA_URL`` — URL or ``key=value`` form —
wins, and so does ``PGCONNECT_TIMEOUT`` in the environment for the connect
deadline.
"""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

#: ``key=`` at the start of a libpq keyword/value DSN or after whitespace.
_KEYWORD_RE = re.compile(r"(?:^|\s)([A-Za-z_]+)\s*=")


def transport_params(
    *,
    connect_timeout: int,
    keepalives_idle: int,
    keepalives_interval: int,
    keepalives_count: int,
    tcp_user_timeout_ms: int,
) -> dict[str, str]:
    """libpq parameters for the configured transport budgets.

    A value of ``0`` leaves that parameter to libpq / the operating system.

    Args:
        connect_timeout: Connect deadline in seconds.
        keepalives_idle: Idle seconds before the first TCP keepalive probe.
        keepalives_interval: Seconds between unanswered keepalive probes.
        keepalives_count: Unanswered probes before the connection is dead.
        tcp_user_timeout_ms: Milliseconds transmitted data may stay
            unacknowledged before the connection is closed (Linux).

    Returns:
        Parameter name to string value, in a stable order.
    """
    params: dict[str, str] = {}
    if connect_timeout > 0 and not os.environ.get("PGCONNECT_TIMEOUT"):
        params["connect_timeout"] = str(connect_timeout)
    keepalive = {
        "keepalives_idle": keepalives_idle,
        "keepalives_interval": keepalives_interval,
        "keepalives_count": keepalives_count,
    }
    if any(value > 0 for value in keepalive.values()):
        params["keepalives"] = "1"
        params.update({k: str(v) for k, v in keepalive.items() if v > 0})
    if tcp_user_timeout_ms > 0:
        params["tcp_user_timeout"] = str(tcp_user_timeout_ms)
    return params


def merge_conninfo(dsn: str, defaults: dict[str, str]) -> str:
    """Add ``defaults`` to ``dsn`` without overriding what it already sets.

    Args:
        dsn: A ``scheme://...`` URL or a libpq ``key=value`` string.
        defaults: Parameters to add when absent.

    Returns:
        The DSN in its original form, with the missing parameters appended.
        An unparsable URL is returned unchanged.
    """
    if not defaults or not dsn:
        return dsn
    if "://" not in dsn:
        present = {match.group(1).lower() for match in _KEYWORD_RE.finditer(dsn)}
        extra = [f"{k}={v}" for k, v in defaults.items() if k not in present]
        return " ".join([dsn.strip(), *extra]) if extra else dsn
    try:
        parts = urlsplit(dsn)
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return dsn
    present = {key.lower() for key, _ in query}
    missing = [(k, v) for k, v in defaults.items() if k not in present]
    if not missing:
        return dsn
    # Append to the query verbatim instead of re-encoding it: ``urlencode``
    # turns ``%20`` into ``+``, which libpq does not read as a space, so an
    # existing ``options=-c%20...`` would silently change meaning.
    # Plain string surgery, not ``urlunsplit``: that drops the ``//`` of a
    # host-less ``postgresql:///db?host=/run/postgresql`` socket URL.
    added = urlencode(missing)
    head, hash_mark, fragment = dsn.partition("#")
    if head.endswith(("?", "&")):
        separator = ""
    else:
        separator = "&" if parts.query else "?"
    return f"{head}{separator}{added}{hash_mark}{fragment}"


__all__ = ["merge_conninfo", "transport_params"]
