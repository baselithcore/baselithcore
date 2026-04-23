"""Skills registry with scope filtering (bundled / managed / workspace)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SkillScope(str, Enum):
    BUNDLED = "bundled"
    MANAGED = "managed"
    WORKSPACE = "workspace"


class Skill(BaseModel):
    name: str
    version: str = "0.0.0"
    scope: SkillScope = SkillScope.WORKSPACE
    description: str = ""
    entrypoint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillRegistry:
    """In-memory registry of installed skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def remove(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self, scope: SkillScope | None = None) -> list[Skill]:
        if scope is None:
            return list(self._skills.values())
        return [s for s in self._skills.values() if s.scope == scope]


__all__ = ["Skill", "SkillRegistry", "SkillScope"]
