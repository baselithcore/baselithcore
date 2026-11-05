"""Where a client that speaks a provider's wire protocol itself sends a call.

:func:`baselith.core.services.llm.runtime.api_base_for` answers ``None`` for a
hosted provider, because the SDK each core provider wraps fills in its own
default. A component that holds no SDK has nobody to fill that gap: it talks
HTTP directly because it must see the provider's answer verbatim (an
evaluation harness recording raw reasoning, a benchmark that must not share the
funnel's response cache). Copying the public API hosts into such a component is
the second, unreviewed path to a provider that the plugin gate
(``scripts/check_no_plugin_llm_keys.py``) forbids, so the roots live here,
beside the resolver they complete, and every provider resolves through one
function.

Two wire protocols cover the six providers: Chat Completions (OpenAI itself,
the ``/v1`` surface of a vLLM or Ollama server, Gemini's OpenAI-compatible
endpoint, the Hugging Face inference router) and Anthropic's Messages API.

A provider that cannot be reached this way resolves to ``None`` rather than to
a guess: vLLM without a configured server (it has no default address; its
``:8000`` is this backend's port), and Anthropic served through Bedrock or
Vertex, which authenticate through a cloud credential chain a plain HTTP
client does not carry.

**SSRF note.** The endpoint always comes from deployment configuration or the
constants below, never from a caller: a caller names a provider id from
:data:`~baselith.core.services.llm.policy.SUPPORTED_PROVIDERS` and, for vLLM, a
model the configured servers are asked about, so nothing a request carries
can aim the client at another host. Credentials are not part of the answer;
read them at call time with :func:`~baselith.core.services.llm.runtime.api_key_for`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from baselith.core.services.llm.policy import SUPPORTED_PROVIDERS
from baselith.core.services.llm.runtime import api_base_for, api_key_for

if TYPE_CHECKING:
    from baselith.core.config.services import LLMConfig

#: The request shape a direct client must speak at the resolved endpoint.
WireProtocol = Literal["chat_completions", "anthropic_messages"]

#: Public API roots of the hosted providers, with the protocol spoken there.
HOSTED_API_ROOTS: Final[Mapping[str, tuple[WireProtocol, str]]] = {
    "openai": ("chat_completions", "https://api.openai.com/v1"),
    "anthropic": ("anthropic_messages", "https://api.anthropic.com"),
    # Gemini's OpenAI-compatible surface, not the native generateContent API.
    "gemini": (
        "chat_completions",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    # The Inference Providers router, OpenAI-compatible for chat models.
    "huggingface": ("chat_completions", "https://router.huggingface.co/v1"),
}

#: Ollama's default listen address, used when nothing is configured.
DEFAULT_OLLAMA_BASE: Final[str] = "http://localhost:11434"

#: Hosted providers whose configured ``api_base`` speaks the same protocol as
#: the root above (a gateway, a proxy), so it replaces the root. Gemini's and
#: Hugging Face's configured bases address their SDKs' native APIs, not the
#: OpenAI-compatible surface a direct client talks to, so they never do.
_BASE_OVERRIDES_ROOT: Final[frozenset[str]] = frozenset({"openai", "anthropic"})


@dataclass(frozen=True)
class DirectEndpoint:
    """Where to send a provider call, and in which protocol.

    Attributes:
        provider: The provider id the endpoint serves.
        protocol: The wire protocol spoken at ``base_url``.
        base_url: The protocol root without a trailing slash: the Chat
            Completions root (``.../v1``, ``/chat/completions`` is appended by
            the client) or the Anthropic API root (``/v1/messages`` appended).
    """

    provider: str
    protocol: WireProtocol
    base_url: str


def ollama_root(config: LLMConfig) -> str:
    """The Ollama server this deployment talks to, without ``/v1``.

    Resolved exactly as the runtime resolves it
    (:func:`~baselith.core.services.llm.runtime.api_base_for`), so a direct
    client and a policy-routed call never dial two different servers.
    ``OLLAMA_HOST`` is conventionally written bare (``box:11434``), so a
    missing scheme is filled in rather than treated as a broken value.

    Args:
        config: The central LLM configuration.

    Returns:
        str: The server root, e.g. ``http://localhost:11434``.
    """
    configured = (api_base_for(config, "ollama") or "").strip().rstrip("/")
    if not configured:
        return DEFAULT_OLLAMA_BASE
    if "://" not in configured:
        configured = f"http://{configured}"
    return configured.removesuffix("/v1")


def _vllm_root(config: LLMConfig, model: str | None) -> str | None:
    """The ``/v1`` root of the vLLM server serving *model*, as the funnel picks it."""
    from baselith.core.services.llm.governed import _vllm_base_for

    return _vllm_base_for(config, model, api_key_for(config, "vllm"))


def direct_endpoint(
    config: LLMConfig, provider: str, model: str | None = None
) -> DirectEndpoint | None:
    """The endpoint a direct client should call for *provider*.

    Blocking for vLLM with several servers (``LLM_VLLM_ENDPOINTS``): the
    server that serves *model* is found by asking each one, through the same
    cached registry the vLLM provider routes with. Call it off the event loop
    (``asyncio.to_thread``) on a request path.

    Args:
        config: The central LLM configuration.
        provider: One of :data:`~baselith.core.services.llm.policy.SUPPORTED_PROVIDERS`.
        model: The model the call will name. Only vLLM uses it, to pick the
            server among several; without it the first server answers.

    Returns:
        DirectEndpoint | None: The endpoint, or ``None`` when the provider is
        unknown or cannot be reached by a plain HTTP client on this
        deployment (no vLLM server configured, Anthropic on Bedrock/Vertex).
    """
    if provider not in SUPPORTED_PROVIDERS:
        return None
    if provider == "ollama":
        return DirectEndpoint(provider, "chat_completions", f"{ollama_root(config)}/v1")
    if provider == "vllm":
        root = _vllm_root(config, model)
        return DirectEndpoint(provider, "chat_completions", root) if root else None
    if provider == "anthropic" and config.anthropic_backend != "api":
        return None
    protocol, root = HOSTED_API_ROOTS[provider]
    configured = (
        api_base_for(config, provider) if provider in _BASE_OVERRIDES_ROOT else None
    )
    return DirectEndpoint(provider, protocol, (configured or root).rstrip("/"))


__all__ = [
    "DEFAULT_OLLAMA_BASE",
    "HOSTED_API_ROOTS",
    "DirectEndpoint",
    "WireProtocol",
    "direct_endpoint",
    "ollama_root",
]
