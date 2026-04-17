"""Desktop screenshot capture for the Computer Use loop.

Uses ``mss`` for fast cross-platform capture and Pillow to encode to PNG.
"""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any

from .computer_use import AuditLogger, ComputerUseConfig


def _load_mss() -> Any:
    try:
        import mss  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("mss not installed; pip install mss pillow") from exc
    return mss


def _load_pillow() -> Any:
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Pillow not installed; pip install pillow") from exc
    return Image


class DesktopVision:
    """Capture full-screen screenshots and return base64 PNG strings."""

    def __init__(self, config: ComputerUseConfig, audit: AuditLogger) -> None:
        self._config = config
        self._audit = audit

    async def screenshot(self, monitor: int = 1) -> str:
        """Capture the given monitor (1-indexed) and return base64 PNG."""
        self._config.require_enabled("screenshot")

        def _grab() -> bytes:
            mss_mod = _load_mss()
            image_mod = _load_pillow()
            with mss_mod.mss() as sct:
                shot = sct.grab(sct.monitors[monitor])
                img = image_mod.frombytes("RGB", shot.size, shot.rgb)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()

        png_bytes = await asyncio.to_thread(_grab)
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self._audit.record("desktop_screenshot", monitor=monitor, bytes=len(png_bytes))
        return b64


__all__ = ["DesktopVision"]
