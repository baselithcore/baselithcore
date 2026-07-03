"""
Deterministic mode helpers.

Utilities to enforce deterministic behavior across the framework
for testing and debugging purposes.
"""

import os
import random
from typing import Any

from core.config import get_core_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


def apply_deterministic_mode(seed: int | None = None) -> None:
    """
    Apply deterministic settings if enabled in config.

    Forces:
    - random.seed
    - numpy.random.seed (if available)
    - PYTHONHASHSEED env var
    """
    config = get_core_config()

    if not config.deterministic_mode:
        return

    effective_seed = seed if seed is not None else config.random_seed
    logger.warning(f"⚠️ DETERMINISTIC MODE ENABLED (Seed: {effective_seed}) ⚠️")

    # 1. Python Random
    random.seed(effective_seed)

    # 2. OS Hash Seed
    os.environ["PYTHONHASHSEED"] = str(effective_seed)

    # 3. Numpy (if installed)
    try:
        import numpy as np

        np.random.seed(effective_seed)
    except ImportError:
        pass


def get_llm_override_kwargs() -> dict[str, Any]:
    """
    Return LLM kwargs overrides when deterministic mode is active.

    Returns:
        Dict with temperature=0.0 and seed if mode is enabled.
    """
    config = get_core_config()

    if config.deterministic_mode:
        return {
            "temperature": 0.0,
            "seed": config.random_seed,
            "top_p": 1.0,  # Reduce randomness from nucleus sampling
        }

    return {}
