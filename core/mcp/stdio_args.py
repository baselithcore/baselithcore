"""Argument policy for stdio MCP servers: an interpreter is not a code allowlist.

Allowing ``python`` or ``node`` by name allows whatever its arguments say:
``python -c``, ``node -e``, ``deno run https://...``, ``uv run --with evil``.
So the arguments are validated per command family, and the only thing a family
may run is **a script file** that really lives inside a directory the operator
approved (``MCP_STDIO_ALLOWED_DIRS``, empty by default - so stdio stays
unusable until someone opts in) or, for Python, a module the operator listed
(``MCP_STDIO_ALLOWED_MODULES``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from core.config import get_mcp_config
from core.mcp.stdio_binaries import is_inside, split_list, world_writable
from core.observability.logging import get_logger

logger = get_logger(__name__)

MAX_ARGS = 64
MAX_ARG_CHARS = 4096

_PY_NAME = re.compile(r"^python(\d+(\.\d+)*)?$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_SCOPED_PKG = re.compile(r"^@[a-z0-9][\w.\-]*/[\w.\-]+(@[\w.^~<>=*\-]+)?$")

#: Flags that only tune a Python/Node/Bun/Deno/uv process and run nothing.
_PY_FLAGS = frozenset({"-u", "-B", "-s", "-E", "-I", "-O", "-OO", "-q", "-b", "-P"})
_NODE_FLAGS = frozenset(
    {"--no-warnings", "--no-deprecation", "--enable-source-maps", "--trace-warnings"}
)
_NODE_PATTERNS = (re.compile(r"^--max-old-space-size=\d+$"),)
_DENO_FLAGS = frozenset(
    {"--quiet", "-q", "--no-prompt", "--no-check", "--no-remote", "--cached-only"}
)
_DENO_PATTERNS = (re.compile(r"^--allow-(read|write|net|env)=.+$"),)
_BUN_FLAGS = frozenset({"--silent", "--bun"})
_UV_FLAGS = frozenset(
    {"--no-project", "--quiet", "-q", "--frozen", "--locked", "--offline", "--no-sync"}
)

#: Inline-code, preload and shell flags a package runner is never given, even
#: with ``MCP_ALLOW_PACKAGE_RUNNERS=true`` (that opt-in is about registry
#: packages, not about running a string).
_RUNNER_FORBIDDEN = frozenset(
    {
        "-c", "--call", "-e", "--eval", "--print", "--shell", "--shell-auto-fallback",
        "-r", "--require", "--import", "--loader", "--experimental-loader",
    }
)  # fmt: skip
_REMOTE_PREFIXES = ("http:", "https:", "file:", "git+", "git:", "github:", "gitlab:")

_RUNNERS = frozenset({"npx", "pnpx", "bunx", "uvx"})


def family_of(*names: str) -> str | None:
    """The command family of the first of *names* that belongs to one."""
    for raw in names:
        base = os.path.basename(raw)
        if _PY_NAME.match(base):
            return "python"
        if base in {"node", "nodejs"}:
            return "node"
        if base in {"deno", "bun", "uv"} | _RUNNERS:
            return base
    return None


def is_package_runner(name: str) -> bool:
    """Whether *name* is a command whose whole job is fetching a registry package."""
    return name in _RUNNERS


@dataclass(frozen=True)
class ArgPlan:
    """What the arguments were found to launch.

    Attributes:
        script: Real path of the script that will run, when there is one.
        module: The allowlisted ``python -m`` module, when there is one.
        runner: Whether the invocation fetches a package from a registry.
    """

    script: str | None = None
    module: str | None = None
    runner: bool = False


def allowed_dirs() -> tuple[str, ...]:
    """Real paths of the approved script directories (absolute, existing only)."""
    result: list[str] = []
    for entry in split_list(getattr(get_mcp_config(), "mcp_stdio_allowed_dirs", "")):
        if not os.path.isabs(entry):
            logger.warning(
                "mcp_stdio_allowed_dir_ignored", entry=entry, reason="relative"
            )
            continue
        real = os.path.realpath(entry)
        if os.path.isdir(real) and real not in result:
            result.append(real)
    return tuple(result)


def _allowed_modules() -> frozenset[str]:
    return frozenset(
        split_list(getattr(get_mcp_config(), "mcp_stdio_allowed_modules", ""))
    )


def check_hygiene(args: list[str]) -> None:
    """Bound the argument vector and refuse bytes no argument may carry."""
    if len(args) > MAX_ARGS:
        raise ValueError(
            f"MCP command has too many arguments ({len(args)} > {MAX_ARGS})"
        )
    for arg in args:
        if not isinstance(arg, str):
            raise ValueError("MCP command arguments must be strings")
        if len(arg) > MAX_ARG_CHARS:
            raise ValueError(f"MCP command argument exceeds {MAX_ARG_CHARS} characters")
        if "\x00" in arg or "\n" in arg or "\r" in arg:
            raise ValueError("MCP command arguments must not contain NUL or newlines")


def check_script(path: str) -> str:
    """Vet a script path against the approved directories; return its real path.

    The path must be absolute, free of ``..``, a regular file whose
    *symlink-resolved* location sits inside one approved directory, and neither
    the file nor any directory from it up to that approved root may be
    world-writable (anyone could swap the code between this check and the exec).
    """
    if _SCHEME.match(path) and not os.path.isabs(path):
        raise ValueError(
            f"MCP script '{path}' is a remote or URL specifier, not a local file"
        )
    if not os.path.isabs(path):
        raise ValueError(
            f"MCP script '{path}' must be an absolute path (a relative path means "
            "whatever the working directory holds)"
        )
    if ".." in path.replace("\\", "/").split("/"):
        raise ValueError(f"MCP script path '{path}' must not contain '..'")
    roots = allowed_dirs()
    if not roots:
        raise ValueError(
            "no stdio script may run: MCP_STDIO_ALLOWED_DIRS is empty. List the "
            "absolute directories whose scripts may be launched."
        )
    real = os.path.realpath(path)
    root = next((r for r in roots if is_inside(real, r)), None)
    if root is None:
        raise ValueError(
            f"MCP script '{path}' resolves to '{real}', outside the allowed "
            "directories (MCP_STDIO_ALLOWED_DIRS)"
        )
    if not os.path.isfile(real):
        raise ValueError(f"MCP script '{real}' is not a regular file")
    if world_writable(real):
        raise ValueError(f"MCP script '{real}' is world-writable")
    current = os.path.dirname(real)
    while True:
        if world_writable(current):
            raise ValueError(f"MCP script directory '{current}' is world-writable")
        if current == root or current == os.path.dirname(current):
            break
        current = os.path.dirname(current)
    return real


def _script_after_flags(
    args: list[str],
    index: int,
    flags: frozenset[str],
    patterns: tuple[re.Pattern[str], ...] = (),
    *,
    what: str,
) -> ArgPlan:
    """Skip harmless leading flags, then require a vetted script path."""
    while index < len(args) and args[index].startswith("-"):
        flag = args[index]
        if flag not in flags and not any(p.match(flag) for p in patterns):
            raise ValueError(
                f"{what} flag '{flag}' is not permitted for a stdio MCP server "
                "(inline code, preloads and permission grants are refused)"
            )
        index += 1
    if index >= len(args):
        raise ValueError(f"{what} needs a script path to run; none given")
    return ArgPlan(script=check_script(args[index]))


def _python(args: list[str]) -> ArgPlan:
    index = 0
    while index < len(args) and args[index].startswith("-"):
        flag = args[index]
        if flag.startswith("-m"):
            module = flag[2:] or (args[index + 1] if index + 1 < len(args) else "")
            if not _MODULE.match(module) or module not in _allowed_modules():
                raise ValueError(
                    f"python -m '{module}' is not permitted; list the module in "
                    "MCP_STDIO_ALLOWED_MODULES to allow it"
                )
            return ArgPlan(module=module)
        if flag not in _PY_FLAGS:
            raise ValueError(
                f"python flag '{flag}' is not permitted for a stdio MCP server "
                "(inline code, -m and interpreter switches are refused)"
            )
        index += 1
    if index >= len(args):
        raise ValueError("python needs a script path to run; none given")
    return ArgPlan(script=check_script(args[index]))


def _node(args: list[str]) -> ArgPlan:
    return _script_after_flags(args, 0, _NODE_FLAGS, _NODE_PATTERNS, what="node")


def _bun(args: list[str], allow_runners: bool) -> ArgPlan:
    first = next((a for a in args if not a.startswith("-")), "")
    if first == "x":
        return _runner(args, allow_runners, "bun x")
    return _script_after_flags(
        args, 1 if args[:1] == ["run"] else 0, _BUN_FLAGS, what="bun"
    )


def _deno(args: list[str], allow_runners: bool) -> ArgPlan:
    if args[:1] != ["run"]:
        raise ValueError("only 'deno run <script>' is permitted for a stdio MCP server")
    spec = next((a for a in args[1:] if not a.startswith("-")), "")
    if spec.startswith(("npm:", "jsr:")):
        return _runner(args[1:], allow_runners, "deno run npm:/jsr:")
    return _script_after_flags(args, 1, _DENO_FLAGS, _DENO_PATTERNS, what="deno run")


def _uv(args: list[str], allow_runners: bool) -> ArgPlan:
    first = args[0] if args else ""
    if first in {"tool", "x"}:
        return _runner(args, allow_runners, f"uv {first}")
    if first != "run":
        raise ValueError("only 'uv run <script>' is permitted for a stdio MCP server")
    return _script_after_flags(args, 1, _UV_FLAGS, what="uv run")


def _runner(args: list[str], allow_runners: bool, label: str) -> ArgPlan:
    """A registry package runner: opt-in, and still never inline code or URLs."""
    if not allow_runners:
        raise ValueError(
            f"MCP command ({label}) downloads and runs a package at spawn time; "
            "set MCP_ALLOW_PACKAGE_RUNNERS=true to permit package runners."
        )
    for arg in args:
        low = arg.lower()
        flag = low.split("=", 1)[0]
        if (
            flag in _RUNNER_FORBIDDEN
            or low.startswith(_REMOTE_PREFIXES)
            or "://" in low
        ):
            raise ValueError(
                f"package-runner argument '{arg}' is refused: inline code, shell "
                "flags and URL/VCS specifiers are never permitted"
            )
        if arg.startswith("@") and not _SCOPED_PKG.match(arg):
            raise ValueError(
                f"argument '{arg}' looks like a response file, not a package"
            )
    return ArgPlan(runner=True)


def plan_arguments(family: str | None, args: list[str], allow_runners: bool) -> ArgPlan:
    """Validate *args* for *family* and say what they would launch.

    Raises:
        ValueError: Any argument that runs inline code, fetches remote code,
            installs a package, or names a script outside the approved dirs.
    """
    check_hygiene(args)
    if family is None:
        # An operator-listed program we know nothing about: hygiene only.
        for arg in args:
            if arg.startswith("@"):
                raise ValueError(f"argument '{arg}' looks like a response file")
        return ArgPlan()
    if family in _RUNNERS:
        plan = _runner(args, allow_runners, family)
    elif family == "python":
        plan = _python(args)
    elif family == "node":
        plan = _node(args)
    elif family == "bun":
        plan = _bun(args, allow_runners)
    elif family == "deno":
        plan = _deno(args, allow_runners)
    else:
        plan = _uv(args, allow_runners)
    if not plan.runner:
        for arg in args:
            if arg.startswith("@"):
                raise ValueError(f"argument '{arg}' looks like a response file")
    return plan


__all__ = [
    "MAX_ARGS",
    "MAX_ARG_CHARS",
    "ArgPlan",
    "allowed_dirs",
    "check_hygiene",
    "check_script",
    "family_of",
    "is_package_runner",
    "plan_arguments",
]
