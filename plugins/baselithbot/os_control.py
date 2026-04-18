"""Cross-platform mouse + keyboard control via pyautogui.

Wraps a small set of pyautogui primitives behind ``OSController``. All
calls require the caller to have invoked ``ComputerUseConfig.require_enabled``
for the relevant capability ("mouse" or "keyboard"). Every successful call
is recorded in the ``AuditLogger``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .approvals import ApprovalGate, ApprovalStatus
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError


def _load_pyautogui() -> Any:
    try:
        import pyautogui  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui not installed; pip install pyautogui pillow"
        ) from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


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
            raise ComputerUseError(
                f"operator {req.status.value} {action} (approval id={req.id})"
            )

    async def screen_size(self) -> tuple[int, int]:
        """Return primary screen size in pixels."""
        self._config.require_enabled("screenshot")
        pa = _load_pyautogui()
        size = await asyncio.to_thread(pa.size)
        return int(size.width), int(size.height)

    async def mouse_move(self, x: int, y: int, duration: float = 0.0) -> None:
        self._config.require_enabled("mouse")
        await self._gate("mouse", "mouse_move", {"x": x, "y": y, "duration": duration})
        pa = _load_pyautogui()
        await asyncio.to_thread(pa.moveTo, x, y, duration)
        self._audit.record("mouse_move", x=x, y=y, duration=duration)

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
        pa = _load_pyautogui()
        await asyncio.to_thread(
            pa.click,
            x,
            y,
            clicks,
            0.0,
            button,
        )
        self._audit.record("mouse_click", x=x, y=y, button=button, clicks=clicks)

    async def mouse_scroll(self, amount: int) -> None:
        self._config.require_enabled("mouse")
        await self._gate("mouse", "mouse_scroll", {"amount": amount})
        pa = _load_pyautogui()
        await asyncio.to_thread(pa.scroll, amount)
        self._audit.record("mouse_scroll", amount=amount)

    async def kbd_type(self, text: str, interval: float = 0.0) -> None:
        self._config.require_enabled("keyboard")
        await self._gate(
            "keyboard",
            "kbd_type",
            {"length": len(text), "interval": interval},
        )
        pa = _load_pyautogui()
        await asyncio.to_thread(pa.typewrite, text, interval)
        self._audit.record("kbd_type", length=len(text))

    async def kbd_press(self, key: str) -> None:
        self._config.require_enabled("keyboard")
        await self._gate("keyboard", "kbd_press", {"key": key})
        pa = _load_pyautogui()
        await asyncio.to_thread(pa.press, key)
        self._audit.record("kbd_press", key=key)

    async def kbd_hotkey(self, *keys: str) -> None:
        self._config.require_enabled("keyboard")
        await self._gate("keyboard", "kbd_hotkey", {"keys": list(keys)})
        pa = _load_pyautogui()
        await asyncio.to_thread(pa.hotkey, *keys)
        self._audit.record("kbd_hotkey", keys=list(keys))


__all__ = ["OSController"]
