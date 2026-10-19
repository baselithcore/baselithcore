#!/usr/bin/env python3
"""Move Python modules inside a plugin and rewrite every reference to them.

    python scripts/move_plugin_modules.py <plugin> OLD=NEW [OLD=NEW ...] [--dry-run]

``OLD`` and ``NEW`` are file paths relative to ``baselith/plugins/<plugin>/``,
for example ``mfa.py=security/mfa.py`` or ``config.py=config/__init__.py``.
The tool

1. ``git mv``s each file (creating ``__init__.py`` for a new package),
2. rewrites every ``import`` / ``from … import`` in every tracked ``.py`` file
   that resolves — absolutely or relatively — to a moved module, re-rendering
   relative imports from the importing file's *new* location,
3. rewrites the dotted module path (``baselith.plugins.<plugin>.<old>``) and
   the file path (``baselith/plugins/<plugin>/<old>.py``) in every other
   tracked text file (docs, YAML, TOML, JSON, patch targets in tests),
4. prints any remaining mention of an old module basename for manual review.

Imports are regenerated with ``ast.unparse``; run ``ruff format`` afterwards.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".txt",
    ".ini",
    ".cfg",
    ".sh",
    ".rst",
}


@dataclass(frozen=True)
class Move:
    old_file: Path  # repo-relative
    new_file: Path  # repo-relative
    old_module: str  # dotted, absolute
    new_module: str


def module_of(path: Path) -> str:
    """Dotted module name of a repo-relative ``.py`` path (``__init__`` stripped)."""
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def package_of(path: Path) -> str:
    """Package a file's relative imports resolve against."""
    return ".".join(path.with_suffix("").parts[:-1])


def tracked(pattern: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [Path(p) for p in out.decode().split("\0") if p]


def parse_moves(plugin: str, specs: list[str]) -> list[Move]:
    base = Path("baselith") / "plugins" / plugin
    moves = []
    for spec in specs:
        old, _, new = spec.partition("=")
        if not new:
            raise SystemExit(f"bad move spec {spec!r}: expected OLD=NEW")
        old_file, new_file = base / old, base / new
        if not (REPO_ROOT / old_file).is_file():
            raise SystemExit(f"{old_file} does not exist")
        if (REPO_ROOT / new_file).exists():
            raise SystemExit(f"{new_file} already exists")
        moves.append(Move(old_file, new_file, module_of(old_file), module_of(new_file)))
    return moves


class Remapper:
    """Maps old absolute module names to new ones (including submodules)."""

    def __init__(self, moves: list[Move], file_moves: dict[Path, Path]):
        self.exact = {
            m.old_module: m.new_module for m in moves if m.old_module != m.new_module
        }
        self.file_moves = file_moves

    def remap(self, module: str) -> str:
        if module in self.exact:
            return self.exact[module]
        for old, new in self.exact.items():
            if module.startswith(old + "."):
                return new + module[len(old) :]
        return module

    def new_path(self, path: Path) -> Path:
        return self.file_moves.get(path, path)


def resolve_relative(level: int, module: str | None, package: str) -> str:
    parts = package.split(".")
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    return f"{base}.{module}" if module else base


def render_relative(target: str, package: str) -> tuple[int, str | None]:
    """Return (level, module) rendering ``target`` relative to ``package``."""
    t, p = target.split("."), package.split(".")
    k = 0
    while k < len(t) and k < len(p) and t[k] == p[k]:
        k += 1
    level = len(p) - k + 1
    rest = ".".join(t[k:])
    return level, rest or None


def rewrite_import_from(
    node: ast.ImportFrom, package_old: str, package_new: str, rm: Remapper
) -> list[ast.stmt] | None:
    """Return replacement statements, or None when nothing changes."""
    relative = node.level > 0
    base_old = (
        resolve_relative(node.level, node.module, package_old)
        if relative
        else (node.module or "")
    )
    base_new = rm.remap(base_old)

    # Names that are themselves moved modules (``from pkg import mod``).
    groups: dict[str, list[ast.alias]] = {}
    moved_name = False
    for alias in node.names:
        full = f"{base_old}.{alias.name}"
        if full in rm.exact:
            moved_name = True
            parent, _, basename = rm.exact[full].rpartition(".")
            asname = alias.asname or (alias.name if basename != alias.name else None)
            groups.setdefault(parent, []).append(
                ast.alias(name=basename, asname=asname)
            )
        else:
            groups.setdefault(base_new, []).append(alias)

    changed = (
        base_new != base_old or moved_name or (relative and package_old != package_new)
    )
    if not changed:
        return None
    stmts: list[ast.stmt] = []
    for parent, aliases in groups.items():
        if relative:
            level, mod = render_relative(parent, package_new)
            stmts.append(ast.ImportFrom(module=mod, names=aliases, level=level))
        else:
            stmts.append(ast.ImportFrom(module=parent, names=aliases, level=0))
    return stmts


def rewrite_import(node: ast.Import, rm: Remapper, where: str) -> ast.stmt | None:
    new_names = []
    changed = False
    for alias in node.names:
        target = rm.remap(alias.name)
        if target != alias.name:
            changed = True
            if alias.asname is None:
                print(
                    f"  WARNING {where}: `import {alias.name}` rewritten to `import {target}`; attribute chains need review"
                )
            new_names.append(ast.alias(name=target, asname=alias.asname))
        else:
            new_names.append(alias)
    return ast.Import(names=new_names) if changed else None


def rewrite_python(path: Path, rm: Remapper, dry_run: bool) -> bool:
    src = (REPO_ROOT / path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    package_old = package_of(path)
    package_new = package_of(rm.new_path(path))
    lines = src.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        replacement: list[ast.stmt] | None = None
        if isinstance(node, ast.ImportFrom):
            if node.level > 0 and not package_old.startswith("baselith."):
                continue
            replacement = rewrite_import_from(node, package_old, package_new, rm)
        elif isinstance(node, ast.Import):
            single = rewrite_import(node, rm, f"{path}:{node.lineno}")
            replacement = [single] if single else None
        if replacement is None:
            continue
        indent = re.match(r"[ \t]*", lines[node.lineno - 1]).group(0)
        # keep a trailing comment (``# noqa: E402``) of a single-line statement
        tail = ""
        if node.end_lineno == node.lineno:
            comment = re.search(r"\s+(#.*)$", lines[node.lineno - 1].rstrip("\n"))
            tail = f"  {comment.group(1)}" if comment else ""
        text = "".join(f"{indent}{ast.unparse(s)}{tail}\n" for s in replacement)
        edits.append((node.lineno - 1, node.end_lineno, text))
    if not edits:
        return False
    for start, end, text in sorted(edits, reverse=True):
        lines[start:end] = [text]
    if not dry_run:
        (REPO_ROOT / path).write_text("".join(lines), encoding="utf-8")
    return True


def rewrite_text(path: Path, moves: list[Move], dry_run: bool) -> bool:
    try:
        src = (REPO_ROOT / path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    out = src
    for m in moves:
        if m.old_module != m.new_module:
            out = re.sub(
                rf"(?<![A-Za-z0-9_.]){re.escape(m.old_module)}(?![A-Za-z0-9_])",
                m.new_module,
                out,
            )
        out = out.replace(m.old_file.as_posix(), m.new_file.as_posix())
        # plugin-root-relative links such as ``(auth/tenancy.py)`` in Markdown
        plugin_rel_old = m.old_file.relative_to("baselith/plugins").as_posix()
        plugin_rel_new = m.new_file.relative_to("baselith/plugins").as_posix()
        out = re.sub(
            rf"(?<![A-Za-z0-9_/.]){re.escape(plugin_rel_old)}(?![A-Za-z0-9_])",
            plugin_rel_new,
            out,
        )
        # a bare backticked basename inside the plugin's own docs/README
        plugin_dir = m.old_file.parent
        if (
            path.suffix == ".md"
            and path.is_relative_to(m.old_file.parts[0])
            and path.parts[:3] == m.old_file.parts[:3]
        ):
            old_rel = (
                m.old_file.relative_to(plugin_dir).as_posix()
                if plugin_dir.parts == m.old_file.parts[:3]
                else None
            )
            if old_rel:
                new_rel = m.new_file.relative_to(plugin_dir).as_posix()
                out = out.replace(f"`{old_rel}`", f"`{new_rel}`")
    if out == src:
        return False
    if not dry_run:
        (REPO_ROOT / path).write_text(out, encoding="utf-8")
    return True


def git_mv(moves: list[Move], dry_run: bool) -> None:
    for m in moves:
        dest = REPO_ROOT / m.new_file
        if dry_run:
            print(f"git mv {m.old_file} {m.new_file}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "mv", str(m.old_file), str(m.new_file)], cwd=REPO_ROOT, check=True
        )
        pkg = dest.parent
        plugin_root = REPO_ROOT / "baselith" / "plugins"
        while pkg != plugin_root and pkg.is_relative_to(plugin_root):
            init = pkg / "__init__.py"
            if not init.exists():
                init.write_text(f'"""{pkg.name} package."""\n', encoding="utf-8")
                subprocess.run(
                    ["git", "add", str(init.relative_to(REPO_ROOT))],
                    cwd=REPO_ROOT,
                    check=True,
                )
            pkg = pkg.parent


def leftovers(moves: list[Move]) -> None:
    names = sorted(
        {
            m.old_file.stem
            for m in moves
            if m.old_file.stem != m.new_file.stem and m.old_file.stem != "__init__"
        }
    )
    if not names:
        return
    pattern = "|".join(re.escape(n) for n in names)
    out = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-E",
            rf"\b({pattern})\b",
            "--",
            ":!*.lock",
            ":!*/node_modules/*",
            ":!*/ui/dist/*",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
    ).stdout.decode()
    if out.strip():
        print("\nRemaining mentions of renamed basenames (review by hand):")
        print(out[:6000])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("plugin")
    parser.add_argument("moves", nargs="+", metavar="OLD=NEW")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    moves = parse_moves(args.plugin, args.moves)
    rm = Remapper(moves, {m.old_file: m.new_file for m in moves})

    py_files = tracked("*.py")
    touched_py = [p for p in py_files if rewrite_python(p, rm, args.dry_run)]
    text_files = [
        p
        for p in tracked(".")
        if p.suffix in TEXT_SUFFIXES
        and "node_modules" not in p.parts
        and "dist" not in p.parts
    ]
    touched_txt = [p for p in text_files if rewrite_text(p, moves, args.dry_run)]
    git_mv(moves, args.dry_run)

    print(
        f"moved {len(moves)} file(s); rewrote imports in {len(touched_py)} .py file(s), references in {len(touched_txt)} text file(s)"
    )
    for p in touched_py:
        print(f"  imports: {p}")
    for p in touched_txt:
        if p.suffix != ".py":
            print(f"  text:    {p}")
    leftovers(moves)
    return 0


if __name__ == "__main__":
    sys.exit(main())
