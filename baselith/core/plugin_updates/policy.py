"""The update policy: which releases install themselves, and when.

An :class:`UpdatePolicy` is a delegated approval. With a channel set to
``patch`` the updater creates pre-approved runs for patch releases inside the
:class:`MaintenanceWindow` (see :mod:`.apply.policy_runner`). The document lives
beside the runs (``policy.json`` in the run store); a change made from a console
is a :class:`PolicyProposal` that a second administrator approves, while the
host shell (``baselith update policy set``) is trusted and writes it directly.

A missing, unreadable or invalid document is :data:`POLICY_OFF`: nothing is
applied automatically unless a valid policy says so.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, time
from typing import Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)

#: ``off`` never applies anything; ``patch`` applies patch releases only.
Channel = Literal["off", "patch"]

_ALLOWED_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"unknown timezone {name!r}") from exc


class MaintenanceWindow(BaseModel):
    """Wall-clock hours on some weekdays, in one IANA time zone.

    ``days`` are ISO weekdays counted from 0 (Monday) to 6 (Sunday) and name
    the day a window *starts* on; an ``end`` earlier than ``start`` spans
    midnight. The DST rules are in :mod:`baselith.core.plugin_updates.window`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    days: frozenset[int]
    start: time
    end: time
    tz: str

    @field_validator("days")
    @classmethod
    def _days(cls, days: frozenset[int]) -> frozenset[int]:
        if not days:
            raise ValueError("a maintenance window needs at least one day")
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return days

    @field_serializer("days")
    def _sorted_days(self, days: frozenset[int]) -> list[int]:
        return sorted(days)  # a stable document: digests agree across processes

    @field_validator("start", "end")
    @classmethod
    def _wall_clock(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("window times are wall-clock times in tz, without offset")
        return value

    @field_validator("tz")
    @classmethod
    def _tz(cls, name: str) -> str:
        _zone(name)
        return name

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.start == self.end:
            raise ValueError("start and end of a maintenance window must differ")
        return self

    @property
    def zone(self) -> ZoneInfo:
        """The window's time zone."""
        return _zone(self.tz)


class UpdatePolicy(BaseModel):
    """What installs itself: a channel for the core and one for the plugins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    core: Channel = "off"
    plugins: Channel = "off"
    #: Plugins the ``plugins`` channel may update; empty means every plugin.
    plugin_allowlist: frozenset[str] = frozenset()
    window: MaintenanceWindow | None = None
    #: Auto-apply only ever installs a signed release; not a switch.
    require_signed: Literal[True] = True

    @field_validator("plugin_allowlist")
    @classmethod
    def _names(cls, names: frozenset[str]) -> frozenset[str]:
        bad = sorted(n for n in names if not _ALLOWED_NAME.match(n))
        if bad:
            raise ValueError(f"not a plugin name: {', '.join(bad)}")
        return names

    @field_serializer("plugin_allowlist")
    def _sorted_names(self, names: frozenset[str]) -> list[str]:
        return sorted(names)  # string hashing is per process: sort for the digest

    @model_validator(mode="after")
    def _window_when_on(self) -> Self:
        if self.enabled and self.window is None:
            raise ValueError("a channel other than off needs a maintenance window")
        return self

    @property
    def enabled(self) -> bool:
        """Whether any channel applies releases."""
        return self.core != "off" or self.plugins != "off"

    def allows_plugin(self, name: str) -> bool:
        """Whether the ``plugins`` channel covers ``name``."""
        return self.plugins == "patch" and (
            not self.plugin_allowlist or name in self.plugin_allowlist
        )


class PolicyProposal(BaseModel):
    """A policy change waiting for a second administrator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: UpdatePolicy
    proposed_by: str
    proposed_at: datetime
    expires_at: datetime

    @property
    def digest(self) -> str:
        """SHA-256 of the proposal: what an approver saw is what goes live."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


#: The policy when none is set: nothing installs itself.
POLICY_OFF: Final = UpdatePolicy()


__all__ = [
    "POLICY_OFF",
    "Channel",
    "MaintenanceWindow",
    "PolicyProposal",
    "UpdatePolicy",
]
