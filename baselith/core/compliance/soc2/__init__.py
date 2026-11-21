"""SOC 2 readiness: scopes, controls, hash-anchored evidence and an auditor's package.

See :mod:`baselith.core.compliance.soc2.types` for what SOC 2 is and is not.
"""

from __future__ import annotations

from baselith.core.compliance.soc2.catalogue import (
    CRITERIA,
    Criterion,
    criteria_for,
    is_known,
)
from baselith.core.compliance.soc2.collectors import (
    CollectorSpec,
    EvidenceDraft,
    register_evidence_collector,
    registered_collectors,
)
from baselith.core.compliance.soc2.package import (
    DISCLAIMER,
    build_package,
    render_markdown,
)
from baselith.core.compliance.soc2.persistence import (
    InMemorySoc2Store,
    RecordExistsError,
    Soc2Store,
    SQLiteSoc2Store,
)
from baselith.core.compliance.soc2.readiness import (
    STATUS_RANK,
    ControlReadiness,
    CriterionReadiness,
    assess_control,
    assess_scope,
    summarise,
)
from baselith.core.compliance.soc2.service import (
    CollectionReport,
    Soc2NotFoundError,
    Soc2Service,
    Soc2ValidationError,
    canonical_sha256,
    get_soc2_service,
    reset_soc2_service,
)
from baselith.core.compliance.soc2.starter import starter_controls
from baselith.core.compliance.soc2.types import (
    FREQUENCY_DAYS,
    AttestationType,
    ControlFrequency,
    EvidenceKind,
    EvidenceOutcome,
    Soc2Control,
    Soc2Evidence,
    Soc2Scope,
    TrustCategory,
)

__all__ = [
    "CRITERIA",
    "DISCLAIMER",
    "FREQUENCY_DAYS",
    "STATUS_RANK",
    "AttestationType",
    "CollectionReport",
    "CollectorSpec",
    "ControlFrequency",
    "ControlReadiness",
    "Criterion",
    "CriterionReadiness",
    "EvidenceDraft",
    "EvidenceKind",
    "EvidenceOutcome",
    "InMemorySoc2Store",
    "RecordExistsError",
    "SQLiteSoc2Store",
    "Soc2Control",
    "Soc2Evidence",
    "Soc2NotFoundError",
    "Soc2Scope",
    "Soc2Service",
    "Soc2Store",
    "Soc2ValidationError",
    "TrustCategory",
    "assess_control",
    "assess_scope",
    "build_package",
    "canonical_sha256",
    "registered_collectors",
    "criteria_for",
    "get_soc2_service",
    "is_known",
    "register_evidence_collector",
    "render_markdown",
    "reset_soc2_service",
    "starter_controls",
    "summarise",
]
