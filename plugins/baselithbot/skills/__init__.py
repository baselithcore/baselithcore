"""Skills registry (ClawHub-style) + AGENTS/SOUL/TOOLS loader + remote client."""

from .bundled import bundled_skills
from .clawhub import DEFAULT_HUB_URL, ClawHubClient, ClawHubConfig
from .loader import (
    InjectionBundle,
    load_injection_bundle,
)
from .registry import Skill, SkillRegistry, SkillScope

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillScope",
    "InjectionBundle",
    "load_injection_bundle",
    "ClawHubClient",
    "ClawHubConfig",
    "DEFAULT_HUB_URL",
    "bundled_skills",
]
