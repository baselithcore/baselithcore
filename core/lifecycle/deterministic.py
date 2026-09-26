"""
Deterministic mode helpers.

Utilities to enforce deterministic behavior across the framework
for testing and debugging purposes.

Two halves, both gated on ``CORE_DETERMINISTIC_MODE``:

* :func:`apply_deterministic_mode` seeds the in-process RNGs; it runs once at
  application startup.
* :func:`get_llm_override_kwargs` pins LLM sampling; every generation path of
  ``LLMService`` (text, structured/tool-calling, message API, streaming) merges
  it into the provider call.

Hash randomization is **not** covered: ``PYTHONHASHSEED`` is read once when
the interpreter starts, so it must be set in the process environment before
launch — writing it from inside the running process changes nothing.
"""

import random
from typing import Any

from core.config import get_core_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Providers whose API has no ``seed`` parameter and rejects ``temperature``
#: combined with ``top_p``: only the temperature pin is sent to them.
_TEMPERATURE_ONLY_PROVIDERS = frozenset({"anthropic"})


def apply_deterministic_mode(seed: int | None = None) -> None:
    """
    Seed the in-process RNGs if deterministic mode is enabled in config.

    Seeds:
    - ``random.seed``
    - ``numpy.random.seed`` (if numpy is installed)

    Args:
        seed: Seed to use; defaults to ``CORE_RANDOM_SEED``.
    """
    config = get_core_config()

    if not config.deterministic_mode:
        return

    effective_seed = seed if seed is not None else config.random_seed
    logger.warning(f"DETERMINISTIC MODE ENABLED (Seed: {effective_seed})")

    # 1. Python Random
    random.seed(effective_seed)

    # 2. Numpy (if installed)
    try:
        import numpy as np

        np.random.seed(effective_seed)
    except ImportError:
        pass


def get_llm_override_kwargs(provider: str | None = None) -> dict[str, Any]:
    """
    Return LLM kwargs overrides when deterministic mode is active.

    Args:
        provider: Name of the provider the kwargs are sent to. Providers
            without a ``seed`` parameter (Anthropic) get the temperature pin
            only, since forwarding an unknown kwarg fails the whole request.

    Returns:
        Dict with ``temperature=0.0`` (plus ``seed`` and ``top_p=1.0`` where
        the provider accepts them) if the mode is enabled, else ``{}``.
    """
    config = get_core_config()

    if not config.deterministic_mode:
        return {}

    if provider in _TEMPERATURE_ONLY_PROVIDERS:
        return {"temperature": 0.0}

    return {
        "temperature": 0.0,
        "seed": config.random_seed,
        "top_p": 1.0,  # Reduce randomness from nucleus sampling
    }
