"""
Flow Handlers for the Example Plugin.

This module defines handlers that are executed when specific intents
are detected by the intent recognition system.
"""

from typing import Any, Dict


class ExampleFlowHandler:
    """
    Handler for specific business logic flows.

    This class demonstrates how to encapsulate complex logic
    that should be triggered by specific user intents.
    """

    async def handle_greeting(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle the 'greeting' intent.

        Args:
            context: Context containing user query, identifying info, etc.

        Returns:
            Result dictionary to be processed by the response generator.
        """
        user_name = context.get("user_name", "User")
        return {
            "response": f"Hello, {user_name}! I am the Example Flow Handler.",
            "status": "success",
            "action": "greet",
        }

    async def handle_complex_task(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a more complex task.

        Args:
            context: Task context

        Returns:
            Task result
        """
        # Perform some business logic here
        # e.g., query database, call external API, etc.
        item_id = context.get("item_id")
        return {
            "response": f"Processed complex task for item {item_id}",
            "status": "completed",
            "data": {"processed": True, "id": item_id},
        }

    def __call__(self, intent_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Callable interface for simple routing within the handler.
        """
        # This is just an example pattern; the actual signature depends
        # on how the core system invokes handlers.
        return {"handled_by": "ExampleFlowHandler", "intent": intent_name}
