import pytest
from unittest.mock import AsyncMock, patch
from core.services.feedback_service import FeedbackService


@pytest.fixture
def feedback_service():
    return FeedbackService()


@pytest.mark.asyncio
async def test_feedback_service_insert(feedback_service):
    with patch(
        "core.db.feedback.insert_feedback", new_callable=AsyncMock
    ) as mock_insert:
        await feedback_service.insert_feedback(
            query="test query",
            answer="test answer",
            feedback="positive",
            conversation_id="123",
            sources=[{"id": "s1"}],
            comment="well done",
        )
        mock_insert.assert_called_once_with(
            query="test query",
            answer="test answer",
            feedback="positive",
            conversation_id="123",
            sources=[{"id": "s1"}],
            comment="well done",
        )


@pytest.mark.asyncio
async def test_feedback_service_get_feedbacks(feedback_service):
    mock_results = [{"query": "q", "answer": "a", "feedback": "positive"}]
    with patch(
        "core.db.feedback.get_feedbacks",
        new_callable=AsyncMock,
        return_value=mock_results,
    ) as mock_get:
        results = await feedback_service.get_feedbacks(feedback="positive", limit=10)
        assert results == mock_results
        mock_get.assert_called_once_with(feedback="positive", limit=10)


@pytest.mark.asyncio
async def test_feedback_service_get_analytics(feedback_service):
    mock_analytics = {"success_rate": 0.9}
    with patch(
        "core.db.feedback.get_feedback_analytics",
        new_callable=AsyncMock,
        return_value=mock_analytics,
    ) as mock_analytics_call:
        results = await feedback_service.get_analytics(
            days=7, recent_limit=5, top_limit=3
        )
        assert results == mock_analytics
        mock_analytics_call.assert_called_once_with(days=7, recent_limit=5, top_limit=3)


def test_get_feedback_service():
    from core.services.feedback_service import get_feedback_service

    service = get_feedback_service()
    assert isinstance(service, FeedbackService)
    assert get_feedback_service() is service
