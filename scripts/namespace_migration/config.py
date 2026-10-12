"""Static configuration of the namespace migration.

Everything the codemod needs to know about the repository that is not derived
from the tree itself lives here: which top-level names move, which paths are
never touched, how a file is classified, and which words are file extensions
rather than module names.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Final

#: The single top-level package everything moves under.
NEW_ROOT: Final = "baselith"

#: The legacy top-level packages that move to ``baselith.<name>``.
OLD_TOPS: Final[tuple[str, ...]] = ("core", "plugins")

#: Line-level opt-out: a line carrying this marker is left untouched.
KEEP_PRAGMA: Final = "ns-migrate: keep"

#: File-level opt-out: a file containing this marker is skipped entirely.
SKIP_FILE_PRAGMA: Final = "ns-migrate: skip-file"

#: Path components that are never scanned (generated, vendored, caches).
EXCLUDED_DIR_PARTS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        "htmlcov",
        "target",
        "dist",
        "out",
        "build",
        ".superpowers",
        ".next",
        ".turbo",
    }
)

#: Glob patterns (repo-relative, POSIX) that are never rewritten. History is
#: history: a changelog entry or a dated design doc describes the tree as it
#: was, and the codemod itself carries the old names as data.
EXCLUDED_GLOBS: Final[tuple[str, ...]] = (
    "CHANGELOG.md",
    "*/CHANGELOG.md",
    "docs/superpowers/*",
    "sdd/*",
    "scripts/migrate_namespace.py",
    "scripts/namespace_migration/*",
    "tests/unit/scripts/test_migrate_namespace*",
    "tests/unit/scripts/namespace_migration_fixtures/*",
    "packages/baselith-core-compat/*",
    # Fingerprinted corpora: evals/baseline.json hashes them, and their text
    # is scenario data (a simulated directory listing), not a reference.
    "evals/*",
    "*.lock",
    "*.min.js",
    "*.map",
    "*/openapi.json",
    # Gitleaks fingerprints name a path as it was in the fingerprinted commit;
    # rewriting core/ to baselith/core/ there silently unignored two findings.
    ".gitleaksignore",
)

#: Suffixes of files read as text. Anything else is skipped as binary.
TEXT_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".py",
        ".pyi",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".cfg",
        ".ini",
        ".yaml",
        ".yml",
        ".json",
        ".sh",
        ".in",
        ".tmpl",
        ".tpl",
        ".mako",
        ".j2",
        ".example",
        ".env",
        ".conf",
        ".tf",
    }
)

#: Extension-less file names read as text.
TEXT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Dockerfile",
        "Makefile",
        "CODEOWNERS",
        ".gitignore",
        ".dockerignore",
        ".gitattributes",
        ".markdownlintignore",
        ".semgrepignore",
        ".gitleaksignore",
        ".coveragerc",
        "Procfile",
        ".releaserc",
        ".editorconfig",
        ".prettierignore",
        ".eslintignore",
        ".npmignore",
        ".mdlrc",
    }
)

#: A two-part dotted word whose second part is one of these is a file name
#: (``plugins.yaml``, ``core.py``), never a module path.
FILE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        "py",
        "pyi",
        "md",
        "yaml",
        "yml",
        "json",
        "toml",
        "txt",
        "ini",
        "cfg",
        "env",
        "lock",
        "log",
        "sh",
        "html",
        "js",
        "ts",
        "tsx",
        "css",
        "example",
        "db",
        "sqlite",
        "csv",
        "pem",
        "key",
        "j2",
        "mako",
        "tmpl",
        "tpl",
        "xyz",
        "com",
        "io",
        "org",
        "dev",
    }
)

#: ``plugins.<word>`` where ``<word>`` is one of these is a mapping key, a
#: method call or a Helm value path (``plugins.items()``,
#: ``.Values.plugins.config``), never the plugin namespace.
PLUGIN_NON_MODULE_WORDS: Final[frozenset[str]] = frozenset(
    {
        "add",
        "append",
        "checked",
        "clear",
        "config",
        "copy",
        "count",
        "disabled",
        "enabled",
        "extend",
        "get",
        "index",
        "items",
        "keys",
        "pop",
        "remove",
        "setdefault",
        "sort",
        "update",
        "values",
    }
)

# --- file classes -----------------------------------------------------------
#: Shipped runtime code (moves under ``baselith/``).
RUNTIME = "runtime"
#: Repository tooling: gates, hooks, packaging, CI, deploy, container recipes.
TOOLING = "tooling"
#: Test code.
TEST = "test"
#: User-facing material describing a *user's* project (docs site, templates,
#: examples): a bare ``plugins/`` there is the user's plugin directory.
USER = "user"
#: Internal docs describing *this* repository (CLAUDE.md, skills, agents).
INTERNAL_DOC = "internal_doc"

_INTERNAL_DOC_GLOBS: Final = (
    "CLAUDE.md",
    "*/CLAUDE.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".claude/*",
    ".github/*",
    "docs/*",
)
_USER_GLOBS: Final = (
    "mkdocs-site/*",
    "templates/*",
    "examples/*",
    "sdk/*",
    "README.md",
    "backstage-portal/*",
    "evals/*",
    "benchmarks/*",
)


def _match(rel: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(rel, g) for g in globs)


def is_excluded(rel: str) -> bool:
    """Whether *rel* (repo-relative POSIX path) is out of scope."""
    parts = PurePosixPath(rel).parts
    if any(p in EXCLUDED_DIR_PARTS for p in parts[:-1]):
        return True
    return _match(rel, EXCLUDED_GLOBS)


def is_text(rel: str) -> bool:
    """Whether *rel* is a text file the codemod should read."""
    path = PurePosixPath(rel)
    if path.name in TEXT_NAMES or path.name.startswith("Dockerfile"):
        return True
    if path.name.startswith(".env"):
        return True
    return path.suffix in TEXT_SUFFIXES


def classify(rel: str) -> str:
    """Return the file class of *rel* (one of the module constants)."""
    path = PurePosixPath(rel)
    parts = path.parts
    top = parts[0] if parts else ""
    if top in OLD_TOPS or (top == NEW_ROOT and len(parts) > 1):
        if "tests" in parts:
            return TEST
        if path.suffix == ".md":
            return INTERNAL_DOC
        return RUNTIME
    if top == "tests":
        return TEST
    if _match(rel, _USER_GLOBS):
        return USER
    if _match(rel, _INTERNAL_DOC_GLOBS) and path.suffix in {".md", ".rst", ".txt"}:
        return INTERNAL_DOC
    if path.suffix in {".md", ".rst"}:
        return USER
    return TOOLING


__all__ = [
    "EXCLUDED_DIR_PARTS",
    "EXCLUDED_GLOBS",
    "FILE_EXTENSIONS",
    "INTERNAL_DOC",
    "KEEP_PRAGMA",
    "NEW_ROOT",
    "OLD_TOPS",
    "PLUGIN_NON_MODULE_WORDS",
    "RUNTIME",
    "SKIP_FILE_PRAGMA",
    "TEST",
    "TEXT_NAMES",
    "TEXT_SUFFIXES",
    "TOOLING",
    "USER",
    "classify",
    "is_excluded",
    "is_text",
]
