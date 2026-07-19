"""Strict serializable application records for the MVP review workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from srs_signal.domain import (
    CANONICAL_DIMENSION_IDS,
    ConfidenceLevel,
    DimensionId,
    DimensionStatus,
    ReviewAction,
    ReviewedAuditResult,
    ReviewStatus,
)
from srs_signal.mvp.models import InstitutionConfirmation, MvpDecisionRecord


_ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
_NarrativeText = Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
_Score = Annotated[int, Field(ge=0, le=4, strict=True)]


class _StrictReviewModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    def model_copy(
        self, *, update: dict[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if not update:
            return super().model_copy(deep=deep)
        values = self.model_dump()
        values.update(update)
        return type(self).model_validate(values)


def _non_blank_optional(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _non_blank_items(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} cannot contain blank entries")
    return values


class MvpReviewedDecisionRecord(_StrictReviewModel):
    """Serializable review provenance; exact-source readmission is still required."""

    record_schema_version: Literal["mvp-reviewed-decision-record-v1"] = (
        "mvp-reviewed-decision-record-v1"
    )
    analysis_record: MvpDecisionRecord
    reviewed_result: ReviewedAuditResult

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        analysis = MvpDecisionRecord.model_validate(self.analysis_record.model_dump())
        reviewed = ReviewedAuditResult.model_validate(self.reviewed_result.model_dump())
        if analysis != self.analysis_record or reviewed != self.reviewed_result:
            raise ValueError("Reviewed application provenance failed reconstruction")
        if reviewed.source_analysis != analysis.validated_result:
            raise ValueError("Reviewed source analysis must equal the retained validation")
        if reviewed.source_analysis.analysis_id != analysis.provisional_result.analysis_id:
            raise ValueError("Reviewed and retained analysis IDs must match")
        if reviewed.source_text_sha256 != analysis.source_text_sha256:
            raise ValueError("Reviewed and retained source hashes must match")
        if (
            reviewed.source_analysis.provisional_result.decision_metadata
            != analysis.decision_metadata
        ):
            raise ValueError("Reviewed and retained decision metadata must match")
        if reviewed.schema_version != analysis.validated_result.schema_version:
            raise ValueError("Reviewed and retained schema versions must match")
        if (
            reviewed.human_review.methodology_version
            != analysis.validated_result.methodology_version
        ):
            raise ValueError("Reviewed and retained methodology versions must match")
        if reviewed.human_review.status is not ReviewStatus.CONFIRMED:
            raise ValueError("MVP reviewed records require a confirmed human review")
        return self


class _MvpFindingEdit(_StrictReviewModel):
    """Only fields the Commit 4 reviewer is permitted to change."""

    score: _Score | None
    status: DimensionStatus | None
    concise_finding: _NarrativeText
    reasoning: _NarrativeText
    confidence: ConfidenceLevel | None
    limitations: tuple[_NarrativeText, ...] = ()
    identified_missing_elements: tuple[_ShortText, ...] = ()
    deficiency_types: tuple[_ShortText, ...] = ()

    @field_validator("concise_finding", "reasoning")
    @classmethod
    def non_blank_required_text(cls, value: str, info: object) -> str:
        if not value.strip():
            raise ValueError(f"{getattr(info, 'field_name', 'value')} must not be blank")
        return value

    @field_validator(
        "limitations", "identified_missing_elements", "deficiency_types"
    )
    @classmethod
    def non_blank_collection_items(
        cls, value: tuple[str, ...], info: object
    ) -> tuple[str, ...]:
        return _non_blank_items(value, getattr(info, "field_name", "values"))


class _MvpDimensionReviewDraft(_StrictReviewModel):
    dimension_id: DimensionId
    original_finding_id: UUID
    reviewed_finding_id: UUID
    action: ReviewAction | None = None
    edit: _MvpFindingEdit | None = None
    reviewer_note: _NarrativeText | None = None

    @field_validator("reviewer_note")
    @classmethod
    def non_blank_note(cls, value: str | None) -> str | None:
        return _non_blank_optional(value, "reviewer_note")

    @model_validator(mode="after")
    def action_matches_edit(self) -> Self:
        if self.action is ReviewAction.EDITED and self.edit is None:
            raise ValueError("Edited draft actions require edited values")
        if self.action is not ReviewAction.EDITED and self.edit is not None:
            raise ValueError("Only edited draft actions may carry edited values")
        return self


class _MvpReviewDraft(_StrictReviewModel):
    """Incomplete UI state bound to one exact admitted analysis."""

    draft_schema_version: Literal["mvp-review-draft-v1"] = "mvp-review-draft-v1"
    review_id: UUID
    case_id: _ShortText
    source_text_sha256: Annotated[
        str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
    ]
    analysis_id: UUID
    evidence_validated_at: datetime
    institution_confirmation: InstitutionConfirmation
    started_at: datetime
    reviewer_label: _ShortText | None = None
    overall_reviewer_note: _NarrativeText | None = None
    dimensions: tuple[_MvpDimensionReviewDraft, ...]

    @field_validator("case_id")
    @classmethod
    def non_blank_case_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case_id must not be blank")
        return value

    @field_validator("reviewer_label", "overall_reviewer_note")
    @classmethod
    def non_blank_optional_text(
        cls, value: str | None, info: object
    ) -> str | None:
        return _non_blank_optional(value, getattr(info, "field_name", "value"))

    @field_validator("evidence_validated_at", "started_at")
    @classmethod
    def aware_timestamps(cls, value: datetime, info: object) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{getattr(info, 'field_name', 'timestamp')} must be aware")
        return value

    @model_validator(mode="after")
    def validate_binding_and_dimensions(self) -> Self:
        if self.started_at < self.evidence_validated_at:
            raise ValueError("Review draft cannot predate evidence validation")
        if (
            self.institution_confirmation.detection_result.source_text_sha256
            != self.source_text_sha256
        ):
            raise ValueError("Review draft confirmation must match the source hash")
        ids = tuple(item.dimension_id for item in self.dimensions)
        if ids != CANONICAL_DIMENSION_IDS:
            raise ValueError("Review draft dimensions must be in canonical order")
        finding_ids = tuple(item.original_finding_id for item in self.dimensions)
        reviewed_ids = tuple(item.reviewed_finding_id for item in self.dimensions)
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("Review draft source finding IDs must be unique")
        if len(set(reviewed_ids)) != len(reviewed_ids):
            raise ValueError("Review draft record IDs must be unique")
        return self

    @property
    def is_complete(self) -> bool:
        return all(item.action is not None for item in self.dimensions)
