"""
AI transparency subsystem (EU AI Act Article 50).

Provides the two technical means Article 50 requires of AI systems that interact
with people or emit synthetic content:

* **Disclosure** (Art 50(1)) — :class:`DisclosureService` builds a
  :class:`DisclosureNotice` telling end users they are talking to an AI.
* **Content marking** (Art 50(2)/(4)) — :class:`ProvenanceTagger` attaches a
  machine-readable, optionally signed :class:`ProvenanceTag` to AI-generated or
  AI-modified output, with C2PA-aligned fields.

Both are unified behind :class:`TransparencyService` /
:func:`get_transparency_service`. The subsystem is opt-in (``TRANSPARENCY_ENABLED``)
and additive — it does not alter responses until a call site attaches a notice or
a provenance header.
"""

from core.transparency.disclosure import (
    DEFAULT_DISCLOSURE_TEXT,
    DisclosureService,
)
from core.transparency.provenance import (
    PROVENANCE_HEADER,
    ProvenanceTagger,
    sha256_hex,
)
from core.transparency.service import (
    TransparencyService,
    get_transparency_service,
)
from core.transparency.types import (
    ContentClass,
    DisclosureNotice,
    Modality,
    ProvenanceTag,
    TransparencyError,
)

__all__ = [
    "DEFAULT_DISCLOSURE_TEXT",
    "DisclosureService",
    "PROVENANCE_HEADER",
    "ProvenanceTagger",
    "sha256_hex",
    "TransparencyService",
    "get_transparency_service",
    "ContentClass",
    "DisclosureNotice",
    "Modality",
    "ProvenanceTag",
    "TransparencyError",
]
