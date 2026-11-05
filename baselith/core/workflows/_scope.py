"""Variable scope and template rendering for workflow nodes.

Nodes that build text or structured values from earlier results (LLM prompts,
HTTP bodies, assignments) reference them with ``{{ path }}`` placeholders. The
scope a placeholder resolves against is deliberately small and data-only:

* ``input`` - the run's initial input.
* ``output`` - the output of the node that ran last.
* ``nodes`` - ``{node_id: output}`` for every node executed so far.
* every variable set with :func:`ExecutionContext.set_variable` (the ASSIGN
  node writes them).

Resolution walks mappings by key and sequences by index only. It never calls
anything and never reads an attribute, so a workflow definition - which may be
uploaded through the API - cannot reach beyond the data it is handed. This is
the reason the engine ships its own renderer instead of a general template
language.

Unresolved references raise :class:`TemplateError`. A node that silently
rendered an empty string would send a half-built prompt or request body to a
model or a remote service; the ``default`` filter is the explicit opt-out.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .executor import ExecutionContext

__all__ = [
    "RESERVED_NAMES",
    "TemplateError",
    "build_scope",
    "render_template",
    "render_value",
    "resolve_path",
]

#: Names a workflow variable may not shadow: the scope always wins for them.
RESERVED_NAMES = frozenset({"input", "output", "nodes"})

#: Upper bound on one rendered string, so a template over a large node output
#: cannot balloon into a prompt or request body nobody sized.
MAX_RENDERED_CHARS = 1_000_000

# The body may not contain "}}": otherwise ``"{{ a }}-{{ b }}"`` would match as
# a single placeholder spanning both and be read as one malformed reference.
_PLACEHOLDER = re.compile(r"\{\{((?:(?!\}\}).)+)\}\}", re.DOTALL)
_SEGMENT = re.compile(
    r"""
    (?P<name>[A-Za-z0-9_][A-Za-z0-9_-]*)      # identifier-ish key
    | \[(?P<index>-?\d+)\]                     # list index
    | \[(?P<quoted>"[^"]*"|'[^']*')\]          # quoted key
    """,
    re.VERBOSE,
)


class TemplateError(ValueError):
    """A placeholder could not be parsed or resolved."""


class _Missing:
    """Marker for an unresolved reference travelling through a filter chain."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


def build_scope(context: ExecutionContext) -> dict[str, Any]:
    """Build the placeholder scope for the node about to run.

    Args:
        context: The run's execution context.

    Returns:
        A fresh mapping; mutating it does not touch the context.
    """
    scope: dict[str, Any] = dict(context.variables)
    scope["output"] = context.get_last_output()
    scope["nodes"] = {nid: res.output for nid, res in context.node_results.items()}
    return scope


def _parse_path(path: str) -> list[str | int]:
    """Split ``a.b[0]["c d"]`` into its keys and indexes."""
    path = path.strip()
    if not path:
        raise TemplateError("empty reference")
    keys: list[str | int] = []
    pos = 0
    expect_segment = True
    while pos < len(path):
        if path[pos] == "." and not expect_segment:
            pos += 1
            expect_segment = True
            continue
        match = _SEGMENT.match(path, pos)
        if match is None or (match.group("name") and not expect_segment):
            raise TemplateError(f"invalid reference {path!r}")
        if match.group("name") is not None:
            keys.append(match.group("name"))
        elif match.group("index") is not None:
            keys.append(int(match.group("index")))
        else:
            keys.append(match.group("quoted")[1:-1])
        pos = match.end()
        expect_segment = False
    if expect_segment:
        raise TemplateError(f"invalid reference {path!r}")
    return keys


def resolve_path(scope: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted/indexed ``path`` against ``scope``.

    Args:
        scope: The data to walk.
        path: For example ``nodes.search[0].content``.

    Returns:
        The value found, which may legitimately be ``None``.

    Raises:
        TemplateError: If the path is malformed or any step is absent.
    """
    current: Any = scope
    for key in _parse_path(path):
        if isinstance(key, int):
            if isinstance(current, Sequence) and not isinstance(current, str | bytes):
                try:
                    current = current[key]
                    continue
                except IndexError:
                    pass
        elif isinstance(current, Mapping) and key in current:
            current = current[key]
            continue
        raise TemplateError(f"unresolved reference {path.strip()!r}")
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def _split_unquoted(expression: str, separator: str) -> list[str]:
    """Split on ``separator`` outside quotes and parentheses."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    depth = 0
    for char in expression:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


_FILTER = re.compile(r"^\s*(?P<name>[a-z_]+)\s*(?:\((?P<arg>.*)\))?\s*$", re.DOTALL)


def _parse_arg(raw: str | None) -> Any:
    if raw is None:
        return None
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1].replace("\\n", "\n")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _need(value: Any, name: str) -> Any:
    if value is _MISSING:
        raise TemplateError(f"filter {name!r} applied to an unresolved reference")
    return value


def _f_default(value: Any, arg: Any) -> Any:
    return arg if value is _MISSING or value is None or value == "" else value


def _f_join(value: Any, arg: Any) -> str:
    items = _need(value, "join")
    if not isinstance(items, list):
        raise TemplateError("filter 'join' needs a list")
    return ("" if arg is None else str(arg)).join(_stringify(i) for i in items)


def _f_pluck(value: Any, arg: Any) -> list[Any]:
    items = _need(value, "pluck")
    if not isinstance(items, list) or arg is None:
        raise TemplateError("filter 'pluck' needs a list and a key")
    try:
        return [item[arg] for item in items]
    except (KeyError, TypeError, IndexError) as exc:
        raise TemplateError(f"filter 'pluck': no {arg!r} in every item") from exc


def _f_length(value: Any, arg: Any) -> int:
    target = _need(value, "length")
    if not isinstance(target, str | Sequence | Mapping):
        raise TemplateError("filter 'length' needs a string, list or mapping")
    return len(target)


def _f_first(value: Any, arg: Any) -> Any:
    items = _need(value, "first")
    if not isinstance(items, list) or not items:
        raise TemplateError("filter 'first' needs a non-empty list")
    return items[0]


def _f_last(value: Any, arg: Any) -> Any:
    items = _need(value, "last")
    if not isinstance(items, list) or not items:
        raise TemplateError("filter 'last' needs a non-empty list")
    return items[-1]


def _f_truncate(value: Any, arg: Any) -> str:
    text = _stringify(_need(value, "truncate"))
    if not isinstance(arg, int) or arg < 0:
        raise TemplateError("filter 'truncate' needs a non-negative integer")
    return text[:arg]


_FILTERS: dict[str, Callable[[Any, Any], Any]] = {
    "default": _f_default,
    "join": _f_join,
    "pluck": _f_pluck,
    "length": _f_length,
    "first": _f_first,
    "last": _f_last,
    "truncate": _f_truncate,
    "json": lambda v, a: json.dumps(_need(v, "json"), ensure_ascii=False, default=str),
    "upper": lambda v, a: _stringify(_need(v, "upper")).upper(),
    "lower": lambda v, a: _stringify(_need(v, "lower")).lower(),
    "trim": lambda v, a: _stringify(_need(v, "trim")).strip(),
}


def _evaluate(expression: str, scope: Mapping[str, Any]) -> Any:
    """Evaluate ``path | filter | filter(arg)`` against ``scope``."""
    head, *filters = _split_unquoted(expression, "|")
    try:
        value: Any = resolve_path(scope, head)
    except TemplateError:
        if not any(f.strip().startswith("default") for f in filters):
            raise
        value = _MISSING
    for raw in filters:
        match = _FILTER.match(raw)
        if match is None or match.group("name") not in _FILTERS:
            raise TemplateError(f"unknown filter {raw.strip()!r}")
        value = _FILTERS[match.group("name")](value, _parse_arg(match.group("arg")))
    if value is _MISSING:
        raise TemplateError(f"unresolved reference {head.strip()!r}")
    return value


def render_template(text: str, scope: Mapping[str, Any]) -> str:
    """Render every ``{{ ... }}`` placeholder in ``text`` as a string.

    Args:
        text: Template text.
        scope: Data the placeholders resolve against.

    Returns:
        The rendered text; structured values are JSON-encoded.

    Raises:
        TemplateError: On a malformed or unresolved placeholder, or when the
            result exceeds :data:`MAX_RENDERED_CHARS`.
    """
    rendered = _PLACEHOLDER.sub(
        lambda m: _stringify(_evaluate(m.group(1), scope)), text
    )
    if len(rendered) > MAX_RENDERED_CHARS:
        raise TemplateError(f"rendered text exceeds {MAX_RENDERED_CHARS} characters")
    return rendered


def render_value(value: Any, scope: Mapping[str, Any]) -> Any:
    """Render placeholders inside a config value, preserving types.

    A string that is exactly one placeholder (``"{{ nodes.search }}"``) yields
    the referenced object unchanged, so a list stays a list. Any other string
    is rendered as text. Lists and mapping values are rendered recursively;
    everything else is returned as is.

    Raises:
        TemplateError: As :func:`render_template`.
    """
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value.strip())
        if whole is not None:
            return _evaluate(whole.group(1), scope)
        return render_template(value, scope)
    if isinstance(value, list):
        return [render_value(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, scope) for key, item in value.items()}
    return value
