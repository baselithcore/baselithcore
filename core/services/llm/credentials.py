"""Central LLM credential seam.

The deployment environment is the authoritative source of provider API keys.
Some deployments additionally let an operator supply a *missing* key from an
admin surface; that store is domain-specific and lives in a plugin, so core
only exposes a registration seam and never imports the plugin — the same
Sacred-Core pattern as :func:`core.services.llm.policy.set_plugin_llm_policy_resolver`.

Precedence is structural, not conditional: :func:`core.services.llm.runtime.api_key_for`
reaches this resolver only on the path where central configuration already
yielded nothing, so a key present in the environment can never be displaced.

The resolver must be cheap and total (cached, never raising). Any failure or
unusable value degrades to ``None`` — "no stored credential" — so a credential
store outage can never break LLM availability. With no resolver registered,
behaviour is identical to a deployment without a credential store.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import SecretStr

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Registered by the credential-store plugin at load time; ``None`` means
#: "environment only", the default for a deployment without such a plugin.
_credential_resolver: Callable[[str], str | None] | None = None


def set_llm_credential_resolver(
    resolver: Callable[[str], str | None] | None,
) -> None:
    """Register (or clear with ``None``) the stored-credential resolver.

    Args:
        resolver: Callable mapping a provider id to its stored key, or ``None``
            when the store holds nothing for it. Must be cheap and must not
            raise; a raising resolver is tolerated but logged.
    """
    global _credential_resolver
    _credential_resolver = resolver


def resolve_llm_credential(provider: str) -> SecretStr | None:
    """The stored credential for *provider*, or ``None``.

    Never raises: an unregistered resolver, a resolver error, a non-string
    value, or a blank string all degrade to ``None``.
    """
    resolver = _credential_resolver
    if resolver is None:
        return None
    try:
        raw = resolver(provider)
    except Exception as exc:  # noqa: BLE001 — a store outage is not an outage
        logger.warning(
            "LLM credential resolver failed for provider %r — "
            "falling back to central configuration: %s",
            provider,
            exc,
        )
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return SecretStr(raw)


__all__ = ["resolve_llm_credential", "set_llm_credential_resolver"]
