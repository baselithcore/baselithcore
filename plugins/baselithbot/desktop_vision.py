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
    """Capture full-screen screenshots and return base64-encoded images.

    Defaults to PNG for fidelity. Pass ``image_format='JPEG'`` (with optional
    ``quality``) for smaller payloads — useful when the screenshot is sent to
    an LLM that does not require pixel-perfect detail.
    """

    def __init__(self, config: ComputerUseConfig, audit: AuditLogger) -> None:
        self._config = config
        self._audit = audit
        # Lazy imports cached after first capture. Calling ``_load_mss`` /
        # ``_load_pillow`` on every screenshot pays an import lookup + two
        # function-call frames per capture; the agent takes one screenshot per
        # Observe step, so caching trims per-step overhead.
        self._mss: Any | None = None
        self._pillow: Any | None = None

    def _modules(self) -> tuple[Any, Any]:
        if self._mss is None:
            self._mss = _load_mss()
        if self._pillow is None:
            self._pillow = _load_pillow()
        return self._mss, self._pillow

    async def screenshot(
        self,
        monitor: int = 1,
        image_format: str = "PNG",
        quality: int = 80,
    ) -> str:
        """Capture the given monitor (1-indexed) and return base64-encoded bytes."""
        self._config.require_enabled("screenshot")

        fmt = image_format.upper()
        if fmt not in ("PNG", "JPEG", "WEBP"):
            raise ValueError(f"unsupported image_format: {image_format}")
        q = max(1, min(100, int(quality)))
        mss_mod, image_mod = self._modules()

        def _grab() -> bytes:
            with mss_mod.mss() as sct:
                shot = sct.grab(sct.monitors[monitor])
                img = image_mod.frombytes("RGB", shot.size, shot.rgb)
                buf = io.BytesIO()
                save_kwargs: dict[str, Any] = {}
                if fmt in ("JPEG", "WEBP"):
                    save_kwargs["quality"] = q
                img.save(buf, format=fmt, **save_kwargs)
                return buf.getvalue()

        payload = await asyncio.to_thread(_grab)
        b64 = base64.b64encode(payload).decode("ascii")
        self._audit.record(
            "desktop_screenshot",
            monitor=monitor,
            format=fmt,
            quality=q if fmt != "PNG" else None,
            bytes=len(payload),
        )
        return b64


__all__ = ["DesktopVision"]
