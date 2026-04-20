"""Skills registry (ClawHub-style) + AGENTS/SOUL/TOOLS loader + remote client."""

from .bundled import bundled_skills
from .clawhub import DEFAULT_HUB_URL, ClawHubClient, ClawHubConfig
from .loader import (
    InjectionBundle,
    LocalSkillSpec,
    LocalSkillValidation,
    discover_local_skill_specs,
    load_injection_bundle,
)
from .registry import Skill, SkillRegistry, SkillScope
from .writer import SkillDraft, delete_workspace_skill, write_workspace_skill

__all__ = [
    "Skill",
    "SkillDraft",
    "SkillRegistry",
    "SkillScope",
    "InjectionBundle",
    "LocalSkillSpec",
    "LocalSkillValidation",
    "delete_workspace_skill",
    "discover_local_skill_specs",
    "load_injection_bundle",
    "write_workspace_skill",
    "ClawHubClient",
    "ClawHubConfig",
    "DEFAULT_HUB_URL",
    "bundled_skills",
]
