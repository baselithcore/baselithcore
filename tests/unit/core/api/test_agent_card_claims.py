"""The discovery card only advertises what the default app serves.

Regression: the card said ``streaming: true`` while the default app mounts no
A2A JSON-RPC endpoint — only ``/.well-known/agent.json`` itself.
"""

from core.api.factory import _build_agent_card
from core.config import get_app_config


def test_card_does_not_promise_streaming_the_app_does_not_mount():
    card = _build_agent_card(get_app_config())
    assert card.agentCapabilities.streaming is False
