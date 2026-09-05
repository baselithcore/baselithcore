"""Extract verifiable claims from the Markdown under ``mkdocs-site/docs``.

Every function here is pure text parsing: it takes page text and returns the
claims a page makes (imports in code samples, repository paths, relative
links, environment variables, CLI invocations, REST routes). Verifying those
claims against the code lives in :mod:`scripts.docs_consistency.known` and the
orchestrating script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

FENCE_RE = re.compile(r"```([\w+.-]*)[^\n]*\n(.*?)```", re.S)

PYTHON_LANGS = frozenset({"python", "py", "pycon", "python3", ""})
SHELL_LANGS = frozenset({"bash", "sh", "shell", "zsh", "console", ""})
DOTENV_LANGS = frozenset({"env", "dotenv", "ini", "properties"})

IMPORT_FROM_RE = re.compile(
    r"^\s*(?:>>>\s*)?from\s+((?:core|plugins)[\w.]*)\s+import\s+"
    r"(?:\(([^)]*)\)|([^\n#]+))",
    re.M,
)
IMPORT_MOD_RE = re.compile(r"^\s*(?:>>>\s*)?import\s+((?:core|plugins)[\w.]*)", re.M)

REPO_ROOTS = (
    "core",
    "plugins",
    "scripts",
    "tests",
    "migrations",
    "examples",
    "configs",
    "deploy",
    "docker",
    "sdk",
    "templates",
    "mkdocs-site",
    ".github",
)
PATH_RE = re.compile(
    r"(?<![\w/.-])((?:" + "|".join(re.escape(r) for r in REPO_ROOTS) + r")/"
    r"[\w\-./]+\.(?:py|yaml|yml|json|toml|md|sh|ini|txt|env|ts|tsx|js|cfg|lock))"
)
ROOT_FILE_RE = re.compile(
    r"(?<![\w/.-])((?:docker-compose[\w.-]*\.ya?ml|Dockerfile[\w.-]*|pyproject\.toml|"
    r"pytest\.ini|\.env\.example|\.releaserc|uv\.lock|backend\.py|alembic\.ini))"
    r"(?![\w/-])"
)
LINK_RE = re.compile(r"\]\(([^)\s#]+\.md)(#[^)]*)?\)")
ENV_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)=", re.M)
ENV_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")
CLI_RE = re.compile(
    r"(?<![\w/.-])baselith\s+([a-z][\w-]*)(?:\s+([a-z][\w-]*))?(?:\s+([a-z][\w-]*))?"
)
ROUTE_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_\-{}./:]*[A-Za-z0-9_}/])"
)


@dataclass(frozen=True)
class Claim:
    """One verifiable statement found on a page."""

    page: Path
    line: int
    kind: str
    value: str
    extra: str = ""

    def __str__(self) -> str:
        detail = f" ({self.extra})" if self.extra else ""
        return f"{self.page}:{self.line}: {self.kind} {self.value}{detail}"


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def iter_pages(docs_root: Path) -> list[Path]:
    """All Markdown pages under ``docs_root``, sorted."""
    return sorted(docs_root.rglob("*.md"))


def _fences(text: str, langs: frozenset[str] | None = None):
    for match in FENCE_RE.finditer(text):
        lang = match.group(1).lower()
        if langs is not None and lang not in langs:
            continue
        yield lang, match.group(2), match.start(2)


def extract_imports(page: Path, text: str) -> list[Claim]:
    """``from core.x import Y`` / ``import core.x`` inside Python fences."""
    claims: list[Claim] = []
    for _lang, body, start in _fences(text, PYTHON_LANGS):
        for m in IMPORT_FROM_RE.finditer(body):
            module = m.group(1)
            raw = m.group(2) if m.group(2) is not None else m.group(3)
            raw = "\n".join(line.split("#", 1)[0] for line in (raw or "").splitlines())
            names = [
                n.strip().split(" as ")[0].strip()
                for n in re.split(r"[,\n]", raw)
                if n.strip()
            ]
            claims.append(
                Claim(
                    page,
                    _line_of(text, start + m.start()),
                    "import",
                    module,
                    ",".join(names),
                )
            )
        for m in IMPORT_MOD_RE.finditer(body):
            claims.append(
                Claim(page, _line_of(text, start + m.start()), "import", m.group(1))
            )
    return claims


def extract_paths(page: Path, text: str) -> list[Claim]:
    """Repository-relative file paths mentioned anywhere on the page."""
    claims: list[Claim] = []
    for regex in (PATH_RE, ROOT_FILE_RE):
        for m in regex.finditer(text):
            ref = m.group(1).rstrip(".")
            if any(ch in ref for ch in "<>{}*…"):
                continue
            claims.append(Claim(page, _line_of(text, m.start()), "path", ref))
    return claims


def extract_links(page: Path, text: str) -> list[Claim]:
    """Relative ``.md`` links (``http(s)`` targets are ignored)."""
    return [
        Claim(page, _line_of(text, m.start()), "link", m.group(1))
        for m in LINK_RE.finditer(text)
        if not m.group(1).startswith(("http://", "https://"))
    ]


def extract_env_names(page: Path, text: str, prefixes: tuple[str, ...]) -> list[Claim]:
    """Environment variables the page presents as real.

    * ``NAME=value`` lines inside ``env``/``dotenv`` fences — every name.
    * ``NAME=value`` lines inside shell fences and ``NAME`` in backticks —
      only names starting with one of the known settings prefixes, so
      generic shell variables (``IMAGE=``, ``PATH``) are never reported.
    """
    claims: list[Claim] = []
    seen: set[tuple[str, int]] = set()

    def add(name: str, offset: int, strict: bool) -> None:
        if name.endswith("_") or (not strict and not name.startswith(prefixes)):
            return
        key = (name, _line_of(text, offset))
        if key not in seen:
            seen.add(key)
            claims.append(Claim(page, key[1], "env", name))

    for lang, body, start in _fences(text):
        if lang in DOTENV_LANGS or lang in SHELL_LANGS:
            for m in ENV_ASSIGN_RE.finditer(body):
                add(m.group(1), start + m.start(1), strict=lang in DOTENV_LANGS)
    prose = FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)
    for m in ENV_TOKEN_RE.finditer(prose):
        add(m.group(1), m.start(1), strict=False)
    return claims


def extract_cli_invocations(page: Path, text: str) -> list[Claim]:
    """``baselith <cmd> [<sub> [<subsub>]]`` word chains, fences and inline code alike."""
    claims: list[Claim] = []
    for m in CLI_RE.finditer(text):
        words = [w for w in m.groups() if w]
        claims.append(Claim(page, _line_of(text, m.start()), "cli", " ".join(words)))
    return claims


def normalize_route(path: str) -> str:
    """``/runs/{run_id}/events`` -> ``/runs/{}/events`` (param names are free-form)."""
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/")) or "/"


def extract_routes(page: Path, text: str) -> list[Claim]:
    """``METHOD /path`` pairs the page documents."""
    claims: list[Claim] = []
    for m in ROUTE_RE.finditer(text):
        path = m.group(2)
        if "..." in path or "…" in path:
            continue
        claims.append(
            Claim(page, _line_of(text, m.start()), "route", f"{m.group(1)} {path}")
        )
    return claims
