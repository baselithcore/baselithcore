---
title: Auto Fine-Tuning
description: Automatic fine-tuning based on feedback and experiences
---

**Module**: `core/finetuning/`

Beyond simply evolving prompts, the framework supports a full `AutoFineTuner` pipeline. When sufficient high-quality interaction data has been collected via the `EvaluationService` and user feedback, the system can automatically prepare and trigger fine-tuning jobs on base models.

---

## Module Structure

```text
core/finetuning/
├── __init__.py           # tuner factory
├── pipeline.py           # FineTuningPipeline core
├── dataset.py            # Dataset preparation
└── providers.py          # Vendor integrations (OpenAI, Together.ai)
```

> [!NOTE]
> Automated triggers are managed by the `AutoFineTuningService` located in `core/learning/auto_finetuning.py`.

---

## Workflow

1. **Threshold Monitoring**: The `AutoFineTuningService` checks if enough low-score interactions (avg_score < threshold) have been accumulated.
2. **Dataset Generation**: Formats the buffered samples into instruction-tuning JSONL format.
3. **Pipeline Trigger**: Automatically starts a `FineTuningPipeline` job.

## Usage

```python
from core.learning.auto_finetuning import AutoFineTuningService

service = AutoFineTuningService()

# Manually trigger fine-tuning from accumulated samples
job_id = await service.trigger_finetuning()

if job_id:
    log.info(f"Auto-triggered fine-tuning job {job_id} started successfully.")
```

## Provider Support

The framework supports multiple fine-tuning backends through established providers:

- **OpenAI**: Native support for GPT-4o-mini and GPT-3.5-turbo models.
- **Together.ai**: Full support for Llama 3, Mistral, and Llama 2 models via the Together.ai Fine-tuning API.

This module is designed to integrate seamlessly with standard Enterprise platforms offering managed fine-tuning, allowing an agentic system to naturally "specialize" on its domain over weeks of operation.
