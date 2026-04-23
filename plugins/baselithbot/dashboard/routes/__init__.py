"""Route registrars for the Baselithbot dashboard REST API."""

from __future__ import annotations

from plugins.baselithbot.dashboard.routes.agents import register_agents_routes
from plugins.baselithbot.dashboard.routes.approvals import register_approvals_routes
from plugins.baselithbot.dashboard.routes.audit import register_audit_routes
from plugins.baselithbot.dashboard.routes.canvas import register_canvas_routes
from plugins.baselithbot.dashboard.routes.channels import register_channels_routes
from plugins.baselithbot.dashboard.routes.computer_use import register_computer_use_routes
from plugins.baselithbot.dashboard.routes.desktop import register_desktop_routes
from plugins.baselithbot.dashboard.routes.diagnostics import register_diagnostics_routes
from plugins.baselithbot.dashboard.routes.events import register_events_routes
from plugins.baselithbot.dashboard.routes.models import register_models_routes
from plugins.baselithbot.dashboard.routes.provider_keys import register_provider_keys_routes
from plugins.baselithbot.dashboard.routes.registry import register_registry_routes
from plugins.baselithbot.dashboard.routes.replay import register_replay_routes
from plugins.baselithbot.dashboard.routes.run_task import register_run_task_routes
from plugins.baselithbot.dashboard.routes.sessions import register_session_routes
from plugins.baselithbot.dashboard.routes.stealth import register_stealth_routes
from plugins.baselithbot.dashboard.routes.workspaces import register_workspaces_routes

__all__ = [
    "register_agents_routes",
    "register_approvals_routes",
    "register_audit_routes",
    "register_canvas_routes",
    "register_channels_routes",
    "register_computer_use_routes",
    "register_desktop_routes",
    "register_diagnostics_routes",
    "register_events_routes",
    "register_models_routes",
    "register_provider_keys_routes",
    "register_registry_routes",
    "register_replay_routes",
    "register_run_task_routes",
    "register_session_routes",
    "register_stealth_routes",
    "register_workspaces_routes",
]
