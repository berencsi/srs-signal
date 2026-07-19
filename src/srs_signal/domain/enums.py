"""Closed vocabularies used at SRS Signal domain boundaries."""

from __future__ import annotations

from enum import StrEnum


class DimensionId(StrEnum):
    SOURCE_TRACEABILITY = "source_traceability"
    LEGAL_BASIS_SPECIFICITY = "legal_basis_specificity"
    REASONING_CHAIN_COMPLETENESS = "reasoning_chain_completeness"
    COUNTERARGUMENT_HANDLING = "counterargument_handling"
    DECISION_EFFECT_JUSTIFICATION = "decision_effect_justification"
    CORRECTION_CAPACITY = "correction_capacity"
    OVERALL_AUDITABILITY = "overall_auditability"


class DimensionStatus(StrEnum):
    CLEARLY_IDENTIFIABLE = "clearly_identifiable"
    PARTIALLY_IDENTIFIABLE = "partially_identifiable"
    NOT_IDENTIFIABLE = "not_identifiable"
    DEMONSTRABLY_ABSENT = "demonstrably_absent"


class AnalysisStatus(StrEnum):
    COMPLETED = "completed"
    INSUFFICIENT_MATERIAL = "insufficient_material"


class ReviewAction(StrEnum):
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"


class ReviewerStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    REVIEW_IN_PROGRESS = "review_in_progress"
    REVIEW_CONFIRMED = "review_confirmed"


class AnalysisProviderType(StrEnum):
    OPENAI_LIVE = "openai_live"
    DETERMINISTIC_DEMO = "deterministic_demo"


class InstitutionType(StrEnum):
    COURT = "court"
    ADMINISTRATIVE_AUTHORITY = "administrative_authority"
    SUPERVISORY_OR_REVIEW_BODY = "supervisory_or_review_body"
    OTHER = "other"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceMatchMethod(StrEnum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    NO_MATCH = "no_match"
