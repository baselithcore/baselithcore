"""The Trust Services Criteria this module knows by identifier.

Only identifiers and **short descriptive titles written here** are kept: the
criteria text is AICPA's and is not reproduced. A title says what area the
criterion is about so an operator can map a control to it; the authoritative
wording is the AICPA publication the auditor works from.

The Common Criteria (``CC``) are the Security category and apply to every
engagement; ``A``, ``C``, ``PI`` and ``P`` belong to Availability,
Confidentiality, Processing Integrity and Privacy.
"""

from __future__ import annotations

from dataclasses import dataclass

from baselith.core.compliance.soc2.types import TrustCategory

_S = TrustCategory.SECURITY
_A = TrustCategory.AVAILABILITY
_C = TrustCategory.CONFIDENTIALITY
_PI = TrustCategory.PROCESSING_INTEGRITY
_P = TrustCategory.PRIVACY


@dataclass(frozen=True)
class Criterion:
    """A criterion identifier, its category and a short descriptive title."""

    id: str
    category: TrustCategory
    title: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "category": self.category.value, "title": self.title}


_ROWS: tuple[tuple[str, TrustCategory, str], ...] = (
    # CC1-CC5: control environment, communication, risk, monitoring, activities
    ("CC1.1", _S, "Integrity and ethical values"),
    ("CC1.2", _S, "Board oversight of internal control"),
    ("CC1.3", _S, "Structure, authority and responsibility"),
    ("CC1.4", _S, "Commitment to competence"),
    ("CC1.5", _S, "Accountability for control responsibilities"),
    ("CC2.1", _S, "Quality information for internal control"),
    ("CC2.2", _S, "Internal communication of control responsibilities"),
    ("CC2.3", _S, "Communication with external parties"),
    ("CC3.1", _S, "Objectives specified clearly enough to assess risk"),
    ("CC3.2", _S, "Risk identification and analysis"),
    ("CC3.3", _S, "Fraud risk consideration"),
    ("CC3.4", _S, "Assessment of changes that affect internal control"),
    ("CC4.1", _S, "Ongoing and separate evaluations of controls"),
    ("CC4.2", _S, "Evaluation and communication of deficiencies"),
    ("CC5.1", _S, "Selection and development of control activities"),
    ("CC5.2", _S, "General controls over technology"),
    ("CC5.3", _S, "Policies and procedures put into action"),
    # CC6: logical and physical access
    ("CC6.1", _S, "Logical access security over information assets"),
    ("CC6.2", _S, "Registration, provisioning and removal of access"),
    ("CC6.3", _S, "Role-based access and least privilege"),
    ("CC6.4", _S, "Physical access to facilities and assets"),
    ("CC6.5", _S, "Disposal of assets and data"),
    ("CC6.6", _S, "Protection against threats from outside the boundary"),
    ("CC6.7", _S, "Restriction of data transmission, movement and removal"),
    ("CC6.8", _S, "Prevention and detection of malicious software"),
    # CC7: system operations
    ("CC7.1", _S, "Detection of vulnerabilities and configuration changes"),
    ("CC7.2", _S, "Monitoring for anomalies and security events"),
    ("CC7.3", _S, "Evaluation of security events as incidents"),
    ("CC7.4", _S, "Incident response"),
    ("CC7.5", _S, "Recovery from incidents"),
    # CC8-CC9
    ("CC8.1", _S, "Change management"),
    ("CC9.1", _S, "Risk mitigation for business disruption"),
    ("CC9.2", _S, "Vendor and business-partner risk"),
    # Availability
    ("A1.1", _A, "Capacity planning and monitoring"),
    ("A1.2", _A, "Environmental protection, backup and recovery infrastructure"),
    ("A1.3", _A, "Testing of recovery plans"),
    # Confidentiality
    ("C1.1", _C, "Identification and protection of confidential information"),
    ("C1.2", _C, "Disposal of confidential information"),
    # Processing integrity
    ("PI1.1", _PI, "Definitions of processing and its objectives"),
    ("PI1.2", _PI, "Completeness and accuracy of system inputs"),
    ("PI1.3", _PI, "Processing completeness, validity and authorisation"),
    ("PI1.4", _PI, "Completeness and accuracy of system outputs"),
    ("PI1.5", _PI, "Storage of inputs, items in process and outputs"),
    # Privacy
    ("P1.1", _P, "Privacy notice"),
    ("P2.1", _P, "Choice and consent"),
    ("P3.1", _P, "Collection limited to what is needed"),
    ("P3.2", _P, "Explicit consent for sensitive information"),
    ("P4.1", _P, "Use limited to the stated purposes"),
    ("P4.2", _P, "Retention of personal information"),
    ("P4.3", _P, "Disposal of personal information"),
    ("P5.1", _P, "Access by data subjects to their information"),
    ("P5.2", _P, "Correction of personal information"),
    ("P6.1", _P, "Disclosure to third parties with consent"),
    ("P6.2", _P, "Authorised disclosures recorded"),
    ("P6.3", _P, "Unauthorised disclosures recorded"),
    ("P6.4", _P, "Third-party privacy commitments"),
    ("P6.5", _P, "Third-party breach and incident notification"),
    ("P6.6", _P, "Notification of privacy breaches"),
    ("P6.7", _P, "Accounting of disclosures to data subjects"),
    ("P7.1", _P, "Quality of personal information"),
    ("P8.1", _P, "Monitoring and enforcement of privacy compliance"),
)

#: Every criterion known, by identifier.
CRITERIA: dict[str, Criterion] = {id_: Criterion(id_, cat, t) for id_, cat, t in _ROWS}


def criteria_for(categories: list[TrustCategory]) -> list[Criterion]:
    """The criteria an engagement covering ``categories`` is examined against."""
    wanted = set(categories) | {TrustCategory.SECURITY}
    return [c for c in CRITERIA.values() if c.category in wanted]


def is_known(criterion_id: str) -> bool:
    return criterion_id in CRITERIA


__all__ = ["CRITERIA", "Criterion", "criteria_for", "is_known"]
