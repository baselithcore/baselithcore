"""
Flow Handlers for the Example Plugin.

This module defines handlers that are executed when specific intents
are detected by the intent recognition system.

The runtime invokes every registered flow handler as ``handler(query, context)``
(or ``handler.handle(query, context)`` when the value is an object), and awaits
the result when it is a coroutine — see ``core/plugins/registration.py``.
"""

from typing import Any


class ExampleFlowHandler:
    """
    Handler for specific business logic flows.

    This class demonstrates how to encapsulate complex logic
    that should be triggered by specific user intents.
    """

    async def handle_greeting(
        self, query: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle the 'example_greeting' intent.

        Args:
            query: The user query that triggered the intent.
            context: Request context (user identity, conversation data, ...).

        Returns:
            Result dictionary to be processed by the response generator.
        """
        user_name = context.get("user_name", "User")
        return {
            "response": f"Hello, {user_name}! I am the Example Flow Handler.",
            "status": "success",
            "action": "greet",
            "query": query,
        }

    async def handle_complex_task(
        self, query: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle the 'example_complex' intent.

        Args:
            query: The user query that triggered the intent.
            context: Task context (for example an ``item_id`` to process).

        Returns:
            Task result
        """
        # Perform some business logic here
        # e.g., query database, call external API, etc.
        item_id = context.get("item_id")
        return {
            "response": f"Processed complex task for item {item_id}",
            "status": "completed",
            "data": {"processed": True, "id": item_id, "query": query},
        }
