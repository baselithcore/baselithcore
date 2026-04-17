"""Skills registry (ClawHub-style) + AGENTS/SOUL/TOOLS loader."""

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
]
