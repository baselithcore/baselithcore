"""
PostgreSQL storage implementation.
"""

from core.observability.logging import get_logger
import json
from typing import List, Optional, Dict, Any
from uuid import UUID

from psycopg.rows import dict_row

from core.config import StorageConfig
from core.storage.interfaces import InteractionRepository, FeedbackRepository
from core.storage.models import Interaction, Feedback
from core.db.connection import get_async_cursor

logger = get_logger(__name__)


class PostgresStorage(InteractionRepository, FeedbackRepository):
    """
    PostgreSQL implementation of storage repositories.
    Uses the shared connection pool from core.db.connection.
    """

    def __init__(self, config: StorageConfig):
        """
        Initialize the PostgresStorage repository.

        Args:
            config: Storage configuration details.
        """
        self.config = config

    async def initialize(self) -> None:
        """Initialize schema. Connection pool is managed globally."""
        if not self.config.postgres_enabled:
            logger.warning("PostgreSQL disabled in config")
            return

        logger.info(f"Initializing PostgresStorage (host={self.config.db_host})")
        await self._initialize_schema()

    def shutdown(self) -> None:
        """Shutdown storage. Pool is managed globally."""
        pass

    async def health_check(self) -> bool:
        """Check database connectivity."""
        if not self.config.postgres_enabled:
            return False

        try:
            # Use a simple query to check connection
            async with get_async_cursor() as cur:
                await cur.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def _initialize_schema(self) -> None:
        """Create necessary tables if they don't exist."""
        ddl = """
        CREATE TABLE IF NOT EXISTS interactions (
            id UUID PRIMARY KEY,
            session_id TEXT,
            user_id TEXT,
            agent_id TEXT,
            input_transcription TEXT,
            output_transcription TEXT,
            metadata JSONB DEFAULT '{}'::jsonb,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id UUID PRIMARY KEY,
            interaction_id UUID REFERENCES interactions(id),
            score FLOAT,
            label TEXT,
            comment TEXT,
            metadata JSONB DEFAULT '{}'::jsonb,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        """
        try:
            # Async execution for initialization
            async with get_async_cursor() as cur:
                await cur.execute(ddl)
                logger.info("Storage schema initialized")
        except Exception as e:
            logger.error(f"Failed to initialize schema: {e}")
            raise

    # === InteractionRepository ===

    async def store_interaction(self, interaction: Interaction) -> Interaction:
        """
        Persist a new interaction record to PostgreSQL.

        Args:
            interaction: The interaction model to store.

        Returns:
            Interaction: The stored interaction.
        """
        sql = """
        INSERT INTO interactions (
            id, session_id, user_id, agent_id, 
            input_transcription, output_transcription, 
            metadata, timestamp
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s,
            %s, %s
        )
        """
        try:
            async with get_async_cursor() as cur:
                await cur.execute(
                    sql,
                    (
                        interaction.id,
                        interaction.session_id,
                        interaction.user_id,
                        interaction.agent_id,
                        interaction.input_transcription,
                        interaction.output_transcription,
                        json.dumps(interaction.metadata),
                        interaction.timestamp,
                    ),
                )
            return interaction
        except Exception as e:
            logger.error(f"Error storing interaction: {e}")
            raise

    async def get_interaction(self, interaction_id: UUID) -> Optional[Interaction]:
        """
        Retrieve a specific interaction by its unique ID.

        Args:
            interaction_id: UUID of the interaction.

        Returns:
            Optional[Interaction]: The interaction if found, else None.
        """
        sql = "SELECT * FROM interactions WHERE id = %s"
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, (interaction_id,))
            row = await cur.fetchone()
            if row and isinstance(row, dict):
                return Interaction(**row)
        return None

    async def get_interactions_by_session(
        self, session_id: str, limit: int = 100, offset: int = 0
    ) -> List[Interaction]:
        """
        Retrieve all interactions associated with a specific session.

        Args:
            session_id: The session identifier.
            limit: Maximum records to return.
            offset: Number of records to skip.

        Returns:
            List[Interaction]: List of matching interactions.
        """
        sql = """
        SELECT * FROM interactions 
        WHERE session_id = %s 
        ORDER BY timestamp DESC 
        LIMIT %s OFFSET %s
        """
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, (session_id, limit, offset))
            rows = await cur.fetchall()
            return [Interaction(**row) for row in rows if isinstance(row, dict)]

    # === FeedbackRepository ===

    async def store_feedback(self, feedback: Feedback) -> Feedback:
        """
        Persist a feedback record linked to an interaction.

        Args:
            feedback: The feedback model to store.

        Returns:
            Feedback: The stored feedback.
        """
        sql = """
        INSERT INTO feedback (
            id, interaction_id, score, label, comment, metadata, timestamp
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s
        )
        """
        try:
            async with get_async_cursor() as cur:
                await cur.execute(
                    sql,
                    (
                        feedback.id,
                        feedback.interaction_id,
                        feedback.score,
                        feedback.label,
                        feedback.comment,
                        json.dumps(feedback.metadata),
                        feedback.timestamp,
                    ),
                )
            return feedback
        except Exception as e:
            logger.error(f"Error storing feedback: {e}")
            raise

    async def get_feedback_for_interaction(
        self, interaction_id: UUID
    ) -> List[Feedback]:
        """
        Retrieve all feedback records for a specific interaction.

        Args:
            interaction_id: UUID of the interaction.

        Returns:
            List[Feedback]: List of associated feedback records.
        """
        sql = "SELECT * FROM feedback WHERE interaction_id = %s"
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, (interaction_id,))
            rows = await cur.fetchall()
            return [Feedback(**row) for row in rows if isinstance(row, dict)]

    async def get_feedback_summary(
        self, agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate a summary of feedback scores and labels.

        Args:
            agent_id: Optional filter for a specific agent.

        Returns:
            Dict[str, Any]: Summary containing average_score and counts.
        """
        params: List[Any] = []
        where_clause = ""
        if agent_id:
            where_clause = (
                "JOIN interactions i ON f.interaction_id = i.id WHERE i.agent_id = %s"
            )
            params.append(agent_id)

        sql = f"""
        SELECT 
            AVG(score) as average_score,
            COUNT(*) as total_feedback,
            COUNT(CASE WHEN label = 'positive' THEN 1 END) as positive_count,
            COUNT(CASE WHEN label = 'negative' THEN 1 END) as negative_count
        FROM feedback f
        {where_clause}
        """  # nosec B608
        async with get_async_cursor(row_factory=dict_row) as cur:  # type: ignore
            await cur.execute(sql, params)
            row = await cur.fetchone() or {}
            # row might be a dict or None (handled by or {})
            # If fetchone returns None:
            if not row:
                row = {}
            return {
                "average_score": row.get("average_score"),
                "total_feedback": row.get("total_feedback"),
                "positive_count": row.get("positive_count"),
                "negative_count": row.get("negative_count"),
            }
