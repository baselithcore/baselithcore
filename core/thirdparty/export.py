"""Render the Register of Information in the ESA ITS template layout.

Maps the domain records onto the templates of Commission Implementing Regulation
(EU) 2024/2956 (the ESA ITS on the register of information). This is a pragmatic
subset of the official templates — the structurally significant ones — keyed by
their template codes so an operator can complete and submit them; it is not the
full XBRL taxonomy.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.thirdparty.types import (
    ContractualArrangement,
    ICTFunction,
    ICTProvider,
)

# Reference to the standard this layout follows, surfaced in the export header.
REGISTER_STANDARD = "Commission Implementing Regulation (EU) 2024/2956"


def build_register(
    providers: List[ICTProvider],
    functions: List[ICTFunction],
    arrangements: List[ContractualArrangement],
) -> Dict[str, Any]:
    """Build the register payload, keyed by ESA ITS template code.

    Args:
        providers: All ICT third-party providers in the register.
        functions: All supported business functions.
        arrangements: All contractual arrangements.

    Returns:
        A dict with a ``_meta`` header and one entry per template
        (``B_05.01`` providers, ``B_05.02`` supply chain, ``B_06.01``
        functions, ``B_02.02`` arrangements, ``B_07.01`` service assessment).
    """
    return {
        "_meta": {
            "standard": REGISTER_STANDARD,
            "note": "Pragmatic subset of the ESA register templates.",
            "providers": len(providers),
            "functions": len(functions),
            "arrangements": len(arrangements),
        },
        "B_05.01": [_provider_row(p) for p in providers],
        "B_05.02": _supply_chain_rows(arrangements),
        "B_06.01": [_function_row(f) for f in functions],
        "B_02.02": [_arrangement_row(a) for a in arrangements],
        "B_07.01": [_assessment_row(a) for a in arrangements],
    }


def _provider_row(provider: ICTProvider) -> Dict[str, Any]:
    """Template B_05.01 — ICT third-party service provider."""
    return {
        "provider_identifier": provider.id,
        "provider_name": provider.name,
        "lei": provider.lei,
        "person_type": provider.provider_type.value,
        "country": provider.country,
        "critical_designation": provider.is_critical_designated,
        "ultimate_parent": provider.parent_id,
        "total_annual_expense": provider.total_annual_expense,
        "currency": provider.currency,
    }


def _function_row(function: ICTFunction) -> Dict[str, Any]:
    """Template B_06.01 — function identification."""
    return {
        "function_identifier": function.id,
        "function_name": function.name,
        "criticality": function.criticality.value,
        "critical_or_important": function.is_critical_or_important,
        "licensed_activity": function.licensed_activity,
        "reasons_for_criticality": function.reasons_for_criticality,
    }


def _arrangement_row(arrangement: ContractualArrangement) -> Dict[str, Any]:
    """Template B_02.02 — contractual arrangement (specific information)."""
    return {
        "contractual_reference": arrangement.reference_number,
        "provider_identifier": arrangement.provider_id,
        "functions_supported": list(arrangement.function_ids),
        "ict_service_type": arrangement.ict_service_type,
        "start_date": (
            arrangement.start_date.isoformat() if arrangement.start_date else None
        ),
        "end_date": (
            arrangement.end_date.isoformat() if arrangement.end_date else None
        ),
        "notice_period_days": arrangement.notice_period_days,
        "governing_law_country": arrangement.governing_law_country,
        "annual_cost": arrangement.annual_cost,
        "currency": arrangement.currency,
        "data_locations": list(arrangement.data_locations),
    }


def _assessment_row(arrangement: ContractualArrangement) -> Dict[str, Any]:
    """Template B_07.01 — assessment of the ICT services (Art. 28(8))."""
    a = arrangement.assessment
    return {
        "contractual_reference": arrangement.reference_number,
        "supports_critical_function": a.supports_critical_function,
        "substitutability": a.substitutability.value,
        "exit_plan_exists": a.exit_plan_exists,
        "reintegration_possible": a.reintegration_possible,
        "alternative_providers_identified": a.alternative_providers_identified,
        "processes_personal_data": a.processes_personal_data,
        "data_sensitivity": a.data_sensitivity.value,
    }


def _supply_chain_rows(
    arrangements: List[ContractualArrangement],
) -> List[Dict[str, Any]]:
    """Template B_05.02 — ICT service supply chain (subcontractors).

    One row per (arrangement, subcontractor), ranked by position in the chain.
    """
    rows: List[Dict[str, Any]] = []
    for arrangement in arrangements:
        for rank, subcontractor_id in enumerate(arrangement.subcontractor_ids, 1):
            rows.append(
                {
                    "contractual_reference": arrangement.reference_number,
                    "provider_identifier": arrangement.provider_id,
                    "rank": rank,
                    "subcontractor_identifier": subcontractor_id,
                }
            )
    return rows


__all__ = ["REGISTER_STANDARD", "build_register"]
