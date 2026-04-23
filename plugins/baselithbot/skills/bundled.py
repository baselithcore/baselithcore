"""Built-in skill catalog exposed by baselithbot at plugin startup."""

from __future__ import annotations

from plugins.baselithbot.skills.registry import Skill, SkillScope

_VERSION = "1.0.0"


def bundled_skills() -> list[Skill]:
    """Return the native capabilities of baselithbot as Skill entries."""
    return [
        Skill(
            name="baselithbot.browser",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Autonomous, stealth-capable browser automation.",
            entrypoint="plugins.baselithbot.browser.agent:BaselithbotAgent",
            metadata={"category": "automation", "tags": ["browser", "stealth"]},
        ),
        Skill(
            name="baselithbot.computer_use",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Anthropic Computer Use loop with screenshot / mouse / keyboard tools.",
            entrypoint="plugins.baselithbot.computer_use.config:ComputerUseConfig",
            metadata={"category": "automation", "tags": ["computer-use", "screenshot"]},
        ),
        Skill(
            name="baselithbot.shell",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Sandboxed shell command execution with timeout + redaction.",
            entrypoint="plugins.baselithbot.computer_use.shell_exec:run_shell",
            metadata={"category": "system", "tags": ["shell", "exec"]},
        ),
        Skill(
            name="baselithbot.filesystem",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Read/write/list filesystem operations scoped to workspace roots.",
            entrypoint="plugins.baselithbot.computer_use.filesystem",
            metadata={"category": "system", "tags": ["fs"]},
        ),
        Skill(
            name="baselithbot.canvas",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="A2UI canvas rendering (text, buttons, images, lists).",
            entrypoint="plugins.baselithbot.canvas:CanvasSurface",
            metadata={"category": "ui", "tags": ["canvas", "a2ui"]},
        ),
        Skill(
            name="baselithbot.voice",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="OS-native text-to-speech fallback.",
            entrypoint="plugins.baselithbot.voice:SystemTTS",
            metadata={"category": "ui", "tags": ["voice", "tts"]},
        ),
        Skill(
            name="baselithbot.channels",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Multi-channel messaging gateway (email, slack, discord, telegram, SMS, webhooks).",
            entrypoint="plugins.baselithbot.channels:ChannelRegistry",
            metadata={
                "category": "messaging",
                "tags": ["channels", "inbound", "outbound"],
            },
        ),
        Skill(
            name="baselithbot.sessions",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Persistent conversational sessions with history + slash commands.",
            entrypoint="plugins.baselithbot.sessions:SessionManager",
            metadata={"category": "conversation", "tags": ["sessions"]},
        ),
        Skill(
            name="baselithbot.cron",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Cron-style scheduler for recurring jobs.",
            entrypoint="plugins.baselithbot.cron.scheduler:CronScheduler",
            metadata={"category": "scheduling", "tags": ["cron"]},
        ),
        Skill(
            name="baselithbot.node_pairing",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Tailscale-aware node pairing with revocable tokens.",
            entrypoint="plugins.baselithbot.nodes:NodePairing",
            metadata={"category": "networking", "tags": ["pairing", "tailscale"]},
        ),
        Skill(
            name="baselithbot.doctor",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Environment/config health probe.",
            entrypoint="plugins.baselithbot.diagnostics.doctor:run_doctor",
            metadata={"category": "diagnostics", "tags": ["health"]},
        ),
        Skill(
            name="baselithbot.workspaces",
            version=_VERSION,
            scope=SkillScope.BUNDLED,
            description="Isolated workspace state (channels/sessions/skills per workspace).",
            entrypoint="plugins.baselithbot.workspace:WorkspaceManager",
            metadata={"category": "state", "tags": ["workspace"]},
        ),
    ]


__all__ = ["bundled_skills"]
