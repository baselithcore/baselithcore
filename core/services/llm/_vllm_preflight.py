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

__all__ = [
    "VLLMProbe",
    "check_vllm_endpoints",
    "probe_vllm",
    "probe_vllm_sync",
    "vllm_targets",
]

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

    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/models", headers=_auth_headers(api_key)
            )
            return _read_catalog(response)
    except Exception:  # silent-ok: an unanswered probe is the finding itself
        return VLLMProbe("unreachable")


def probe_vllm_sync(
    base_url: str,
    api_key: str | None,
    timeout: float = 2.0,
    *,
    transport: httpx.BaseTransport | None = None,
) -> VLLMProbe:
    """Synchronous :func:`probe_vllm`, for code that resolves in sync paths.

    Plugins that hold their own SDK client (docheck, agent_jira, wikigen,
    dbview) resolve their endpoint synchronously; this lets them route by model
    across several vLLM servers too. Never raises.
    """
    import httpx

    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(
                f"{base_url.rstrip('/')}/models", headers=_auth_headers(api_key)
            )
            return _read_catalog(response)
    except Exception:  # silent-ok: an unanswered probe is the finding itself
        return VLLMProbe("unreachable")


def _auth_headers(api_key: str | None) -> dict[str, str]:
    """A bearer header when a key is set — never ``Bearer None``."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _read_catalog(response: httpx.Response) -> VLLMProbe:
    """The probe outcome for a ``GET /v1/models`` response."""
    if response.status_code in (401, 403):
        return VLLMProbe("unauthorized")
    response.raise_for_status()
    payload: Any = response.json()
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    return VLLMProbe(
        "ok",
        {
            str(entry["id"])
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        },
    )


def _required_models(config: LLMConfig) -> list[str]:
    """The vLLM models the primary and the fallback chain ask for."""
    from core.services.llm._fallback_support import parse_fallback_chain

    models: list[str] = []
    if config.provider == "vllm":
        models.append(config.model)
    try:
        stages = parse_fallback_chain(getattr(config, "fallback_chain", "") or "")
    except ValueError:
        stages = []  # already reported as chain_malformed
    models.extend(model for provider, model in stages if provider == "vllm")
    return models


def vllm_targets(config: LLMConfig) -> dict[str, set[str]]:
    """Every ``endpoint -> models`` the deployment needs from vLLM.

    With several servers (``LLM_VLLM_ENDPOINTS``) each is probed and a model
    only has to be served by one of them. A deployment that asks vLLM for
    nothing, or configures no server, has nothing to probe — configuration
    findings already report a missing endpoint.
    """
    from core.services.llm.vllm_endpoints import vllm_endpoints

    models = set(_required_models(config))
    endpoints = vllm_endpoints(config)
    if not models or not endpoints:
        return {}
    return {endpoint: set(models) for endpoint in endpoints}


async def check_vllm_endpoints(
    config: LLMConfig, timeout: float = 2.0
) -> list[PreflightFinding]:
    """Findings about the vLLM servers this deployment routes to.

    Every server is probed; each required model must be served by at least one
    reachable server (calls are routed by model across them).

    Args:
        config: The deployment's LLM configuration.
        timeout: Per-endpoint probe timeout.

    Returns:
        list: One finding per unreachable server, rejected key, and model no
        reachable server serves.
    """
    from core.services.llm.preflight import PreflightFinding
    from core.services.llm.runtime import api_key_for

    targets = vllm_targets(config)
    if not targets:
        return []
    models = next(iter(targets.values()))
    # ``api_key_for``: the dedicated key, else one stored from the console —
    # the same key the provider will send, so the probe answers for it.
    secret = api_key_for(config, "vllm")
    api_key = secret.get_secret_value() if secret is not None else None
    findings: list[PreflightFinding] = []
    served: dict[str, set[str]] = {}
    silent: list[str] = []
    for target in targets:
        probe = await probe_vllm(target, api_key, timeout)
        # Findings are logged and folded into a raised error; basic-auth
        # userinfo in the configured URL must reach neither.
        endpoint = redact_url_credentials(target)
        if probe.status == "unreachable":
            silent.append(endpoint)
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_unreachable",
                    message=f"vLLM server {endpoint}/models did not answer",
                    remedy=(
                        "Start the vLLM server, or fix LLM_VLLM_ENDPOINTS / "
                        "LLM_VLLM_API_BASE."
                    ),
                )
            )
        elif probe.status == "unauthorized":
            silent.append(endpoint)
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_unauthorized",
                    message=f"vLLM at {endpoint} rejected the configured key",
                    remedy=(
                        "Set LLM_VLLM_API_KEY to the servers' --api-key "
                        "(or leave both unset)."
                    ),
                )
            )
        else:
            served[endpoint] = set(probe.models)
    available = sorted({m for names in served.values() for m in names})
    unaccounted = sorted(models - set(available))
    if silent:
        # A server that did not answer may be the one serving these: say so
        # on its finding instead of calling them missing.
        if unaccounted and len(targets) > 1:
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="vllm_models_unverified",
                    message=(
                        f"cannot confirm {unaccounted} are served: "
                        f"{', '.join(silent)} did not answer"
                    ),
                    remedy="Bring the server up, then re-run the check.",
                )
            )
        return findings
    listing = ", ".join(available) or "nothing"
    for model in unaccounted:
        findings.append(
            PreflightFinding(
                severity="error",
                code="vllm_model_missing",
                message=(
                    f"model {model!r} is not served by any reachable vLLM "
                    f"server ({', '.join(served) or 'none reachable'})"
                ),
                remedy=(
                    f"The servers serve: {listing}. Set LLM_MODEL (or the chain "
                    f"stage) to one of them, or start a server with "
                    f"--served-model-name {model} and add it to "
                    "LLM_VLLM_ENDPOINTS."
                ),
            )
        )
    return findings
