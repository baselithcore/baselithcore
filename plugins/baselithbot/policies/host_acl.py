"""Host access-control rules engine for Computer Use actions."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class HostACLRule(BaseModel):
    name: str
    action: str = Field(
        ..., description="Capability action name, e.g. 'mouse_click' or 'shell_run'."
    )
    pattern: str | None = Field(
        default=None,
        description=(
            "Optional regex matched against a serialized representation of the call "
            "arguments. ``None`` means rule applies regardless of arguments."
        ),
    )
    decision: str = Field(default="allow", pattern="^(allow|deny)$")


class HostACL:
    """Evaluate ordered ALLOW/DENY rules against an action + argument blob."""

    def __init__(
        self,
        rules: list[HostACLRule] | None = None,
        default: str = "allow",
    ) -> None:
        if default not in ("allow", "deny"):
            raise ValueError("default must be 'allow' or 'deny'")
        self._rules = rules or []
        self._default = default

    def add(self, rule: HostACLRule) -> None:
        self._rules.append(rule)

    def decide(self, action: str, args: dict[str, Any] | None = None) -> bool:
        blob = repr(args or {})
        for rule in self._rules:
            if rule.action != action:
                continue
            if rule.pattern is None or re.search(rule.pattern, blob):
                return rule.decision == "allow"
        return self._default == "allow"

    def status(self) -> dict[str, Any]:
        return {
            "default": self._default,
            "rules": [r.model_dump() for r in self._rules],
        }


__all__ = ["HostACL", "HostACLRule"]
