"""
Indexing Background Jobs.

Defines the RQ workers operations specifically bound for orchestrating
asynchronous document ingestion and vector indexing operations.
"""

import asyncio
from core.observability.logging import get_logger
from rq import get_current_job
from core.services.indexing import get_indexing_service
from core.realtime.pubsub import PubSubManager
from core.realtime.events import RealtimeEvent, EventType

logger = get_logger(__name__)


async def _run_indexing_logic(incremental: bool, job_id: str) -> int:
    """Async core of the indexing job."""

    # helper for async publish inside async job
    async def _publish(event: RealtimeEvent):
        # TODO: Ideally inject this or pass it down, but for RQ job wrapper we might need to instantiate
        from core.config import get_storage_config

        config = get_storage_config()
        redis_url = config.cache_redis_url
        pubsub = PubSubManager(redis_url)
        await pubsub.publish("global", event)

    try:
        # 1. Publish Started
        await _publish(
            RealtimeEvent(
                type=EventType.JOB_STARTED,
                job_id=job_id,
                payload={"type": "indexing", "incremental": incremental},
            )
        )

        # 2. Run Task
        indexing_service = get_indexing_service()
        stats = await indexing_service.index_documents(incremental=incremental)
        result = stats.new_documents

        # 3. Publish Completed
        await _publish(
            RealtimeEvent(
                type=EventType.JOB_COMPLETED,
                job_id=job_id,
                payload={"processed_docs": result},
            )
        )

        logger.info(f"[job] Indexing job completed. Processed {result} docs.")
        return result

    except Exception as e:
        logger.error(f"[job] Indexing job failed: {e}", exc_info=True)
        # 4. Publish Failed
        try:
            await _publish(
                RealtimeEvent(
                    type=EventType.JOB_FAILED,
                    job_id=job_id,
                    payload={"error": str(e)},
                )
            )
        except Exception as e:
            logger.error(f"Failed to publish failure event: {e}")
        raise


def run_indexing_job(incremental: bool = True) -> int:
    """
    RQ Job wrapper for document indexing.
    Runs the async _run_indexing_logic function using asyncio.run().
    """
    rq_job = get_current_job()
    job_id = rq_job.id if rq_job else "unknown"

    logger.info(f"[job] Starting indexing job (incremental={incremental}, id={job_id})")

    return asyncio.run(_run_indexing_logic(incremental, job_id))
