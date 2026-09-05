#!/usr/bin/env python
"""Export the BaselithCore OpenAPI schema to a JSON file.

The emitted schema is the single source of truth for client SDKs: it can be fed
to ``openapi-typescript``, ``openapi-python-client``, or any other generator,
and is used to verify that the hand-written SDKs under ``sdk/`` still match the
server contract.

Usage::

    python scripts/export_openapi.py                 # -> sdk/openapi.json
    python scripts/export_openapi.py path/to/out.json

The app is only *constructed* (not served), so no network/DB connections are
opened. Minimal env defaults are injected so construction succeeds outside a
fully-configured deployment.

Construction alone is not the whole contract: the ``api_routers`` plugin mounts
``/prompts``, ``/chat/ws``, ``/agent/*``, ``/webhooks``, ``/privacy``,
``/compliance``, ``/runs`` and ``/approvals`` from the FastAPI *lifespan*, and
several of those are feature-gated. This exporter includes those routers
explicitly with every gate opened, so the emitted schema describes the full
server surface rather than the subset a bare ``create_app()`` shows.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

# Minimal config so create_app() can build without a real deployment. Set before
# importing the app factory (Pydantic settings read the environment at import).
os.environ.setdefault("SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("AUTH_REQUIRED", "false")

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "sdk" / "openapi.json"


# Routers the api_routers plugin mounts at lifespan only when their flag is on.
ROUTE_GATES = (
    "MCP_HTTP_TRANSPORT_ENABLED",
    "WEBHOOKS_ENABLED",
    "PRIVACY_ENABLED",
    "COMPLIANCE_ENABLED",
    "ENABLE_FEEDBACK",
    "ORCHESTRATOR_CHECKPOINT_ENABLED",
)


def _include_lifespan_routers(app) -> None:
    """Add the plugin-mounted routers a bare ``create_app()`` has not seen."""
    from plugins.api_routers.plugin import ApiRoutersPlugin

    plugin = ApiRoutersPlugin()
    prefix = plugin.get_router_prefix()
    for router in plugin.get_routers():
        app.include_router(router, prefix=prefix)


def export(out_path: Path) -> dict:
    """Build the app, extract its OpenAPI schema, and write it to ``out_path``."""
    for gate in ROUTE_GATES:
        os.environ.setdefault(gate, "true")

    from core.api.factory import create_app

    app = create_app()
    _include_lifespan_routers(app)
    schema = app.openapi()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return schema


def main(argv: list[str]) -> int:
    out_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    schema = export(out_path)
    paths = schema.get("paths", {})
    print(f"Wrote {out_path} ({len(paths)} paths, OpenAPI {schema.get('openapi')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
