"""Stealth mode utilities for Baselithbot.

Applies anti-bot detection countermeasures to a Playwright ``BrowserContext``:
user-agent rotation, navigator.webdriver masking, language/timezone spoofing,
canvas/WebGL fingerprint perturbation.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from core.observability.logging import get_logger

from .types import StealthConfig

logger = get_logger(__name__)


def _build_fingerprint_init_script(config: StealthConfig) -> str:
    languages = [entry for entry in config.spoof_languages if entry]
    primary_language = languages[0] if languages else ""
    webdriver_line = (
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        if config.mask_webdriver
        else ""
    )
    languages_block = (
        f"""
    Object.defineProperty(navigator, 'languages', {{
        get: () => {json.dumps(languages)}
    }});
    Object.defineProperty(navigator, 'language', {{
        get: () => {json.dumps(primary_language)}
    }});
"""
        if languages
        else ""
    )

    return f"""
(() => {{
    {webdriver_line}
    {languages_block}
    Object.defineProperty(navigator, 'plugins', {{
        get: () => [1, 2, 3, 4, 5].map(() => ({{}}))
    }});
    const origGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {{
        if (parameter === 37445) {{ return 'Intel Inc.'; }}
        if (parameter === 37446) {{ return 'Intel Iris OpenGL Engine'; }}
        return origGetParameter.apply(this, [parameter]);
    }};
}})();
"""


def pick_user_agent(config: StealthConfig) -> str:
    """Return the configured user-agent, randomizing only when rotation is on."""
    if not config.user_agents:
        return ""
    if config.rotate_user_agent:
        return secrets.choice(config.user_agents)
    return config.user_agents[0]


def build_browser_context_options(config: StealthConfig) -> dict[str, Any]:
    """Translate ``StealthConfig`` into Playwright ``Browser.new_context`` kwargs."""
    if not config.enabled:
        return {}

    options: dict[str, Any] = {}
    user_agent = pick_user_agent(config).strip()
    if user_agent:
        options["user_agent"] = user_agent

    if config.spoof_languages:
        locale = next(
            (entry.strip() for entry in config.spoof_languages if entry.strip()), ""
        )
        if locale:
            options["locale"] = locale

    timezone = config.spoof_timezone.strip()
    if timezone:
        options["timezone_id"] = timezone

    return options


async def apply_stealth(context: Any, config: StealthConfig) -> None:
    """Apply stealth mutations to a Playwright BrowserContext.

    Args:
        context: Playwright ``BrowserContext`` instance.
        config: Stealth configuration.
    """
    if not config.enabled:
        return

    await context.add_init_script(_build_fingerprint_init_script(config))

    extra_headers: dict[str, str] = {}
    if config.spoof_languages:
        extra_headers["Accept-Language"] = ",".join(config.spoof_languages)
    if extra_headers:
        await context.set_extra_http_headers(extra_headers)

    try:
        try:
            from playwright_stealth import stealth_async  # type: ignore[import-not-found]
        except ImportError:
            from playwright_stealth import Stealth  # type: ignore[import-not-found]

            _stealth = Stealth()

            async def stealth_async(page: Any) -> None:
                await _stealth.apply_stealth_async(page)

        for page in context.pages:
            await stealth_async(page)
    except ImportError:
        logger.info(
            "playwright_stealth_unavailable",
            note="install playwright-stealth for full stealth coverage",
        )

    logger.info(
        "baselithbot_stealth_applied",
        webdriver_masked=config.mask_webdriver,
        languages=config.spoof_languages,
        timezone=config.spoof_timezone,
        user_agent_rotated=config.rotate_user_agent,
    )


__all__ = ["apply_stealth", "build_browser_context_options", "pick_user_agent"]
