"""Tool and secret access, held to a plugin's declared permissions.

:mod:`core.plugins.egress` enforces the ``network.egress`` half of a
``permissions:`` block. This module enforces the other two halves that have a
chokepoint the framework owns:

* **tools** — every gated invocation passes
  :func:`core.orchestration.enforcement.enforce_tool_invocation`, which calls
  :func:`check_tool_permitted` alongside the contract and autonomy gates. A
  plugin that declared ``tools`` cannot invoke one it left out.
* **secrets** — every read through :func:`core.security.secrets.get_secret`
  passes :func:`_secret_guard`. A plugin that declared ``secrets`` cannot read
  a credential it left out.

Both follow the same staged rollout as egress, through the shared sequence in
:mod:`core.plugins.permission_runtime`: core traffic is never gated, ``off``
never consults a declaration, and a plugin that declared nothing is never
refused — so ``BASELITH_PLUGIN_PERMISSIONS=enforce`` remains safe to switch on.

**What this does not cover.** A plugin can still call ``os.environ`` or an
SDK's own credential lookup directly; nothing in an in-process plugin model
prevents that, and claiming otherwise would be worse than the gap. What the
declaration buys is that the framework's own paths are held to it, and that an
operator can read a manifest and know what a plugin says it needs. Real
containment needs process isolation, which is a different design.

``filesystem`` is parsed and surfaced but still not enforced: a plugin's file
access has no comparable chokepoint — it is ``open()`` — so a guard here would
imply a guarantee that does not exist. The packaging documentation says so
plainly rather than implying otherwise.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from core.plugins.permission_runtime import Decision, decide
from core.security.secrets import set_secret_guard

logger = get_logger(__name__)

__all__ = [
    "SecretNotPermittedError",
    "ToolNotPermittedError",
    "check_tool_permitted",
    "install_secret_guard",
    "uninstall_secret_guard",
]

#: Reported once per (plugin, name) so a hot loop cannot flood the log.
_WARNED: set[tuple[str, str, str]] = set()


class ToolNotPermittedError(PermissionError):
    """A plugin invoked a tool outside its declared ``tools`` set."""

    def __init__(self, plugin: str, tool: str) -> None:
        self.plugin = plugin
        self.tool = tool
        super().__init__(
            f"Plugin {plugin!r} is not permitted to invoke tool {tool!r}: add "
            "it to the manifest's permissions.tools list"
        )


class SecretNotPermittedError(PermissionError):
    """A plugin read a secret outside its declared ``secrets`` set."""

    def __init__(self, plugin: str, secret: str) -> None:
        self.plugin = plugin
        self.secret = secret
        super().__init__(
            f"Plugin {plugin!r} is not permitted to read secret {secret!r}: add "
            "it to the manifest's permissions.secrets list"
        )


def _warn_once(kind: str, plugin: str, name: str, mode: str, hint: str) -> None:
    """Log an undeclared access once per plugin and name."""
    key = (kind, plugin, name)
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(
        f"plugin_{kind}_undeclared",
        extra={"plugin": plugin, "name": name, "mode": mode, "hint": hint},
    )


def check_tool_permitted(tool_name: str) -> None:
    """Refuse a tool the bound plugin did not declare.

    Args:
        tool_name: The tool about to be invoked.

    Raises:
        ToolNotPermittedError: The plugin declared a ``tools`` list that does
            not cover ``tool_name``, and the mode enforces.
    """
    decision, plugin, mode = decide(
        lambda permissions: permissions.allows_tool(tool_name)
    )
    if decision is Decision.ALLOW:
        return
    if decision is Decision.DENY:
        logger.warning(
            "plugin_tool_denied",
            extra={"plugin": plugin, "tool": tool_name, "mode": mode.value},
        )
        raise ToolNotPermittedError(plugin, tool_name)
    _warn_once(
        "tool",
        plugin,
        tool_name,
        mode.value,
        "add it to permissions.tools, or set "
        "BASELITH_PLUGIN_PERMISSIONS=enforce to refuse it",
    )


def _secret_guard(name: str) -> None:
    """Refuse a secret the bound plugin did not declare.

    Only the secret's *name* reaches this function — the value is resolved
    afterwards, and only if the read is permitted, so a refused read never
    materialises the credential.
    """
    decision, plugin, mode = decide(lambda permissions: permissions.allows_secret(name))
    if decision is Decision.ALLOW:
        return
    if decision is Decision.DENY:
        logger.warning(
            "plugin_secret_denied",
            extra={"plugin": plugin, "secret": name, "mode": mode.value},
        )
        raise SecretNotPermittedError(plugin, name)
    _warn_once(
        "secret",
        plugin,
        name,
        mode.value,
        "add it to permissions.secrets, or set "
        "BASELITH_PLUGIN_PERMISSIONS=enforce to refuse it",
    )


def install_secret_guard() -> None:
    """Route :func:`core.security.secrets.get_secret` through the declaration."""
    set_secret_guard(_secret_guard)


def uninstall_secret_guard() -> None:
    """Remove the guard and forget what has been warned about."""
    set_secret_guard(None)
    _WARNED.clear()
