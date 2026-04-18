"""Deterministic Spotify control via AppleScript (macOS-only).

Spotify exposes a small AppleScript dictionary on macOS that lets external
callers issue transport commands and switch the current playback context
by URI. This module wraps those commands behind a typed interface so the
desktop agent can drive Spotify without relying on fragile mouse-click
chains from vision-model screenshots.

Security model:
    - Every action runs through ``osascript`` via ``subprocess.run`` with
      ``shell=False`` and an argv vector. The AppleScript source is fixed
      in this module, not passed by the LLM.
    - Gated on ``ComputerUseConfig.allow_shell`` (same risk class as any
      subprocess call) and on ``osascript`` being present in
      ``allowed_shell_commands``. If the operator has not allowlisted
      ``osascript``, every invocation short-circuits with a clear error.
    - ``require_approval_for`` with ``shell`` still applies, so the
      dashboard approval gate funnels Spotify calls when configured.
"""

from __future__ import annotations

import asyncio
import subprocess  # nosec B404 - argv-only invocation, shell=False
import sys
from typing import Any, Literal

from .approvals import ApprovalGate, ApprovalStatus
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError

SpotifyAction = Literal[
    "play",
    "pause",
    "toggle",
    "next",
    "previous",
    "play_uri",
    "status",
]

_SCRIPTS: dict[str, str] = {
    "play": 'tell application "Spotify" to play',
    "pause": 'tell application "Spotify" to pause',
    "toggle": 'tell application "Spotify" to playpause',
    "next": 'tell application "Spotify" to next track',
    "previous": 'tell application "Spotify" to previous track',
    # ``status`` returns a compact summary so the agent knows what happened
    # without needing a screenshot.
    "status": (
        'tell application "Spotify" to return '
        'player state & "|" & '
        '(name of current track as string) & "|" & '
        '(artist of current track as string) & "|" & '
        "(spotify url of current track as string)"
    ),
}

_ACTIVATE_SCRIPT = 'tell application "Spotify" to activate'
_PLAY_URI_TEMPLATE = 'tell application "Spotify" to play track "{uri}"'
_VALID_URI_PREFIX = "spotify:"


class SpotifyController:
    """Run typed Spotify commands through AppleScript."""

    def __init__(
        self,
        config: ComputerUseConfig,
        audit: AuditLogger,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._approvals = approvals

    def _require_platform(self) -> None:
        if sys.platform != "darwin":
            raise ComputerUseError(
                "Spotify AppleScript control is macOS-only; current platform is "
                f"{sys.platform!r}"
            )

    def _require_osascript_allowlisted(self) -> None:
        """Ensure the operator explicitly consented to ``osascript`` runs."""
        if "osascript" not in self._config.allowed_shell_commands:
            raise ComputerUseError(
                "osascript is not in computer_use.allowed_shell_commands; "
                "add 'osascript' to the Shell allowlist before using Spotify "
                "control"
            )

    async def _gate(self, action: str, script: str) -> None:
        if self._approvals is None:
            return
        if "shell" not in self._config.require_approval_for:
            return
        req = await self._approvals.submit(
            capability="shell",
            action=f"spotify.{action}",
            params={"script": script},
            timeout_seconds=self._config.approval_timeout_seconds,
        )
        if req.status != ApprovalStatus.APPROVED:
            self._audit.record(
                f"spotify.{action}.{req.status.value}",
                spotify_action=action,
                status=req.status.value,
                approval_id=req.id,
            )
            raise ComputerUseError(
                f"operator {req.status.value} spotify.{action} (approval id={req.id})"
            )

    async def run(
        self, action: SpotifyAction, uri: str | None = None
    ) -> dict[str, Any]:
        """Dispatch a typed Spotify action. Returns ``stdout`` for ``status``."""
        self._require_platform()
        self._config.require_enabled("shell")
        self._require_osascript_allowlisted()

        if action == "play_uri":
            if not uri or not uri.startswith(_VALID_URI_PREFIX):
                raise ComputerUseError(
                    "play_uri requires a URI beginning with 'spotify:' "
                    "(e.g. 'spotify:playlist:...', 'spotify:track:...')"
                )
            script = _PLAY_URI_TEMPLATE.format(uri=uri.replace('"', '\\"'))
        elif action in _SCRIPTS:
            script = _SCRIPTS[action]
        else:
            raise ComputerUseError(f"unknown spotify action: {action!r}")

        await self._gate(action, script)

        activate_needed = action not in {"status"}
        combined_scripts: list[str] = []
        if activate_needed:
            combined_scripts.append(_ACTIVATE_SCRIPT)
        combined_scripts.append(script)

        argv = ["osascript"]
        for body in combined_scripts:
            argv += ["-e", body]

        def _invoke() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # nosec B603 - argv vector, shell=False
                argv,
                shell=False,
                capture_output=True,
                timeout=self._config.shell_timeout_seconds,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(_invoke)
        except subprocess.TimeoutExpired as exc:
            self._audit.record("spotify.timeout", spotify_action=action)
            raise ComputerUseError(
                f"spotify.{action} timed out after "
                f"{self._config.shell_timeout_seconds}s"
            ) from exc

        stdout = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        self._audit.record(
            "spotify.run",
            spotify_action=action,
            return_code=completed.returncode,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
        )
        if completed.returncode != 0:
            raise ComputerUseError(
                f"osascript exit {completed.returncode}: {stderr[:200]}"
            )

        result: dict[str, Any] = {
            "action": action,
            "return_code": completed.returncode,
        }
        if action == "status":
            parts = stdout.split("|")
            if len(parts) == 4:
                result.update(
                    {
                        "player_state": parts[0],
                        "track": parts[1],
                        "artist": parts[2],
                        "spotify_url": parts[3],
                    }
                )
            else:
                result["raw"] = stdout
        elif action == "play_uri":
            result["uri"] = uri
        return result


__all__ = ["SpotifyController", "SpotifyAction"]
