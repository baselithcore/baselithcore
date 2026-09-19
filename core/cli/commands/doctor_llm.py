"""Doctor checks for where inference actually runs.

``baselith doctor`` reported the configured provider and stopped there, which
answers the easy half of the question. The half that surprises operators is
everything the configuration does *next*: the fallback chain that decides where
a failure sends inference, and the vision provider, which is configured
separately and lands on a different host more often than anyone expects.

These checks only read configuration and probe **local** endpoints — the same
rule :mod:`core.services.llm.preflight` follows, and for the same reason: a
diagnostic command must not spend money or depend on a vendor being up.
"""

from __future__ import annotations

import asyncio

from core.cli.commands.doctor_checks import CheckResult


def _findings(prefix: str) -> list[str]:
    """Preflight findings whose code starts with *prefix*, as display lines."""
    from core.config import get_llm_config
    from core.services.llm.preflight import check_configuration

    return [
        f"{f.message} — {f.remedy}"
        for f in check_configuration(get_llm_config())
        if f.code.startswith(prefix)
    ]


def check_llm_fallback_chain() -> CheckResult:
    """Report the fallback chain, and whether it could actually serve.

    A chain is invisible until the primary fails, which is the worst moment to
    discover that its stages have no credentials — or that its tail is a local
    model on this host.
    """
    try:
        from core.config import get_llm_config

        config = get_llm_config()
        chain = getattr(config, "fallback_chain", "") or ""
        if not chain:
            return CheckResult("LLM Fallback", True, "No fallback chain configured")

        problems = _findings("chain")
        if problems:
            return CheckResult("LLM Fallback", False, problems[0], "Fix or drop it.")

        local = [s for s in chain.split(",") if s.strip().startswith("ollama:")]
        if local:
            return CheckResult(
                "LLM Fallback",
                True,
                f"Chain: {chain}",
                "A primary failure runs inference locally on this host.",
                "warn",
            )
        return CheckResult("LLM Fallback", True, f"Chain: {chain}")
    except Exception as e:
        return CheckResult("LLM Fallback", False, f"Error: {e}")


def check_llm_local_endpoints() -> CheckResult:
    """Probe every local endpoint this deployment may reach, models included.

    Covers the primary, each local fallback stage and the vision provider. A
    missing model matters as much as an unreachable server: nothing is pulled
    automatically, so the first call fails on a host that looks configured.
    """
    try:
        from core.config import get_llm_config
        from core.services.llm.preflight import check_local_endpoints

        findings = asyncio.run(check_local_endpoints(get_llm_config()))
        if not findings:
            return CheckResult("LLM Local Models", True, "All local targets reachable")
        first = findings[0]
        extra = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
        return CheckResult(
            "LLM Local Models", False, f"{first.message}{extra}", first.remedy
        )
    except Exception as e:
        return CheckResult("LLM Local Models", False, f"Error: {e}")


__all__ = ["check_llm_fallback_chain", "check_llm_local_endpoints"]
