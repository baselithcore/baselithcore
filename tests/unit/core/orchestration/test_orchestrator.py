import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.orchestration.orchestrator import Orchestrator
from core.events import EventNames


class TestOrchestratorEvents:
    def setup_method(self):
        self.mock_intent_classifier = MagicMock()
        self.mock_event_bus = MagicMock()

        # Patch get_event_bus to return our mock
        self.patcher = patch(
            "core.orchestration.mixins.execution.get_event_bus",
            return_value=self.mock_event_bus,
        )
        self.patcher.start()

        self.orchestrator = Orchestrator(intent_classifier=self.mock_intent_classifier)

    def teardown_method(self):
        self.patcher.stop()

    @pytest.mark.asyncio
    async def test_process_emits_flow_completed_with_rich_data(self):
        # Setup
        query = "test query"
        intent = "test_intent"
        response = "test response"
        context = {"user_id": 123, "complex_obj": object()}

        self.mock_intent_classifier.classify = AsyncMock(return_value=intent)

        # Mock handler
        mock_handler = AsyncMock()
        mock_handler.handle.return_value = {"response": response}
        self.orchestrator.register_handler(intent, mock_handler)

        # Execute
        await self.orchestrator.process(query, context)

        # Verify
        self.mock_event_bus.emit_sync.assert_called()
        calls = self.mock_event_bus.emit_sync.call_args_list

        # Check FLOW_COMPLETED event (should be last call)
        flow_completed_call = calls[-1]
        args, _ = flow_completed_call
        event_name, data = args

        assert event_name == EventNames.FLOW_COMPLETED
        assert data["intent"] == intent
        assert data["query"] == query
        assert data["response"] == response
        assert data["success"] is True

        # Check safe context filtering
        assert "user_id" in data["context"]
        assert "complex_obj" not in data["context"]  # Should be filtered out
