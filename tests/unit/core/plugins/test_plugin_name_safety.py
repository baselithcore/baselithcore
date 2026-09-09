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

    def test_honours_the_modern_plugin_dependencies_map(self) -> None:
        """``plugin_dependencies`` orders the graph, not just legacy ``dependencies``.

        Manifests declare ``plugin_dependencies: {provider: '>=2.0.0'}``; only
        a handful still use the legacy ``dependencies:`` list. Reading just the
        legacy field left every manifest on the modern key topologically
        unordered, so a consumer could initialise before the plugin whose
        service it resolves.
        """

        class _Meta:
            def __init__(self, deps: dict[str, str]) -> None:
                self.dependencies: list[str] = []
                self.plugin_dependencies = deps

        class _Plugin:
            def __init__(self, deps: dict[str, str]) -> None:
                self.metadata = _Meta(deps)

        plugins = {
            "consumer": _Plugin({"provider": ">=2.0.0"}),
            "provider": _Plugin({}),
            "solo": _Plugin({"absent": "*"}),
        }
        order = sort_by_dependencies(plugins)  # type: ignore[arg-type]
        assert order.index("provider") < order.index("consumer")
        assert set(order) == {"consumer", "provider", "solo"}

    def test_merges_both_dependency_declarations(self) -> None:
        """A manifest carrying both keys is ordered after every named plugin."""

        class _Meta:
            def __init__(self, legacy: list[str], modern: dict[str, str]) -> None:
                self.dependencies = legacy
                self.plugin_dependencies = modern

        class _Plugin:
            def __init__(self, legacy: list[str], modern: dict[str, str]) -> None:
                self.metadata = _Meta(legacy, modern)

        plugins = {
            "app": _Plugin(["legacy_dep"], {"modern_dep": "*"}),
            "legacy_dep": _Plugin([], {}),
            "modern_dep": _Plugin([], {}),
        }
        order = sort_by_dependencies(plugins)  # type: ignore[arg-type]
        assert order.index("legacy_dep") < order.index("app")
        assert order.index("modern_dep") < order.index("app")
