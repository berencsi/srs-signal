"""Runtime-only source-backed workflow admission boundaries."""

from __future__ import annotations

from typing import Protocol, TypeVar
from weakref import WeakSet

from pydantic import BaseModel, ValidationError

from srs_signal.domain.enums import AnalysisStatus, DimensionId, ReviewAction, ReviewStatus
from srs_signal.domain.schemas import (
    HumanReview,
    ReviewedAuditResult,
    ReviewedDimensionFinding,
    ValidatedAuditResult,
)
from srs_signal.domain.validation import (
    EvidenceVerificationError,
    _PreparedSource,
    _build_validated_audit_from_prepared_source,
    _prepare_audit_source,
    _validate_finding_against_prepared_source,
)


class AdmissionError(ValueError):
    """Raised when an untrusted provenance record cannot be readmitted."""


class AdmittedValidatedAuditHandle(Protocol):
    """Non-instantiable public typing surface for an admitted validation."""

    @property
    def record(self) -> ValidatedAuditResult: ...


class AdmittedReviewedAuditHandle(Protocol):
    """Non-instantiable public typing surface for an admitted review."""

    @property
    def record(self) -> ReviewedAuditResult: ...

    @property
    def aggregation_eligible_dimension_ids(self) -> tuple[DimensionId, ...]: ...

    @property
    def is_aggregation_eligible(self) -> bool: ...


class _AdmissionCapability:
    __slots__ = ()


_ADMISSION_CAPABILITY = _AdmissionCapability()


class _AdmittedValidatedAudit:
    """Guarded internal handle created only after source-backed admission."""

    __slots__ = ("__weakref__", "_prepared_source", "_record")

    def __init__(
        self,
        capability: object,
        *,
        record: ValidatedAuditResult,
        prepared_source: _PreparedSource,
    ) -> None:
        if capability is not _ADMISSION_CAPABILITY:
            raise TypeError("Admitted validation handles are factory-created")
        object.__setattr__(self, "_record", record)
        object.__setattr__(self, "_prepared_source", prepared_source)
        _ADMITTED_VALIDATED_HANDLES.add(self)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Admitted validation handles are immutable")

    @property
    def record(self) -> ValidatedAuditResult:
        return self._record


class _AdmittedReviewedAudit:
    """Guarded internal handle created only after complete review readmission."""

    __slots__ = ("__weakref__", "_admitted_source", "_record")

    def __init__(
        self,
        capability: object,
        *,
        record: ReviewedAuditResult,
        admitted_source: _AdmittedValidatedAudit,
    ) -> None:
        if capability is not _ADMISSION_CAPABILITY:
            raise TypeError("Admitted review handles are factory-created")
        _require_admitted_validated(admitted_source)
        object.__setattr__(self, "_record", record)
        object.__setattr__(self, "_admitted_source", admitted_source)
        _ADMITTED_REVIEWED_HANDLES.add(self)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Admitted review handles are immutable")

    @property
    def record(self) -> ReviewedAuditResult:
        return self._record

    @property
    def aggregation_eligible_dimension_ids(self) -> tuple[DimensionId, ...]:
        if (
            self.record.human_review.status is not ReviewStatus.CONFIRMED
            or self.record.source_analysis.analysis_status is not AnalysisStatus.COMPLETED
        ):
            return ()
        return tuple(
            reviewed.original_finding.dimension_id
            for reviewed in self.record.reviewed_findings
            if reviewed.review_action in {ReviewAction.ACCEPTED, ReviewAction.EDITED}
            and reviewed.final_finding is not None
            and reviewed.final_finding.finding.assessment_performed
            and reviewed.final_finding.finding.score is not None
        )

    @property
    def is_aggregation_eligible(self) -> bool:
        return bool(self.aggregation_eligible_dimension_ids)


_ADMITTED_VALIDATED_HANDLES: WeakSet[_AdmittedValidatedAudit] = WeakSet()
_ADMITTED_REVIEWED_HANDLES: WeakSet[_AdmittedReviewedAudit] = WeakSet()


def _require_admitted_validated(value: object) -> _AdmittedValidatedAudit:
    if (
        type(value) is not _AdmittedValidatedAudit
        or value not in _ADMITTED_VALIDATED_HANDLES
    ):
        raise AdmissionError("Review creation requires a factory-admitted audit")
    return value


ModelT = TypeVar("ModelT", bound=BaseModel)


def _revalidate_record(record: ModelT, model_type: type[ModelT]) -> ModelT:
    """Force normal validation even if the input used an unchecked constructor."""

    try:
        return model_type.model_validate(record.model_dump())
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise AdmissionError(
            f"{model_type.__name__} failed structural revalidation"
        ) from exc


def admit_validated_audit(
    record: ValidatedAuditResult, source_text: str
) -> AdmittedValidatedAuditHandle:
    """Readmit persisted evidence only by reproducing it from exact source text."""

    structural = _revalidate_record(record, ValidatedAuditResult)
    try:
        prepared = _prepare_audit_source(structural.provisional_result, source_text)
        reproduced = _build_validated_audit_from_prepared_source(
            structural.provisional_result,
            prepared,
            validated_at=structural.evidence_validated_at,
        )
    except (EvidenceVerificationError, ValidationError, TypeError, ValueError) as exc:
        raise AdmissionError("Validated audit evidence could not be reproduced") from exc
    if reproduced != structural:
        raise AdmissionError(
            "Stored validation provenance does not match source-backed verification"
        )
    return _AdmittedValidatedAudit(
        _ADMISSION_CAPABILITY, record=reproduced, prepared_source=prepared
    )


def _verify_reviewed_findings(
    admitted_source: _AdmittedValidatedAudit,
    reviewed_findings: tuple[ReviewedDimensionFinding, ...],
) -> tuple[ReviewedDimensionFinding, ...]:
    source_by_id = {
        item.finding_id: item for item in admitted_source.record.validated_findings
    }
    verified: list[ReviewedDimensionFinding] = []
    for submitted in reviewed_findings:
        item = _revalidate_record(submitted, ReviewedDimensionFinding)
        source = source_by_id.get(item.original_finding.finding_id)
        if source is None or source != item.original_finding:
            raise AdmissionError("Review does not retain the admitted source finding")
        if item.review_action is ReviewAction.EDITED:
            assert item.final_finding is not None
            try:
                reproduced_final = _validate_finding_against_prepared_source(
                    item.final_finding.finding, admitted_source._prepared_source
                )
            except (EvidenceVerificationError, ValidationError, ValueError) as exc:
                raise AdmissionError("Edited finding evidence could not be reproduced") from exc
            if reproduced_final != item.final_finding:
                raise AdmissionError(
                    "Edited finding provenance does not match source-backed verification"
                )
        verified.append(item)
    return tuple(verified)


def create_reviewed_audit(
    admitted_source: AdmittedValidatedAuditHandle,
    *,
    human_review: HumanReview,
    reviewed_findings: tuple[ReviewedDimensionFinding, ...],
    final_overall_limitations: tuple[str, ...] = (),
) -> ReviewedAuditResult:
    """Create review provenance only from a factory-admitted source audit."""

    admitted = _require_admitted_validated(admitted_source)
    verified_findings = _verify_reviewed_findings(admitted, reviewed_findings)
    try:
        return ReviewedAuditResult(
            schema_version=admitted.record.schema_version,
            source_analysis=admitted.record,
            source_text_sha256=admitted.record.source_text_sha256,
            human_review=_revalidate_record(human_review, HumanReview),
            reviewed_findings=verified_findings,
            final_overall_limitations=final_overall_limitations,
        )
    except (ValidationError, ValueError) as exc:
        raise AdmissionError("Review record failed source-backed creation") from exc


def admit_reviewed_audit(
    record: ReviewedAuditResult, source_text: str
) -> AdmittedReviewedAuditHandle:
    """Readmit a persisted review by reproducing its complete evidence chain."""

    structural = _revalidate_record(record, ReviewedAuditResult)
    admitted_source = admit_validated_audit(structural.source_analysis, source_text)
    admitted = _require_admitted_validated(admitted_source)
    reproduced = create_reviewed_audit(
        admitted,
        human_review=structural.human_review,
        reviewed_findings=structural.reviewed_findings,
        final_overall_limitations=structural.final_overall_limitations,
    )
    if reproduced != structural:
        raise AdmissionError("Stored review provenance could not be reproduced")
    return _AdmittedReviewedAudit(
        _ADMISSION_CAPABILITY, record=reproduced, admitted_source=admitted
    )
