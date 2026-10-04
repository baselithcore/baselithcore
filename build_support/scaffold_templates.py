"""Ship the ``baselith init`` starters inside the wheel.

The starters live in ``templates/`` at the repository root, outside every
package, so the wheel used to carry none of them and ``pip install
baselith-core`` offered only the built-in ``minimal`` template. Moving them
under ``core/`` would put scaffold code (with ``{project_name}`` placeholders
and its own tests) in front of every ``core/`` gate, so they stay where they
are and this ``build_py`` hook copies them into
``core/cli/scaffold_templates/`` of the build tree, which is where
``core.cli.commands.init`` looks in an installed package.

Wired through ``[tool.setuptools] cmdclass`` in ``pyproject.toml``;
``MANIFEST.in`` puts this module and ``templates/`` in the sdist so a wheel
built from the sdist carries them too. ``scripts/check_distribution_artifacts.py``
asserts the result on the built wheel.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from setuptools.command.build_py import build_py

#: The ``templates/`` directories ``baselith init`` scaffolds. ``backstage``
#: (read by a Backstage instance from the repository) and ``plugin-template``
#: (a plugin, not a project) are deliberately absent; a unit test keeps this
#: list equal to what the CLI offers from a checkout.
SCAFFOLD_TEMPLATES = (
    "baselith-core-template",
    "custom-agent-template",
    "multi-agent-collab",
    "rag-system",
)
#: Where the starters land inside the built tree (POSIX, repository-relative).
PACKAGE_TARGET = "core/cli/scaffold_templates"
_SKIPPED_NAMES = frozenset({"__pycache__", ".DS_Store"})
_SKIPPED_SUFFIXES = (".pyc", ".pyo")


def copy_scaffold_templates(
    source: Path, target: Path, names: Iterable[str] = SCAFFOLD_TEMPLATES
) -> list[Path]:
    """Copy the named starter directories from ``source`` into ``target``.

    Args:
        source: The repository's ``templates/`` directory.
        target: The directory the starters are copied into.
        names: Template directory names to copy.

    Returns:
        Every file written, sorted.

    Raises:
        FileNotFoundError: A named template does not exist — a wheel silently
            missing a starter is the defect this hook exists to prevent.
    """
    written: list[Path] = []
    for name in names:
        template = source / name
        if not template.is_dir():
            raise FileNotFoundError(f"scaffold template not found: {template}")
        for path in sorted(template.rglob("*")):
            rel = path.relative_to(source)
            if not path.is_file() or _SKIPPED_NAMES.intersection(rel.parts):
                continue
            if path.suffix in _SKIPPED_SUFFIXES:
                continue
            destination = target / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            written.append(destination)
    return sorted(written)


class BuildPy(build_py):
    """``build_py`` that also places the scaffold starters in the package."""

    def run(self) -> None:
        """Build as usual, then copy the starters into the build tree."""
        super().run()
        # An editable install maps ``core`` to the checkout, where the CLI
        # finds ``templates/`` directly; copying would only litter build_lib.
        if getattr(self, "editable_mode", False):
            return
        copy_scaffold_templates(
            Path("templates"), Path(self.build_lib) / PACKAGE_TARGET
        )
