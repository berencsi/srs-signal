"""Exact-source human-review orchestration for the hackathon MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from srs_signal.domain import (
    CANONICAL_DIMENSION_IDS,
    AdmissionError,
    DimensionFinding,
    DimensionId,
    EvidenceValidatedDimensionFinding,
    HumanReview,
    ReviewAction,
    ReviewedDimensionFinding,
    ReviewStatus,
    admit_reviewed_audit,
    admit_validated_audit,
    create_reviewed_audit,
)
from srs_signal.domain.validation import (
    EvidenceVerificationError,
    validate_finding_evidence,
)
from srs_signal.mvp.models import MvpDecisionRecord
from srs_signal.mvp.review_models import (
    MvpReviewedDecisionRecord,
    _MvpDimensionReviewDraft,
    _MvpFindingEdit,
    _MvpReviewDraft,
)


class ReviewWorkflowError(ValueError):
    """Raised when review provenance cannot cross the MVP workflow boundary."""


class ReviewDraftMismatchError(ReviewWorkflowError):
    """Raised when a draft is not bound to the selected analysis."""


@dataclass(frozen=True, slots=True)
class _ReadmittedProfile:
    record: MvpReviewedDecisionRecord
    eligible_dimension_ids: tuple[DimensionId, ...]
    is_aggregation_eligible: bool


def _revalidate_analysis(record: MvpDecisionRecord) -> MvpDecisionRecord:
    if type(record) is not MvpDecisionRecord:
        raise ReviewWorkflowError("Review requires an exact MvpDecisionRecord")
    try:
        checked = MvpDecisionRecord.model_validate(record.model_dump())
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ReviewWorkflowError("Stored analysis failed structural revalidation") from exc
    try:
        admitted = admit_validated_audit(checked.validated_result, checked.source_text)
    except (AdmissionError, ValidationError, ValueError) as exc:
        raise ReviewWorkflowError("Stored analysis failed exact-source admission") from exc
    if admitted.record != checked.validated_result:
        raise ReviewWorkflowError("Admitted analysis did not reproduce stored provenance")
    return checked


def _create_review_draft(
    analysis_record: MvpDecisionRecord,
    *,
    now: datetime | None = None,
    reviewer_label: str | None = None,
    overall_reviewer_note: str | None = None,
) -> _MvpReviewDraft:
    checked = _revalidate_analysis(analysis_record)
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ReviewWorkflowError("Review draft time must be timezone-aware")
    started_at = max(current, checked.validated_result.evidence_validated_at)
    by_dimension = {
        item.dimension_id: item for item in checked.validated_result.validated_findings
    }
    return _MvpReviewDraft(
        review_id=uuid4(),
        case_id=checked.case_id,
        source_text_sha256=checked.source_text_sha256,
        analysis_id=checked.provisional_result.analysis_id,
        evidence_validated_at=checked.validated_result.evidence_validated_at,
        institution_confirmation=checked.institution_confirmation,
        started_at=started_at,
        reviewer_label=reviewer_label.strip() if reviewer_label and reviewer_label.strip() else None,
        overall_reviewer_note=(
            overall_reviewer_note.strip()
            if overall_reviewer_note and overall_reviewer_note.strip()
            else None
        ),
        dimensions=tuple(
            _MvpDimensionReviewDraft(
                dimension_id=dimension_id,
                original_finding_id=by_dimension[dimension_id].finding_id,
                reviewed_finding_id=uuid4(),
            )
            for dimension_id in CANONICAL_DIMENSION_IDS
        ),
    )


def _validate_draft_for_analysis(
    draft: _MvpReviewDraft, analysis_record: MvpDecisionRecord
) -> _MvpReviewDraft:
    checked_analysis = _revalidate_analysis(analysis_record)
    try:
        checked_draft = _MvpReviewDraft.model_validate(draft.model_dump())
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ReviewDraftMismatchError("Stored review draft is malformed") from exc
    if checked_draft.case_id != checked_analysis.case_id:
        raise ReviewDraftMismatchError("Review draft belongs to another case")
    if checked_draft.source_text_sha256 != checked_analysis.source_text_sha256:
        raise ReviewDraftMismatchError("Review draft source hash is stale")
    if checked_draft.analysis_id != checked_analysis.provisional_result.analysis_id:
        raise ReviewDraftMismatchError("Review draft analysis identity is stale")
    if (
        checked_draft.evidence_validated_at
        != checked_analysis.validated_result.evidence_validated_at
    ):
        raise ReviewDraftMismatchError("Review draft validation identity is stale")
    if checked_draft.institution_confirmation != checked_analysis.institution_confirmation:
        raise ReviewDraftMismatchError("Review draft institution confirmation is stale")
    expected_findings = {
        item.dimension_id: item.finding_id
        for item in checked_analysis.validated_result.validated_findings
    }
    if any(
        expected_findings.get(item.dimension_id) != item.original_finding_id
        for item in checked_draft.dimensions
    ):
        raise ReviewDraftMismatchError("Review draft source findings are stale")
    return checked_draft


def _apply_edit(
    original: EvidenceValidatedDimensionFinding,
    edit: _MvpFindingEdit,
    *,
    source_text: str,
    source_hash: str,
) -> EvidenceValidatedDimensionFinding:
    raw_original = original.finding
    values = raw_original.model_dump()
    values.update(edit.model_dump())
    try:
        changed = DimensionFinding.model_validate(values)
        return validate_finding_evidence(
            changed,
            source_text,
            expected_source_text_sha256=source_hash,
        )
    except (ValidationError, ValueError, EvidenceVerificationError) as exc:
        raise ReviewWorkflowError("Edited finding failed domain or evidence validation") from exc


def _reviewed_findings_from_draft(
    draft: _MvpReviewDraft,
    analysis_record: MvpDecisionRecord,
) -> tuple[ReviewedDimensionFinding, ...]:
    checked_draft = _validate_draft_for_analysis(draft, analysis_record)
    if not checked_draft.is_complete:
        raise ReviewWorkflowError("All seven dimensions require an explicit disposition")
    originals = {
        item.dimension_id: item
        for item in analysis_record.validated_result.validated_findings
    }
    reviewed: list[ReviewedDimensionFinding] = []
    for draft_item in checked_draft.dimensions:
        original = originals[draft_item.dimension_id]
        assert draft_item.action is not None
        final = None
        if draft_item.action is ReviewAction.ACCEPTED:
            final = original
        elif draft_item.action is ReviewAction.EDITED:
            assert draft_item.edit is not None
            final = _apply_edit(
                original,
                draft_item.edit,
                source_text=analysis_record.source_text,
                source_hash=analysis_record.source_text_sha256,
            )
        reviewed.append(
            ReviewedDimensionFinding(
                reviewed_finding_id=draft_item.reviewed_finding_id,
                original_finding=original,
                review_action=draft_item.action,
                final_finding=final,
                reviewer_note=draft_item.reviewer_note,
            )
        )
    return tuple(reviewed)


def _human_review_from_draft(
    draft: _MvpReviewDraft,
    analysis_record: MvpDecisionRecord,
    *,
    finalized_at: datetime | None = None,
) -> HumanReview:
    checked = _validate_draft_for_analysis(draft, analysis_record)
    current = finalized_at or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ReviewWorkflowError("Review finalization time must be timezone-aware")
    completed = max(current, checked.started_at)
    return HumanReview(
        review_id=checked.review_id,
        analysis_id=checked.analysis_id,
        reviewer_label=checked.reviewer_label,
        status=ReviewStatus.CONFIRMED,
        started_at=checked.started_at,
        updated_at=completed,
        confirmed_at=completed,
        overall_reviewer_note=checked.overall_reviewer_note,
        methodology_version=analysis_record.validated_result.methodology_version,
    )


def finalize_human_review(
    analysis_record: MvpDecisionRecord,
    *,
    human_review: HumanReview,
    reviewed_findings: tuple[ReviewedDimensionFinding, ...],
    final_overall_limitations: tuple[str, ...] = (),
) -> MvpReviewedDecisionRecord:
    """Create and readmit confirmed review provenance without leaking handles."""

    checked = _revalidate_analysis(analysis_record)
    try:
        review = HumanReview.model_validate(human_review.model_dump())
        findings = tuple(
            ReviewedDimensionFinding.model_validate(item.model_dump())
            for item in reviewed_findings
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise ReviewWorkflowError("Review input failed structural revalidation") from exc
    if review.status is not ReviewStatus.CONFIRMED:
        raise ReviewWorkflowError("MVP finalization requires a confirmed human review")
    dimensions = tuple(item.original_finding.dimension_id for item in findings)
    if dimensions != CANONICAL_DIMENSION_IDS:
        raise ReviewWorkflowError("Reviewed findings must use exact canonical order")
    try:
        admitted_validation = admit_validated_audit(
            checked.validated_result, checked.source_text
        )
        reviewed_result = create_reviewed_audit(
            admitted_validation,
            human_review=review,
            reviewed_findings=findings,
            final_overall_limitations=final_overall_limitations,
        )
        admitted_review = admit_reviewed_audit(reviewed_result, checked.source_text)
    except (AdmissionError, ValidationError, ValueError) as exc:
        raise ReviewWorkflowError("Human review failed exact-source creation") from exc
    if admitted_review.record != reviewed_result:
        raise ReviewWorkflowError("Reviewed admission did not reproduce created provenance")
    try:
        return MvpReviewedDecisionRecord(
            analysis_record=checked,
            reviewed_result=reviewed_result,
        )
    except (ValidationError, ValueError) as exc:
        raise ReviewWorkflowError(
            "Reviewed application provenance failed structural validation"
        ) from exc


def _finalize_review_draft(
    analysis_record: MvpDecisionRecord,
    draft: _MvpReviewDraft,
    *,
    finalized_at: datetime | None = None,
) -> MvpReviewedDecisionRecord:
    checked_draft = _validate_draft_for_analysis(draft, analysis_record)
    findings = _reviewed_findings_from_draft(checked_draft, analysis_record)
    human_review = _human_review_from_draft(
        checked_draft, analysis_record, finalized_at=finalized_at
    )
    return finalize_human_review(
        analysis_record,
        human_review=human_review,
        reviewed_findings=findings,
        final_overall_limitations=analysis_record.provisional_result.overall_limitations,
    )


def _readmit_reviewed_profile(record: MvpReviewedDecisionRecord) -> _ReadmittedProfile:
    try:
        checked = MvpReviewedDecisionRecord.model_validate(record.model_dump())
        admitted = admit_reviewed_audit(
            checked.reviewed_result, checked.analysis_record.source_text
        )
    except (AttributeError, TypeError, AdmissionError, ValidationError, ValueError) as exc:
        raise ReviewWorkflowError("Reviewed profile failed exact-source readmission") from exc
    if admitted.record != checked.reviewed_result:
        raise ReviewWorkflowError("Reviewed profile admission did not reproduce provenance")
    return _ReadmittedProfile(
        record=checked,
        eligible_dimension_ids=tuple(admitted.aggregation_eligible_dimension_ids),
        is_aggregation_eligible=admitted.is_aggregation_eligible,
    )
