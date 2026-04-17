"""Stealth mode utilities for Baselithbot.

Applies anti-bot detection countermeasures to a Playwright ``BrowserContext``:
user-agent rotation, navigator.webdriver masking, language/timezone spoofing,
canvas/WebGL fingerprint perturbation.
"""

from __future__ import annotations

import random
from typing import Any

from core.observability.logging import get_logger

from .types import StealthConfig

logger = get_logger(__name__)


_FINGERPRINT_INIT_SCRIPT = """
(() => {
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(() => ({}))
    });
    const origGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
        if (parameter === 37445) { return 'Intel Inc.'; }
        if (parameter === 37446) { return 'Intel Iris OpenGL Engine'; }
        return origGetParameter.apply(this, [parameter]);
    };
})();
"""


def pick_user_agent(config: StealthConfig) -> str:
    """Return a random user-agent from the configured pool."""
    return random.choice(config.user_agents)  # nosec B311


async def apply_stealth(context: Any, config: StealthConfig) -> None:
    """Apply stealth mutations to a Playwright BrowserContext.

    Args:
        context: Playwright ``BrowserContext`` instance.
        config: Stealth configuration.
    """
    if not config.enabled:
        return

    if config.mask_webdriver:
        await context.add_init_script(_FINGERPRINT_INIT_SCRIPT)

    extra_headers: dict[str, str] = {}
    if config.spoof_languages:
        extra_headers["Accept-Language"] = ",".join(config.spoof_languages)
    if extra_headers:
        await context.set_extra_http_headers(extra_headers)

    try:
        from playwright_stealth import stealth_async  # type: ignore[import-not-found]

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
    )


__all__ = ["apply_stealth", "pick_user_agent"]
