"""The environment a stdio MCP server process is spawned with.

The parent's environment is never inherited wholesale (it carries API keys, DB
URLs, cloud credentials), and the server spec's own ``env`` is not trusted to
steer how the interpreter loads code: ``LD_PRELOAD``, ``PYTHONPATH``,
``NODE_OPTIONS`` and friends turn "set an environment variable" into "run my
code before main()". Those keys are an *error* in a spec, not a silent drop,
unless the operator lists the exact key in ``MCP_STDIO_ALLOWED_ENV_KEYS``.
"""

from __future__ import annotations

import os
import re

from core.config import get_mcp_config
from core.mcp.stdio_binaries import split_list, trusted_bin_dirs

_ENV_ALLOW_EXACT = frozenset(
    {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TMPDIR", "TEMP",
        "TMP", "TZ", "TERM",
        # Windows: a process without these cannot start or find DLLs.
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA",
        "LOCALAPPDATA",
    }
)  # fmt: skip
_ENV_ALLOW_PREFIXES = ("LC_",)

#: Exact (upper-cased) names a spec may not set.
_DENIED_EXACT = frozenset(
    {
        "PATH", "PATHEXT", "COMSPEC", "IFS", "ENV", "BASH_ENV", "CDPATH", "SHELLOPTS",
        "BASHOPTS", "PS4", "PROMPT_COMMAND", "HOME", "USERPROFILE", "APPDATA",
        "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CONFIG_DIRS",
        "XDG_DATA_DIRS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "FTP_PROXY", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
        "RUBYOPT", "RUBYLIB", "PERL5OPT", "PERL5LIB", "GCONV_PATH", "LOCPATH",
        "NLSPATH", "HOSTALIASES", "RESOLV_HOST_CONF", "MALLOC_CHECK_",
    }
)  # fmt: skip
#: Prefixes a spec may not set: loader, runtime and package-manager switches.
_DENIED_PREFIXES = (
    "LD_", "DYLD_", "GIT_", "NODE_", "NPM_CONFIG_", "BUN_", "DENO_", "UV_", "PIP_",
    "PNPM_", "YARN_", "COREPACK_", "PYTHON",
)  # fmt: skip
#: ``PYTHON*`` switches that change output encoding or buffering, not code loading.
_PYTHON_SAFE = frozenset(
    {
        "PYTHONUNBUFFERED", "PYTHONUTF8", "PYTHONIOENCODING",
        "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED",
    }
)  # fmt: skip

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_VALUE_CHARS = 32768


def _passthrough_patterns() -> tuple[str, ...]:
    raw = getattr(get_mcp_config(), "mcp_stdio_env_passthrough", "")
    return tuple(item.upper() for item in split_list(raw))


def _is_denied(name: str) -> bool:
    upper = name.upper()
    if upper in _PYTHON_SAFE:
        return False
    return upper in _DENIED_EXACT or upper.startswith(_DENIED_PREFIXES)


def check_spec_env(extra: dict[str, str]) -> None:
    """Refuse malformed or code-injecting entries of a server spec's ``env``.

    Raises:
        ValueError: Naming the key, so the operator can fix the spec or list the
            exact key in ``MCP_STDIO_ALLOWED_ENV_KEYS``.
    """
    permitted = {
        item.upper()
        for item in split_list(
            getattr(get_mcp_config(), "mcp_stdio_allowed_env_keys", "")
        )
    }
    for key, value in extra.items():
        if not isinstance(key, str) or not _KEY.match(key):
            raise ValueError(f"MCP server env key {key!r} is not a valid variable name")
        if not isinstance(value, str) or any(c in value for c in "\x00\n\r"):
            raise ValueError(
                f"MCP server env value for {key} must be a single-line string"
            )
        if len(value) > _MAX_VALUE_CHARS:
            raise ValueError(
                f"MCP server env value for {key} exceeds {_MAX_VALUE_CHARS} chars"
            )
        if _is_denied(key) and key.upper() not in permitted:
            raise ValueError(
                f"MCP server env key '{key}' can change how code is loaded or where "
                "traffic goes and is refused; list it in MCP_STDIO_ALLOWED_ENV_KEYS "
                "to allow exactly this key"
            )


def build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a stdio server is spawned with.

    Only a small allowlist crosses from the parent (``PATH``, ``HOME``,
    ``USER``, ``LOGNAME``, ``LANG``, ``LANGUAGE``, ``LC_*``, ``TMPDIR``/
    ``TEMP``/``TMP``, ``TZ``, ``TERM`` and the Windows system variables) plus
    whatever ``MCP_STDIO_ENV_PASSTHROUGH`` names. The spec's *extra* entries are
    validated by :func:`check_spec_env` and added last. ``PATH`` is then pinned
    to the trusted bin directories (the parent's may hold a relative ``.``), and
    ``PYTHONUNBUFFERED=1`` is always set.

    Raises:
        ValueError: *extra* holds a malformed or code-injecting entry.
    """
    if extra:
        check_spec_env(extra)
    patterns = _passthrough_patterns()
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        name = key.upper()
        if (
            name in _ENV_ALLOW_EXACT
            or name.startswith(_ENV_ALLOW_PREFIXES)
            or any(
                name.startswith(p[:-1]) if p.endswith("*") else name == p
                for p in patterns
            )
        ):
            env[key] = value
    if extra:
        env.update(extra)
    if os.name != "nt":
        env["PATH"] = os.pathsep.join(trusted_bin_dirs())
    env["PYTHONUNBUFFERED"] = "1"
    return env


__all__ = ["build_child_env", "check_spec_env"]
