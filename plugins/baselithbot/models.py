"""Pydantic and dataclass models used by the Baselithbot plugin.

Named ``models`` rather than ``types`` so the file does not shadow the Python
stdlib ``types`` module when the plugin ships as a flat-layout standalone repo
(``plugin-baselithbot``), where ``types.py`` would otherwise sit directly on
``sys.path``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StealthConfig(BaseModel):
    """Stealth mode configuration applied to a Playwright BrowserContext."""

    enabled: bool = Field(default=True, description="Toggle stealth mode globally.")
    rotate_user_agent: bool = Field(default=True)
    mask_webdriver: bool = Field(default=True)
    spoof_languages: list[str] = Field(default_factory=lambda: ["en-US", "en"])
    spoof_timezone: str = Field(default="UTC")
    user_agents: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36",
        ]
    )


class BaselithbotTask(BaseModel):
    """Input task envelope for ``BaselithbotAgent.execute``."""

    goal: str = Field(..., description="Natural language description of the goal.")
    start_url: str | None = Field(default=None)
    max_steps: int = Field(default=20, ge=1, le=100)
    extract_fields: list[str] = Field(default_factory=list)


class BaselithbotResult(BaseModel):
    """Structured result returned by ``BaselithbotAgent.execute``."""

    run_id: str | None = None
    success: bool
    final_url: str
    steps_taken: int
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    history: list[str] = Field(default_factory=list)
    error: str | None = None
    last_screenshot_b64: str | None = None
    tokens_used: int = 0
    model: str | None = None
    provider: str | None = None


__all__ = ["StealthConfig", "BaselithbotTask", "BaselithbotResult"]
