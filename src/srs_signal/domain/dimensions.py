"""Canonical dimension registry."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from srs_signal.domain.enums import DimensionId


CANONICAL_DIMENSION_NAMES: Final = MappingProxyType(
    {
        DimensionId.SOURCE_TRACEABILITY: "Source traceability",
        DimensionId.LEGAL_BASIS_SPECIFICITY: "Legal-basis specificity",
        DimensionId.REASONING_CHAIN_COMPLETENESS: "Reasoning-chain completeness",
        DimensionId.COUNTERARGUMENT_HANDLING: "Counterargument handling",
        DimensionId.DECISION_EFFECT_JUSTIFICATION: "Decision-effect justification",
        DimensionId.CORRECTION_CAPACITY: "Correction capacity",
        DimensionId.OVERALL_AUDITABILITY: "Overall auditability",
    }
)

CANONICAL_DIMENSION_IDS: Final = tuple(CANONICAL_DIMENSION_NAMES)
