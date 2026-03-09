"""
Feedback Service implementation.
"""

from typing import Any, Dict, List, Optional
from core.db import feedback as db_feedback


class FeedbackService:
    """
    Service for handling feedback operations and analytics.
    Wraps core.db.feedback functions.
    """

    async def insert_feedback(
        self,
        query: str,
        answer: str,
        feedback: str,
        conversation_id: Optional[str] = None,
        sources: Optional[List[Dict[str, Any]]] = None,
        comment: Optional[str] = None,
    ) -> None:
        """
        Record a new user feedback entry into the persistent database.

        Captures the original query, the model's response, and the user's
        rating/sentiment. Optionally attaches source evidence for RAG tasks.

        Args:
            query: The user's original input string.
            answer: The AI agent's generated response.
            feedback: The user's rating (e.g., 'positive', 'negative', '1-5').
            conversation_id: Unique identifier for the session.
            sources: List of metadata dictionaries for retrieved documents.
            comment: Optional qualitative feedback provided by the user.
        """
        await db_feedback.insert_feedback(
            query=query,
            answer=answer,
            feedback=feedback,
            conversation_id=conversation_id,
            sources=sources,
            comment=comment,
        )

    async def get_feedbacks(
        self,
        feedback: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve a list of feedback entries from the database.

        Args:
            feedback: Optional value to filter by (e.g., 'positive', 'negative').
            limit: Maximum number of entries to return.

        Returns:
            List[Dict[str, Any]]: List of feedback records.
        """
        return await db_feedback.get_feedbacks(feedback=feedback, limit=limit)

    async def get_analytics(
        self,
        days: Optional[int] = None,
        recent_limit: int = 20,
        top_limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Aggregate feedback data for performance monitoring and reporting.

        Calculates success rates, identifies common failure patterns, and
        summarizes user sentiment over a specific time window.

        Args:
            days: Lookback period in days for historical analysis.
            recent_limit: Number of latest entries to include in a high-level log.
            top_limit: Number of top-performing sources or categories to rank.

        Returns:
            Dict[str, Any]: A structured analytics report containing
                            metrics and trend summaries.
        """
        return await db_feedback.get_feedback_analytics(
            days=days,
            recent_limit=recent_limit,
            top_limit=top_limit,
        )


# Global instance
_feedback_service = FeedbackService()


def get_feedback_service() -> FeedbackService:
    """
    Get the global FeedbackService singleton.

    Returns:
        FeedbackService: The shared service instance.
    """
    return _feedback_service
