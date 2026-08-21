"""Plugin identifiers must never build a path outside the plugins root."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.plugins._resolve import (
    is_valid_plugin_name,
    safe_plugin_path,
    sort_by_dependencies,
)


class TestPluginNameValidation:
    @pytest.mark.parametrize(
        "name", ["baselithbot", "api_routers", "web-scraper", "a.b_c-1", "x"]
    )
    def test_accepts_ordinary_identifiers(self, name: str) -> None:
        assert is_valid_plugin_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "..",
            "../etc",
            "../../etc/passwd",
            "sub/dir",
            "sub\\dir",
            "/absolute",
            ".hidden",
            "-leading-dash",
            "with space",
            "nul\x00byte",
            "",
            "x" * 65,
        ],
    )
    def test_rejects_traversal_and_odd_names(self, name: str) -> None:
        assert not is_valid_plugin_name(name)


class TestSafePluginPath:
    def test_joins_a_valid_name(self, tmp_path: Path) -> None:
        # The helper returns the canonical path, so compare against the
        # canonical root (/tmp is a symlink to /private/tmp on macOS).
        root = Path(os.path.realpath(tmp_path))
        assert safe_plugin_path(tmp_path, "demo") == root / "demo"

    @pytest.mark.parametrize("name", ["../outside", "..", "/etc", "a/b"])
    def test_refuses_to_escape_the_root(self, tmp_path: Path, name: str) -> None:
        with pytest.raises(ValueError):
            safe_plugin_path(tmp_path, name)

    def test_refuses_a_symlink_pointing_outside(self, tmp_path: Path) -> None:
        root = tmp_path / "plugins"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            safe_plugin_path(root, "escape")


class TestSortByDependencies:
    def test_dependencies_come_first(self) -> None:
        class _Meta:
            def __init__(self, deps: list[str]) -> None:
                self.dependencies = deps

        class _Plugin:
            def __init__(self, deps: list[str]) -> None:
                self.metadata = _Meta(deps)

        plugins = {"a": _Plugin(["b"]), "b": _Plugin([]), "c": _Plugin(["missing"])}
        order = sort_by_dependencies(plugins)  # type: ignore[arg-type]
        assert order.index("b") < order.index("a")
        assert set(order) == {"a", "b", "c"}
