from __future__ import annotations

from pathlib import Path

from scripts.check_architecture_boundaries import check_boundaries


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_allows_grandfathered_core_to_plugin_shim(tmp_path: Path) -> None:
    write_file(
        tmp_path / "core/goals/__init__.py",
        "from plugins.goals import GoalTracker\n",
    )

    violations = check_boundaries(
        tmp_path,
        frozen_prefixes=("core/goals/",),
        legacy_allowlist=frozenset({"core/goals/__init__.py"}),
        core_to_plugin_allowlist={"core/goals/__init__.py": {"plugins.goals"}},
    )

    assert violations == []


def test_rejects_new_core_to_plugin_import(tmp_path: Path) -> None:
    write_file(
        tmp_path / "core/services/example.py",
        "from plugins.goals import GoalTracker\n",
    )

    violations = check_boundaries(tmp_path)

    assert violations == [
        "core/services/example.py: forbidden core->plugins import 'plugins.goals'"
    ]


def test_rejects_new_file_inside_frozen_core_area(tmp_path: Path) -> None:
    write_file(
        tmp_path / "core/routers/new_feature.py",
        "def handler() -> None:\n    return None\n",
    )

    violations = check_boundaries(
        tmp_path,
        frozen_prefixes=("core/routers/",),
        legacy_allowlist=frozenset(),
        core_to_plugin_allowlist={},
    )

    assert violations == [
        "core/routers/new_feature.py: new legacy/domain-specific module added under frozen core path"
    ]


def test_regular_core_module_passes(tmp_path: Path) -> None:
    write_file(
        tmp_path / "core/services/example.py",
        "from core.interfaces.services import LLMServiceProtocol\n",
    )

    violations = check_boundaries(tmp_path)

    assert violations == []
