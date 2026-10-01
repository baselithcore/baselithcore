"""Data models for version-specific upgrade instructions.

The framework never upgrades itself or installs a plugin from the console or
the API. When a newer release exists, the administrator is told **how** to
upgrade this deployment with the standard tools of its installation method
(Helm, Docker Compose, pip, a source checkout, or the operator's own
procedure). These models are
that guidance: the same payload feeds the console, the API and any CLI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: How a deployment was installed, which decides the upgrade instructions.
InstallMethod = Literal["helm", "docker", "pip", "source", "custom"]
#: Every valid ``SYSTEM_INSTALL_METHOD`` value, in documentation order.
INSTALL_METHODS: tuple[InstallMethod, ...] = (
    "helm",
    "docker",
    "pip",
    "source",
    "custom",
)


class UpgradeStep(BaseModel):
    """One step of an upgrade or one pre/post-upgrade checklist item.

    ``id`` is stable (a console translates it); ``text`` is the English
    wording; ``command`` is shown verbatim with a copy button and is never
    executed by the framework; ``url`` is an optional https reference.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    command: str | None = None
    url: str | None = None


class PluginBoundsIssue(BaseModel):
    """An installed plugin whose declared core bounds exclude the target."""

    model_config = ConfigDict(frozen=True)

    plugin: str
    version: str | None = None
    min_core_version: str | None = None
    max_core_version: str | None = None


class PluginCompatibility(BaseModel):
    """Installed plugins checked against the target core version.

    ``checked`` is False when the check cannot be computed honestly here:
    plugin manifests declare ``min_core_version``/``max_core_version`` against
    ``core._version``, and a downstream distribution versions that file
    independently of the public core release (``reason`` says so).
    """

    model_config = ConfigDict(frozen=True)

    checked: bool
    target_version: str
    incompatible: list[PluginBoundsIssue] = []
    reason: str | None = None


class UpgradeInstructions(BaseModel):
    """How to upgrade this deployment to the next core release.

    ``target_version`` is the release the steps install: the first stop of
    ``path`` when the jump crosses a major version (upgrade one stop at a
    time), otherwise the latest release. ``path`` lists every stop in order,
    ending at the latest release, and is empty for a direct upgrade.
    ``custom_text`` is the operator's own procedure (method ``custom``) with
    its placeholders filled in: untrusted text, to be shown without raw HTML.
    ``distribution`` names the downstream distribution this deployment runs
    (``core._version.__distribution__``); its steps are then only a pointer to
    the operator's own procedure, since the public core procedure would
    replace the distribution with the public release. ``image`` is the
    published container image of the target release, which the Docker steps
    name (null for the other methods).
    """

    model_config = ConfigDict(frozen=True)

    method: InstallMethod
    detected: bool
    current_version: str
    target_version: str
    latest_version: str
    path: list[str] = []
    steps: list[UpgradeStep] = []
    checklist: list[UpgradeStep] = []
    plugins: PluginCompatibility
    guide_url: str | None = None
    release_notes_url: str | None = None
    custom_text: str | None = None
    custom_error: str | None = None
    distribution: str | None = None
    image: str | None = None


class PluginInstallGuidance(BaseModel):
    """How a newer plugin release reaches this deployment.

    No console action and no command installs a signed plugin release yet, so
    this carries only what is true: the installation method (which decides
    where plugins come from) and the operator's instructions link.
    ``automated`` is always False; it exists so a client never has to guess.
    """

    model_config = ConfigDict(frozen=True)

    method: InstallMethod
    guide_url: str | None = None
    automated: bool = False


__all__ = [
    "INSTALL_METHODS",
    "InstallMethod",
    "PluginBoundsIssue",
    "PluginCompatibility",
    "PluginInstallGuidance",
    "UpgradeInstructions",
    "UpgradeStep",
]
