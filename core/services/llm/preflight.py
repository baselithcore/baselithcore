"""Startup validation of a deployment's LLM posture.

Every other check in this package runs at call time, which is the wrong time
for the failures that matter here. A deployment whose configuration never named
a provider inherits the package default and quietly serves from a local one; a
fallback chain naming a provider with no credentials is decorative until the
day the primary fails, when it turns a degradation into an outage; a local
stage pointed at a host with nothing listening on its port adds a round trip to
every failure and buries the primary's error behind a connection refusal. All
three are visible before the first request, from configuration plus one free
local probe — and all three are invisible afterwards, because the symptom is a
slow success or a misattributed error.

What this deliberately does **not** do is call a hosted provider. A startup
that depends on a third party answering is a startup that a vendor incident can
stop, and a credential check that costs money is one nobody leaves enabled.
Hosted providers are validated from configuration only (is a credential
present); the network probe is reserved for local endpoints, where it is free,
fast and exactly the thing an operator cannot otherwise see.

Modes (``LLM_PREFLIGHT``):

- ``auto`` (default) — ``strict`` when the runtime environment declares itself
  production, ``warn`` otherwise. A laptop keeps booting with a half-filled
  ``.env``; a production rollout does not.
- ``warn`` — log every finding and continue.
- ``strict`` — raise :class:`LLMPreflightError` on any error-severity finding,
  so an incomplete deployment fails its own rollout instead of serving from
  whatever happens to be installed on the host.
- ``off`` — skip entirely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.config.services import LLMConfig

logger = get_logger(__name__)

#: Providers served from a host the deployment itself runs, and therefore the
#: only ones this module may probe over the network.
_LOCAL_PROVIDERS = ("ollama",)

#: The endpoint an Ollama client reaches when nothing is configured.
DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"

Severity = Literal["error", "warning"]


class LLMPreflightError(RuntimeError):
    """Raised in ``strict`` mode when the LLM posture is not deployable."""


@dataclass(frozen=True)
class PreflightFinding:
    """One thing wrong with the deployment's LLM configuration.

    Args:
        severity: ``error`` blocks a strict rollout; ``warning`` never does.
        code: Stable slug, so an operator can grep for it and a dashboard can
            count it without parsing prose.
        message: What is wrong, in the terms an operator configured it in.
        remedy: The setting to change. Every finding has one — a check that
            reports a problem without naming its fix just relocates the
            debugging.
    """

    severity: Severity
    code: str
    message: str
    remedy: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message} — {self.remedy}"


def _explicit(name: str) -> bool:
    """Whether *name* was actually set by the deployment.

    The repository ``.env`` is loaded into ``os.environ`` before any settings
    class binds (see :func:`core.config.env.load_project_env`), so this answers
    "did a human or an orchestrator choose this", not "is a file present".
    """
    return bool(os.environ.get(name, "").strip())


def check_configuration(config: LLMConfig) -> list[PreflightFinding]:
    """Findings derivable from configuration alone. Never touches the network.

    Args:
        config: The deployment's LLM configuration.

    Returns:
        list: Every problem found, most structural first.
    """
    from core.services.llm.runtime import provider_configured

    findings: list[PreflightFinding] = []

    # 1. The inherited default. ``provider`` defaults to a local provider so a
    # laptop works with no configuration at all — which also means a container
    # whose ConfigMap forgot the variable runs local inference against its own
    # localhost, forever, and reports nothing. The default is a convenience,
    # never a deployment decision.
    if not _explicit("LLM_PROVIDER"):
        findings.append(
            PreflightFinding(
                severity="error",
                code="llm_provider_unset",
                message=(
                    f"LLM_PROVIDER is not set: the deployment inherited the "
                    f"package default ({config.provider!r}), which serves every "
                    f"request from a local model on this host"
                ),
                remedy="Set LLM_PROVIDER explicitly (it is never inferred).",
            )
        )

    # 2. The primary's credentials. ``create_provider`` raises on the first
    # request instead — after the deployment is live and taking traffic.
    if not provider_configured(config, config.provider):
        findings.append(
            PreflightFinding(
                severity="error",
                code="primary_provider_unconfigured",
                message=(
                    f"primary provider {config.provider!r} has no usable "
                    f"credentials, so every request will fail at call time"
                ),
                remedy=f"Set the API key for {config.provider!r}.",
            )
        )

    findings.extend(_check_chain(config))
    return findings


def _check_chain(config: LLMConfig) -> list[PreflightFinding]:
    """Findings about ``LLM_FALLBACK_CHAIN``."""
    from core.services.llm._fallback_support import parse_fallback_chain
    from core.services.llm.runtime import provider_configured

    findings: list[PreflightFinding] = []
    spec = getattr(config, "fallback_chain", "") or ""
    if not spec:
        return findings

    try:
        stages = parse_fallback_chain(spec)
    except ValueError as exc:
        # The chain is parsed per request, so a malformed one is an error that
        # only appears under the load it was meant to survive.
        return [
            PreflightFinding(
                severity="error",
                code="chain_malformed",
                message=f"LLM_FALLBACK_CHAIN cannot be parsed: {exc}",
                remedy="Use comma-separated 'provider:model' entries.",
            )
        ]

    for provider, model in stages:
        if not provider_configured(config, provider):
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="chain_stage_unconfigured",
                    message=(
                        f"fallback stage {provider}:{model} has no usable "
                        f"credentials: the chain is decorative and the primary's "
                        f"first outage is an outage"
                    ),
                    remedy=f"Set the API key for {provider!r}, or drop the stage.",
                )
            )
        if provider in _LOCAL_PROVIDERS:
            # Worth stating plainly, because it is the surprise this whole
            # module exists for: the chain's tail decides where inference runs.
            logger.info(
                "llm_preflight_local_stage",
                extra={"provider": provider, "model": model},
            )
    return findings


async def probe_ollama(base_url: str, timeout: float = 2.0) -> set[str] | None:
    """Installed model tags at *base_url*, or ``None`` when unreachable.

    ``GET /api/tags`` is Ollama's own catalog endpoint: free, local, and
    unambiguous. An empty set means a reachable server with nothing pulled,
    which is a different problem from an absent one and must read differently.

    Args:
        base_url: The Ollama endpoint to query.
        timeout: Seconds to wait. Short on purpose — this runs on the startup
            path, where a hung probe is worse than an unanswered question.

    Returns:
        set: The installed tags, or ``None`` when the endpoint did not answer.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception:  # silent-ok: an unreachable endpoint is the answer here
        # Any failure means "cannot confirm", never "confirmed absent": the
        # caller reports unreachability, and unreachability is not a crash.
        return None
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return {
        str(entry.get("name", ""))
        for entry in models
        if isinstance(entry, dict) and entry.get("name")
    }


def _ollama_targets(config: LLMConfig) -> dict[str, set[str]]:
    """Every ``endpoint -> {model}`` pair this deployment may reach.

    Covers the primary, each local fallback stage and the vision provider,
    because all three land on the same host and any of them can be the one
    nobody configured.
    """
    from core.services.llm._fallback_support import parse_fallback_chain
    from core.services.llm.runtime import api_base_for

    targets: dict[str, set[str]] = {}

    def add(endpoint: str | None, model: str) -> None:
        targets.setdefault(endpoint or DEFAULT_OLLAMA_ENDPOINT, set()).add(model)

    if config.provider == "ollama":
        add(api_base_for(config, "ollama"), config.model)
    spec = getattr(config, "fallback_chain", "") or ""
    if spec:
        try:
            stages = parse_fallback_chain(spec)
        except ValueError:
            stages = []  # already reported by _check_chain
        for provider, model in stages:
            if provider == "ollama":
                add(api_base_for(config, "ollama"), model)

    try:
        from core.config.multimodal import get_vision_config

        vision = get_vision_config()
        if vision.provider == "ollama":
            add(vision.ollama_url, vision.ollama_model)
    except Exception:  # pragma: no cover - vision config is optional surface
        logger.debug("llm_preflight_vision_config_unavailable", exc_info=True)
    return targets


async def check_local_endpoints(
    config: LLMConfig, timeout: float = 2.0
) -> list[PreflightFinding]:
    """Findings that need the network — local endpoints only.

    A missing model is reported, not fixed: pulling one is several gigabytes of
    someone else's bandwidth and minutes of a startup path, decided by an
    operator rather than by a process booting.
    """
    findings: list[PreflightFinding] = []
    for endpoint, models in _ollama_targets(config).items():
        installed = await probe_ollama(endpoint, timeout=timeout)
        if installed is None:
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="ollama_unreachable",
                    message=(
                        f"Ollama is configured for {sorted(models)} but "
                        f"{endpoint} did not answer"
                    ),
                    remedy="Start Ollama, or point LLM_OLLAMA_API_BASE elsewhere.",
                )
            )
            continue
        for model in sorted(models):
            if not _tag_installed(model, installed):
                findings.append(
                    PreflightFinding(
                        severity="error",
                        code="ollama_model_missing",
                        message=(
                            f"model {model!r} is not installed at {endpoint} "
                            f"(the first call fails; nothing is pulled for you)"
                        ),
                        remedy=f"Run: ollama pull {model}",
                    )
                )
    return findings


def _tag_installed(model: str, installed: set[str]) -> bool:
    """Whether *model* matches an installed tag.

    Ollama reports fully-qualified tags (``llama3.2:latest``) while a
    configuration usually names the bare model (``llama3.2``), which resolves
    to ``:latest``. Treating those as different would report a model missing
    that is right there.
    """
    if model in installed:
        return True
    if ":" in model:
        return False
    return f"{model}:latest" in installed


def _resolve_mode(configured: str) -> str:
    """Resolve ``auto`` against the declared runtime environment."""
    if configured != "auto":
        return configured
    try:
        from core.config.environment import is_production_env

        return "strict" if is_production_env() else "warn"
    except Exception:  # silent-ok: an unreadable environment means "not production"
        return "warn"


async def run_llm_preflight(
    config: LLMConfig | None = None, *, timeout: float = 2.0
) -> list[PreflightFinding]:
    """Validate the LLM posture and report it. The startup entry point.

    Args:
        config: Configuration override (the process-wide one by default).
        timeout: Per-endpoint probe timeout.

    Returns:
        list: Every finding, already logged.

    Raises:
        LLMPreflightError: In ``strict`` mode, when any finding is an error.
    """
    if config is None:
        from core.config import get_llm_config

        config = get_llm_config()

    mode = _resolve_mode(str(getattr(config, "preflight", "auto")))
    if mode == "off":
        return []

    findings = check_configuration(config)
    findings.extend(await check_local_endpoints(config, timeout=timeout))

    if not findings:
        logger.info(
            "llm_preflight_ok",
            extra={"provider": config.provider, "model": config.model},
        )
        return findings

    errors = [f for f in findings if f.severity == "error"]
    for finding in findings:
        log = logger.error if finding.severity == "error" else logger.warning
        log(
            "llm_preflight_finding",
            extra={
                "code": finding.code,
                "detail": finding.message,
                "remedy": finding.remedy,
            },
        )
    if mode == "strict" and errors:
        raise LLMPreflightError(
            "LLM preflight failed: " + "; ".join(str(f) for f in errors)
        )
    return findings


__all__ = [
    "LLMPreflightError",
    "PreflightFinding",
    "check_configuration",
    "check_local_endpoints",
    "probe_ollama",
    "run_llm_preflight",
]
