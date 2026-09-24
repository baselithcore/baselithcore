"""What ``baselith plugin create`` writes must match the framework it ships with.

Two defects in the scaffold templates:

* every manifest hard-coded ``min_core_version: 0.31.0`` — six minor releases
  behind the framework generating it, so a freshly scaffolded plugin declared
  compatibility with cores that lack the APIs its own generated code imports;
* every generated module imported ``typing.Dict/List/Optional``, which the
  repository's own ruff ``UP`` rules reject. The templates live in string
  literals, so no linter ever saw them — the first thing a new plugin author
  ran into was a lint failure in code they had not written.

The manifest now declares the running core version, and the generated code uses
PEP 585/604 builtins.
"""

import ast
from pathlib import Path

import pytest
import yaml

from core import __version__
from core.cli.commands.plugin.const import PLUGIN_TEMPLATE
from core.cli.commands.plugin.create import create_plugin

pytestmark = pytest.mark.unit

LEGACY_TYPING = {"Dict", "List", "Optional", "Tuple", "Set", "Union"}


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plugins").mkdir()
    return tmp_path


@pytest.mark.parametrize("plugin_type", sorted(PLUGIN_TEMPLATE))
def test_manifest_declares_the_running_core_version(
    workdir: Path, plugin_type: str
) -> None:
    name = f"demo-{plugin_type}"
    assert create_plugin(name, plugin_type) == 0

    manifest = yaml.safe_load(
        (workdir / "plugins" / name / "manifest.yaml").read_text()
    )

    assert manifest["min_core_version"] == __version__


@pytest.mark.parametrize("plugin_type", sorted(PLUGIN_TEMPLATE))
def test_generated_python_parses_and_avoids_legacy_typing(
    workdir: Path, plugin_type: str
) -> None:
    name = f"demo-{plugin_type}"
    assert create_plugin(name, plugin_type) == 0

    for source in sorted((workdir / "plugins" / name).glob("*.py")):
        tree = ast.parse(source.read_text(), filename=str(source))
        legacy = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "typing"
            for alias in node.names
            if alias.name in LEGACY_TYPING
        }
        assert not legacy, f"{source.name} imports legacy typing aliases {legacy}"
