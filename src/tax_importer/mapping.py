from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class LocalTax:
    jurisdiction_code: str
    description: str
    tax_type: str
    rate: Optional[float]  # decimal fraction, e.g., 0.015


@dataclass(frozen=True)
class WorkdayElection:
    code: str
    amount: Optional[float]
    percentage: Optional[float]
    additional_fields: Dict[str, Any]


def load_mapping(mappings_path: Path) -> Dict[str, Any]:
    with open(mappings_path, "r", encoding="utf-8") as f:
        return json.load(f)


def map_symmetry_to_workday(
    taxes: Iterable[Dict[str, Any]],
    mapping_config: Dict[str, Any],
) -> List[WorkdayElection]:
    """
    Convert Symmetry tax list to Workday local tax election payloads.
    """
    code_map: Dict[str, str] = mapping_config.get("jurisdiction_to_workday_code", {})
    tolerance: float = float(mapping_config.get("fallback_rate_tolerance", 0.0005))

    elections: List[WorkdayElection] = []
    for item in taxes:
        jurisdiction_id = (
            item.get("code")
            or item.get("jurisdictionId")
            or item.get("jurisdiction_code")
            or item.get("id")
        )
        if not jurisdiction_id:
            continue
        workday_code = code_map.get(jurisdiction_id)
        if not workday_code:
            # Skip unknown jurisdictions; alternatively could log
            continue

        rate = item.get("rate") or item.get("taxRate") or item.get("percentage")
        percentage = None
        if rate is not None:
            try:
                rate_value = float(rate)
                # Normalize common percent forms (e.g., 1.5 for 1.5%)
                percentage = rate_value if rate_value <= 1.0 + tolerance else rate_value / 100.0
            except (ValueError, TypeError):
                percentage = None

        elections.append(
            WorkdayElection(
                code=workday_code,
                amount=None,
                percentage=percentage,
                additional_fields={
                    "sourceJurisdiction": jurisdiction_id,
                    "description": item.get("description") or item.get("name") or "",
                },
            )
        )

    return elections
