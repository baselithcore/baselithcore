"""With the ``remote`` backend, importing the core services must not load torch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

SCRIPT = """
import sys
import core.services.inference as inf
from core.services.inference import EmbeddingService, RerankService, SyncInference
heavy = [m for m in ("torch", "sentence_transformers", "FlagEmbedding", "transformers") if m in sys.modules]
print("HEAVY:" + ",".join(heavy))
"""


def _heavy_modules(script: str) -> str:
    env = {
        "PYTHONPATH": str(ROOT),
        "BASELITH_EMBEDDING_BACKEND": "remote",
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout.strip().splitlines()[-1]


def test_detector_sees_torch_when_it_is_imported() -> None:
    """Negative control: the probe must fail loudly if torch does get loaded.

    A stub module stands in for torch, so the control runs where the heavy
    extras are not installed (CI's base set) and what it proves is the
    detector, not the wheel.
    """
    probe = SCRIPT.replace(
        "import core.services.inference as inf",
        "import types\nsys.modules['torch'] = types.ModuleType('torch')",
    )
    assert _heavy_modules(probe).startswith("HEAVY:torch")


def test_no_torch_after_importing_the_inference_services() -> None:
    assert _heavy_modules(SCRIPT) == "HEAVY:"
