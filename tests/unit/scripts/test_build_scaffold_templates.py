"""The wheel carries the ``baselith init`` starters, and only those.

``templates/`` sits outside every package, so ``pip install baselith-core``
used to offer nothing but the built-in ``minimal`` template: the richer
starters existed only in a checkout. ``build_support.scaffold_templates``
copies them into ``core/cli/scaffold_templates`` at build time; these tests
pin what it copies and keep its list in step with what the CLI scaffolds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from build_support.scaffold_templates import (
    PACKAGE_TARGET,
    SCAFFOLD_TEMPLATES,
    copy_scaffold_templates,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_every_cli_project_template_is_shipped() -> None:
    from core.cli.commands.init import PROJECT_TEMPLATES, available_templates

    directory_templates = {
        name for name in available_templates() if name not in PROJECT_TEMPLATES
    }
    assert set(SCAFFOLD_TEMPLATES) == directory_templates


def test_non_project_trees_are_not_shipped() -> None:
    # Backstage templates are read by a Backstage instance from the
    # repository, never scaffolded by the CLI.
    assert "backstage" not in SCAFFOLD_TEMPLATES


def test_target_is_where_the_cli_looks() -> None:
    from core.cli.commands import init

    assert init.PACKAGED_TEMPLATES.relative_to(REPO_ROOT).as_posix() == PACKAGE_TARGET


def test_copy_skips_bytecode_and_unlisted_trees(tmp_path: Path) -> None:
    source = tmp_path / "templates"
    (source / "rag-system" / "__pycache__").mkdir(parents=True)
    (source / "rag-system" / "README.md").write_text("r")
    (source / "rag-system" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"x")
    (source / "rag-system" / ".DS_Store").write_bytes(b"x")
    (source / "backstage").mkdir()
    (source / "backstage" / "t.yaml").write_text("t")

    written = copy_scaffold_templates(source, tmp_path / "out", ("rag-system",))

    assert [p.relative_to(tmp_path / "out").as_posix() for p in written] == [
        "rag-system/README.md"
    ]
    assert not (tmp_path / "out" / "backstage").exists()


def test_copy_refuses_a_missing_template(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    with pytest.raises(FileNotFoundError):
        copy_scaffold_templates(tmp_path / "templates", tmp_path / "out", ("nope",))
