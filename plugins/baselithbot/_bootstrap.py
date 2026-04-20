"""Bootstrap helpers for ``BaselithbotPlugin``.

Extracted from ``plugin.py`` to keep that module under the 500-line cap.
These functions operate on the plugin instance and its sub-components; they
are intentionally private (leading underscore module name) and should only
be called from ``BaselithbotPlugin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .agents import AgentEntry
from .skills import (
    LocalSkillSpec,
    Skill,
    SkillDraft,
    SkillScope,
    bundled_skills,
    discover_local_skill_specs,
    load_injection_bundle,
    write_workspace_skill,
)

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin

logger = get_logger(__name__)


def register_default_agents(plugin: "BaselithbotPlugin") -> None:
    """Seed the agent registry with built-in system agents.

    System agents have the ``kind=system`` metadata flag and cannot be
    removed through the dashboard API — only custom agents created via
    ``CustomAgentRegistry`` are deletable.
    """

    async def browse_invoker(query: str, context: dict[str, Any]) -> dict[str, Any]:
        return await plugin._flow_handler.handle_browse(query, context)

    async def usage_invoker(_query: str, _context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "agent": "system.usage",
            "result": {
                **plugin._usage.summary(),
                "by_model": plugin._usage.by_model_breakdown(),
            },
        }

    async def canvas_invoker(_query: str, _context: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "agent": "system.canvas",
            "result": plugin._canvas.snapshot(),
        }

    plugin._agent_registry.register(
        AgentEntry(
            name="system.browse",
            description="Autonomous browser navigation via the Baselithbot agent.",
            keywords=["browse", "web", "url", "navigate", "website", "scrape"],
            priority=200,
            metadata={"kind": "system", "handler": "flow.browse"},
        ),
        browse_invoker,
    )
    plugin._agent_registry.register(
        AgentEntry(
            name="system.usage",
            description="Return the current usage ledger summary.",
            keywords=["usage", "tokens", "cost", "spend", "billing"],
            priority=100,
            metadata={"kind": "system", "handler": "usage.summary"},
        ),
        usage_invoker,
    )
    plugin._agent_registry.register(
        AgentEntry(
            name="system.canvas",
            description="Return the current canvas snapshot for UI surfacing.",
            keywords=["canvas", "surface", "paint", "draw"],
            priority=80,
            metadata={"kind": "system", "handler": "canvas.snapshot"},
        ),
        canvas_invoker,
    )


def register_default_cron_jobs(plugin: "BaselithbotPlugin") -> None:
    """Register the maintenance jobs that ship with the plugin."""

    async def prune_pairing_tokens() -> None:
        dropped = plugin._pairing.prune_expired()
        if dropped:
            logger.info("baselithbot_cron_pairing_pruned", dropped=dropped)

    async def prune_inactive_sessions() -> None:
        dropped = plugin._sessions.prune_inactive(ttl_seconds=3600.0)
        if dropped:
            logger.info("baselithbot_cron_sessions_pruned", dropped=dropped)

    async def rescan_workspace_skills() -> None:
        reloaded = plugin.rescan_workspace_skills()
        logger.info("baselithbot_cron_workspace_rescan", reloaded=reloaded)

    async def usage_heartbeat() -> None:
        summary = plugin._usage.summary()
        logger.info("baselithbot_cron_usage_heartbeat", **summary)

    async def prune_replay_history() -> None:
        dropped = plugin._replay.prune_older_than(retention_seconds=14 * 24 * 3600.0)
        if dropped:
            logger.info("baselithbot_cron_replay_pruned", dropped=dropped)

    plugin._cron.add_interval(
        "pairing.prune_tokens",
        prune_pairing_tokens,
        seconds=60.0,
        description="Drop expired node-pairing tokens.",
    )
    plugin._cron.add_interval(
        "sessions.prune_inactive",
        prune_inactive_sessions,
        seconds=300.0,
        description="Evict non-primary sessions idle > 1h.",
    )
    plugin._cron.add_interval(
        "workspace.rescan_skills",
        rescan_workspace_skills,
        seconds=600.0,
        description="Rescan workspace directories for skill changes.",
    )
    plugin._cron.add_interval(
        "usage.heartbeat",
        usage_heartbeat,
        seconds=900.0,
        description="Log aggregate usage ledger summary.",
    )
    plugin._cron.add_interval(
        "replay.prune_history",
        prune_replay_history,
        seconds=6 * 3600.0,
        description="Evict replay runs older than 14 days.",
    )


def purge_skill_on_disk(plugin: "BaselithbotPlugin", skill: Skill) -> bool:
    """Remove the on-disk bundle for ``skill`` when safe.

    Returns ``True`` if a directory was deleted. Only workspace custom
    skills and managed (ClawHub) installs are purged; workspace prompt
    bundles (``AGENTS.md``/``SOUL.md``/``TOOLS.md``) live at the state
    root and are left alone. A :class:`ValueError` is raised when the
    skill's entrypoint escapes the plugin's state directory — defense
    against future-me authoring a skill with a malicious ``entrypoint``.
    """
    import shutil

    entrypoint = skill.entrypoint
    if not entrypoint:
        return False
    kind = skill.metadata.get("kind") if isinstance(skill.metadata, dict) else None
    if skill.scope == SkillScope.WORKSPACE and kind != "custom_skill":
        return False
    path = Path(entrypoint).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    state_root = Path(plugin._state_dir).resolve()
    try:
        resolved.relative_to(state_root)
    except ValueError as exc:
        raise ValueError(
            f"refusing to purge skill outside state_dir: {resolved}"
        ) from exc
    if not resolved.is_dir():
        return False
    shutil.rmtree(resolved)
    logger.info(
        "baselithbot_skill_bundle_purged",
        name=skill.name,
        scope=skill.scope.value,
        path=str(resolved),
    )
    return True


def create_workspace_skill(
    plugin: "BaselithbotPlugin",
    draft: SkillDraft,
    *,
    workspace: str | None = None,
    overwrite: bool = False,
) -> LocalSkillSpec:
    """Persist ``draft`` into the active state dir and re-register scopes."""
    root = Path(plugin._state_dir)
    if workspace:
        ws = plugin._workspaces.get(workspace)
        root = root / "workspaces" / ws.config.name
    spec = write_workspace_skill(draft, root=root, overwrite=overwrite)
    plugin.rescan_workspace_skills()
    return spec


def register_bundled_skills(plugin: "BaselithbotPlugin") -> None:
    """Register baselithbot's native capabilities into the skill registry."""
    for skill in bundled_skills():
        plugin._skills.register(skill)
    logger.info(
        "baselithbot_bundled_skills_registered",
        count=len(bundled_skills()),
    )


def discover_workspace_skills(plugin: "BaselithbotPlugin") -> None:
    """Scan plugin roots for prompt bundles and local custom skills."""
    roots: list[Path] = [Path(plugin._state_dir)]
    for ws in plugin._workspaces.list():
        roots.append(Path(plugin._state_dir) / "workspaces" / ws.config.name)
    plugin._workspace_skill_reports = []
    for root in roots:
        if not root.exists():
            continue
        bundle = load_injection_bundle(root)
        if bundle.sources:
            skill_name = f"workspace.{root.name}.prompt_bundle"
            plugin._skills.register(
                Skill(
                    name=skill_name,
                    version="1.0.0",
                    scope=SkillScope.WORKSPACE,
                    description=(
                        f"Workspace markdown prompt bundle ({len(bundle.sources)} files)."
                    ),
                    entrypoint=str(root),
                    metadata={
                        "kind": "prompt_bundle",
                        "sources": bundle.sources,
                        "prompt_block_chars": len(bundle.to_prompt_block()),
                        "validation": {
                            "status": "verified",
                            "errors": [],
                            "warnings": [],
                            "surfaces": ["chat", "cli"],
                            "tested_on": [],
                        },
                    },
                )
            )
            plugin._workspace_skill_reports.append(
                {
                    "name": skill_name,
                    "kind": "prompt_bundle",
                    "root": str(root),
                    "entrypoint": str(root),
                    "validation": {
                        "status": "verified",
                        "errors": [],
                        "warnings": [],
                        "surfaces": ["chat", "cli"],
                        "tested_on": [],
                    },
                    "files": bundle.sources,
                }
            )
            logger.info(
                "baselithbot_workspace_skill_registered",
                skill=skill_name,
                sources=list(bundle.sources.keys()),
            )

        for spec in discover_local_skill_specs(root):
            report = {
                "name": spec.name,
                "slug": spec.slug,
                "kind": "custom_skill",
                "root": str(root),
                "entrypoint": spec.entrypoint,
                "files": spec.files,
                "validation": spec.validation.model_dump(mode="json"),
            }
            plugin._workspace_skill_reports.append(report)
            if spec.validation.status == "invalid":
                logger.warning(
                    "baselithbot_workspace_skill_invalid",
                    skill=spec.name,
                    entrypoint=spec.entrypoint,
                    errors=spec.validation.errors,
                )
                continue
            plugin._skills.register(
                Skill(
                    name=f"workspace.{root.name}.{spec.slug}",
                    version=spec.version,
                    scope=SkillScope.WORKSPACE,
                    description=spec.description,
                    entrypoint=spec.entrypoint,
                    metadata={
                        "kind": "custom_skill",
                        "files": spec.files,
                        "manifest": spec.manifest,
                        "frontmatter": spec.frontmatter,
                        "validation": spec.validation.model_dump(mode="json"),
                    },
                )
            )
            logger.info(
                "baselithbot_workspace_custom_skill_registered",
                skill=spec.name,
                entrypoint=spec.entrypoint,
                validation=spec.validation.status,
            )


async def autostart_enabled_channels(plugin: "BaselithbotPlugin") -> None:
    """Auto-start every channel flagged ``enabled`` in the config store."""
    for name in plugin._channel_configs.enabled_channels():
        cfg = plugin._channel_configs.get_config(name) or {}
        try:
            await plugin._channels.start(name, cfg)
            logger.info("baselithbot_channel_autostart", channel=name)
        except Exception as exc:
            logger.warning(
                "baselithbot_channel_autostart_failed",
                channel=name,
                error=str(exc),
            )


def apply_model_preferences(plugin: "BaselithbotPlugin") -> None:
    """Push operator-selected vision prefs into the global VisionConfig.

    Also pins the selected vision model onto ``VisionService.DEFAULT_MODELS``
    so non-Ollama providers honor the dashboard choice (the stock service
    hardcodes models per provider). Ollama still flows through
    ``VisionConfig.ollama_model`` which ``_analyze_ollama`` reads each call.
    """
    prefs = plugin._model_prefs.get()
    try:
        from core.config import services as cfg_mod
        from core.services.vision.models import VisionProvider
        from core.services.vision.service import VisionService

        current = cfg_mod.get_vision_config()
        updates: dict[str, Any] = {"provider": prefs.vision_provider}
        if prefs.vision_provider == "ollama":
            updates["ollama_model"] = prefs.vision_model
        cfg_mod._vision_config = current.model_copy(update=updates)

        VisionService.DEFAULT_MODELS[VisionProvider(prefs.vision_provider)] = (
            prefs.vision_model
        )

        logger.info(
            "baselithbot_model_prefs_applied",
            provider=prefs.provider,
            model=prefs.model,
            vision_provider=prefs.vision_provider,
            vision_model=prefs.vision_model,
            failover_entries=len(prefs.failover_chain),
        )
    except Exception as exc:
        logger.warning("baselithbot_model_prefs_apply_failed", error=str(exc))


__all__ = [
    "register_default_agents",
    "register_default_cron_jobs",
    "register_bundled_skills",
    "discover_workspace_skills",
    "autostart_enabled_channels",
    "apply_model_preferences",
]
