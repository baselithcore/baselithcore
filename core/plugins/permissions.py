"""Declared capabilities for a plugin, and the staged rollout that enforces them.

A plugin runs **in-process with the host's full authority**: any egress host,
any file, any secret in the environment, any tool. The manifest already proves
*which code* is running — ``integrity_sha256`` is verified before
``exec_module`` and an Ed25519 signature can be required — but integrity
answers "is this the code the author shipped?", never "should this code be able
to reach ``169.254.169.254``?".

A ``permissions:`` block answers the second question::

    permissions:
      network:
        egress: ["api.github.com", "*.openai.com"]
      tools: ["search_knowledge_base", "scrape_url"]
      secrets: ["GITHUB_TOKEN"]
      filesystem: ["./data/plugins/my-plugin"]

**The rollout is staged, so an upgrade breaks nothing**
(``BASELITH_PLUGIN_PERMISSIONS``):

``off``
    Declarations are parsed and exposed, never consulted.
``warn`` *(default)*
    A call outside the declared set is logged once per plugin and host, and
    proceeds. This is the observation window: an operator sees what their
    plugins actually reach before anything is denied.
``enforce``
    A plugin that **declared** a block is held to it — an undeclared egress
    host raises. A plugin that declared **nothing** is untouched: undeclared
    means "not migrated yet", not "denied everything", so flipping the flag
    cannot brick every plugin written before this existed. The same shape as
    ``BASELITH_REQUIRE_SIGNED_PLUGINS``.

Three of the four blocks are enforced, each at the one choke point the
framework owns: ``network.egress`` through :mod:`core.security.ssrf`, ``tools``
through :func:`core.orchestration.enforcement.enforce_tool_invocation`, and
``secrets`` through :func:`core.security.secrets.get_secret` (see
:mod:`core.plugins.access`). ``filesystem`` is parsed, exposed to the
marketplace and reported by the CLI, but **not** refused: file access is
``open()``, with no comparable choke point, and a guard there would imply a
guarantee that does not exist. The plugins documentation says so plainly rather
than implying otherwise.

None of this is containment. A plugin runs in-process and can read
``os.environ`` directly; what the declaration buys is that the framework's own
paths are held to it, and that an operator can read a manifest and know what a
plugin says it needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "PermissionMode",
    "PluginPermissions",
    "parse_permissions",
    "resolve_permission_mode",
]

WILDCARD = "*"
#: Environment variable selecting the rollout stage.
MODE_ENV_VAR = "BASELITH_PLUGIN_PERMISSIONS"


class PermissionMode(str, Enum):
    """How strictly declared permissions are applied."""

    OFF = "off"
    WARN = "warn"
    ENFORCE = "enforce"

    @property
    def enforces(self) -> bool:
        """Whether a call outside the declared set is refused."""
        return self is PermissionMode.ENFORCE


def resolve_permission_mode(value: str | None = None) -> PermissionMode:
    """Map a configured value to a mode, defaulting to :attr:`PermissionMode.WARN`.

    Args:
        value: Raw setting; ``None`` reads ``BASELITH_PLUGIN_PERMISSIONS``.

    Returns:
        The selected mode. Anything unrecognised falls back to ``warn``: an
        operator's typo must not silently disable the observation window, and
        must not silently start denying traffic either.
    """
    if value is None:
        import os

        value = os.environ.get(MODE_ENV_VAR, "")
    normalized = (value or "").strip().lower()
    if normalized in {"enforce", "true", "1", "yes", "on", "strict"}:
        return PermissionMode.ENFORCE
    if normalized in {"off", "false", "0", "no", "none", "disabled"}:
        return PermissionMode.OFF
    return PermissionMode.WARN


def _string_tuple(raw: Any, *, lower: bool = False) -> tuple[str, ...]:
    """Coerce a manifest list to a clean tuple of non-empty strings."""
    if not isinstance(raw, list | tuple):
        return ()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        value = entry.strip()
        if not value:
            continue
        out.append(value.lower() if lower else value)
    return tuple(out)


def _host_matches(pattern: str, host: str) -> bool:
    """Whether ``host`` satisfies one egress pattern.

    ``*`` grants everything. ``*.example.com`` matches any **sub**domain, not
    the apex and — crucially — not ``api.example.com.evil.net``, which a naive
    ``endswith`` check would wave through.
    """
    if pattern == WILDCARD:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == pattern


@dataclass(frozen=True)
class PluginPermissions:
    """What a plugin declared it needs. Immutable: a plugin cannot widen itself."""

    declared: bool = False
    network_egress: tuple[str, ...] = field(default_factory=tuple)
    tools: tuple[str, ...] = field(default_factory=tuple)
    secrets: tuple[str, ...] = field(default_factory=tuple)
    filesystem: tuple[str, ...] = field(default_factory=tuple)

    def allows_host(self, host: str) -> bool:
        """Whether ``host`` is inside the declared egress set."""
        target = (host or "").strip().lower()
        if not target:
            return False
        return any(_host_matches(pattern, target) for pattern in self.network_egress)

    def allows_tool(self, name: str) -> bool:
        """Whether ``name`` is inside the declared tool set."""
        return WILDCARD in self.tools or name in self.tools

    def allows_secret(self, name: str) -> bool:
        """Whether ``name`` is inside the declared secret set (case-sensitive)."""
        return WILDCARD in self.secrets or name in self.secrets

    def egress_denied(self, host: str, mode: PermissionMode) -> bool:
        """Whether an outbound call to ``host`` must be refused.

        A plugin that declared nothing is never denied — undeclared means "not
        migrated yet". Only a plugin that opted in is held to what it wrote.
        """
        if not mode.enforces or not self.declared:
            return False
        return not self.allows_host(host)

    def summary(self) -> dict[str, Any]:
        """A JSON-friendly view for the marketplace and the CLI."""
        return {
            "declared": self.declared,
            "network_egress": list(self.network_egress),
            "tools": list(self.tools),
            "secrets": list(self.secrets),
            "filesystem": list(self.filesystem),
        }


def parse_permissions(raw: Any) -> PluginPermissions:
    """Read a manifest ``permissions:`` block.

    Tolerant by design, like every other manifest normalizer here: a malformed
    declaration grants nothing rather than aborting plugin load. The difference
    between *absent* and *empty* is load-bearing — ``permissions: {}`` is a
    plugin stating it needs nothing, while no block at all is a plugin that
    predates the mechanism.

    Args:
        raw: The manifest value, or ``None`` when the key is absent.

    Returns:
        The parsed declaration.
    """
    if raw is None:
        return PluginPermissions()
    if not isinstance(raw, dict):
        # A non-mapping is malformed, not a statement of intent: treat it as an
        # empty declaration so `enforce` denies rather than silently allows.
        return PluginPermissions(declared=True)

    network = raw.get("network")
    egress = network.get("egress") if isinstance(network, dict) else None
    return PluginPermissions(
        declared=True,
        network_egress=_string_tuple(egress, lower=True),
        tools=_string_tuple(raw.get("tools")),
        secrets=_string_tuple(raw.get("secrets")),
        filesystem=_string_tuple(raw.get("filesystem")),
    )
