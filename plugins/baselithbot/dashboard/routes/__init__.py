"""Route registrars for the Baselithbot dashboard REST API."""

from __future__ import annotations

from .agents import register_agents_routes
from .approvals import register_approvals_routes
from .audit import register_audit_routes
from .canvas import register_canvas_routes
from .channels import register_channels_routes
from .computer_use import register_computer_use_routes
from .diagnostics import register_diagnostics_routes
from .events import register_events_routes
from .models import register_models_routes
from .provider_keys import register_provider_keys_routes
from .registry import register_registry_routes
from .replay import register_replay_routes
from .run_task import register_run_task_routes
from .sessions import register_session_routes
from .stealth import register_stealth_routes
from .workspaces import register_workspaces_routes

__all__ = [
    "register_agents_routes",
    "register_approvals_routes",
    "register_audit_routes",
    "register_canvas_routes",
    "register_channels_routes",
    "register_computer_use_routes",
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
