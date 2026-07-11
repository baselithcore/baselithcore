"""Tests for the plugin skill catalog service (core.plugins.skills_service)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.human import HumanIntervention
from core.plugins.registry import PluginRegistry
from core.plugins.result import SkillResult
from core.plugins.skills_service import (
    ACTIVATE_SKILL_TOOL_NAME,
    SkillService,
    make_activation_tool_fn,
)


def _write_skill(
    root: Path,
    slug: str,
    *,
    name: str | None = None,
    description: str = "A test skill.",
    body: str = "Do the thing step by step.",
    requires_approval: bool = False,
    tools: list[str] | None = None,
) -> Path:
    skill_dir = root / slug
    skill_dir.mkdir(parents=True)
    front = [
        "---",
        f"name: {name or slug}",
        f"description: {description}",
    ]
    if requires_approval:
        front.append("requires_approval: true")
    if tools:
        front.append(f"tools: [{', '.join(tools)}]")
    front.append("---")
    path = skill_dir / "SKILL.md"
    path.write_text("\n".join(front) + f"\n\n{body}\n", encoding="utf-8")
    return path


class _FakeRegistry:
    """Minimal stand-in exposing the registry's skill-root lookup."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self._roots = roots

    def get_all_skill_roots(self) -> dict[str, Path]:
        return {name: root for name, root in self._roots.items() if root.is_dir()}


@pytest.fixture
def two_plugin_service(tmp_path: Path) -> SkillService:
    root_a = tmp_path / "alpha" / "skills"
    root_b = tmp_path / "beta" / "skills"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    _write_skill(root_a, "review", description="Review code changes.")
    _write_skill(root_b, "deploy", description="Ship to production.", tools=["shell"])
    registry = _FakeRegistry({"alpha": root_a, "beta": root_b})
    return SkillService(registry)  # type: ignore[arg-type]


class TestCatalog:
    def test_aggregates_across_plugins_with_attribution(self, two_plugin_service):
        cards = two_plugin_service.catalog()
        assert [(c.name, c.plugin) for c in cards] == [
            ("deploy", "beta"),
            ("review", "alpha"),
        ]

    def test_duplicate_names_first_plugin_wins(self, tmp_path):
        root_a = tmp_path / "alpha" / "skills"
        root_b = tmp_path / "beta" / "skills"
        _write_skill(root_a, "review", description="From alpha.")
        _write_skill(root_b, "review", description="From beta.")
        service = SkillService(_FakeRegistry({"beta": root_b, "alpha": root_a}))  # type: ignore[arg-type]
        cards = service.catalog()
        assert len(cards) == 1
        assert cards[0].plugin == "alpha"  # sorted plugin order, not dict order

    def test_malformed_skill_disables_only_that_plugin(self, tmp_path):
        root_a = tmp_path / "alpha" / "skills"
        root_b = tmp_path / "beta" / "skills"
        _write_skill(root_a, "good")
        bad = root_b / "bad"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
        service = SkillService(_FakeRegistry({"alpha": root_a, "beta": root_b}))  # type: ignore[arg-type]
        assert [c.name for c in service.catalog()] == ["good"]

    def test_render_catalog_lists_skills_and_tool_usage(self, two_plugin_service):
        text = two_plugin_service.render_catalog()
        assert ACTIVATE_SKILL_TOOL_NAME in text
        assert "- deploy: Ship to production." in text
        assert "- review: Review code changes." in text

    def test_render_catalog_empty_without_skills(self, tmp_path):
        service = SkillService(_FakeRegistry({}))  # type: ignore[arg-type]
        assert service.render_catalog() == ""

    def test_render_catalog_marks_approval(self, tmp_path):
        root = tmp_path / "p" / "skills"
        _write_skill(root, "danger", requires_approval=True)
        service = SkillService(_FakeRegistry({"p": root}))  # type: ignore[arg-type]
        assert "(requires human approval)" in service.render_catalog()

    def test_get_card_sees_skill_added_after_first_walk(self, tmp_path):
        root = tmp_path / "p" / "skills"
        _write_skill(root, "first")
        service = SkillService(_FakeRegistry({"p": root}), catalog_ttl=3600)  # type: ignore[arg-type]
        assert service.get_card("later") is None  # builds + misses
        _write_skill(root, "later")
        card = service.get_card("later")  # forced refresh on miss
        assert card is not None and card.plugin == "p"


class TestActivation:
    async def test_activate_returns_body(self, two_plugin_service):
        result = await two_plugin_service.activate("review")
        assert isinstance(result, SkillResult) and result.success
        assert result.data["plugin"] == "alpha"
        assert "step by step" in result.data["body"]

    async def test_unknown_skill_fails(self, two_plugin_service):
        result = await two_plugin_service.activate("nope")
        assert not result.success
        assert result.error_code == "skill_not_found"
        assert "review" in result.message  # lists what IS available

    async def test_declared_tools_missing_degrades_to_partial(self, two_plugin_service):
        result = await two_plugin_service.activate("deploy", available_tools=["search"])
        assert not result.success
        assert result.error_code == "skill_tools_unavailable"
        assert "shell" in result.message
        assert result.data["body"]  # body still delivered

    async def test_declared_tools_present_is_ok(self, two_plugin_service):
        result = await two_plugin_service.activate(
            "deploy", available_tools=["shell", "search"]
        )
        assert result.success

    async def test_approval_fails_closed_without_channel(self, tmp_path):
        root = tmp_path / "p" / "skills"
        _write_skill(root, "danger", requires_approval=True)
        service = SkillService(_FakeRegistry({"p": root}))  # type: ignore[arg-type]
        result = await service.activate("danger")
        assert not result.success
        assert result.error_code == "skill_approval_required"

    async def test_approval_denied(self, tmp_path):
        root = tmp_path / "p" / "skills"
        _write_skill(root, "danger", requires_approval=True)
        service = SkillService(_FakeRegistry({"p": root}))  # type: ignore[arg-type]
        human = HumanIntervention(callback=lambda request: False)
        result = await service.activate("danger", human_intervention=human)
        assert not result.success
        assert result.error_code == "skill_approval_denied"

    async def test_approval_granted(self, tmp_path):
        root = tmp_path / "p" / "skills"
        _write_skill(root, "danger", requires_approval=True)
        service = SkillService(_FakeRegistry({"p": root}))  # type: ignore[arg-type]
        human = HumanIntervention(callback=lambda request: True)
        result = await service.activate("danger", human_intervention=human)
        assert result.success


class TestActivationToolFn:
    async def test_returns_body_text(self, two_plugin_service):
        tool = make_activation_tool_fn(two_plugin_service)
        text = await tool("review")
        assert text.startswith("# Skill activated: review")
        assert "step by step" in text

    async def test_partial_body_carries_note(self, two_plugin_service):
        tool = make_activation_tool_fn(two_plugin_service, available_tools=["search"])
        text = await tool("deploy")
        assert "# Skill activated: deploy" in text
        assert "NOTE:" in text and "shell" in text

    async def test_empty_name_is_usage_error(self, two_plugin_service):
        tool = make_activation_tool_fn(two_plugin_service)
        assert "requires a skill name" in await tool("")

    async def test_unknown_name_is_error_string(self, two_plugin_service):
        tool = make_activation_tool_fn(two_plugin_service)
        assert (await tool("nope")).startswith("Error:")


class TestRegistrySkillRoots:
    def test_lookup_returns_only_dirs_with_skills(self, tmp_path):
        registry = PluginRegistry()
        with_skills = tmp_path / "with_skills"
        (with_skills / "skills").mkdir(parents=True)
        without = tmp_path / "without"
        without.mkdir()
        registry._plugins["with_skills"] = object()  # type: ignore[assignment]
        registry._plugin_directories["with_skills"] = with_skills
        registry._plugins["without"] = object()  # type: ignore[assignment]
        registry._plugin_directories["without"] = without
        roots = registry.get_all_skill_roots()
        assert roots == {"with_skills": with_skills / "skills"}
