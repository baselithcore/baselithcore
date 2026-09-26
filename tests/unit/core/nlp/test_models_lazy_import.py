"""Importing core.nlp must not import the sentence-transformers stack.

core.nlp sits on the app's import path (core.api.lifespan ->
core.services.indexing -> core.nlp); a module-scope sentence_transformers
import cost every process start ~2.2 s and ~3 000 modules.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

_PROBE = """
import sys
import core.nlp.models
heavy = [m for m in ("sentence_transformers", "transformers", "torch", "sklearn")
         if m in sys.modules]
print(",".join(heavy))
"""


def test_import_loads_no_ml_stack() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert loaded == "", f"core.nlp.models eagerly imported: {loaded}"


def test_model_classes_resolve_lazily_as_attributes() -> None:
    import core.nlp.models as models

    sentence_transformer = models.SentenceTransformer
    cross_encoder = models.CrossEncoder

    # Either both resolve (the [rag] extra is installed) or both are None.
    assert (sentence_transformer is None) == (cross_encoder is None)


def test_unknown_attribute_still_raises() -> None:
    import core.nlp.models as models

    try:
        models.does_not_exist
    except AttributeError:
        return
    raise AssertionError("expected AttributeError")
