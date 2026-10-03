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


NO_QDRANT = """
import sys
sys.modules["qdrant_client"] = None  # a base install: no [qdrant] extra
from core.services.inference import shutdown_sync_inference, EmbeddingService
from core.services.inference import RerankService, SyncInference, InferenceError
shutdown_sync_inference()
print("QDRANT:" + str("qdrant_client" in sys.modules and sys.modules["qdrant_client"] is not None))
"""


def test_inference_imports_and_shuts_down_without_qdrant_client() -> None:
    """``qdrant_client`` is the optional ``[qdrant]`` extra; a pgvector
    deployment must still import the package (lifespan shutdown does)."""
    assert _heavy_modules(NO_QDRANT) == "QDRANT:False"


def test_package_import_does_not_load_qdrant_client() -> None:
    probe = (
        "import sys\nimport core.services.inference\n"
        "print('QDRANT:' + str('qdrant_client' in sys.modules))"
    )
    assert _heavy_modules(probe) == "QDRANT:False"


def test_qdrant_runtime_explains_the_missing_extra() -> None:
    probe = (
        "import sys\nsys.modules['qdrant_client'] = None\n"
        "from core.config.inference import QdrantServerConfig\n"
        "from core.services.inference import QdrantRuntime, InferenceConfigError\n"
        "try:\n"
        "    QdrantRuntime.open(QdrantServerConfig(url='http://qdrant:6333'))\n"
        "except InferenceConfigError as exc:\n"
        "    print('ERR:' + ('[qdrant]' in str(exc)).__str__())\n"
    )
    assert _heavy_modules(probe) == "ERR:True"


def test_lazy_exports_and_all_stay_in_step() -> None:
    import core.services.inference as inf

    assert sorted(inf._EXPORTS) == sorted(inf.__all__)
    for name in inf.__all__:
        assert getattr(inf, name) is not None
