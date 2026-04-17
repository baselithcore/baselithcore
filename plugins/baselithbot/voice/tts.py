"""Text-to-speech adapters: system fallback + abstract base."""

from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess  # nosec B404 - args allowlisted, shell=False
from abc import ABC, abstractmethod
from typing import Any


class TTSAdapter(ABC):
    """Abstract TTS interface."""

    name: str = "abstract"

    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> dict[str, Any]:
        """Render ``text`` to audio. Returns metadata dict."""


class SystemTTS(TTSAdapter):
    """OS-native TTS fallback (macOS ``say``, Linux ``espeak``, Windows SAPI)."""

    name = "system"

    async def synthesize(self, text: str, voice: str | None = None) -> dict[str, Any]:
        system = platform.system()
        argv: list[str] | None = None

        if system == "Darwin" and shutil.which("say"):
            argv = ["say"]
            if voice:
                argv += ["-v", voice]
            argv.append(text)
        elif system == "Linux" and shutil.which("espeak"):
            argv = ["espeak"]
            if voice:
                argv += ["-v", voice]
            argv.append(text)
        elif system == "Windows":
            argv = [
                "powershell",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Speech;"
                    " (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                    f".Speak({text!r})"
                ),
            ]

        if argv is None:
            return {"status": "unsupported", "system": system}

        def _run() -> int:
            return subprocess.run(  # nosec B603 - argv built, shell=False
                argv, shell=False, check=False
            ).returncode

        rc = await asyncio.to_thread(_run)
        return {
            "status": "success" if rc == 0 else "failed",
            "system": system,
            "return_code": rc,
        }


__all__ = ["TTSAdapter", "SystemTTS"]
