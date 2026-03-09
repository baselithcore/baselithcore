"""
Dataset Builder for Fine-Tuning.

Tools for preparing and validating training datasets.

Usage:
    from core.finetuning import DatasetBuilder, DatasetFormat

    builder = DatasetBuilder()
    builder.add_conversation("What is Python?", "Python is a programming language...")
    builder.save("training_data.jsonl")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator

from core.observability.logging import get_logger
from core.finetuning.models import TrainingExample

logger = get_logger(__name__)


class DatasetFormat(str, Enum):
    """Supported dataset formats."""

    JSONL = "jsonl"  # Standard for OpenAI
    CSV = "csv"
    PARQUET = "parquet"


@dataclass
class DatasetStats:
    """Statistics about a dataset."""

    total_examples: int = 0
    total_tokens: int = 0
    avg_tokens_per_example: float = 0.0
    min_tokens: int = 0
    max_tokens: int = 0
    total_user_tokens: int = 0
    total_assistant_tokens: int = 0


class DatasetBuilder:
    """
    Builder for creating fine-tuning datasets.

    Features:
    - Add examples from various sources
    - Validate format compliance
    - Estimate token counts
    - Export to JSONL
    """

    def __init__(self, system_prompt: str | None = None) -> None:
        """
        Initialize dataset builder.

        Args:
            system_prompt: Default system prompt for all examples
        """
        self.system_prompt = system_prompt
        self._examples: list[TrainingExample] = []
        self._validation_errors: list[str] = []

        logger.info(
            "dataset_builder_initialized",
            has_system_prompt=bool(system_prompt),
        )

    @property
    def size(self) -> int:
        """Number of examples in dataset."""
        return len(self._examples)

    def add_example(self, example: TrainingExample) -> DatasetBuilder:
        """Add a training example (fluent API)."""
        self._examples.append(example)
        return self

    def add_conversation(
        self,
        user_message: str,
        assistant_response: str,
        system_prompt: str | None = None,
    ) -> DatasetBuilder:
        """Add a simple conversation pair."""
        example = TrainingExample.from_conversation(
            user_message=user_message,
            assistant_response=assistant_response,
            system_prompt=system_prompt or self.system_prompt,
        )
        return self.add_example(example)

    def add_multi_turn(self, messages: list[dict[str, str]]) -> DatasetBuilder:
        """Add a multi-turn conversation."""
        # Optionally prepend system prompt
        if self.system_prompt and (not messages or messages[0]["role"] != "system"):
            messages = [{"role": "system", "content": self.system_prompt}] + messages

        example = TrainingExample(messages=messages)
        return self.add_example(example)

    def add_from_jsonl(self, file_path: str | Path) -> DatasetBuilder:
        """Load examples from a JSONL file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Dataset file not found: {path}")

        with open(path, "r") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    example = TrainingExample(messages=data.get("messages", []))
                    self.add_example(example)

        logger.info("dataset_loaded_from_file", path=str(path), examples=self.size)
        return self

    def validate(self) -> tuple[bool, list[str]]:
        """
        Validate the dataset for fine-tuning compatibility.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: list[str] = []

        if self.size == 0:
            errors.append("Dataset is empty")
            return False, errors

        if self.size < 10:
            errors.append(f"Dataset too small: {self.size} examples (minimum 10)")

        for i, example in enumerate(self._examples):
            # Check messages exist
            if not example.messages:
                errors.append(f"Example {i}: No messages")
                continue

            # Check for at least user and assistant
            roles = [m.get("role") for m in example.messages]
            if "user" not in roles:
                errors.append(f"Example {i}: Missing user message")
            if "assistant" not in roles:
                errors.append(f"Example {i}: Missing assistant response")

            # Check valid roles
            valid_roles = {"system", "user", "assistant"}
            for j, msg in enumerate(example.messages):
                role = msg.get("role")
                if role not in valid_roles:
                    errors.append(f"Example {i}, message {j}: Invalid role '{role}'")
                if not msg.get("content"):
                    errors.append(f"Example {i}, message {j}: Empty content")

        self._validation_errors = errors
        is_valid = len(errors) == 0

        logger.info(
            "dataset_validation_complete",
            is_valid=is_valid,
            error_count=len(errors),
        )

        return is_valid, errors

    def estimate_tokens(self, chars_per_token: float = 4.0) -> DatasetStats:
        """
        Estimate token counts for the dataset.

        Args:
            chars_per_token: Average characters per token (4 is typical)

        Returns:
            DatasetStats with token estimates
        """
        token_counts = []
        user_tokens = 0
        assistant_tokens = 0

        for example in self._examples:
            example_tokens = 0
            for msg in example.messages:
                content = msg.get("content", "")
                tokens = int(len(content) / chars_per_token)
                example_tokens += tokens

                if msg.get("role") == "user":
                    user_tokens += tokens
                elif msg.get("role") == "assistant":
                    assistant_tokens += tokens

            token_counts.append(example_tokens)

        return DatasetStats(
            total_examples=self.size,
            total_tokens=sum(token_counts),
            avg_tokens_per_example=sum(token_counts) / max(len(token_counts), 1),
            min_tokens=min(token_counts) if token_counts else 0,
            max_tokens=max(token_counts) if token_counts else 0,
            total_user_tokens=user_tokens,
            total_assistant_tokens=assistant_tokens,
        )

    def save(
        self,
        output_path: str | Path,
        format: DatasetFormat = DatasetFormat.JSONL,
    ) -> str:
        """
        Save dataset to file.

        Args:
            output_path: Path to save the dataset
            format: Output format

        Returns:
            Path to saved file
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if format == DatasetFormat.JSONL:
            with open(path, "w") as f:
                for example in self._examples:
                    f.write(json.dumps(example.to_jsonl_row()) + "\n")
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info(
            "dataset_saved",
            path=str(path),
            format=format.value,
            examples=self.size,
        )

        return str(path)

    def split(
        self, validation_ratio: float = 0.1
    ) -> tuple[DatasetBuilder, DatasetBuilder]:
        """
        Split dataset into training and validation sets.

        Args:
            validation_ratio: Fraction for validation (0.0 to 0.5)

        Returns:
            Tuple of (training_builder, validation_builder)
        """
        import random

        if not 0.0 < validation_ratio < 0.5:
            raise ValueError("validation_ratio must be between 0.0 and 0.5")

        # Shuffle and split
        examples = self._examples.copy()
        random.shuffle(examples)

        split_idx = int(len(examples) * (1 - validation_ratio))

        train_builder = DatasetBuilder(system_prompt=self.system_prompt)
        train_builder._examples = examples[:split_idx]

        val_builder = DatasetBuilder(system_prompt=self.system_prompt)
        val_builder._examples = examples[split_idx:]

        logger.info(
            "dataset_split",
            training_size=train_builder.size,
            validation_size=val_builder.size,
        )

        return train_builder, val_builder

    def __iter__(self) -> Iterator[TrainingExample]:
        """Iterate over examples."""
        return iter(self._examples)

    def __len__(self) -> int:
        """Get number of examples."""
        return self.size
