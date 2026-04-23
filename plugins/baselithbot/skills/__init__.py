"""Skills registry (ClawHub-style) + AGENTS/SOUL/TOOLS loader + remote client."""

from plugins.baselithbot.skills.bundled import bundled_skills
from plugins.baselithbot.skills.clawhub import DEFAULT_HUB_URL, ClawHubClient, ClawHubConfig
from plugins.baselithbot.skills.loader import (
    InjectionBundle,
    LocalSkillSpec,
    LocalSkillValidation,
    OpenClawRequires,
    OpenClawSpec,
    discover_local_skill_specs,
    load_injection_bundle,
)
from plugins.baselithbot.skills.registry import Skill, SkillRegistry, SkillScope
from plugins.baselithbot.skills.writer import (
    OpenClawDraft,
    OpenClawRequiresDraft,
    SkillDraft,
    delete_workspace_skill,
    write_workspace_skill,
)

__all__ = [
    "Skill",
    "SkillDraft",
    "SkillRegistry",
    "SkillScope",
    "InjectionBundle",
    "LocalSkillSpec",
    "LocalSkillValidation",
    "OpenClawDraft",
    "OpenClawRequires",
    "OpenClawRequiresDraft",
    "OpenClawSpec",
    "delete_workspace_skill",
    "discover_local_skill_specs",
    "load_injection_bundle",
    "write_workspace_skill",
    "ClawHubClient",
    "ClawHubConfig",
    "DEFAULT_HUB_URL",
    "bundled_skills",
]
