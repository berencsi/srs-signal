"""Reviewed-only threshold aggregation for the hackathon MVP."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID

from pydantic import ValidationError

from srs_signal.domain import (
    CANONICAL_DIMENSION_IDS,
    CANONICAL_DIMENSION_NAMES,
    AdmissionError,
    ConfidenceLevel,
    DimensionId,
    DimensionStatus,
    InstitutionType,
    ReviewAction,
    admit_reviewed_audit,
)
from srs_signal.domain.scoring import is_serious_deficiency
from srs_signal.mvp.aggregation_identity import (
    _dimension_pattern_id,
    _label_recurrence_id,
    _length_delimited_sha256,
)
from srs_signal.mvp.aggregation_models import (
    METHODOLOGICAL_LIMITATIONS,
    THRESHOLD_EXPLANATION,
    MvpAggregationReport,
    PrototypeSignalStatus,
    _DeficiencyLabel,
    _DimensionRecurrencePattern,
    _DimensionSignalSummary,
    _ExcludedReviewedEntry,
    _LabelRecurrence,
    _PatternContributor,
    _ReviewedDecisionReference,
)
from srs_signal.mvp.aggregation_normalization import (
    _normalize_deficiency_label,
    _select_recurrence_display_label,
)
from srs_signal.mvp.review_models import MvpReviewedDecisionRecord
from srs_signal.mvp.store import _list_reviewed_records, _reviewed_key


class AggregationWorkflowError(ValueError):
    """Raised when reviewed inputs cannot form a safe threshold report."""


@dataclass(frozen=True, slots=True)
class _ObservationLabel:
    normalized_label: str
    display_label: str


@dataclass(frozen=True, slots=True)
class _EligibleDimensionObservation:
    observation_id: str
    case_id: str
    decision_id: str
    analysis_id: UUID
    review_id: UUID
    final_finding_id: UUID
    dimension_id: DimensionId
    dimension_name: str
    source_text_sha256: str
    institution_name: str
    institution_type: InstitutionType
    review_action: ReviewAction
    score: int
    status: DimensionStatus
    concise_finding: str
    confidence: ConfidenceLevel
    deficiency_labels: tuple[_ObservationLabel, ...]
    evidence_quote_ids: tuple[str, ...]


def _canonical_record_bytes(record: MvpReviewedDecisionRecord) -> bytes:
    native = record.model_dump(mode="json")
    return json.dumps(
        native,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")


def _input_fingerprint(records: tuple[MvpReviewedDecisionRecord, ...]) -> str:
    record_digests = tuple(
        sorted(sha256(_canonical_record_bytes(record)).hexdigest() for record in records)
    )
    return _length_delimited_sha256(*record_digests)


def _normalize_labels(values: tuple[str, ...]) -> tuple[_ObservationLabel, ...]:
    candidates: dict[str, str] = {}
    for value in values:
        normalized = _normalize_deficiency_label(value)
        if normalized is None:
            continue
        key, display = normalized
        candidates.setdefault(key, display)
    return tuple(
        _ObservationLabel(
            normalized_label=key,
            display_label=candidates[key],
        )
        for key in sorted(candidates)
    )


def _safe_issue_reason(reason: str) -> str:
    return reason if reason.strip() else "Reviewed provenance was excluded"


def _readmit_for_aggregation(
    record: MvpReviewedDecisionRecord,
) -> tuple[MvpReviewedDecisionRecord, tuple[DimensionId, ...]]:
    try:
        checked = MvpReviewedDecisionRecord.model_validate(record.model_dump())
        admitted = admit_reviewed_audit(
            checked.reviewed_result,
            checked.analysis_record.source_text,
        )
    except (AttributeError, TypeError, AdmissionError, ValidationError, ValueError) as exc:
        raise AggregationWorkflowError(
            "Reviewed record failed aggregation-boundary exact-source admission"
        ) from exc
    if admitted.record != checked.reviewed_result:
        raise AggregationWorkflowError(
            "Reviewed admission did not reproduce aggregation provenance"
        )
    eligible = tuple(admitted.aggregation_eligible_dimension_ids)
    return checked, eligible


def _observations_for_record(
    record: MvpReviewedDecisionRecord,
    eligible_dimension_ids: tuple[DimensionId, ...],
) -> tuple[_EligibleDimensionObservation, ...]:
    reviewed = {
        item.original_finding.dimension_id: item
        for item in record.reviewed_result.reviewed_findings
    }
    observations: list[_EligibleDimensionObservation] = []
    metadata = record.analysis_record.decision_metadata
    review_id = record.reviewed_result.human_review.review_id
    analysis_id = record.reviewed_result.source_analysis.analysis_id
    for dimension_id in CANONICAL_DIMENSION_IDS:
        if dimension_id not in eligible_dimension_ids:
            continue
        item = reviewed[dimension_id]
        final = item.final_finding
        if (
            item.review_action not in {ReviewAction.ACCEPTED, ReviewAction.EDITED}
            or final is None
            or not final.finding.assessment_performed
            or final.finding.score is None
            or final.finding.status is None
            or final.finding.confidence is None
        ):
            raise AggregationWorkflowError(
                "Derived eligibility did not match the admitted reviewed finding"
            )
        observation_id = _length_delimited_sha256(
            str(review_id),
            str(analysis_id),
            dimension_id.value,
            str(final.finding_id),
        )
        observations.append(
            _EligibleDimensionObservation(
                observation_id=observation_id,
                case_id=record.analysis_record.case_id,
                decision_id=metadata.decision_id,
                analysis_id=analysis_id,
                review_id=review_id,
                final_finding_id=final.finding_id,
                dimension_id=dimension_id,
                dimension_name=CANONICAL_DIMENSION_NAMES[dimension_id],
                source_text_sha256=record.analysis_record.source_text_sha256,
                institution_name=metadata.institution_name,
                institution_type=metadata.institution_type,
                review_action=item.review_action,
                score=final.finding.score,
                status=final.finding.status,
                concise_finding=final.finding.concise_finding,
                confidence=final.finding.confidence,
                deficiency_labels=_normalize_labels(final.finding.deficiency_types),
                evidence_quote_ids=tuple(
                    verification.quote.quote_id
                    for verification in final.evidence_verifications
                ),
            )
        )
    return tuple(observations)


def _contributor(observation: _EligibleDimensionObservation) -> _PatternContributor:
    return _PatternContributor(
        observation_id=observation.observation_id,
        case_id=observation.case_id,
        review_id=observation.review_id,
        institution_type=observation.institution_type,
        review_action=observation.review_action,
        score=observation.score,
        status=observation.status,
        concise_finding=observation.concise_finding,
        confidence=observation.confidence,
        deficiency_labels=tuple(
            _DeficiencyLabel(
                normalized_label=item.normalized_label,
                display_label=item.display_label,
            )
            for item in observation.deficiency_labels
        ),
    )


def _label_recurrences(
    dimension_id: DimensionId,
    serious: tuple[_EligibleDimensionObservation, ...],
) -> tuple[_LabelRecurrence, ...]:
    grouped: dict[str, list[tuple[_EligibleDimensionObservation, str]]] = {}
    for observation in serious:
        for label in observation.deficiency_labels:
            grouped.setdefault(label.normalized_label, []).append(
                (observation, label.display_label)
            )
    recurrent: list[_LabelRecurrence] = []
    for normalized_label in sorted(grouped):
        entries = sorted(
            grouped[normalized_label],
            key=lambda item: (item[0].case_id, item[0].observation_id),
        )
        case_ids = tuple(item[0].case_id for item in entries)
        if len(set(case_ids)) < 2:
            continue
        observation_ids = tuple(item[0].observation_id for item in entries)
        review_ids = tuple(item[0].review_id for item in entries)
        display_label = _select_recurrence_display_label(
            normalized_label,
            tuple(
                (
                    observation.observation_id,
                    tuple(
                        (label.normalized_label, label.display_label)
                        for label in observation.deficiency_labels
                    ),
                )
                for observation, _display in entries
            ),
        )
        recurrent.append(
            _LabelRecurrence(
                recurrence_id=_label_recurrence_id(
                    dimension_id,
                    normalized_label,
                    observation_ids,
                ),
                normalized_label=normalized_label,
                display_label=display_label,
                contributing_observation_ids=observation_ids,
                contributing_case_ids=case_ids,
                contributing_review_ids=review_ids,
            )
        )
    return tuple(recurrent)


def _summaries_and_patterns(
    observations: tuple[_EligibleDimensionObservation, ...],
) -> tuple[
    tuple[_DimensionSignalSummary, ...],
    tuple[_DimensionRecurrencePattern, ...],
]:
    summaries: list[_DimensionSignalSummary] = []
    patterns: list[_DimensionRecurrencePattern] = []
    for dimension_id in CANONICAL_DIMENSION_IDS:
        eligible = tuple(
            sorted(
                (item for item in observations if item.dimension_id is dimension_id),
                key=lambda item: (item.case_id, item.observation_id),
            )
        )
        serious = tuple(item for item in eligible if is_serious_deficiency(item.score))
        contributors = tuple(_contributor(item) for item in serious)
        labels = _label_recurrences(dimension_id, serious)
        institution_types = tuple(
            sorted({item.institution_type for item in serious})
        )
        recurring = len(serious) >= 2
        cross = recurring and len(institution_types) >= 2
        summary = _DimensionSignalSummary(
            dimension_id=dimension_id,
            dimension_name=CANONICAL_DIMENSION_NAMES[dimension_id],
            eligible_reviewed_decision_count=len(eligible),
            eligible_case_ids=tuple(item.case_id for item in eligible),
            serious_reviewed_decision_count=len(serious),
            serious_case_ids=tuple(item.case_id for item in serious),
            serious_institution_types=institution_types,
            serious_contributors=contributors,
            recurrence=recurring,
            cross_institution_recurrence=cross,
            recurring_deficiency_labels=labels,
        )
        summaries.append(summary)
        if recurring:
            observation_ids = tuple(item.observation_id for item in serious)
            patterns.append(
                _DimensionRecurrencePattern(
                    pattern_id=_dimension_pattern_id(
                        dimension_id, observation_ids
                    ),
                    dimension_id=dimension_id,
                    dimension_name=CANONICAL_DIMENSION_NAMES[dimension_id],
                    contributors=contributors,
                    contributing_case_ids=tuple(item.case_id for item in serious),
                    contributing_review_ids=tuple(item.review_id for item in serious),
                    contributing_institution_types=institution_types,
                    recurring_deficiency_labels=labels,
                    cross_institution=cross,
                )
            )
    return tuple(summaries), tuple(patterns)


def build_systemic_signal_report(
    store: Mapping[str, object],
    *,
    generated_at: datetime | None = None,
) -> MvpAggregationReport:
    """Build a fresh report only from current, exact-source-readmitted reviews."""

    if not isinstance(store, Mapping):
        raise AggregationWorkflowError("Aggregation requires the complete MVP store")
    listing = _list_reviewed_records(store)
    issue_by_key = {
        issue.key: _safe_issue_reason(issue.message) for issue in listing.issues
    }
    admitted_records: list[MvpReviewedDecisionRecord] = []
    observations: list[_EligibleDimensionObservation] = []
    seen_review_ids: set[UUID] = set()
    seen_observation_ids: set[str] = set()

    for record in sorted(listing.records, key=lambda item: item.analysis_record.case_id):
        key = _reviewed_key(record.analysis_record.case_id)
        try:
            checked, eligible = _readmit_for_aggregation(record)
            review_id = checked.reviewed_result.human_review.review_id
            if review_id in seen_review_ids:
                raise AggregationWorkflowError(
                    "Reviewed record duplicates an admitted review identity"
                )
            record_observations = _observations_for_record(checked, eligible)
            if any(item.observation_id in seen_observation_ids for item in record_observations):
                raise AggregationWorkflowError(
                    "Reviewed record duplicates an aggregation observation"
                )
        except (AggregationWorkflowError, ValidationError, ValueError) as exc:
            issue_by_key.setdefault(key, _safe_issue_reason(str(exc)))
            continue
        seen_review_ids.add(review_id)
        seen_observation_ids.update(item.observation_id for item in record_observations)
        admitted_records.append(checked)
        observations.extend(record_observations)

    records = tuple(sorted(admitted_records, key=lambda item: item.analysis_record.case_id))
    ordered_observations = tuple(
        sorted(observations, key=lambda item: (item.dimension_id.value, item.case_id))
    )
    try:
        summaries, patterns = _summaries_and_patterns(ordered_observations)
        references = tuple(
            _ReviewedDecisionReference(
                case_id=record.analysis_record.case_id,
                decision_id=record.analysis_record.decision_metadata.decision_id,
                analysis_id=record.analysis_record.provisional_result.analysis_id,
                review_id=record.reviewed_result.human_review.review_id,
                decision_title=record.analysis_record.decision_metadata.title,
                institution_name=record.analysis_record.decision_metadata.institution_name,
                institution_type=record.analysis_record.decision_metadata.institution_type,
                source_text_sha256=record.analysis_record.source_text_sha256,
                review_confirmed_at=record.reviewed_result.human_review.confirmed_at,
            )
            for record in records
        )
    except (AttributeError, TypeError, ValidationError, ValueError) as exc:
        raise AggregationWorkflowError(
            "Admitted reviewed inputs failed aggregation snapshot construction"
        ) from exc
    status = PrototypeSignalStatus.NO_RECURRENCE
    if patterns:
        status = PrototypeSignalStatus.RECURRING_DEFICIENCY
    if any(pattern.cross_institution for pattern in patterns):
        status = PrototypeSignalStatus.CROSS_INSTITUTION_SYSTEMIC_SIGNAL
    current = generated_at or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AggregationWorkflowError("Report generation time must be timezone-aware")
    current = current.astimezone(UTC)
    exclusions = tuple(
        _ExcludedReviewedEntry(store_key=key, reason=issue_by_key[key])
        for key in sorted(issue_by_key)
    )
    try:
        report = MvpAggregationReport(
            generated_at=current,
            input_fingerprint=_input_fingerprint(records),
            valid_reviewed_decision_count=len(records),
            excluded_reviewed_entry_count=len(exclusions),
            excluded_entries=exclusions,
            considered_case_ids=tuple(item.case_id for item in references),
            signal_contributing_case_ids=tuple(
                sorted(
                    {
                        case_id
                        for pattern in patterns
                        for case_id in pattern.contributing_case_ids
                    }
                )
            ),
            reviewed_decisions=references,
            represented_institution_types=tuple(
                sorted({item.institution_type for item in references})
            ),
            serious_observation_count=sum(
                summary.serious_reviewed_decision_count for summary in summaries
            ),
            dimension_summaries=summaries,
            recurrence_patterns=patterns,
            prototype_signal_status=status,
            threshold_explanation=THRESHOLD_EXPLANATION,
            methodological_limitations=METHODOLOGICAL_LIMITATIONS,
        )
        return MvpAggregationReport.model_validate(report.model_dump())
    except (ValidationError, ValueError) as exc:
        raise AggregationWorkflowError("Aggregation report failed revalidation") from exc
