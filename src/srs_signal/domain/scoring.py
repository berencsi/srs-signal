"""Approved MVP score semantics without aggregate calculations."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from srs_signal.domain.enums import DimensionStatus


SCORE_LABELS: Final = MappingProxyType(
    {
        0: "Demonstrably absent essential auditable component",
        1: "Severely limited or not identifiable",
        2: "Partially auditable",
        3: "Substantially auditable",
        4: "Fully auditable",
    }
)

SERIOUS_DEFICIENCY_SCORES: Final = frozenset({0, 1})
PROVISIONAL_RECURRENCE_MINIMUM: Final = 2
PROVISIONAL_CROSS_INSTITUTION_MINIMUM: Final = 2


def score_label(score: int) -> str:
    """Return the approved human-readable rubric label."""

    try:
        return SCORE_LABELS[score]
    except KeyError as exc:
        raise ValueError("Score must be an integer from 0 to 4") from exc


def validate_score_status(score: int, status: DimensionStatus) -> None:
    """Enforce approved score/status compatibility.

    Status describes visibility while score describes auditability, so the two
    are not generally interchangeable. The MVP has two strict endpoints:
    demonstrated absence maps to score 0, and non-identifiability in an
    otherwise sufficient audit object maps to score 1.
    """

    if isinstance(score, bool) or score not in SCORE_LABELS:
        raise ValueError("Score must be an integer from 0 to 4")
    allowed_scores = {
        DimensionStatus.CLEARLY_IDENTIFIABLE: {2, 3, 4},
        DimensionStatus.PARTIALLY_IDENTIFIABLE: {1, 2, 3},
        DimensionStatus.NOT_IDENTIFIABLE: {1},
        DimensionStatus.DEMONSTRABLY_ABSENT: {0},
    }
    if score not in allowed_scores[status]:
        allowed = ", ".join(str(value) for value in sorted(allowed_scores[status]))
        raise ValueError(f"{status.value} status permits only score(s): {allowed}")


def is_serious_deficiency(score: int) -> bool:
    """Apply the approved provisional threshold without aggregating results."""

    if isinstance(score, bool) or score not in SCORE_LABELS:
        raise ValueError("Score must be an integer from 0 to 4")
    return score in SERIOUS_DEFICIENCY_SCORES
