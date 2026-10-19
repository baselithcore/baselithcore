"""API Routers plugin package."""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .plugin import ApiRoutersPlugin

__all__ = ["ApiRoutersPlugin"]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "_sse": "routes._sse",
        "admin": "routes.admin",
        "approvals": "routes.approvals",
        "async_runs": "routes.async_runs",
        "chat": "routes.chat",
        "chat_ws": "routes.chat_ws",
        "compliance_artefacts": "routes.compliance_artefacts",
        "console": "routes.console",
        "evaluation": "routes.evaluation",
        "feedback": "routes.feedback",
        "index": "routes.index",
        "privacy": "routes.privacy",
        "prompts": "routes.prompts",
        "runs": "routes.runs",
        "status": "routes.status",
        "tenant": "routes.tenant",
        "webhooks": "routes.webhooks",
    },
    packages=(__name__,),
)
