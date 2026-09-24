"""Startup probe of the vLLM servers a deployment depends on.

Split from :mod:`core.services.llm.preflight` (module size cap) and following
its rule: a network probe is allowed only against infrastructure the deployment
runs itself, where it is free. A vLLM server is exactly that.

The failure this exists for is the one vLLM makes easy: the server registers
the model under ``--served-model-name`` (or, without it, under the full model
path), and ``LLM_MODEL`` names it some other way. Every call then fails with a
404 that reads like an outage. ``GET /v1/models`` settles it before the first
request, and the finding lists what the server does serve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from core.observability.logging import get_logger
from core.observability.redaction import redact_url_credentials

if TYPE_CHECKING:
    import httpx

    from core.config.services import LLMConfig
    from core.services.llm.preflight import PreflightFinding

logger = get_logger(__name__)

__all__ = ["VLLMProbe", "check_vllm_endpoints", "probe_vllm", "vllm_targets"]

ProbeStatus = Literal["ok", "unauthorized", "unreachable"]


@dataclass(frozen=True)
class VLLMProbe:
    """What one vLLM endpoint answered.

    Args:
        status: ``ok`` with a model catalog, ``unauthorized`` when the server
            rejected the key (it is up — a different fix from being down), or
            ``unreachable`` for anything else.
        models: The served model ids; empty unless ``status`` is ``ok``.
    """

    status: ProbeStatus
    models: set[str] = field(default_factory=set)


async def probe_vllm(
    base_url: str,
    api_key: str | None,
    timeout: float = 2.0,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> VLLMProbe:
    """Read the served model catalog at *base_url* (an ``.../v1`` root).

    Args:
        base_url: The normalised OpenAI-compatible root.
        api_key: The server's key, or ``None`` for a keyless server.
        timeout: Seconds to wait; short, since this runs on the boot path.
        transport: Test seam for an in-process transport.

    Returns:
        VLLMProbe: The outcome. Never raises.
    """
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models", headers=headers
            )
            if response.status_code in (401, 403):
                return VLLMProbe("unauthorized")
            response.raise_for_status()
            payload: Any = response.json()
    except Exception:  # silent-ok: an unanswered probe is the finding itself
        return VLLMProbe("unreachable")
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    return VLLMProbe(
        "ok",
        {
            str(entry["id"])
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        },
    )


def vllm_targets(config: LLMConfig) -> dict[str, set[str]]:
    """Every ``endpoint -> models`` the primary and fallback chain send to vLLM.

    A stage whose endpoint is unset is skipped here: configuration findings
    already report it, and probing nothing would only repeat them.
    """
    from core.services.llm._fallback_support import parse_fallback_chain
    from core.services.llm.providers.vllm_provider import normalize_vllm_base_url
    from core.services.llm.runtime import api_base_for

    models: list[str] = []
    if config.provider == "vllm":
        models.append(config.model)
    try:
        stages = parse_fallback_chain(getattr(config, "fallback_chain", "") or "")
    except ValueError:
        stages = []  # already reported as chain_malformed
    models.extend(model for provider, model in stages if provider == "vllm")

    endpoint = api_base_for(config, "vllm")
    if not models or not endpoint:
        return {}
    return {normalize_vllm_base_url(endpoint): set(models)}


async def check_vllm_endpoints(
    config: LLMConfig, timeout: float = 2.0
) -> list[PreflightFinding]:
    """Findings about the vLLM servers this deployment routes to.

    Args:
        config: The deployment's LLM configuration.
        timeout: Per-endpoint probe timeout.

    Returns:
        list: One finding per unreachable server, rejected key or unserved model.
    """
    from core.services.llm.preflight import PreflightFinding
    from core.services.llm.runtime import api_key_from_config

    secret = api_key_from_config(config, "vllm")
    api_key = secret.get_secret_value() if secret is not None else None
    findings: list[PreflightFinding] = []
    for target, models in vllm_targets(config).items():
        probe = await probe_vllm(target, api_key, timeout)
        # Findings are logged and folded into a raised error; basic-auth
        # userinfo in the configured URL must reach neither.
        endpoint = redact_url_credentials(target)
        if probe.status == "unreachable":
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_unreachable",
                    message=(
                        f"vLLM is configured for {sorted(models)} but "
                        f"{endpoint}/models did not answer"
                    ),
                    remedy="Start the vLLM server, or fix LLM_VLLM_API_BASE.",
                )
            )
            continue
        if probe.status == "unauthorized":
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_unauthorized",
                    message=f"vLLM at {endpoint} rejected the configured key",
                    remedy=(
                        "Set LLM_VLLM_API_KEY to the server's --api-key "
                        "(or leave both unset)."
                    ),
                )
            )
            continue
        served = ", ".join(sorted(probe.models)) or "nothing"
        for model in sorted(models - probe.models):
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_model_missing",
                    message=f"model {model!r} is not served at {endpoint}",
                    remedy=(
                        f"The server serves: {served}. Set LLM_MODEL (or the "
                        f"chain stage) to one of them, or restart vLLM with "
                        f"--served-model-name {model}."
                    ),
                )
            )
    return findings
