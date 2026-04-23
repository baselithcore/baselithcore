"""Cross-platform mouse + keyboard control via pyautogui.

Wraps a small set of pyautogui primitives behind ``OSController``. All
calls require the caller to have invoked ``ComputerUseConfig.require_enabled``
for the relevant capability ("mouse" or "keyboard"). Every successful call
is recorded in the ``AuditLogger``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig, ComputerUseError
from plugins.baselithbot.control.approvals import ApprovalGate, ApprovalStatus


def _load_pyautogui() -> Any:
    try:
        import pyautogui  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("pyautogui not installed; pip install pyautogui pillow") from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def _load_mss() -> Any:
    try:
        import mss  # type: ignore[import-not-found]
    except ImportError:
        return None
    return mss


class OSController:
    """Mouse + keyboard automation gated by ``ComputerUseConfig``."""

    def __init__(
        self,
        config: ComputerUseConfig,
        audit: AuditLogger,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._approvals = approvals
        self._logical_size: tuple[int, int] | None = None
        self._device_size: tuple[int, int] | None = None
        # Lazy imports cached after first successful load. The import +
        # FAILSAFE/PAUSE assignments in ``_load_pyautogui`` otherwise run on
        # every mouse or keyboard call (~30 redundant invocations per agent
        # run).
        self._pa: Any | None = None
        self._mss: Any | None = None
        self._mss_probed: bool = False

    def _pyautogui(self) -> Any:
        pa = self._pa
        if pa is None:
            pa = _load_pyautogui()
            self._pa = pa
        return pa

    def _mss_module(self) -> Any | None:
        # ``mss`` is optional — cache the lookup result (``None`` if missing)
        # after the first call so subsequent geometry probes are O(1).
        if not self._mss_probed:
            self._mss = _load_mss()
            self._mss_probed = True
        return self._mss

    async def _resolve_screen_geometry(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Cache + return (logical_size, device_size) in pixels.

        ``logical_size`` = pyautogui virtual pixels (what mouse API expects).
        ``device_size`` = mss screenshot pixels (what the vision model sees).
        On Retina / HiDPI hosts these differ by the display scale factor
        (typically 2x on macOS). Returned ``(0, 0)`` when mss is unavailable.
        """
        if self._logical_size is None:
            pa = self._pyautogui()
            size = await asyncio.to_thread(pa.size)
            self._logical_size = (int(size.width), int(size.height))
        if self._device_size is None:
            mss_mod = self._mss_module()
            if mss_mod is None:
                self._device_size = (0, 0)
            else:

                def _probe() -> tuple[int, int]:
                    with mss_mod.mss() as sct:
                        mon = sct.monitors[1]
                        return int(mon["width"]), int(mon["height"])

                try:
                    self._device_size = await asyncio.to_thread(_probe)
                except Exception:
                    self._device_size = (0, 0)
        return self._logical_size, self._device_size

    async def _to_logical_coords(
        self, x: int | None, y: int | None
    ) -> tuple[int | None, int | None]:
        """Convert screenshot-pixel coords to pyautogui logical coords.

        Scales only when the provided coordinate exceeds the logical screen
        bound — vision models typically read pixel coords straight from the
        screenshot (device pixels on Retina) so we translate those into the
        logical space pyautogui actually moves through. Coords already in the
        logical range are left untouched so callers can still pass
        ``pyautogui.size()``-space values when they know what they want.
        """
        if x is None and y is None:
            return x, y
        logical, device = await self._resolve_screen_geometry()
        lw, lh = logical
        dw, dh = device
        if lw == 0 or lh == 0 or dw == 0 or dh == 0:
            return x, y
        if lw == dw and lh == dh:
            return x, y
        rx = lw / dw
        ry = lh / dh
        nx = x if x is None else (int(round(x * rx)) if x > lw else x)
        ny = y if y is None else (int(round(y * ry)) if y > lh else y)
        return nx, ny

    async def _gate(self, capability: str, action: str, params: dict[str, Any]) -> None:
        """Block the caller until an operator approves the action.

        No-op when the capability is not in ``require_approval_for`` or when
        no :class:`ApprovalGate` is wired in. On denial/timeout, raise
        :class:`ComputerUseError` so the caller short-circuits with
        ``{status: "denied"}``.
        """
        if self._approvals is None:
            return
        if capability not in self._config.require_approval_for:
            return
        req = await self._approvals.submit(
            capability=capability,
            action=action,
            params=params,
            timeout_seconds=self._config.approval_timeout_seconds,
        )
        if req.status != ApprovalStatus.APPROVED:
            self._audit.record(
                f"{action}.{req.status.value}",
                status=req.status.value,
                approval_id=req.id,
                capability=capability,
            )
            raise ComputerUseError(f"operator {req.status.value} {action} (approval id={req.id})")

    async def screen_size(self) -> tuple[int, int]:
        """Return primary screen size in pixels."""
        self._config.require_enabled("screenshot")
        pa = self._pyautogui()
        size = await asyncio.to_thread(pa.size)
        return int(size.width), int(size.height)

    async def mouse_move(self, x: int, y: int, duration: float = 0.0) -> None:
        self._config.require_enabled("mouse")
        await self._gate("mouse", "mouse_move", {"x": x, "y": y, "duration": duration})
        pa = self._pyautogui()
        lx, ly = await self._to_logical_coords(x, y)
        await asyncio.to_thread(pa.moveTo, lx, ly, duration)
        self._audit.record(
            "mouse_move",
            x=x,
            y=y,
            x_logical=lx,
            y_logical=ly,
            duration=duration,
        )

    async def mouse_click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        self._config.require_enabled("mouse")
        await self._gate(
            "mouse",
            "mouse_click",
            {"x": x, "y": y, "button": button, "clicks": clicks},
        )
        pa = self._pyautogui()
        lx, ly = await self._to_logical_coords(x, y)
        await asyncio.to_thread(
            pa.click,
            lx,
            ly,
            clicks,
            0.0,
            button,
        )
        self._audit.record(
            "mouse_click",
            x=x,
            y=y,
            x_logical=lx,
            y_logical=ly,
            button=button,
            clicks=clicks,
        )

    async def mouse_scroll(self, amount: int) -> None:
        self._config.require_enabled("mouse")
        await self._gate("mouse", "mouse_scroll", {"amount": amount})
        pa = self._pyautogui()
        await asyncio.to_thread(pa.scroll, amount)
        self._audit.record("mouse_scroll", amount=amount)

    async def kbd_type(self, text: str, interval: float = 0.0) -> None:
        self._config.require_enabled("keyboard")
        await self._gate(
            "keyboard",
            "kbd_type",
            {"length": len(text), "interval": interval},
        )
        pa = self._pyautogui()
        await asyncio.to_thread(pa.typewrite, text, interval)
        self._audit.record("kbd_type", length=len(text))

    async def kbd_press(self, key: str) -> None:
        self._config.require_enabled("keyboard")
        await self._gate("keyboard", "kbd_press", {"key": key})
        pa = self._pyautogui()
        await asyncio.to_thread(pa.press, key)
        self._audit.record("kbd_press", key=key)

    async def kbd_hotkey(self, *keys: str) -> None:
        self._config.require_enabled("keyboard")
        await self._gate("keyboard", "kbd_hotkey", {"keys": list(keys)})
        pa = self._pyautogui()
        await asyncio.to_thread(pa.hotkey, *keys)
        self._audit.record("kbd_hotkey", keys=list(keys))


__all__ = ["OSController"]
