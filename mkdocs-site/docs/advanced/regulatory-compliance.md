---
title: Regulatory Compliance Matrix
description: Which article maps to which module, and what the framework deliberately does not do
---

A framework cannot be "compliant". Compliance attaches to a **deployed system
and the organisation running it** — the same library can be deployed inside a
fully conformant high-risk AI system or an unlawful one. What a framework can
supply is two things: the technical primitives an obligation requires, and the
**evidence** that they were used.

This page maps each obligation to the module that implements it and the tests
that hold it in place, and is explicit about the gaps. An honest matrix with
stated gaps is worth more to an auditor than a green wall of checkmarks.

Everything below is **opt-in and default-off**. See
[compliance profiles](../core-modules/compliance.md#compliance-profiles) for
turning on a coherent set and having startup verify it.

## EU AI Act (Regulation (EU) 2024/1689)

| Article | Obligation | Where | Tests |
| ------- | ---------- | ----- | ----- |
| **Art. 5** | Prohibited practices | `core/compliance/prohibited.py` — the eight practices, screened and audited at registration | `tests/unit/core/compliance/test_classification.py` |
| **Art. 6 + Annex I/III** | Risk classification, incl. the Art. 6(3) derogation and its profiling exception | `core/compliance/classification.py` | `test_classification.py` |
| **Art. 9** | Risk management system | `core/compliance/risk_management.py` — lifecycle risk file; a risk closes only when treated, accepted by a named person **and** verified; `review()` refuses while risks are open | `test_artefacts.py` |
| **Art. 10(2)(f)/(g)** | Bias examination of data sets | `core/evaluation/fairness.py` + the **Bias Examination Gate** in CI (`scripts/run_fairness_evals.py` over `evals/fairness/`) — an empty dataset directory fails the job | `test_fairness.py`, `test_fairness_gate.py` |
| **Art. 11 + Annex IV** | Technical documentation | `core/compliance/annex_iv.py` — nine sections, drafted from the registry, completeness checked | `test_registry_and_documents.py` |
| **Art. 12** | Automatic recording of events | `core/observability/audit.py` + `audit_chain.py` | `tests/unit/core/observability/test_audit_chain.py` |
| **Art. 13** | Transparency and instructions for use | `core/compliance/instructions.py` — the sixteen Art. 13(3) elements, drafted from the registry/risk file/Art. 72 plan; `issue()` refuses while any is empty | `test_artefacts.py` |
| **Art. 14** | Human oversight | `core/human/interaction.py`, checkpoints + `/approvals` API | `tests/unit/core/human/` |
| **Art. 15** | Accuracy, robustness, cybersecurity | `core/evaluation/`, `core/guardrails/`, `core/resilience/`, `core/security/` | across those suites |
| **Art. 17** | Quality management system | ❌ **Organisational** — not a code artefact. |  — |
| **Art. 18** | Keep the documentation 10 years | `core/compliance/annex_iv.py` (`RETENTION_YEARS`) — the horizon is stated; enforcing storage is an ops duty. | — |
| **Art. 19 / Art. 26(6)** | Retain automatic logs ≥ 6 months | `core/config/audit.py` — 180-day default, warns below the floor | `test_audit_chain.py` |
| **Art. 27** | Fundamental rights impact assessment | `core/compliance/fria.py` — six elements, completion refused while any is empty | `test_registry_and_documents.py` |
| **Art. 43/47/48** | Conformity assessment, EU declaration, CE marking | ⚠️ **Recorded, not performed** — `ConformityRecord` stores whether and when. | `test_registry_and_documents.py` |
| **Art. 49** | Registration in the EU database | ⚠️ **Tracked, not filed** — `registry.unregistered_with_authority()` lists open duties, including Art. 49(2) derogation cases. | `test_registry_and_documents.py` |
| **Art. 50(1)** | Inform people they interact with an AI | `core/transparency/disclosure.py` | `tests/unit/core/transparency/` |
| **Art. 50(2)/(4)** | Machine-readable marking of synthetic content | `core/transparency/provenance.py` — SHA-256 + HMAC, C2PA assertion | `tests/unit/core/transparency/` |
| **Art. 53/55** | GPAI model obligations | ❌ **Out of scope** — applies to model providers. The classifier recognises the category and lists the duties; training-data summaries and copyright policies are the model provider's artefacts. | `test_classification.py` |
| **Art. 72** | Post-market monitoring | `core/compliance/post_market.py` + `post_market_service.py` — durable plans and observations, breach detection, daily review sweep | `test_registry_and_documents.py`, `test_post_market_service.py` |
| **Art. 73** | Serious incident reporting | `core/incidents/ai_act.py` + `ai_act_service.py` — the 2/10/15-day category-dependent clock | `tests/unit/core/incidents/test_ai_act_incidents.py` |

**Not implemented, by design:** watermarking at the logit level (SynthID-style)
and full C2PA manifest embedding (JUMBF/COSE) — the first happens inside the
model provider, the second needs media-format tooling. `ProvenanceTag` emits a
C2PA-shaped assertion a deployer can promote into a manifest.

## NIS2 (Directive (EU) 2022/2555)

| Article | Obligation | Where |
| ------- | ---------- | ----- |
| **Art. 21(2)(a)** | Risk analysis and information system security policies | ⚠️ Organisational; `core/config/security.py` carries the technical settings |
| **Art. 21(2)(b)** | Incident handling | `core/incidents/` + the [audit trail](../core-modules/audit-trail.md) as the evidence behind each filing |
| **Art. 21(2)(c)** | Business continuity, backup, crisis management | ❌ **Operational** — see [runbooks](runbooks.md) and [deployment](deployment.md) |
| **Art. 21(2)(d)/(e)** | Supply chain security, secure development | SBOM (CycloneDX), `pip-audit`, `bandit`, Semgrep and Trivy in CI; plugin `integrity_sha256` + `BASELITH_REQUIRE_SIGNED_PLUGINS` |
| **Art. 21(2)(g)** | Cyber hygiene and training | ❌ **Organisational** |
| **Art. 21(2)(h)** | Cryptography policy | `core/security/encryption.py`, `core/tenancy/encryption.py`, TLS/HSTS settings |
| **Art. 21(2)(i)/(j)** | Access control, MFA | `core/auth/` (OIDC, scopes, API keys), `core/auth/mfa.py` (TOTP) |
| **Art. 23** | Incident reporting: 24h / 72h / one month | `core/incidents/service.py` |

## GDPR (Regulation (EU) 2016/679)

| Article | Obligation | Where |
| ------- | ---------- | ----- |
| **Art. 5(1)(e)** | Storage limitation | `core/privacy/scheduler.py` — enforced daily sweep, not merely available |
| **Art. 5(2)** | Accountability | [Audit trail](../core-modules/audit-trail.md) |
| **Art. 7** | Consent and its proof | `core/privacy/consent.py` — append-only record chain, withdrawal as a new state, durable via `PRIVACY_CONSENT_DB_PATH` |
| **Art. 15 / 20** | Access and portability | `core/privacy/service.py` (`export_subject`) |
| **Art. 16** | Rectification | `core/privacy/service.py` (`rectify_subject`); providers that cannot comply are named, not skipped |
| **Art. 17** | Erasure | `core/privacy/service.py` (`erase_subject`) |
| **Art. 18** | Restriction of processing | `core/privacy/service.py` (`restrict_subject`) |
| **Art. 21** | Objection | `core/privacy/service.py` (`record_objection`) — direct-marketing objections are absolute |
| **Art. 22** | Automated decision-making safeguards | `core/privacy/automated_decisions.py` — scope test, Art. 22(2) grounds, the three Art. 22(3) channels, and the Art. 15(1)(h) disclosure |
| **Art. 25 / 32** | Data protection by design, security of processing | `core/security/`, `core/tenancy/`, `core/middleware/` |
| **Art. 28** | Processor contracts and sub-processors | ❌ **Organisational** |
| **Art. 30** | Records of processing activities | `core/compliance/ropa.py` |
| **Art. 33 / 34** | Breach notification (72h) and communication to subjects | `core/incidents/gdpr.py` + `gdpr_service.py` |
| **Art. 35** | Data protection impact assessment | `core/compliance/dpia.py` — the four Art. 35(7) elements, Art. 35(2) DPO advice and Art. 35(9) subject views |
| **Art. 36** | Prior consultation | `core/compliance/artefact_services.py` — a high residual risk keeps `may_start_processing` false until the consultation is recorded |
| **Art. 44–49** | International transfers | ⚠️ **Recorded only** — `InternationalTransfer` in the ROPA records destination and safeguard; there is no data-residency enforcement |

## DORA (Regulation (EU) 2022/2554)

| Article | Obligation | Where |
| ------- | ---------- | ----- |
| **Art. 19** | Major ICT-incident reporting (4h / 72h / one month) | `core/incidents/dora.py` + `dora_service.py` |
| **Art. 28/29** | Register of information, concentration risk | `core/thirdparty/` |

## What remains the operator's job

No amount of framework work discharges these:

- **AI literacy** (AI Act Art. 4) and staff training (NIS2 Art. 21(2)(g)).
- **Quality management system** (Art. 17) and **conformity assessment**
  (Art. 43) — a procedure, possibly with a notified body.
- **EU declaration of conformity** (Art. 47), **CE marking** (Art. 48), and
  **registration in the EU database** (Art. 49) — acts toward authorities.
- **Designating** a DPO, an authorised representative, and registering as a
  NIS2 entity.
- **Filing** every notification. The framework produces the record and makes the
  clock visible; no subsystem here transmits anything to an authority.
- **Judging substance.** A FRIA with six filled fields can still be a bad FRIA;
  an Annex IV with nine filled sections can still be thin. Completeness is
  checkable, adequacy is not.

## Reading the records

With `COMPLIANCE_ENABLED` the governance records are reachable over HTTP at
`/compliance`, gated by the `compliance:manage` scope — the inventory, the open
Art. 49 duties, Annex IV documents with their missing sections, FRIAs, the ROPA,
post-market plans with overdue reviews, the profile report and an audit-chain
verification endpoint. See the
[module documentation](../core-modules/compliance.md#admin-api).

## Verifying your own deployment

```bash
export BASELITH_COMPLIANCE_PROFILE=ai-act-high-risk
export BASELITH_COMPLIANCE_PROFILE_STRICT=true
python backend.py     # fails fast, listing each gap with the article behind it
```

```python
from core.compliance import ComplianceProfile, evaluate_profile
from core.observability.audit_setup import get_durable_audit_sink

report = evaluate_profile(ComplianceProfile.AI_ACT_HIGH_RISK)
print(report.to_dict())

# Is the audit trail intact?
print(get_durable_audit_sink().verify_chain().to_dict())
```
