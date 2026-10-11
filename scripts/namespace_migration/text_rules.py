"""Rewrite rules shared by Python string literals, comments and other text.

Three shapes name the legacy packages in text:

* dotted paths — ``core.services.llm`` (module or object references),
* filesystem paths — ``core/services/llm.py``, ``./plugins/``, ``/core/``,
* repository URLs — ``…/blob/main/core/x.py``.

A dotted path is rewritten only when the index resolves it; a path only when
its first segment exists (or it is the bare directory in a file that talks
about *this* repository). Anything else is reported, never guessed.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field

from .config import (
    FILE_EXTENSIONS,
    INTERNAL_DOC,
    KEEP_PRAGMA,
    NEW_ROOT,
    PLUGIN_NON_MODULE_WORDS,
    RUNTIME,
    TOOLING,
)
from .index import ModuleIndex

_SEG = r"[A-Za-z_][\w\-]*"
#: ``core.a.b`` not preceded by a word char, dot, dash, slash, ``@`` or ``$``.
DOTTED_RE = re.compile(rf"(?<![\w.\-/@$])(?P<top>core|plugins)(?P<rest>(?:\.{_SEG})+)")
#: ``core/…`` / ``./core/…`` / ``../../plugins/…``.
PATH_RE = re.compile(
    r"(?<![\w.\-/@$~])(?P<dots>(?:\.{1,2}/)*)(?P<top>core|plugins)/(?P<seg>(?:[\w.\-*]|\\\.)*)"
)
#: ``/core/…`` anchored at a line start or after a delimiter (CODEOWNERS,
#: ignore files, rooted globs).
ROOTED_RE = re.compile(
    r"(?:^|(?<=[\s\"'`(\[=,!]))/(?P<top>core|plugins)/(?P<seg>(?:[\w.\-*]|\\\.)*)", re.M
)
#: ``…/blob/<ref>/core/…`` in repository links.
URL_RE = re.compile(
    r"(?P<pre>/(?:blob|tree|edit|raw)/[\w.\-]+/)(?P<top>core|plugins)/(?P<seg>(?:[\w.\-*]|\\\.)*)"
)
#: ``./core`` or ``./plugins`` as a whole directory (compose volumes, CLI args).
DOT_DIR_RE = re.compile(
    r"(?<![\w.\-/@$\\])\./(?P<top>core|plugins)(?=[:\s\"',)\]]|$)", re.M
)
#: A literal listing module prefixes, CSV style (``"core.,plugins."``).
PREFIX_CSV_RE = re.compile(r"(?:core|plugins)\.(?:\s*,\s*(?:core|plugins|[\w.]+)\.)+")
#: ``%(here)s/core/…`` (ini files) or ``$ROOT/core/…`` (shell): a variable
#: holding the repository root.
ROOTVAR_RE = re.compile(
    r"(?P<pre>(?:%\(here\)s|\$\{?(?:PWD|ROOT|REPO_ROOT|REPO|PROJECT_ROOT|GITHUB_WORKSPACE)\}?)/)"
    r"(?P<top>core|plugins)/(?P<seg>(?:[\w.\-*]|\\\.)*)"
)
#: A string literal that is exactly a dotted reference (``mod.path:attr``).
FULL_DOTTED_RE = re.compile(rf"(?P<top>core|plugins)(?:\.{_SEG})+(?::[\w.]+)?")

_GLOB = re.compile(r"[*?\[]")
#: A plugin directory name (not a dotfile, not a file in the plugin root).
_PLUGIN_DIR = re.compile(r"[A-Za-z_][\w\-]*")
#: A test module that exercises the repository's own tooling.
REPO_FIXTURE_RE = re.compile(
    r"""^\s*(?:from|import)\s+scripts\b|["']scripts/[\w/]+\.py["']|/\s*["']scripts["']\s*/""",
    re.M,
)


@dataclass
class TextContext:
    """Per-file state the rules read and write.

    Attributes:
        index: What exists under the legacy packages.
        file_class: Classification from :func:`config.classify`.
        shadowed: Legacy tops that a local package shadows for this file.
        rooted_paths: Whether ``/core/…`` means a repository path. True only
            for non-Python tooling text (CODEOWNERS, ignore files); in code and
            docs a leading ``/plugins/…`` is an HTTP route
            (``/plugins/<name>/static``), never a file.
        repo_fixture: A test that drives the repository's own tooling
            (it imports ``scripts``): its fixture trees mimic the repository
            layout, so their ``core/…`` and ``plugins/…`` paths move too.
        local_dir: Directory of a Markdown file: a relative path that resolves
            from there (a docs-site ``plugins/`` section) is a local link.
        counts: Rewrites applied, by category (shared with the caller).
        unresolved: ``(category, snippet)`` pairs for the report.
    """

    index: ModuleIndex
    file_class: str
    shadowed: frozenset[str] = frozenset()
    local_dir: str | None = None
    repo_fixture: bool = False
    rooted_paths: bool = False
    counts: Counter[str] = field(default_factory=Counter)
    unresolved: list[tuple[str, str]] = field(default_factory=list)

    @property
    def talks_about_repo(self) -> bool:
        """A bare ``plugins/`` here means this repository's plugin tree."""
        return self.repo_fixture or self.file_class in (TOOLING, INTERNAL_DOC)

    @property
    def tooling_like(self) -> bool:
        """Tooling rules (root-named paths, root tuples) apply here."""
        return self.repo_fixture or self.file_class == TOOLING


def _is_filename(rest: str) -> bool:
    segs = rest.lstrip(".").split(".")
    return len(segs) == 1 and segs[0].lower() in FILE_EXTENSIONS


def _plugin_namespace(rest: str) -> bool:
    """``plugins.<rest>`` names the runtime plugin namespace.

    Every plugin — bundled, enterprise-only or installed into a project's
    ``plugins/`` directory — is imported as ``plugins.<name>`` and moves to
    ``baselith.plugins.<name>``. Resolution against this tree would differ
    between checkouts that carry different plugins, and the two ``core/``
    trees must come out byte-identical, so the namespace rule does not
    consult the tree.
    """
    first = rest.lstrip(".").split(".")[0]
    return not first.startswith("__") and first not in PLUGIN_NON_MODULE_WORDS


def _dotted(text: str, ctx: TextContext, *, full: bool) -> str:
    def repl(m: re.Match[str]) -> str:
        top, rest = m.group("top"), m.group("rest")
        if top in ctx.shadowed:
            return m.group(0)
        dotted = top + rest
        if ctx.index.resolves(dotted):
            ctx.counts["dotted_full" if full else "dotted_resolved"] += 1
            return f"{NEW_ROOT}.{dotted}"
        if _is_filename(rest):
            return m.group(0)
        if top == "plugins" and _plugin_namespace(rest):
            ctx.counts["dotted_plugin_namespace"] += 1
            return f"{NEW_ROOT}.{dotted}"
        if (full or ctx.repo_fixture) and _plugin_namespace(rest):
            # A literal that is nothing but a dotted ``core.…`` name is a
            # module reference even when the module is hypothetical (a
            # test's fake job path, a gate fixture): it lives in the
            # namespace that moved.
            ctx.counts["dotted_core_namespace"] += 1
            return f"{NEW_ROOT}.{dotted}"
        ctx.unresolved.append(("unresolved_dotted", dotted))
        return m.group(0)

    return DOTTED_RE.sub(repl, text)


def _path_decision(top: str, seg: str, ctx: TextContext) -> str | None:
    """``"rewrite"``, ``"keep"`` or ``None`` (unresolved) for a path hit."""
    seg = seg.replace("\\", "")  # a path spelled inside a regex: ``core/x\.py``
    if top in ctx.shadowed:
        return "keep"
    if ctx.repo_fixture:
        return "rewrite"
    bare = seg == "" or seg.startswith(("*", "<", "{")) or bool(_GLOB.search(seg))
    if top == "core":
        if bare or ctx.index.first_segment_exists(top, seg):
            return "rewrite"
        return None
    if not bare and ctx.file_class == RUNTIME and _PLUGIN_DIR.fullmatch(seg):
        # Shipped code names plugin paths the same way in every checkout;
        # the verdict must not depend on which plugins this one carries.
        return "rewrite"
    if not bare and ctx.index.first_segment_exists(top, seg):
        return "rewrite"
    if bare:
        return "rewrite" if ctx.talks_about_repo else "keep"
    return "rewrite" if seg in ("__init__.py",) else None


def _is_local(dots: str, top: str, seg: str, ctx: TextContext) -> bool:
    """A Markdown-relative path that exists next to the document."""
    if ctx.local_dir is None:
        return False
    target = posixpath.normpath(posixpath.join(ctx.local_dir, dots, top, seg))
    return target in ctx.index.nested_paths


def _paths(text: str, ctx: TextContext) -> str:
    def rel_repl(m: re.Match[str]) -> str:
        top, seg = m.group("top"), m.group("seg")
        if _is_local(m.group("dots"), top, seg, ctx):
            ctx.counts["kept_local_relative"] += 1
            return m.group(0)
        verdict = _path_decision(top, seg, ctx)
        if verdict == "rewrite":
            ctx.counts["path_relative"] += 1
            return f"{m.group('dots')}{NEW_ROOT}/{top}/{seg}"
        if verdict is None:
            ctx.unresolved.append(("unresolved_path", f"{top}/{seg}"))
        else:
            ctx.counts["kept_user_plugins_dir"] += 1
        return m.group(0)

    def rooted_repl(m: re.Match[str]) -> str:
        top, seg = m.group("top"), m.group("seg")
        if not ctx.rooted_paths:
            return m.group(0)
        if _path_decision(top, seg, ctx) == "rewrite":
            ctx.counts["path_rooted"] += 1
            return f"/{NEW_ROOT}/{top}/{seg}"
        return m.group(0)

    def url_repl(m: re.Match[str]) -> str:
        top, seg = m.group("top"), m.group("seg")
        if _path_decision(top, seg, ctx) == "rewrite" and seg:
            ctx.counts["path_url"] += 1
            return f"{m.group('pre')}{NEW_ROOT}/{top}/{seg}"
        return m.group(0)

    def dot_dir_repl(m: re.Match[str]) -> str:
        if not ctx.talks_about_repo or m.group("top") in ctx.shadowed:
            return m.group(0)
        ctx.counts["path_dot_dir"] += 1
        return f"./{NEW_ROOT}/{m.group('top')}"

    def rootvar_repl(m: re.Match[str]) -> str:
        top, seg = m.group("top"), m.group("seg")
        if _path_decision(top, seg, ctx) == "rewrite":
            ctx.counts["path_rootvar"] += 1
            return f"{m.group('pre')}{NEW_ROOT}/{top}/{seg}"
        return m.group(0)

    text = URL_RE.sub(url_repl, text)
    text = ROOTVAR_RE.sub(rootvar_repl, text)
    text = ROOTED_RE.sub(rooted_repl, text)
    text = PATH_RE.sub(rel_repl, text)
    return DOT_DIR_RE.sub(dot_dir_repl, text)


def _rewrite_chunk(text: str, ctx: TextContext, *, full: bool) -> str:
    if "core" not in text and "plugins" not in text:
        return text
    return _paths(_dotted(text, ctx, full=full), ctx)


def rewrite_text(text: str, ctx: TextContext) -> str:
    """Rewrite prose or config text, honouring the line-level keep pragma."""
    if "core" not in text and "plugins" not in text:
        return text
    if KEEP_PRAGMA not in text:
        return _rewrite_chunk(text, ctx, full=False)
    out = []
    for line in text.splitlines(keepends=True):
        out.append(
            line if KEEP_PRAGMA in line else _rewrite_chunk(line, ctx, full=False)
        )
    return "".join(out)


def rewrite_literal(content: str, ctx: TextContext) -> str:
    """Rewrite the content of one string literal (no quotes, no prefix).

    A literal that is exactly ``"core."`` / ``"plugins."`` is a module-name
    prefix (an allowlist entry, a ``startswith`` probe) and moves with the
    package. A literal that is exactly a dotted reference is a module/object
    path. Anything longer is prose and goes through :func:`rewrite_text`.
    """
    if content in ("core.", "plugins."):
        if content[:-1] in ctx.shadowed:
            return content
        ctx.counts["prefix_literal"] += 1
        return f"{NEW_ROOT}.{content}"
    if PREFIX_CSV_RE.fullmatch(content):
        ctx.counts["prefix_literal"] += 1
        return re.sub(r"\b(core|plugins)\.", rf"{NEW_ROOT}.\1.", content)
    if FULL_DOTTED_RE.fullmatch(content):
        return _dotted(content, ctx, full=True)
    return rewrite_text(content, ctx)


__all__ = [
    "DOTTED_RE",
    "PATH_RE",
    "REPO_FIXTURE_RE",
    "TextContext",
    "rewrite_literal",
    "rewrite_text",
]
