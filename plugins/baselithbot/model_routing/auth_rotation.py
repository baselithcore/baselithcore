"""Auth profile rotation (round-robin / least-used / random)."""

from __future__ import annotations

import random
import time
from collections import deque
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AuthRotationPolicy(str, Enum):
    ROUND_ROBIN = "round_robin"
    LEAST_USED = "least_used"
    RANDOM = "random"


class AuthProfile(BaseModel):
    name: str
    api_key: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ProfileStats(BaseModel):
    name: str
    uses: int = 0
    last_used_at: float = 0.0
    last_error_at: float | None = None


class AuthProfilePool:
    """Pool of auth profiles with rotation + temporary disable on errors."""

    def __init__(
        self,
        profiles: list[AuthProfile],
        policy: AuthRotationPolicy = AuthRotationPolicy.ROUND_ROBIN,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if not profiles:
            raise ValueError("AuthProfilePool requires at least one profile")
        self._profiles = {p.name: p for p in profiles}
        self._stats = {p.name: _ProfileStats(name=p.name) for p in profiles}
        self._policy = policy
        self._cooldown = cooldown_seconds
        self._round_robin: deque[str] = deque(p.name for p in profiles)

    def _eligible(self) -> list[str]:
        now = time.time()
        return [
            name
            for name, st in self._stats.items()
            if st.last_error_at is None or (now - st.last_error_at) > self._cooldown
        ]

    def acquire(self) -> AuthProfile:
        eligible = self._eligible()
        if not eligible:
            for st in self._stats.values():
                st.last_error_at = None
            eligible = list(self._profiles.keys())

        if self._policy == AuthRotationPolicy.RANDOM:
            chosen = random.choice(eligible)  # nosec B311
        elif self._policy == AuthRotationPolicy.LEAST_USED:
            chosen = min(eligible, key=lambda n: self._stats[n].uses)
        else:
            chosen = next(
                (n for n in self._round_robin if n in eligible),
                eligible[0],
            )
            try:
                self._round_robin.remove(chosen)
            except ValueError:
                pass
            self._round_robin.append(chosen)

        self._stats[chosen].uses += 1
        self._stats[chosen].last_used_at = time.time()
        return self._profiles[chosen]

    def report_error(self, name: str) -> None:
        if name in self._stats:
            self._stats[name].last_error_at = time.time()

    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        return [
            {
                "name": st.name,
                "uses": st.uses,
                "last_used_at": st.last_used_at or None,
                "in_cooldown": (
                    st.last_error_at is not None and (now - st.last_error_at) <= self._cooldown
                ),
            }
            for st in self._stats.values()
        ]


__all__ = ["AuthProfile", "AuthProfilePool", "AuthRotationPolicy"]
