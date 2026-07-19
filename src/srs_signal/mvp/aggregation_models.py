"""Strict serializable reports for the MVP threshold dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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
    CANONICAL_DIMENSION_NAMES,
    ConfidenceLevel,
    DimensionId,
    DimensionStatus,
    InstitutionType,
    ReviewAction,
)
from srs_signal.domain.scoring import validate_score_status
from srs_signal.mvp.aggregation_identity import (
    _dimension_pattern_id,
    _label_recurrence_id,
)
from srs_signal.mvp.aggregation_normalization import (
    _normalize_deficiency_label,
    _select_recurrence_display_label,
)


_ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
_NarrativeText = Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
_Sha256Text = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
_NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
_Score = Annotated[int, Field(ge=0, le=4, strict=True)]

THRESHOLD_EXPLANATION = (
    "A serious deficiency is an admitted eligible reviewed score of 0 or 1. "
    "Recurrence requires the same canonical dimension in at least two distinct "
    "reviewed decisions. Cross-institution recurrence additionally requires at "
    "least two institution types."
)

METHODOLOGICAL_LIMITATIONS = (
    "All demonstration decisions are wholly fictional.",
    "The sample is small, purposive, and non-representative.",
    "This is a transparent threshold demonstration, not statistical inference.",
    "The report does not determine legality, institutional intent, or democratic quality.",
    "No weighted 0–100 score is calculated.",
    "Commit 5 uses deterministic local data and no live AI provider.",
)


class _StrictAggregationModel(BaseModel):
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


class PrototypeSignalStatus(StrEnum):
    NO_RECURRENCE = "no_recurrence"
    RECURRING_DEFICIENCY = "recurring_deficiency"
    CROSS_INSTITUTION_SYSTEMIC_SIGNAL = "cross_institution_systemic_signal"


class _ReviewedDecisionReference(_StrictAggregationModel):
    case_id: _ShortText
    decision_id: _ShortText
    analysis_id: UUID
    review_id: UUID
    decision_title: _ShortText
    institution_name: _ShortText
    institution_type: InstitutionType
    source_text_sha256: _Sha256Text
    review_confirmed_at: datetime

    @field_validator("review_confirmed_at")
    @classmethod
    def confirmed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review_confirmed_at must be timezone-aware")
        return value


class _ExcludedReviewedEntry(_StrictAggregationModel):
    store_key: _ShortText
    reason: _NarrativeText


class _DeficiencyLabel(_StrictAggregationModel):
    normalized_label: _ShortText
    display_label: _ShortText

    @model_validator(mode="after")
    def validate_label(self) -> Self:
        normalized = _normalize_deficiency_label(self.display_label)
        if normalized != (self.normalized_label, self.display_label):
            raise ValueError("Deficiency label must use its canonical normalized form")
        return self


class _PatternContributor(_StrictAggregationModel):
    observation_id: _Sha256Text
    case_id: _ShortText
    review_id: UUID
    institution_type: InstitutionType
    review_action: ReviewAction
    score: _Score
    status: DimensionStatus
    concise_finding: _NarrativeText
    confidence: ConfidenceLevel
    deficiency_labels: tuple[_DeficiencyLabel, ...] = ()

    @model_validator(mode="after")
    def validate_contributor(self) -> Self:
        if self.review_action not in {ReviewAction.ACCEPTED, ReviewAction.EDITED}:
            raise ValueError("Pattern contributors must be accepted or edited")
        if self.score not in {0, 1}:
            raise ValueError("Pattern contributors must be serious deficiencies")
        validate_score_status(self.score, self.status)
        normalized = tuple(item.normalized_label for item in self.deficiency_labels)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Contributor deficiency labels must be unique")
        if normalized != tuple(sorted(normalized)):
            raise ValueError("Contributor deficiency labels must be sorted")
        return self


class _LabelRecurrence(_StrictAggregationModel):
    recurrence_id: _Sha256Text
    normalized_label: _ShortText
    display_label: _ShortText
    contributing_observation_ids: tuple[_Sha256Text, ...]
    contributing_case_ids: tuple[_ShortText, ...]
    contributing_review_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def validate_recurrence(self) -> Self:
        if len(self.contributing_case_ids) < 2:
            raise ValueError("Label recurrence requires two reviewed decisions")
        for values, label in (
            (self.contributing_observation_ids, "observation IDs"),
            (self.contributing_case_ids, "case IDs"),
            (self.contributing_review_ids, "review IDs"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"Label recurrence {label} must be unique")
        if not (
            len(self.contributing_observation_ids)
            == len(self.contributing_case_ids)
            == len(self.contributing_review_ids)
        ):
            raise ValueError("Label recurrence contributor identities must align")
        if self.contributing_case_ids != tuple(sorted(self.contributing_case_ids)):
            raise ValueError("Label recurrence case IDs must be sorted")
        return self


def _validate_label_recurrences(
    dimension_id: DimensionId,
    recurrences: tuple[_LabelRecurrence, ...],
    contributors: tuple[_PatternContributor, ...],
) -> None:
    contributor_by_observation_id = {
        item.observation_id: item for item in contributors
    }
    if len(contributor_by_observation_id) != len(contributors):
        raise ValueError("Dimension contributor observation IDs must be unique")
    recurrence_ids = tuple(item.recurrence_id for item in recurrences)
    normalized_labels = tuple(item.normalized_label for item in recurrences)
    if len(set(recurrence_ids)) != len(recurrence_ids):
        raise ValueError("Label recurrence IDs must be unique")
    if len(set(normalized_labels)) != len(normalized_labels):
        raise ValueError("Recurring normalized labels must be unique")
    if normalized_labels != tuple(sorted(normalized_labels)):
        raise ValueError("Recurring normalized labels must be sorted")

    for recurrence in recurrences:
        observation_ids = recurrence.contributing_observation_ids
        expected_id = _label_recurrence_id(
            dimension_id,
            recurrence.normalized_label,
            observation_ids,
        )
        if recurrence.recurrence_id != expected_id:
            raise ValueError("Label recurrence ID must reproduce its contributors")
        aligned_contributors: list[_PatternContributor] = []
        for observation_id, case_id, review_id in zip(
            observation_ids,
            recurrence.contributing_case_ids,
            recurrence.contributing_review_ids,
            strict=True,
        ):
            contributor = contributor_by_observation_id.get(observation_id)
            if contributor is None:
                raise ValueError(
                    "Label recurrence observation must belong to the dimension"
                )
            if contributor.case_id != case_id:
                raise ValueError("Label recurrence case identity is misaligned")
            if contributor.review_id != review_id:
                raise ValueError("Label recurrence review identity is misaligned")
            aligned_contributors.append(contributor)
        expected_display = _select_recurrence_display_label(
            recurrence.normalized_label,
            tuple(
                (
                    contributor.observation_id,
                    tuple(
                        (label.normalized_label, label.display_label)
                        for label in contributor.deficiency_labels
                    ),
                )
                for contributor in aligned_contributors
            ),
        )
        if recurrence.display_label != expected_display:
            raise ValueError(
                "Recurrence display label must reproduce its contributors"
            )


def _validate_contributors_against_reviewed_decisions(
    contributors: tuple[_PatternContributor, ...],
    reviewed_decision_by_case_id: dict[str, _ReviewedDecisionReference],
) -> None:
    for contributor in contributors:
        reviewed = reviewed_decision_by_case_id.get(contributor.case_id)
        if reviewed is None:
            raise ValueError(
                "Serious contributor must reference a considered reviewed decision"
            )
        if contributor.review_id != reviewed.review_id:
            raise ValueError("Serious contributor review ID must match its case")
        if contributor.institution_type is not reviewed.institution_type:
            raise ValueError("Serious contributor institution type must match its case")


class _DimensionSignalSummary(_StrictAggregationModel):
    dimension_id: DimensionId
    dimension_name: _ShortText
    eligible_reviewed_decision_count: _NonNegativeInt
    eligible_case_ids: tuple[_ShortText, ...]
    serious_reviewed_decision_count: _NonNegativeInt
    serious_case_ids: tuple[_ShortText, ...]
    serious_institution_types: tuple[InstitutionType, ...]
    serious_contributors: tuple[_PatternContributor, ...]
    recurrence: bool
    cross_institution_recurrence: bool
    recurring_deficiency_labels: tuple[_LabelRecurrence, ...] = ()

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.dimension_name != CANONICAL_DIMENSION_NAMES[self.dimension_id]:
            raise ValueError("Dimension summary name must be canonical")
        if len(set(self.eligible_case_ids)) != len(self.eligible_case_ids):
            raise ValueError("Eligible case IDs must be unique")
        if self.eligible_reviewed_decision_count != len(self.eligible_case_ids):
            raise ValueError("Eligible count must match eligible case IDs")
        if self.eligible_case_ids != tuple(sorted(self.eligible_case_ids)):
            raise ValueError("Eligible case IDs must be sorted")
        contributor_cases = tuple(item.case_id for item in self.serious_contributors)
        contributor_observations = tuple(
            item.observation_id for item in self.serious_contributors
        )
        if len(set(contributor_cases)) != len(contributor_cases):
            raise ValueError("A case may contribute once to one dimension")
        if len(set(contributor_observations)) != len(contributor_observations):
            raise ValueError("Serious observation IDs must be unique")
        if self.serious_case_ids != contributor_cases:
            raise ValueError("Serious case IDs must reproduce contributors")
        if self.serious_case_ids != tuple(sorted(self.serious_case_ids)):
            raise ValueError("Serious case IDs must be sorted")
        if self.serious_reviewed_decision_count != len(self.serious_contributors):
            raise ValueError("Serious count must reproduce contributors")
        if self.serious_reviewed_decision_count > self.eligible_reviewed_decision_count:
            raise ValueError("Serious count cannot exceed eligible count")
        if not set(self.serious_case_ids).issubset(set(self.eligible_case_ids)):
            raise ValueError("Every serious contributor case must be eligible")
        expected_types = tuple(
            sorted({item.institution_type for item in self.serious_contributors})
        )
        if self.serious_institution_types != expected_types:
            raise ValueError("Serious institution types must reproduce contributors")
        expected_recurrence = self.serious_reviewed_decision_count >= 2
        expected_cross = expected_recurrence and len(expected_types) >= 2
        if self.recurrence is not expected_recurrence:
            raise ValueError("Dimension recurrence must be derived from contributors")
        if self.cross_institution_recurrence is not expected_cross:
            raise ValueError("Cross-institution recurrence must be derived")
        _validate_label_recurrences(
            self.dimension_id,
            self.recurring_deficiency_labels,
            self.serious_contributors,
        )
        return self


class _DimensionRecurrencePattern(_StrictAggregationModel):
    pattern_id: _Sha256Text
    dimension_id: DimensionId
    dimension_name: _ShortText
    contributors: tuple[_PatternContributor, ...]
    contributing_case_ids: tuple[_ShortText, ...]
    contributing_review_ids: tuple[UUID, ...]
    contributing_institution_types: tuple[InstitutionType, ...]
    recurring_deficiency_labels: tuple[_LabelRecurrence, ...] = ()
    cross_institution: bool
    threshold_explanation: Literal[THRESHOLD_EXPLANATION] = THRESHOLD_EXPLANATION

    @model_validator(mode="after")
    def validate_pattern(self) -> Self:
        if self.dimension_name != CANONICAL_DIMENSION_NAMES[self.dimension_id]:
            raise ValueError("Pattern dimension name must be canonical")
        if len(self.contributors) < 2:
            raise ValueError("Dimension recurrence requires two reviewed decisions")
        cases = tuple(item.case_id for item in self.contributors)
        reviews = tuple(item.review_id for item in self.contributors)
        observations = tuple(item.observation_id for item in self.contributors)
        for values, label in (
            (cases, "cases"),
            (reviews, "reviews"),
            (observations, "observations"),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"Pattern {label} must be unique")
        if self.contributing_case_ids != cases:
            raise ValueError("Pattern case IDs must reproduce contributors")
        if self.contributing_case_ids != tuple(sorted(self.contributing_case_ids)):
            raise ValueError("Pattern case IDs must be sorted")
        if self.contributing_review_ids != reviews:
            raise ValueError("Pattern review IDs must reproduce contributors")
        expected_types = tuple(
            sorted({item.institution_type for item in self.contributors})
        )
        if self.contributing_institution_types != expected_types:
            raise ValueError("Pattern institution types must reproduce contributors")
        if self.cross_institution is not (len(expected_types) >= 2):
            raise ValueError("Pattern cross-institution status must be derived")
        expected_pattern_id = _dimension_pattern_id(
            self.dimension_id,
            observations,
        )
        if self.pattern_id != expected_pattern_id:
            raise ValueError("Pattern ID must reproduce its dimension and contributors")
        _validate_label_recurrences(
            self.dimension_id,
            self.recurring_deficiency_labels,
            self.contributors,
        )
        return self


class MvpAggregationReport(_StrictAggregationModel):
    """Serializable threshold report; it contains no admission capability."""

    report_schema_version: Literal["mvp-aggregation-report-v1"] = (
        "mvp-aggregation-report-v1"
    )
    generated_at: datetime
    # This digest covers complete admitted wrappers in the builder. The report
    # retains only references, so validation enforces its shape but never treats
    # it as proof of admission or attempts a weaker partial recomputation.
    input_fingerprint: _Sha256Text
    valid_reviewed_decision_count: _NonNegativeInt
    excluded_reviewed_entry_count: _NonNegativeInt
    excluded_entries: tuple[_ExcludedReviewedEntry, ...]
    considered_case_ids: tuple[_ShortText, ...]
    signal_contributing_case_ids: tuple[_ShortText, ...]
    reviewed_decisions: tuple[_ReviewedDecisionReference, ...]
    represented_institution_types: tuple[InstitutionType, ...]
    serious_observation_count: _NonNegativeInt
    dimension_summaries: tuple[_DimensionSignalSummary, ...]
    recurrence_patterns: tuple[_DimensionRecurrencePattern, ...]
    prototype_signal_status: PrototypeSignalStatus
    threshold_explanation: Literal[THRESHOLD_EXPLANATION] = THRESHOLD_EXPLANATION
    methodological_limitations: tuple[_NarrativeText, ...] = METHODOLOGICAL_LIMITATIONS

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.methodological_limitations != METHODOLOGICAL_LIMITATIONS:
            raise ValueError("Methodological limitations are fixed by the MVP contract")
        if self.excluded_reviewed_entry_count != len(self.excluded_entries):
            raise ValueError("Excluded count must match excluded entries")
        excluded_keys = tuple(item.store_key for item in self.excluded_entries)
        if len(set(excluded_keys)) != len(excluded_keys):
            raise ValueError("Excluded store keys must be unique")
        if excluded_keys != tuple(sorted(excluded_keys)):
            raise ValueError("Excluded entries must use deterministic ordering")

        cases = tuple(item.case_id for item in self.reviewed_decisions)
        reviews = tuple(item.review_id for item in self.reviewed_decisions)
        if len(set(cases)) != len(cases):
            raise ValueError("Reviewed case IDs must be unique")
        if len(set(reviews)) != len(reviews):
            raise ValueError("Reviewed review IDs must be unique")
        reviewed_decision_by_case_id = {
            item.case_id: item for item in self.reviewed_decisions
        }
        if len(reviewed_decision_by_case_id) != len(self.reviewed_decisions):
            raise ValueError("Reviewed decision cases must map uniquely")
        if self.considered_case_ids != cases:
            raise ValueError("Considered cases must reproduce reviewed decisions")
        if self.considered_case_ids != tuple(sorted(self.considered_case_ids)):
            raise ValueError("Considered cases must be sorted")
        if self.valid_reviewed_decision_count != len(self.reviewed_decisions):
            raise ValueError("Valid reviewed count must reproduce reviewed decisions")
        expected_types = tuple(
            sorted({item.institution_type for item in self.reviewed_decisions})
        )
        if self.represented_institution_types != expected_types:
            raise ValueError("Represented institution types must be derived")

        dimensions = tuple(item.dimension_id for item in self.dimension_summaries)
        if dimensions != CANONICAL_DIMENSION_IDS:
            raise ValueError("Report requires seven canonical dimension summaries")
        expected_serious = sum(
            item.serious_reviewed_decision_count for item in self.dimension_summaries
        )
        if self.serious_observation_count != expected_serious:
            raise ValueError("Serious observation count must reproduce summaries")
        for summary in self.dimension_summaries:
            # Structural validation binds eligibility claims to considered cases.
            # Authentic eligibility remains builder-derived from reviewed admission.
            if not set(summary.eligible_case_ids).issubset(
                reviewed_decision_by_case_id
            ):
                raise ValueError(
                    "Every eligible case must reference a considered reviewed decision"
                )
            _validate_contributors_against_reviewed_decisions(
                summary.serious_contributors,
                reviewed_decision_by_case_id,
            )

        pattern_ids = tuple(item.pattern_id for item in self.recurrence_patterns)
        pattern_dimensions = tuple(item.dimension_id for item in self.recurrence_patterns)
        if len(set(pattern_ids)) != len(pattern_ids):
            raise ValueError("Pattern IDs must be unique")
        if len(set(pattern_dimensions)) != len(pattern_dimensions):
            raise ValueError("A dimension may have one recurrence pattern")
        recurring_summaries = tuple(
            item.dimension_id for item in self.dimension_summaries if item.recurrence
        )
        if pattern_dimensions != recurring_summaries:
            raise ValueError("Patterns must reproduce recurring dimensions")
        summary_by_id = {item.dimension_id: item for item in self.dimension_summaries}
        for pattern in self.recurrence_patterns:
            summary = summary_by_id[pattern.dimension_id]
            _validate_contributors_against_reviewed_decisions(
                pattern.contributors,
                reviewed_decision_by_case_id,
            )
            if pattern.contributors != summary.serious_contributors:
                raise ValueError("Pattern contributors must reproduce the summary")
            if (
                pattern.recurring_deficiency_labels
                != summary.recurring_deficiency_labels
            ):
                raise ValueError("Pattern labels must reproduce the summary")

        expected_signal_cases = tuple(
            sorted(
                {
                    case_id
                    for pattern in self.recurrence_patterns
                    for case_id in pattern.contributing_case_ids
                }
            )
        )
        if self.signal_contributing_case_ids != expected_signal_cases:
            raise ValueError("Signal contributors must be derived from patterns")
        if not set(expected_signal_cases).issubset(set(self.considered_case_ids)):
            raise ValueError("Signal contributors must be considered cases")

        expected_status = PrototypeSignalStatus.NO_RECURRENCE
        if self.recurrence_patterns:
            expected_status = PrototypeSignalStatus.RECURRING_DEFICIENCY
        if any(item.cross_institution for item in self.recurrence_patterns):
            expected_status = PrototypeSignalStatus.CROSS_INSTITUTION_SYSTEMIC_SIGNAL
        if self.prototype_signal_status is not expected_status:
            raise ValueError("Prototype signal status must be derived from patterns")
        return self
