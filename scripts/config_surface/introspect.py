"""Read the settings classes without importing them.

Everything here is AST-based on purpose. Importing ``core.config`` would load
the repository ``.env`` into ``os.environ`` (see ``core/config/env.py``), so a
developer's local overrides would leak into the generated reference as if they
were the shipped defaults; plugin settings modules would additionally need
their optional dependencies installed. Parsing the source avoids both and
renders richer type/default labels (``Literal["qdrant", "pgvector"]`` rather
than a runtime repr).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

#: Where settings classes are looked for, in the order their sections appear.
SETTINGS_ROOTS: tuple[str, ...] = ("core/config", "core", "plugins")

#: Files scanned for bare ``"SOME_VAR"`` string literals, to recognise the
#: variables that are read through ``os.getenv`` or module-level constants
#: instead of a settings field.
LITERAL_ROOTS: tuple[str, ...] = ("core", "plugins", "scripts", "backend.py")

#: Plugin manifests declare the configuration keys the loader injects, so a key
#: can be bound without any Python literal naming it. Only manifests are read —
#: a plugin's prose mentions variables it does not implement.
MANIFEST_NAMES = frozenset({"manifest.yaml", "manifest.yml", "manifest.json"})

SKIP_DIRS = frozenset({"node_modules", "dist", "__pycache__", ".state", "ui", "tests"})

#: The checker must not be evidence for itself: it names variables in its own
#: prose, and counting those would let a stale entry stay green forever.
SELF_REFERENTIAL = frozenset(
    {"scripts/check_config_surface.py", "scripts/config_surface"}
)

ENV_LITERAL_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
ENV_ENTRY_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Setting:
    """One configurable field, as declared in its ``BaseSettings`` class."""

    env: str
    alternatives: tuple[str, ...]
    module: str
    section: str
    class_name: str
    type_label: str
    default: str
    description: str
    secret: bool

    @property
    def names(self) -> tuple[str, ...]:
        """Every environment variable name that binds this field."""
        return (self.env, *self.alternatives)


def _is_settings_class(node: ast.ClassDef) -> bool:
    return any(
        isinstance(base, ast.Name) and base.id == "BaseSettings" for base in node.bases
    )


def _env_prefix(node: ast.ClassDef) -> str:
    """``env_prefix`` from the class's ``model_config`` assignment, or ``""``."""
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
        ):
            continue
        value = stmt.value
        pairs: list[tuple[ast.expr | None, ast.expr]] = []
        if isinstance(value, ast.Call):
            pairs = [(ast.Constant(kw.arg), kw.value) for kw in value.keywords]
        elif isinstance(value, ast.Dict):
            pairs = list(zip(value.keys, value.values, strict=False))
        for key, val in pairs:
            if (
                isinstance(key, ast.Constant)
                and key.value == "env_prefix"
                and isinstance(val, ast.Constant)
            ):
                return str(val.value)
    return ""


def _constants(node: ast.expr) -> list[str]:
    """String constants in ``node``, whether bare or inside ``AliasChoices``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Call):
        return [
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
    return []


def _field_call(node: ast.expr | None) -> dict[str, ast.expr]:
    """Keyword arguments of a ``Field(...)`` call, empty for anything else."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Field"
    ):
        return {kw.arg: kw.value for kw in node.keywords if kw.arg}
    return {}


def _render_default(field: dict[str, ast.expr], assigned: ast.expr | None) -> str:
    if "default_factory" in field:
        return "computed"
    node = field.get("default", assigned if not _field_call(assigned) else None)
    if node is None:
        return "required"
    if isinstance(node, ast.Constant):
        return "" if node.value in ("", None) else str(node.value)
    return ast.unparse(node)


#: ``# === Section ===`` banners separate groups of fields; they title the block,
#: they do not describe the field that happens to follow them.
_BANNER_RE = re.compile(r"^#\s*={2,}.*$|^#\s*[-─=]{3,}\s*$")


def _comment_above(lines: list[str], lineno: int) -> str:
    """The contiguous ``#`` block directly above line ``lineno`` (1-indexed).

    More than half the fields carry no ``description=``; their prose was written
    as an ordinary comment above the declaration. Reading it keeps the reference
    page as informative as the source instead of showing an empty cell.
    """
    collected: list[str] = []
    index = lineno - 2
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped.startswith("#") or _BANNER_RE.match(stripped):
            break
        collected.append(stripped.lstrip("#").strip())
        index -= 1
    return " ".join(reversed(collected)).strip()


def _render_description(field: dict[str, ast.expr]) -> str:
    node = field.get("description")
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return " ".join(node.value.split())
    return ""


#: Module docstrings open with house phrasing that adds nothing to a heading.
_TITLE_NOISE = (
    (" for the BaselithCore framework", ""),
    (" for BaselithCore", ""),
    ("Configuration for ", ""),
    ("Configuration ", ""),
)


def _section_title(tree: ast.Module, module: str) -> str:
    doc = ast.get_docstring(tree) or ""
    first = next((line.strip() for line in doc.splitlines() if line.strip()), "")
    title = first.rstrip(".")
    for noise, replacement in _TITLE_NOISE:
        title = title.replace(noise, replacement)
    title = title.strip()
    return (title[:1].upper() + title[1:]) if title else module


def _settings_in(path: Path, module: str) -> list[Setting]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return []
    section = _section_title(tree, module)
    source_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    found: list[Setting] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_settings_class(node):
            continue
        prefix = _env_prefix(node)
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(
                stmt.target, ast.Name
            ):
                continue
            name = stmt.target.id
            if name.startswith("_") or name == "model_config":
                continue
            field = _field_call(stmt.value)
            names = [
                alias.upper()
                for key in ("validation_alias", "alias")
                if key in field
                for alias in _constants(field[key])
            ] or [f"{prefix}{name}".upper()]
            annotation = ast.unparse(stmt.annotation)
            found.append(
                Setting(
                    env=names[0],
                    alternatives=tuple(dict.fromkeys(names[1:])),
                    module=module,
                    section=section,
                    class_name=node.name,
                    type_label=annotation,
                    default=_render_default(field, stmt.value),
                    description=_render_description(field)
                    or _comment_above(source_lines, stmt.lineno),
                    secret="SecretStr" in annotation,
                )
            )
    return found


def _iter_python_files(repo_root: Path, roots: tuple[str, ...]):
    seen: set[Path] = set()
    for entry in roots:
        path = repo_root / entry
        candidates = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for file in candidates:
            if file in seen or SKIP_DIRS & set(file.relative_to(repo_root).parts):
                continue
            seen.add(file)
            yield file


def iter_settings(repo_root: Path) -> list[Setting]:
    """Every settings field declared in the repository, deduplicated by name."""
    settings: list[Setting] = []
    seen: set[tuple[str, str]] = set()
    for file in _iter_python_files(repo_root, SETTINGS_ROOTS):
        module = (
            file.relative_to(repo_root).with_suffix("").as_posix().replace("/", ".")
        )
        for setting in _settings_in(file, module):
            key = (setting.class_name, setting.env)
            if key in seen:
                continue
            seen.add(key)
            settings.append(setting)
    return settings


def env_literals(repo_root: Path) -> set[str]:
    """``UPPER_SNAKE`` tokens appearing in first-party source.

    ``.env.example`` is deliberately not scanned: it is the file being audited,
    so counting its own entries as evidence would make every check vacuous. The
    checker's own sources are skipped for the same reason.
    """
    names: set[str] = set()
    for file in _iter_python_files(repo_root, LITERAL_ROOTS):
        relative = file.relative_to(repo_root).as_posix()
        if any(relative.startswith(entry) for entry in SELF_REFERENTIAL):
            continue
        text = file.read_text(encoding="utf-8", errors="ignore")
        names.update(ENV_LITERAL_RE.findall(text))
    plugins_root = repo_root / "plugins"
    if plugins_root.is_dir():
        for manifest in sorted(plugins_root.rglob("*")):
            if manifest.name in MANIFEST_NAMES and manifest.is_file():
                text = manifest.read_text(encoding="utf-8", errors="ignore")
                names.update(ENV_LITERAL_RE.findall(text))
    return names


def env_example_entries(path: Path) -> dict[str, int]:
    """``{variable: line number}`` for every entry of an env template.

    Commented-out entries count: they document a variable just as much as an
    active one, and a stale name misleads the same way.
    """
    entries: dict[str, int] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = ENV_ENTRY_RE.match(line)
        if match:
            entries.setdefault(match.group(1), number)
    return entries
