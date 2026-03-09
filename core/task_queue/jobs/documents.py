"""
Document Ingestion Tasks

RQ tasks for async document processing pipeline.
"""

import asyncio
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional

from rq import get_current_job

from core.task_queue.status import get_task_tracker, update_job_progress

logger = get_logger(__name__)


def _run_async(coro):
    """Helper to run async code in RQ job."""
    return asyncio.run(coro)


def ingest_document_task(
    file_path: str,
    collection: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ingest a single document into the vector store.

    Args:
        file_path: Path to the document file
        collection: Target collection name
        metadata: Optional document metadata

    Returns:
        Dict with ingestion results
    """
    job = get_current_job()
    job_id = job.id if job else "unknown"

    logger.info(f"[ingest] Starting document ingestion: {file_path}")
    get_task_tracker().mark_started(job_id, f"Ingesting {file_path}")

    try:
        # Import here to avoid circular imports
        from core.services.indexing import get_indexing_service

        update_job_progress(10, "Loading document")

        indexing_service = get_indexing_service()

        update_job_progress(30, "Processing document")

        # Run async indexing
        async def _ingest():
            return await indexing_service.ingest_file(
                file_path=file_path,
                collection=collection,
                metadata=metadata or {},
            )

        result = _run_async(_ingest())

        update_job_progress(90, "Finalizing")

        result_data = {
            "file_path": file_path,
            "collection": collection,
            "chunks_created": getattr(result, "chunks_created", 0),
            "status": "completed",
        }

        get_task_tracker().mark_completed(job_id, result=result_data)
        logger.info(f"[ingest] Document ingestion completed: {file_path}")

        return result_data

    except Exception as e:
        logger.error(f"[ingest] Document ingestion failed: {e}", exc_info=True)
        get_task_tracker().mark_failed(job_id, str(e))
        raise


def batch_ingest_task(
    file_paths: List[str],
    collection: str = "default",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ingest multiple documents in batch.

    Args:
        file_paths: List of document file paths
        collection: Target collection name
        metadata: Optional shared metadata

    Returns:
        Dict with batch results
    """
    job = get_current_job()
    job_id = job.id if job else "unknown"

    total = len(file_paths)
    logger.info(f"[batch_ingest] Starting batch ingestion of {total} documents")
    get_task_tracker().mark_started(job_id, f"Batch ingesting {total} documents")

    results: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    try:
        from core.services.indexing import get_indexing_service

        indexing_service = get_indexing_service()

        for i, file_path in enumerate(file_paths):
            progress = int((i / total) * 100)
            update_job_progress(progress, f"Processing {i + 1}/{total}: {file_path}")

            try:

                async def _ingest():
                    return await indexing_service.ingest_file(
                        file_path=file_path,
                        collection=collection,
                        metadata=metadata or {},
                    )

                result = _run_async(_ingest())
                results.append(
                    {
                        "file_path": file_path,
                        "status": "completed",
                        "chunks": getattr(result, "chunks_created", 0),
                    }
                )
            except Exception as e:
                logger.warning(f"[batch_ingest] Failed to ingest {file_path}: {e}")
                failed.append({"file_path": file_path, "error": str(e)})

        result_data = {
            "total": total,
            "successful": len(results),
            "failed": len(failed),
            "results": results,
            "failures": failed,
        }

        get_task_tracker().mark_completed(job_id, result=result_data)
        logger.info(f"[batch_ingest] Completed: {len(results)}/{total} successful")

        return result_data

    except Exception as e:
        logger.error(f"[batch_ingest] Batch ingestion failed: {e}", exc_info=True)
        get_task_tracker().mark_failed(job_id, str(e))
        raise


def reindex_collection_task(
    collection: str,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Reindex an entire collection.

    Args:
        collection: Collection to reindex
        force: If True, reindex all regardless of changes

    Returns:
        Dict with reindexing results
    """
    job = get_current_job()
    job_id = job.id if job else "unknown"

    logger.info(f"[reindex] Starting collection reindex: {collection}")
    get_task_tracker().mark_started(job_id, f"Reindexing {collection}")

    try:
        from core.services.indexing import get_indexing_service

        update_job_progress(10, "Preparing reindex")

        indexing_service = get_indexing_service()

        async def _reindex():
            return await indexing_service.reindex_collection(
                collection_name=collection,
                force=force,
            )

        update_job_progress(20, "Reindexing documents")

        stats = _run_async(_reindex())

        result_data = {
            "collection": collection,
            "documents_processed": getattr(stats, "documents_processed", 0),
            "chunks_created": getattr(stats, "chunks_created", 0),
            "status": "completed",
        }

        get_task_tracker().mark_completed(job_id, result=result_data)
        logger.info(f"[reindex] Collection reindex completed: {collection}")

        return result_data

    except Exception as e:
        logger.error(f"[reindex] Collection reindex failed: {e}", exc_info=True)
        get_task_tracker().mark_failed(job_id, str(e))
        raise
