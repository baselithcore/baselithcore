"""Shared helpers for the Baselithbot dashboard API test modules.

Not collected by pytest (leading underscore); imported by the
``test_baselithbot_dashboard_*`` siblings.
"""

from __future__ import annotations

import tempfile

from fastapi import FastAPI

from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.plugin import BaselithbotPlugin


def _build_app() -> tuple[FastAPI, BaselithbotPlugin]:
    plugin = BaselithbotPlugin(
        state_dir=tempfile.mkdtemp(prefix="baselithbot-dashboard-tests-")
    )
    app = FastAPI()
    app.include_router(create_router(plugin), prefix="/baselithbot")
    return app, plugin
