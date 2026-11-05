"""The catalogue calls of the ElevenLabs client: the voice list, the key check, the usage read.

A mixin of :class:`baselith.core.services.voice._elevenlabs.ElevenLabsClient`, which provides the
wire (``_call``, ``_json``). Kept apart from the speech calls for size; the safety properties are
the client's.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from baselith.core.services.voice._elevenlabs_errors import Kind
from baselith.core.services.voice.errors import VoiceError, safe_text
from baselith.core.services.voice.models import VoiceInfo

#: What a voice id looks like; it goes into a URL path, so nothing else is let through.
VOICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PAGE_SIZE = 100
_MAX_PAGES = 3
_LANG_NAMES = {"italian": "it", "english": "en"}


class _Target(Protocol):
    @property
    def api_key(self) -> str: ...


class _Wire(Protocol):
    async def _call(
        self, target: Any, method: str, path: str, *, kind: Kind, **kwargs: Any
    ) -> bytes: ...

    @staticmethod
    def _json(body: bytes) -> Any: ...


def _language_codes(raw: Any) -> tuple[str, ...]:
    """Lower-case ISO 639-1 codes out of a voice's ``verified_languages``."""
    found: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        value = item.get("language") if isinstance(item, dict) else None
        if not isinstance(value, str):
            continue
        code = value.strip().lower().split("-")[0].split("_")[0]
        code = _LANG_NAMES.get(code, code)
        if 2 <= len(code) <= 3 and code.isalpha() and code not in found:
            found.append(code)
    return tuple(found)


def parse_voices(payload: Any) -> list[VoiceInfo]:
    """The usable voices of a ``/v2/voices`` page; malformed entries are skipped."""
    voices: list[VoiceInfo] = []
    items = payload.get("voices") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        vid, name = item.get("voice_id"), item.get("name")
        if (
            not isinstance(vid, str)
            or not VOICE_ID.fullmatch(vid)
            or not isinstance(name, str)
        ):
            continue
        category = item.get("category")
        voices.append(
            VoiceInfo(
                id=vid,
                name=safe_text(name)[:80] or vid,
                languages=_language_codes(item.get("verified_languages")),
                category=category if isinstance(category, str) else "",
            )
        )
    return voices


class CatalogCalls:
    """Mixin: the voice list, the cheap key check and the subscription read."""

    async def voices(self: _Wire, target: _Target) -> list[VoiceInfo]:
        """The voices the account can use (a few pages at most)."""
        found: list[VoiceInfo] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "page_size": _PAGE_SIZE,
                "include_total_count": "false",
            }
            if token:
                params["next_page_token"] = token
            payload = self._json(
                await self._call(
                    target, "GET", "/v2/voices", kind="voices", params=params
                )
            )
            found.extend(parse_voices(payload))
            token = (
                payload.get("next_page_token") if isinstance(payload, dict) else None
            )
            if not (isinstance(payload, dict) and payload.get("has_more") and token):
                break
        return found

    async def check(self: _Wire, target: _Target) -> None:
        """A cheap authenticated read (one voice); raises the classified error on failure."""
        await self._call(
            target,
            "GET",
            "/v2/voices",
            kind="voices",
            params={"page_size": 1, "include_total_count": "false"},
        )

    async def usage(self: _Wire, target: _Target) -> tuple[int, int] | None:
        """``(used, limit)`` characters this period, or None (no permission, or unparseable)."""
        try:
            payload = self._json(
                await self._call(target, "GET", "/v1/user/subscription", kind="voices")
            )
        except VoiceError:
            return None
        used, limit = payload.get("character_count"), payload.get("character_limit")
        if isinstance(used, int) and isinstance(limit, int) and limit > 0:
            return used, limit
        return None


__all__ = ["VOICE_ID", "CatalogCalls", "parse_voices"]
