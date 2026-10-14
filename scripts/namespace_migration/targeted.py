"""File-targeted rules: the fixes the generic rules cannot infer.

Each rule is a regex scoped to a set of files. They run *before* the generic
rules, and every replacement is written so that its own output no longer
matches (idempotency). A rule that matches nothing is not an error: the same
table runs on prod, on enterprise and on in-flight branches, which do not all
carry every file.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .text_rules import TextContext


@dataclass(frozen=True)
class TargetedRule:
    """One scoped regex substitution.

    Attributes:
        name: Report category suffix (``targeted_<name>``).
        globs: Repo-relative globs the rule applies to.
        pattern: Compiled regex.
        replacement: ``re.sub`` replacement template.
    """

    name: str
    globs: tuple[str, ...]
    pattern: re.Pattern[str]
    replacement: str

    def applies_to(self, rel: str) -> bool:
        return any(fnmatch.fnmatchcase(rel, g) for g in self.globs)


def _literal(name: str, globs: tuple[str, ...], old: str, new: str) -> TargetedRule:
    """A rule replacing one exact snippet (multi-line allowed).

    When the replacement extends the snippet, the pattern refuses to match a
    snippet already followed by that extension, so a second run is a no-op.
    """
    pattern = re.escape(old)
    if new.startswith(old):
        pattern += f"(?!{re.escape(new[len(old) :])})"
    return TargetedRule(name, globs, re.compile(pattern), new.replace("\\", "\\\\"))


_PUBLIC_API = ("scripts/check_public_api.py",)

_DEPLOY = (
    "Dockerfile*",
    "*/Dockerfile*",
    "compose*.y*ml",
    "docker-compose*.y*ml",
    "deploy/*",
    ".devcontainer/*",
    ".github/workflows/*",
)

RULES: tuple[TargetedRule, ...] = (
    # --- core/stability.py: the facade tier stops at the facade -----------
    _literal(
        "stability_floor",
        ("core/stability.py", "baselith/core/stability.py"),
        '    parts = dotted_name.split(".")\n'
        "    for stop in range(len(parts), 0, -1):\n",
        '    parts = dotted_name.split(".")\n'
        "    # ``baselith.core`` and ``baselith.plugins`` live inside the facade\n"
        "    # package but are not the facade: they never inherit its tier.\n"
        '    floor = 2 if parts[:2] in (["baselith", "core"], ["baselith", "plugins"]) else 0\n'
        "    for stop in range(len(parts), floor, -1):\n",
    ),
    # --- the public-API surface keys the namespace package by module name --
    TargetedRule(
        "public_api_baseline_key",
        ("scripts/public_api_baseline.json",),
        re.compile(r'^(\s*)"core": ', re.M),
        r'\1"baselith.core": ',
    ),
    TargetedRule(
        "public_api_test_key",
        ("tests/unit/test_public_api_gate.py",),
        re.compile(r'(_package\(root, |\{|^\s+)"core"(?=[,:])', re.M),
        r'\1"baselith.core"',
    ),
    # --- scripts/check_core_strict_typing.py: the facade is one file now ---
    _literal(
        "strict_typing_facade",
        ("scripts/check_core_strict_typing.py",),
        '    directory = root.joinpath(*name.split("."))\n    if directory.is_dir():\n',
        '    directory = root.joinpath(*name.split("."))\n'
        '    if name == "baselith":\n'
        "        # The facade only: the package also holds ``core`` and ``plugins``.\n"
        '        return directory / "__init__.py"\n'
        "    if directory.is_dir():\n",
    ),
    # --- scripts/core_plugin_contract/: module names derive from the repo --
    _literal(
        "contract_module_root",
        ("scripts/core_plugin_contract/_index.py",),
        'relative = path.relative_to(root.parent).with_suffix("")',
        'relative = path.relative_to(root.parents[1]).with_suffix("")',
    ),
    _literal(
        "contract_reexport_prefix",
        ("scripts/core_plugin_contract/_index.py",),
        'if source.startswith("core"):',
        'if source.startswith("baselith.core"):',
    ),
    _literal(
        "contract_scan_prefix",
        ("scripts/core_plugin_contract/_scan.py",),
        'module == "core" or',
        'module == "baselith.core" or',
    ),
    # --- scripts/check_architecture_boundaries.py: module-name probe -------
    _literal(
        "boundaries_plugin_prefix",
        ("scripts/check_architecture_boundaries.py",),
        'if not module_name.startswith("plugins"):',
        'if not module_name.startswith("baselith.plugins"):',
    ),
    # --- scripts/docs_consistency/scan.py: imports the docs must name -------
    TargetedRule(
        "docs_import_regex",
        ("scripts/docs_consistency/scan.py",),
        re.compile(r"\(\(\?:core\|plugins\)\[\\w\.\]\*\)"),
        r"(baselith\\.(?:core|plugins)[\\w.]*)",
    ),
    # --- scripts/check_public_api.py: scan the package, not two roots ------
    _literal(
        "public_api_roots",
        _PUBLIC_API,
        'SCANNED_ROOTS = ("baselith", "core")\n',
        'SCANNED_ROOTS = ("baselith",)\n'
        "#: Inside the scanned package but not public API: the bundled plugins.\n"
        'EXCLUDED_PREFIXES = ("baselith/plugins/",)\n',
    ),
    _literal(
        "public_api_collect",
        _PUBLIC_API,
        "            if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):\n",
        "            if any(part in EXCLUDED_DIR_NAMES for part in relative.parts) or (\n"
        "                relative.as_posix().startswith(EXCLUDED_PREFIXES)\n"
        "            ):\n",
    ),
    _literal(
        "public_api_dynamic",
        _PUBLIC_API,
        '        if "__pycache__" not in init_py.parts\n',
        '        if "__pycache__" not in init_py.parts\n'
        "        and not init_py.relative_to(root).as_posix().startswith(EXCLUDED_PREFIXES)\n",
    ),
    _literal(
        "public_api_top_level",
        _PUBLIC_API,
        '        for child in (root / scanned_root).glob("*/__init__.py"):\n'
        "            if child.parent.name not in EXCLUDED_DIR_NAMES:\n"
        '                found.add(f"{scanned_root}.{child.parent.name}")\n'
        "    # ``core`` itself is the namespace, not a classified surface: its\n"
        "    # ``__init__`` exports only ``__version__``.\n"
        '    return {name for name in found if name != "core"}\n',
        '    for child in (root / "baselith" / "core").glob("*/__init__.py"):\n'
        "        if child.parent.name not in EXCLUDED_DIR_NAMES:\n"
        '            found.add(f"baselith.core.{child.parent.name}")\n'
        "    # ``baselith.core`` itself is the namespace, not a classified surface:\n"
        "    # its ``__init__`` exports only ``__version__``.\n"
        "    return found\n",
    ),
    # mypy over the whole namespace package: `baselith` now contains core.
    TargetedRule(
        "mypy_targets",
        (".pre-commit-config.yaml", ".github/workflows/*", "Makefile"),
        re.compile(r"\bmypy core baselith\b"),
        "mypy baselith",
    ),
    TargetedRule(
        "mypy_targets_bare",
        (".pre-commit-config.yaml", ".github/workflows/*", "Makefile"),
        re.compile(r"\bmypy core(?=[ \n])"),
        "mypy baselith/core",
    ),
    # bandit over the same trees as before, at their new location.
    TargetedRule(
        "bandit_targets",
        (".pre-commit-config.yaml", ".github/workflows/*", "Makefile"),
        re.compile(r"(\bbandit -r )core plugins\b"),
        r"\1baselith",
    ),
    # Hook `files:` alternations naming both trees.
    TargetedRule(
        "hook_alternation",
        (".pre-commit-config.yaml",),
        re.compile(r"\^\(core\|baselith\)/"),
        "^baselith/",
    ),
    TargetedRule(
        "hook_alternation_plugins",
        (".pre-commit-config.yaml",),
        re.compile(r"\^\(core\|plugins\)/"),
        "^baselith/(core|plugins)/",
    ),
    # Wheel contents: one top-level name.
    TargetedRule(
        "packages_find",
        ("pyproject.toml",),
        re.compile(r'include = \["baselith\*", "core\*", "plugins\*"\]'),
        'include = ["baselith*"]',
    ),
    # `--cov=baselith` now spans the bundled plugins too; keep the measured
    # set what it was (kernel + facade) so the fail-under keeps its meaning.
    _literal(
        "coverage_omit_plugins",
        ("pyproject.toml",),
        "[tool.coverage.run]\n",
        "[tool.coverage.run]\n"
        "# Bundled plugins sit inside `baselith/` since 1.0 but were never part of\n"
        "# the measured kernel; their own suites cover them.\n"
        'omit = ["baselith/plugins/*"]\n',
    ),
    # Coverage of the kernel keeps measuring the kernel.
    TargetedRule(
        "pytest_cov",
        ("pytest.ini", "setup.cfg", "pyproject.toml", ".github/workflows/*"),
        re.compile(r"--cov=(core|plugins)\b(?![./])"),
        r"--cov=baselith/\1",
    ),
    # In-container source paths (/app is the image WORKDIR).
    TargetedRule(
        "container_app_paths",
        _DEPLOY,
        # A line carrying the keep pragma names a user-owned mount on purpose.
        re.compile(
            r"(?<=/app/)(core|plugins)\b(?=[/\s\"':]|$)(?![^\n]*ns-migrate: keep)",
            re.M,
        ),
        r"baselith/\1",
    ),
)


def apply_targeted(text: str, rel: str, ctx: TextContext) -> str:
    """Apply every rule scoped to *rel*, counting each substitution."""
    for rule in RULES:
        if not rule.applies_to(rel):
            continue
        text, n = rule.pattern.subn(rule.replacement, text)
        if n:
            ctx.counts[f"targeted_{rule.name}"] += n
    return text


__all__ = ["RULES", "TargetedRule", "apply_targeted"]
