"""
Automatic Fine-Tuning Service.

Monitors evaluation results and automatically triggers fine-tuning
when performance drops below threshold.

The service:
1. Listens to EVALUATION_COMPLETED events
2. Accumulates low-score interactions in a buffer
3. When buffer reaches min_samples AND avg_score < threshold:
   - Creates a JSONL training dataset
   - Triggers FineTuningPipeline
   - Emits FINETUNING_TRIGGERED event
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.events import get_event_bus, EventNames
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.finetuning import FineTuningPipeline

logger = get_logger(__name__)


@dataclass
class InteractionSample:
    """A single interaction sample for fine-tuning."""

    query: str
    response: str
    expected_response: Optional[str] = None
    score: float = 0.0
    intent: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    feedback: str = ""

    def to_training_format(self) -> Dict[str, Any]:
        """Convert to OpenAI fine-tuning JSONL format."""
        # If we have expected response (from human feedback), use it
        # Otherwise, include the original response with improvement hints
        assistant_content = (
            self.expected_response
            if self.expected_response
            else f"[Improvement required] {self.response}"
        )

        return {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": self.query},
                {"role": "assistant", "content": assistant_content},
            ]
        }


@dataclass
class AutoFineTuneConfig:
    """Configuration for automatic fine-tuning."""

    enabled: bool = True
    min_samples: int = 100
    score_threshold: float = 0.5
    max_buffer_size: int = 1000
    auto_trigger: bool = True
    provider: str = "openai"
    base_model: str = "gpt-3.5-turbo"
    output_dir: str = "data/finetuning"


class AutoFineTuningService:
    """
    Automatic fine-tuning trigger based on evaluation feedback.

    Workflow:
    1. Listen to EVALUATION_COMPLETED events
    2. Collect low-score interactions in buffer
    3. When buffer reaches min_samples and avg_score < threshold:
       - Create JSONL dataset from collected samples
       - Trigger FineTuningPipeline
       - Emit FINETUNING_TRIGGERED event

    Example:
        ```python
        service = AutoFineTuningService(
            min_samples=100,
            score_threshold=0.5,
        )
        service.start()

        # Later...
        job_id = await service.trigger_finetuning()
        ```
    """

    def __init__(
        self,
        config: Optional[AutoFineTuneConfig] = None,
    ):
        """
        Initialize auto fine-tuning service.

        Args:
            config: Configuration for the service
        """
        self.config = config or AutoFineTuneConfig()
        self.event_bus = get_event_bus()
        self._buffer: List[InteractionSample] = []
        self._running = False
        self._lock = asyncio.Lock()
        self._total_samples_collected = 0
        self._last_finetune_time: Optional[datetime] = None
        self._finetune_pipeline: Optional["FineTuningPipeline"] = None  # Lazy loaded

    @property
    def finetune_pipeline(self) -> Optional["FineTuningPipeline"]:
        """Lazy load FineTuningPipeline."""
        if self._finetune_pipeline is None:
            try:
                from core.finetuning import FineTuningPipeline

                self._finetune_pipeline = FineTuningPipeline()
            except Exception as e:
                logger.error(f"Failed to load FineTuningPipeline: {e}")
        return self._finetune_pipeline

    def start(self) -> None:
        """Start listening to evaluation events."""
        if self._running:
            return

        if not self.config.enabled:
            logger.info("AutoFineTuningService disabled by config")
            return

        self.event_bus.subscribe(
            EventNames.EVALUATION_COMPLETED, self._on_evaluation_completed
        )
        self._running = True
        logger.info(
            f"AutoFineTuningService started (threshold={self.config.score_threshold}, "
            f"min_samples={self.config.min_samples})"
        )

    def stop(self) -> None:
        """Stop listening to events."""
        self._running = False
        logger.info("AutoFineTuningService stopped")

    async def _on_evaluation_completed(self, data: Dict[str, Any]) -> None:
        """Handle evaluation completed events."""
        if not self._running:
            return

        score = data.get("score", 1.0)

        # Only collect samples below threshold
        if score >= self.config.score_threshold:
            return

        # Create sample from event data
        sample = InteractionSample(
            query=data.get("query", ""),
            response=data.get("response", ""),
            score=score,
            intent=data.get("intent", ""),
            feedback=data.get("feedback", ""),
        )

        # Skip if missing essential data
        if not sample.query or not sample.response:
            logger.debug("Skipping sample: missing query or response")
            return

        async with self._lock:
            # Add to buffer (with size limit)
            self._buffer.append(sample)
            self._total_samples_collected += 1

            if len(self._buffer) > self.config.max_buffer_size:
                # Remove oldest samples
                self._buffer = self._buffer[-self.config.max_buffer_size :]

            logger.debug(
                f"Collected low-score sample (score={score:.2f}), "
                f"buffer size: {len(self._buffer)}"
            )

            # Check if we should trigger fine-tuning
            if self.config.auto_trigger and self._should_trigger():
                asyncio.create_task(self._auto_trigger_finetuning())

    def _should_trigger(self) -> bool:
        """Check if fine-tuning should be triggered."""
        if len(self._buffer) < self.config.min_samples:
            return False

        # Calculate average score
        avg_score = sum(s.score for s in self._buffer) / len(self._buffer)

        # Trigger if average score is below threshold
        return avg_score < self.config.score_threshold

    async def _auto_trigger_finetuning(self) -> None:
        """Automatically trigger fine-tuning (internal)."""
        try:
            job_id = await self.trigger_finetuning()
            if job_id:
                logger.info(f"Auto-triggered fine-tuning job: {job_id}")
        except Exception as e:
            logger.error(f"Auto fine-tuning failed: {e}")

    async def trigger_finetuning(self) -> Optional[str]:
        """
        Manually trigger fine-tuning from collected samples.

        Returns:
            Job ID if successful, None otherwise
        """
        async with self._lock:
            if not self._buffer:
                logger.warning("No samples in buffer for fine-tuning")
                return None

            samples = list(self._buffer)
            # Clear buffer after taking samples
            self._buffer = []

        # Create dataset file
        dataset_path = self._create_dataset(samples)
        if not dataset_path:
            return None

        # Emit triggered event
        await self.event_bus.emit(
            EventNames.FINETUNING_TRIGGERED,
            {
                "samples_count": len(samples),
                "dataset_path": str(dataset_path),
                "avg_score": sum(s.score for s in samples) / len(samples),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Trigger fine-tuning pipeline
        pipeline = self.finetune_pipeline
        if not pipeline:
            logger.error("FineTuningPipeline not available")
            return None

        try:
            from core.finetuning import FineTuneConfig

            config = FineTuneConfig(
                base_model=self.config.base_model,
                suffix=f"auto-{datetime.now().strftime('%Y%m%d-%H%M')}",
            )

            result = await pipeline.start_training(
                training_file=dataset_path,
                config=config,
            )

            job_id = result.job.id if result.job else None
            if not job_id:
                logger.error("Fine-tuning started but no job ID returned")
                return None
            self._last_finetune_time = datetime.now(timezone.utc)

            # Emit started event
            await self.event_bus.emit(
                EventNames.FINETUNING_STARTED,
                {"job_id": job_id, "samples_count": len(samples)},
            )

            return job_id

        except Exception as e:
            logger.error(f"Fine-tuning failed: {e}")
            await self.event_bus.emit(
                EventNames.FINETUNING_FAILED,
                {"error": str(e), "samples_count": len(samples)},
            )
            return None

    def _create_dataset(self, samples: List[InteractionSample]) -> Optional[Path]:
        """Create JSONL dataset from samples."""
        try:
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"auto_finetune_{timestamp}.jsonl"
            filepath = output_dir / filename

            with open(filepath, "w", encoding="utf-8") as f:
                for sample in samples:
                    line = json.dumps(sample.to_training_format(), ensure_ascii=False)
                    f.write(line + "\n")

            logger.info(
                f"Created fine-tuning dataset: {filepath} ({len(samples)} samples)"
            )
            return filepath

        except Exception as e:
            logger.error(f"Failed to create dataset: {e}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "buffer_size": len(self._buffer),
            "total_samples_collected": self._total_samples_collected,
            "min_samples": self.config.min_samples,
            "score_threshold": self.config.score_threshold,
            "avg_buffer_score": (
                sum(s.score for s in self._buffer) / len(self._buffer)
                if self._buffer
                else None
            ),
            "last_finetune_time": (
                self._last_finetune_time.isoformat()
                if self._last_finetune_time
                else None
            ),
            "ready_to_trigger": self._should_trigger(),
        }

    async def add_sample_with_correction(
        self,
        query: str,
        original_response: str,
        corrected_response: str,
        score: float = 0.0,
    ) -> None:
        """
        Add a sample with human-corrected response.

        This is useful when humans provide corrections for bad responses.
        These samples have higher quality for fine-tuning.

        Args:
            query: Original user query
            original_response: The agent's original response
            corrected_response: Human-corrected response
            score: Optional quality score
        """
        sample = InteractionSample(
            query=query,
            response=original_response,
            expected_response=corrected_response,
            score=score,
        )

        async with self._lock:
            self._buffer.append(sample)
            self._total_samples_collected += 1
            logger.info("Added human-corrected sample to fine-tuning buffer")
