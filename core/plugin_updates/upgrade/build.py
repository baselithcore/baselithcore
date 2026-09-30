"""Assemble the upgrade instructions served with an update notice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.config.plugin_updates import PluginUpdateConfig

from ..models import SystemUpdate
from ..upgrade_models import (
    InstallMethod,
    PluginBoundsIssue,
    PluginInstallGuidance,
    UpgradeInstructions,
    UpgradeStep,
)
from .compat import plugin_compatibility
from .custom import render_instructions
from .templates import (
    TemplateContext,
    backup_step,
    default_steps,
    distribution_step,
    post_checks,
)

#: Where Kubernetes mounts the pod's namespace.
NAMESPACE_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


@dataclass(frozen=True)
class Deployment:
    """What the instructions need to know about this deployment."""

    method: InstallMethod
    detected: bool
    framework_version: str
    distribution: str | None = None
    namespace: str | None = None
    base_url: str | None = None


def read_namespace(path: Path = NAMESPACE_FILE) -> str | None:
    """The pod's namespace when running in Kubernetes, else None."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    safe = text.replace("-", "").replace(".", "").isalnum()
    return text if text and safe and len(text) <= 63 else None


def _safe_https(url: str | None) -> str | None:
    return url if url and url.startswith("https://") else None


def build_upgrade_instructions(
    system: SystemUpdate,
    config: PluginUpdateConfig,
    deployment: Deployment,
    bounds: list[PluginBoundsIssue] | None,
) -> UpgradeInstructions | None:
    """Version-specific upgrade instructions, or None when nothing is newer.

    The steps install the next stop of ``system.upgrade_path`` (the latest
    release for a direct upgrade); the checklist carries the backup, the
    release notes, the plugin compatibility check and the post-upgrade checks.
    A downstream distribution (``deployment.distribution``) gets no public core
    steps: one step says its own procedure applies (method ``custom`` shows it).

    Args:
        system: The notice being served.
        config: The update settings (instructions file, guide link).
        deployment: The installation method and what is known about it.
        bounds: The installed plugins' declared core bounds, or None when they
            could not be read (the plugin check is then reported as not
            computed).
    """
    latest = system.latest
    if not system.available or latest is None:
        return None
    path = list(system.upgrade_path)
    target = path[0] if path else latest.version
    ctx = TemplateContext(
        version=target,
        current=system.installed_version,
        repo=system.repo,
        namespace=deployment.namespace or "<namespace>",
        base_url=(deployment.base_url or "<base-url>").rstrip("/"),
    )
    plugins = plugin_compatibility(
        target,
        bounds,
        framework_version=deployment.framework_version,
        core_version=system.installed_version,
        distribution=deployment.distribution,
    )
    custom_text = custom_error = None
    if deployment.method == "custom":
        custom_text, custom_error = render_instructions(
            config.upgrade_instructions_file,
            version=target,
            current=system.installed_version,
        )
    releases_url = f"https://github.com/{system.repo}/releases"
    checklist = [
        backup_step(deployment.method, ctx),
        UpgradeStep(
            id="checklist.release_notes",
            text=f"Read the release notes of every release from "
            f"{system.installed_version} to {target}, for breaking changes and "
            "upgrade notes.",
            url=releases_url,
        ),
        UpgradeStep(
            id="checklist.plugins",
            text="Check that the installed plugins support the target release.",
        ),
        *post_checks(ctx),
    ]
    if deployment.distribution and deployment.method != "custom":
        # The public procedure would replace the distribution with the public
        # release (pip, image) or chart: point at the operator's own instead.
        steps = [distribution_step(deployment.distribution)]
    else:
        steps = default_steps(deployment.method, ctx)
    return UpgradeInstructions(
        method=deployment.method,
        detected=deployment.detected,
        current_version=system.installed_version,
        target_version=target,
        latest_version=latest.version,
        path=path,
        steps=steps,
        checklist=checklist,
        plugins=plugins,
        guide_url=config.upgrade_guide_url,
        release_notes_url=_safe_https(latest.html_url) or releases_url,
        custom_text=custom_text,
        custom_error=custom_error,
        distribution=deployment.distribution,
        image=ctx.image if deployment.method == "docker" else None,
    )


def plugin_install_guidance(
    config: PluginUpdateConfig, method: InstallMethod
) -> PluginInstallGuidance:
    """How a newer plugin release reaches this deployment (never automated)."""
    return PluginInstallGuidance(method=method, guide_url=config.upgrade_guide_url)


__all__ = [
    "NAMESPACE_FILE",
    "Deployment",
    "build_upgrade_instructions",
    "plugin_install_guidance",
    "read_namespace",
]
