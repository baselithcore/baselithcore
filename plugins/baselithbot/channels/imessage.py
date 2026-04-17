"""iMessage adapter alias backed by BlueBubbles bridge."""

from __future__ import annotations

from .bluebubbles import BlueBubblesAdapter


class IMessageAdapter(BlueBubblesAdapter):
    """``iMessage`` exposed as a separate registry entry; same transport."""

    name = "imessage"


__all__ = ["IMessageAdapter"]
