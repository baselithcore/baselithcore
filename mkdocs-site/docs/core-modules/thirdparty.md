---
title: ICT Third-Party Register (DORA)
description: DORA Art. 28 Register of Information — ICT third-party providers, functions, and contractual arrangements
---

`core/thirdparty/` maintains the **Register of Information** that **DORA
(EU 2022/2554) Art. 28(3)** requires financial entities to keep on every
contractual arrangement for the use of ICT services. Its structure follows the
ESA ITS templates of **Commission Implementing Regulation (EU) 2024/2956**.

The register models three linked records — **providers**, the business
**functions** they support, and the **contractual arrangements** between them —
validates their cross-references, surfaces the **concentration view** that backs
the Art. 29 risk assessment, and renders the whole register in the ESA template
layout. The framework holds and validates the records; the financial entity
remains responsible for completeness and for the regulatory submission.

## Records

| Record                  | Template  | Captures                                                            |
| ----------------------- | --------- | ------------------------------------------------------------------- |
| `ICTProvider`           | `B_05.01` | Name, LEI, person type, country, ESA critical designation, parent, annual expense. |
| `ICTFunction`           | `B_06.01` | Supported function, criticality band, licensed activity, reasons.   |
| `ContractualArrangement`| `B_02.02` / `B_05.02` | Contract reference, provider, supported functions, ICT service type, dates, notice period, governing law, cost, data locations, **supply chain** (subcontractors). |
| `ServiceAssessment`     | `B_07.01` | Art. 28(8) assessment: supports a critical function, substitutability, exit plan, reintegration, alternatives, data sensitivity. |

## Design

- **Sacred Core.** Domain-agnostic infrastructure, so it lives in `core/`.
- **Storage-agnostic.** `RegisterStore` is a Protocol with an in-memory
  reference implementation; register a durable store (e.g. Postgres) for
  production, exactly like other subsystems.
- **Referentially validated.** Registering an arrangement rejects unknown
  providers, functions, or subcontractors, and rejects an arrangement that
  claims to support a critical function without referencing one with
  critical/important criticality.
- **Auditable.** Every mutation emits an `AUDIT | DORA-REGISTER | …` log line.

## Usage

```python
from core.thirdparty import (
    ContractualArrangement,
    FunctionCriticality,
    ICTFunction,
    ICTProvider,
    ServiceAssessment,
    Substitutability,
    get_register,
)

reg = get_register()

# 1. Register the provider and the function it supports.
provider = await reg.register_provider(
    ICTProvider(name="AcmeCloud", country="IE", lei="X" * 20)
)
function = await reg.register_function(
    ICTFunction(name="Settlement", criticality=FunctionCriticality.CRITICAL)
)

# 2. Register the contractual arrangement (cross-refs are validated).
await reg.register_arrangement(
    ContractualArrangement(
        reference_number="C-2026-001",
        provider_id=provider.id,
        function_ids=[function.id],
        ict_service_type="cloud_compute",
        data_locations=["IE", "DE"],
        assessment=ServiceAssessment(
            supports_critical_function=True,
            substitutability=Substitutability.HIGHLY_COMPLEX,
            exit_plan_exists=True,
        ),
    )
)

# 3. Concentration view backing the Art. 29 risk assessment.
summary = await reg.concentration_summary()
for flag in summary["concentration_flags"]:
    alert(f"Concentration risk on provider {flag['provider_id']}")

# 4. Render the register in the ESA ITS template layout.
register = await reg.export_register()   # keyed by B_05.01, B_06.01, B_02.02, …
```

## API surface

| Symbol                                   | Purpose                                              |
| ---------------------------------------- | ---------------------------------------------------- |
| `ICTProvider` / `ICTFunction` / `ContractualArrangement` / `ServiceAssessment` | The register records. |
| `ProviderType` / `FunctionCriticality` / `Substitutability` / `DataSensitivity` | Classification enums. |
| `RegisterOfInformation`                  | Register, validate, query, and export the records.   |
| `RegisterStore` / `InMemoryRegisterStore`| Persistence Protocol + reference store.              |
| `RegisterValidationError`                | Raised on unknown references or inconsistency.       |
| `build_register()` / `REGISTER_STANDARD` | Render the ESA ITS template layout.                  |
| `get_register()`                         | Shared register over an in-memory store.             |

All symbols are re-exported from `core.thirdparty`.

## Operational notes

- **Register a durable store** before relying on this in production; the default
  in-memory store does not survive a restart.
- **Keep it current.** Art. 28 requires the register to be kept up to date and
  made available to the competent authority on request — drive registrations
  from your procurement/contract lifecycle, not a one-off backfill.
- **The export is a pragmatic subset** of the ESA templates for the structurally
  significant records, not the full XBRL taxonomy; treat it as the working
  layout an operator completes and submits.
