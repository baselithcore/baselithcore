"""
CLI script to run semantic evaluation.
Usage: python scripts/run_eval.py [--datasets path/to/data.json]
"""

import argparse
import json
import logging
from core.observability.logging import get_logger
import asyncio
from core.evaluation.metrics import FaithfulnessEvaluator, AnswerRelevancyEvaluator
from core.config.evaluation import evaluation_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = get_logger("evaluator")


async def run_evaluation(dataset_path: str):
    if not evaluation_config.is_enabled:
        logger.warning(
            "EVALUATION_ENABLED is False or missing. Enabling it temporarily for this run..."
        )
        # Force enable for this script run primarily
        evaluation_config.enabled = True

    # Load dataset
    try:
        with open(dataset_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error(f"Dataset not found: {dataset_path}")
        return

    faithfulness = FaithfulnessEvaluator()
    relevancy = AnswerRelevancyEvaluator()

    results = []

    logger.info(f"Starting evaluation on {len(data)} samples...")

    for idx, sample in enumerate(data):
        logger.info(f"Evaluating sample {idx + 1}/{len(data)}...")

        input_text = sample.get("input")
        actual_output = sample.get("actual_output")
        retrieval_context = sample.get("retrieval_context", [])

        # Measure Faithfulness (requires context)
        f_score = 0.0
        if retrieval_context:
            f_score = faithfulness.measure(input_text, actual_output, retrieval_context)

        # Measure Relevancy
        r_score = relevancy.measure(input_text, actual_output)

        result = {
            "id": idx,
            "input": input_text,
            "metrics": {"faithfulness": f_score, "answer_relevancy": r_score},
        }
        results.append(result)

    # Calculate averages
    avg_f = (
        sum(r["metrics"]["faithfulness"] for r in results) / len(results)
        if results
        else 0
    )
    avg_r = (
        sum(r["metrics"]["answer_relevancy"] for r in results) / len(results)
        if results
        else 0
    )

    logger.info("=" * 40)
    logger.info("EVALUATION REPORT")
    logger.info("=" * 40)
    logger.info(f"Average Faithfulness: {avg_f:.2f}")
    logger.info(f"Average Relevancy:    {avg_r:.2f}")
    logger.info("=" * 40)

    # Save report
    output_path = "evaluation_report.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Detailed report saved to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run LLM evaluation")
    parser.add_argument(
        "--dataset",
        type=str,
        default="data/eval/golden_qa.json",
        help="Path to golden dataset",
    )
    args = parser.parse_args()

    asyncio.run(run_evaluation(args.dataset))
