---
title: Auto Fine-Tuning
description: Automatic fine-tuning based on feedback and evaluation results
---

**Modules**: `core/finetuning/` (pipeline) and `core/learning/auto_finetuning.py` (trigger)

Beyond continuous learning, the framework supports a full fine-tuning pipeline.
When sufficient low-quality interaction data has been collected via the
evaluation event stream, the `AutoFineTuningService` automatically prepares a
JSONL dataset and triggers a `FineTuningPipeline` job on a base model.

---

## Module Structure

```text
core/finetuning/
├── __init__.py            # Public exports
├── pipeline.py            # FineTuningPipeline
├── dataset.py             # DatasetBuilder, DatasetFormat
├── models.py              # FineTuneJob, FineTuneConfig, FineTuneResult, TrainingStatus, FineTuneProvider, EvaluationMetrics
└── providers.py           # OpenAIProvider, TogetherProvider
```

Public exports from `core.finetuning`:

```python
from core.finetuning import (
    FineTuningPipeline,
    DatasetBuilder,
    DatasetFormat,
    FineTuneJob,
    FineTuneConfig,
    FineTuneResult,
    TrainingStatus,
)
```

> [!NOTE]
> The automated trigger is the `AutoFineTuningService` in
> `core/learning/auto_finetuning.py`. The pipeline class it drives is
> `FineTuningPipeline` (in `core/finetuning/pipeline.py`).

---

## Workflow

1. **Threshold monitoring**: `AutoFineTuningService` subscribes to
   `EVALUATION_COMPLETED` events and buffers interactions whose score falls
   below `score_threshold`.
2. **Trigger condition**: when the buffer reaches `min_samples` **and** its
   average score is below `score_threshold`, fine-tuning fires automatically
   (if `auto_trigger` is enabled).
3. **Dataset generation**: buffered `InteractionSample`s are written to an
   OpenAI-style instruction-tuning JSONL file under `output_dir`.
4. **Pipeline trigger**: `FineTuningPipeline.start_training()` is invoked with a
   `FineTuneConfig`, emitting `FINETUNING_TRIGGERED` / `FINETUNING_STARTED`
   events.

## Usage

```python
from core.learning.auto_finetuning import (
    AutoFineTuningService,
    AutoFineTuneConfig,
)

service = AutoFineTuningService(
    config=AutoFineTuneConfig(
        min_samples=100,
        score_threshold=0.5,
        provider="openai",
        base_model="gpt-3.5-turbo",
    )
)
service.start()  # begins listening to EVALUATION_COMPLETED events

# Manually trigger fine-tuning from the accumulated buffer (async)
job_id = await service.trigger_finetuning()
if job_id:
    log.info(f"Auto fine-tuning job {job_id} started")

# Add a human-corrected sample (higher-quality training signal)
await service.add_sample_with_correction(
    query="...",
    original_response="...",
    corrected_response="...",
)

print(service.get_stats())
service.stop()
```

`trigger_finetuning()` is **async** and returns the job ID (or `None` if the
buffer is empty or the pipeline is unavailable). `AutoFineTuneConfig` fields:
`enabled`, `min_samples` (100), `score_threshold` (0.5), `max_buffer_size`
(1000), `auto_trigger`, `provider` (`"openai"`), `base_model`
(`"gpt-3.5-turbo"`), `output_dir` (`"data/finetuning"`).

## Driving the Pipeline Directly

```python
from core.finetuning import FineTuningPipeline, FineTuneConfig, DatasetBuilder

pipeline = FineTuningPipeline()  # auto-loads provider API keys from config

result = await pipeline.start_training(
    training_file="data/train.jsonl",   # path or DatasetBuilder
    config=FineTuneConfig(base_model="gpt-4o-mini-2024-07-18"),
)

if result.success and result.job:
    job = await pipeline.wait_for_completion(result.job.id)
    print(job.fine_tuned_model)
```

Other `FineTuningPipeline` methods: `get_job_status(job_id)`,
`wait_for_completion(job_id, poll_interval=60, timeout=7200)`,
`cancel_job(job_id)`, `list_jobs(limit=10, provider=None)`,
`test_model(model_id, prompt, system_prompt="")`,
`evaluate_model(model_id, test_dataset)`, and the `supported_models` property.

## Provider Support

Providers live in `core/finetuning/providers.py`:

- **`OpenAIProvider`** — `SUPPORTED_MODELS`:
  `gpt-4o-mini-2024-07-18`, `gpt-4o-2024-08-06`, `gpt-4-0613`,
  `gpt-3.5-turbo-0125`.
- **`TogetherProvider`** — `SUPPORTED_MODELS`:
  `meta-llama/Llama-3-8b-hf`, `mistralai/Mistral-7B-v0.1`,
  `meta-llama/Llama-2-7b-hf`.

Both read their API keys from `core.config.get_finetuning_config()`
(`openai_api_key` / `together_api_key`, stored as `SecretStr`). Each exposes
`is_available`, `train()`, `get_status()`, `cancel()`, and `list_jobs()`.
