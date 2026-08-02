"""Unit tests for the Baselithbot plugin — skills registry, discovery, dashboard."""

from __future__ import annotations


def test_skill_registry_scopes() -> None:
    from plugins.baselithbot.skills import Skill, SkillRegistry, SkillScope

    reg = SkillRegistry()
    reg.register(Skill(name="a", scope=SkillScope.BUNDLED))
    reg.register(Skill(name="b", scope=SkillScope.WORKSPACE))
    bundled = reg.list(scope=SkillScope.BUNDLED)
    assert {s.name for s in bundled} == {"a"}


def test_load_injection_bundle(tmp_path) -> None:
    from plugins.baselithbot.skills import load_injection_bundle

    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "SOUL.md").write_text("# soul")
    bundle = load_injection_bundle(tmp_path)
    assert bundle.agents_md and "agents" in bundle.agents_md
    assert bundle.soul_md and "soul" in bundle.soul_md
    assert bundle.tools_md is None
    block = bundle.to_prompt_block()
    assert "<soul>" in block and "<agents>" in block


def test_discover_local_skill_specs(tmp_path) -> None:
    from plugins.baselithbot.skills import discover_local_skill_specs

    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\ndescription: Local demo\n---\n\n# Demo\n"
    )
    (skill_dir / "MANIFEST.yaml").write_text(
        """
bundle: demo-skill
bundle_version: 1.0.0
compatibility:
  designed_for:
    surfaces:
      - cli
  tested_on:
    - platform: Baselithbot
      model: local
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    )

    specs = discover_local_skill_specs(tmp_path)
    assert len(specs) == 1
    assert specs[0].name == "Demo Skill"
    assert specs[0].validation.status == "verified"


def test_bundled_skills_cover_core_capabilities() -> None:
    from plugins.baselithbot.skills import SkillScope, bundled_skills

    names = {s.name for s in bundled_skills()}
    assert {
        "baselithbot.browser",
        "baselithbot.computer_use",
        "baselithbot.shell",
        "baselithbot.canvas",
        "baselithbot.channels",
    }.issubset(names)
    assert all(s.scope == SkillScope.BUNDLED for s in bundled_skills())


def test_plugin_bootstrap_registers_bundled_skills(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope, bundled_skills

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    registered = {s.name for s in plugin.skills.list(SkillScope.BUNDLED)}
    assert registered == {s.name for s in bundled_skills()}


def test_plugin_bootstrap_scans_workspace_markdown(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "TOOLS.md").write_text("# tools")

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    workspace_skills = plugin.skills.list(SkillScope.WORKSPACE)
    assert len(workspace_skills) == 1
    skill = workspace_skills[0]
    assert "AGENTS.md" in skill.metadata.get("sources", {})
    assert "TOOLS.md" in skill.metadata.get("sources", {})


def test_plugin_rescan_workspace_skills_picks_up_new_files(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    assert plugin.skills.list(SkillScope.WORKSPACE) == []

    (tmp_path / "SOUL.md").write_text("# soul")
    removed = plugin.rescan_workspace_skills()
    assert removed == 0
    assert len(plugin.skills.list(SkillScope.WORKSPACE)) == 1


def test_plugin_bootstrap_registers_local_custom_skill(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    skill_dir = tmp_path / "skills" / "local-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Local Demo\ndescription: Custom local skill\n---\n\n# Demo\n"
    )
    (skill_dir / "MANIFEST.yaml").write_text(
        """
bundle: local-demo
bundle_version: 1.0.0
compatibility:
  designed_for:
    surfaces:
      - cli
      - chat
  tested_on:
    - platform: Baselithbot
      model: local
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    )

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    workspace_skills = plugin.skills.list(SkillScope.WORKSPACE)
    custom = next(
        skill
        for skill in workspace_skills
        if skill.metadata.get("kind") == "custom_skill"
    )
    assert custom.name == f"workspace.{tmp_path.name}.local-demo"
    assert custom.metadata["validation"]["status"] == "verified"


def test_plugin_reports_invalid_local_custom_skill(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    skill_dir = tmp_path / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# missing frontmatter")

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    assert plugin.skills.list(SkillScope.WORKSPACE) == []
    reports = plugin.workspace_skill_reports()
    assert len(reports) == 1
    assert reports[0]["validation"]["status"] == "invalid"


def test_skills_dashboard_routes_full_lifecycle(tmp_path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins.baselithbot.dashboard.app import create_dashboard_router
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import Skill, SkillScope

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    plugin.skills.register(
        Skill(name="managed.test", scope=SkillScope.MANAGED, version="1.0.0")
    )

    app = FastAPI()
    app.include_router(create_dashboard_router(plugin))
    client = TestClient(app)

    res = client.get("/dash/skills")
    assert res.status_code == 200
    names = {s["name"] for s in res.json()["skills"]}
    assert "baselithbot.browser" in names
    assert "managed.test" in names

    res = client.get("/dash/skills?scope=managed")
    assert {s["name"] for s in res.json()["skills"]} == {"managed.test"}

    res = client.get("/dash/skills/workspace/validate")
    assert res.status_code == 200
    assert "counts" in res.json()

    res = client.get("/dash/skills/clawhub")
    body = res.json()
    assert res.status_code == 200
    assert body["base_url"]
    assert body["install_dir"]

    res = client.delete("/dash/skills/baselithbot.browser")
    assert res.status_code == 409

    res = client.delete("/dash/skills/managed.test")
    assert res.status_code == 200
    assert res.json()["status"] == "removed"
    assert plugin.skills.get("managed.test") is None

    res = client.delete("/dash/skills/does.not.exist")
    assert res.status_code == 404

    res = client.post("/dash/skills/rescan")
    assert res.status_code == 200
    assert "workspace_skills" in res.json()
