"""Plugin smoke tests.

These intentionally stay shallow: Baselithbot's ``__init__`` wires up a dozen
stores, bootstraps bundled skills, and registers chat-command handlers.
The goal here is to catch import-level breakage and metadata regressions,
not to run the agent.
"""

from __future__ import annotations

import importlib

import pytest


class TestImportSurface:
    def test_plugin_module_imports(self) -> None:
        module = importlib.import_module("plugins.baselithbot.plugin")
        assert hasattr(module, "BaselithbotPlugin")

    def test_public_api_reexports(self) -> None:
        pkg = importlib.import_module("plugins.baselithbot")
        required = {
            "BaselithbotPlugin",
            "BaselithbotAgent",
            "BaselithbotResult",
            "BaselithbotTask",
            "StealthConfig",
            "ComputerUseConfig",
            "AuditLogger",
            "ChannelRegistry",
            "SessionManager",
            "ChatCommandRouter",
            "Skill",
            "SkillRegistry",
            "CronScheduler",
            "CanvasSurface",
            "UsageLedger",
            "WorkspaceManager",
        }
        missing = required - set(pkg.__all__)
        assert not missing, f"missing from __all__: {missing}"


class TestPluginMetadata:
    def test_plugin_can_instantiate(self, state_dir: str) -> None:
        # Plugin __init__ bootstraps stores under state_dir; heavy optional
        # deps (playwright) should be lazy-imported, so construction alone
        # must not fail on a clean test host.
        pytest.importorskip("fastapi")
        from plugins.baselithbot.plugin import BaselithbotPlugin

        plugin = BaselithbotPlugin(state_dir=state_dir)
        assert plugin is not None
        # The agent is lazy — it gets created on initialize().
        assert plugin.agent is None
