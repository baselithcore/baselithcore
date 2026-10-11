"""Markdown: Python fences go through libcst, everything else through text rules."""

from __future__ import annotations

import re
import textwrap

import libcst as cst

from .text_rules import TextContext, rewrite_text

_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>```+|~~~+)[ \t]*(?P<lang>[\w+-]*)[^\n]*\n"
    r"(?P<body>.*?)"
    r"^(?P=indent)(?P=fence)[ \t]*$",
    re.M | re.S,
)
_PY_LANGS = frozenset({"python", "py", "python3", "pycon"})
#: Fallback for fences libcst cannot parse (``...`` placeholders, doctest).
IMPORT_LINE_RE = re.compile(
    r"^(?P<lead>[ \t]*(?:>>> |\.\.\. )?)(?P<kw>from|import)[ \t]+(?P<top>core|plugins)\b(?=[.\s]|$)",
    re.M,
)


def rewrite_imports_regex(text: str, ctx: TextContext) -> str:
    """Regex import rewrite for code libcst cannot parse."""

    def repl(m: re.Match[str]) -> str:
        if m.group("top") in ctx.shadowed:
            return m.group(0)
        ctx.counts["md_import_fallback"] += 1
        return f"{m.group('lead')}{m.group('kw')} baselith.{m.group('top')}"

    return IMPORT_LINE_RE.sub(repl, text)


def _python_block(body: str, ctx: TextContext, rel: str) -> str:
    from .py_rewrite import rewrite_python  # local: avoid an import cycle

    dedented = textwrap.dedent(body)
    indent = body[: len(body) - len(body.lstrip(" \t"))] if dedented != body else ""
    try:
        new = rewrite_python(dedented, rel, ctx, moves=False)
    except cst.ParserSyntaxError:
        return rewrite_text(rewrite_imports_regex(body, ctx), ctx)
    if indent:
        new = textwrap.indent(new, indent, lambda line: bool(line.strip()))
    return new


_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)


def _slug(heading: str) -> str:
    """GitHub/markdownlint anchor slug of a heading's text."""
    text = re.sub(r"[`*_~]|<[^>]+>", "", heading).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _remap_anchors(old: str, new: str, ctx: TextContext) -> str:
    """Keep ``](#anchor)`` links valid when a rewritten heading changed slug."""
    before = [_slug(h) for h in _HEADING_RE.findall(old)]
    after = [_slug(h) for h in _HEADING_RE.findall(new)]
    if len(before) != len(after):
        return new
    mapping = {b: a for b, a in zip(before, after, strict=True) if b != a}
    if not mapping:
        return new

    def repl(m: re.Match[str]) -> str:
        target = mapping.get(m.group(1))
        if target is None:
            return m.group(0)
        ctx.counts["md_anchor"] += 1
        return f"](#{target})"

    return re.sub(r"\]\(#([\w\-]+)\)", repl, new)


def rewrite_markdown(text: str, ctx: TextContext, rel: str) -> str:
    """Rewrite one Markdown document, then repair in-page anchors."""
    if "core" not in text and "plugins" not in text:
        return text
    return _remap_anchors(text, _rewrite_markdown(text, ctx, rel), ctx)


def _rewrite_markdown(text: str, ctx: TextContext, rel: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        out.append(rewrite_text(text[pos : m.start("body")], ctx))
        body = m.group("body")
        if m.group("lang").lower() in _PY_LANGS:
            out.append(_python_block(body, ctx, rel))
        else:
            out.append(rewrite_text(rewrite_imports_regex(body, ctx), ctx))
        pos = m.end("body")
    out.append(rewrite_text(text[pos:], ctx))
    return "".join(out)


__all__ = ["IMPORT_LINE_RE", "rewrite_imports_regex", "rewrite_markdown"]
