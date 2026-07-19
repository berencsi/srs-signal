"""Strict and deeply immutable Pydantic v2 domain contracts."""

from __future__ import annotations

import math
import unicodedata
from datetime import date, datetime
from statistics import median
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

from srs_signal.domain.dimensions import (
    CANONICAL_DIMENSION_IDS,
    CANONICAL_DIMENSION_NAMES,
)
from srs_signal.domain.enums import (
    AnalysisProviderType,
    AnalysisStatus,
    ConfidenceLevel,
    DimensionId,
    DimensionStatus,
    EvidenceMatchMethod,
    InstitutionType,
    ReviewAction,
    ReviewerStatus,
    ReviewStatus,
)
from srs_signal.domain.scoring import (
    PROVISIONAL_CROSS_INSTITUTION_MINIMUM,
    PROVISIONAL_RECURRENCE_MINIMUM,
    validate_score_status,
)
from srs_signal.domain.limits import MAX_OCCURRENCES_PER_QUOTATION


ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
NarrativeText = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
VersionText = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Sha256Text = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)
]
Score = Annotated[int, Field(ge=0, le=4, strict=True)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
Percentage = Annotated[float, Field(ge=0.0, le=100.0, strict=True)]
ScoreStatistic = Annotated[float, Field(ge=0.0, le=4.0, strict=True)]
STATISTICAL_TOLERANCE = 1e-9


class StrictDomainModel(BaseModel):
    """Base contract for frozen records with validated copy operations."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )

    def model_copy(
        self, *, update: dict[str, Any] | None = None, deep: bool = False
    ) -> Self:
        """Revalidate updates instead of permitting Pydantic's unchecked copy path."""

        if not update:
            return super().model_copy(deep=deep)
        values = self.model_dump()
        values.update(update)
        return type(self).model_validate(values)


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _require_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _percentage(numerator: int, denominator: int) -> float:
    return numerator / denominator * 100.0


def _normalized_evidence_value(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    characters: list[str] = []
    for character in normalized:
        if character.isspace():
            if characters and characters[-1] == " ":
                continue
            character = " "
        characters.append(character)
    return "".join(characters)


class DecisionMetadata(StrictDomainModel):
    decision_id: ShortText
    title: ShortText
    institution_name: ShortText
    institution_type: InstitutionType
    decision_type: ShortText
    jurisdiction_or_domain: ShortText
    decision_date: date | None = None
    source_type: ShortText
    contextual_note: NarrativeText | None = None

    @field_validator(
        "decision_id",
        "title",
        "institution_name",
        "decision_type",
        "jurisdiction_or_domain",
        "source_type",
    )
    @classmethod
    def non_blank_required_fields(cls, value: str, info: object) -> str:
        return _require_non_blank(value, getattr(info, "field_name", "value"))

    @field_validator("contextual_note")
    @classmethod
    def non_blank_optional_note(cls, value: str | None) -> str | None:
        return None if value is None else _require_non_blank(value, "contextual_note")


class EvidenceQuote(StrictDomainModel):
    quote_id: ShortText
    text: NarrativeText
    reported_document_location: ShortText
    character_start: NonNegativeInt | None = None
    character_end: NonNegativeInt | None = None

    @field_validator("quote_id", "text", "reported_document_location")
    @classmethod
    def non_blank_quote_fields(cls, value: str, info: object) -> str:
        return _require_non_blank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def validate_offset_pair(self) -> Self:
        if (self.character_start is None) != (self.character_end is None):
            raise ValueError("character_start and character_end must be supplied together")
        if (
            self.character_start is not None
            and self.character_end is not None
            and self.character_end <= self.character_start
        ):
            raise ValueError("character_end must be greater than character_start")
        return self


class EvidenceOccurrence(StrictDomainModel):
    character_start: NonNegativeInt
    character_end: NonNegativeInt
    source_text: NarrativeText
    match_method: Literal[EvidenceMatchMethod.EXACT, EvidenceMatchMethod.NORMALIZED]

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.character_end <= self.character_start:
            raise ValueError("Evidence occurrence must have a non-empty span")
        if self.character_end - self.character_start != len(self.source_text):
            raise ValueError("Evidence span length must match retained source text")
        return self


class EvidenceVerificationResult(StrictDomainModel):
    quote: EvidenceQuote
    matched: bool
    primary_match_method: EvidenceMatchMethod
    occurrences: Annotated[
        tuple[EvidenceOccurrence, ...],
        Field(max_length=MAX_OCCURRENCES_PER_QUOTATION),
    ]
    supplied_offsets_valid: bool | None
    ambiguous: bool

    @model_validator(mode="after")
    def validate_result_consistency(self) -> Self:
        if self.matched != bool(self.occurrences):
            raise ValueError("matched must reflect whether occurrences exist")
        occurrence_methods = {occurrence.match_method for occurrence in self.occurrences}
        if self.matched:
            expected_primary = (
                EvidenceMatchMethod.EXACT
                if EvidenceMatchMethod.EXACT in occurrence_methods
                else EvidenceMatchMethod.NORMALIZED
            )
            if self.primary_match_method is not expected_primary:
                raise ValueError("Primary method must preserve exact-match precedence")
        elif self.primary_match_method is not EvidenceMatchMethod.NO_MATCH:
            raise ValueError("Unmatched evidence must use no_match")
        spans = {
            (occurrence.character_start, occurrence.character_end)
            for occurrence in self.occurrences
        }
        if len(spans) != len(self.occurrences):
            raise ValueError("Evidence occurrences must have unique source spans")
        for occurrence in self.occurrences:
            if occurrence.match_method is EvidenceMatchMethod.EXACT:
                if occurrence.source_text != self.quote.text:
                    raise ValueError("Exact occurrence must equal the displayed quotation")
            else:
                if occurrence.source_text == self.quote.text:
                    raise ValueError("An identical occurrence must be classified as exact")
                if (
                    _normalized_evidence_value(occurrence.source_text)
                    != _normalized_evidence_value(self.quote.text)
                ):
                    raise ValueError(
                        "Normalized occurrence must be normalization-equivalent"
                    )
        if self.ambiguous != (len(self.occurrences) > 1):
            raise ValueError("ambiguous must reflect equivalent repeated occurrences")
        offsets_supplied = self.quote.character_start is not None
        if offsets_supplied != (self.supplied_offsets_valid is not None):
            raise ValueError("supplied_offsets_valid must reflect offset presence")
        if self.quote.character_start is not None and self.quote.character_end is not None:
            expected_validity = (
                self.quote.character_start,
                self.quote.character_end,
            ) in spans
            if self.supplied_offsets_valid is not expected_validity:
                raise ValueError("supplied_offsets_valid must match equivalent spans")
        return self


class DimensionFinding(StrictDomainModel):
    """Structurally valid provisional finding; evidence is not yet trusted."""

    finding_id: UUID
    dimension_id: DimensionId
    dimension_name: ShortText
    assessment_performed: bool
    score: Score | None
    status: DimensionStatus | None
    concise_finding: NarrativeText
    reasoning: NarrativeText
    supporting_quotations: tuple[EvidenceQuote, ...] = ()
    confidence: ConfidenceLevel | None
    limitations: tuple[NarrativeText, ...] = ()
    identified_missing_elements: tuple[ShortText, ...] = ()
    deficiency_types: tuple[ShortText, ...] = ()

    @field_validator("dimension_name", "concise_finding", "reasoning")
    @classmethod
    def non_blank_finding_fields(cls, value: str, info: object) -> str:
        return _require_non_blank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        expected_name = CANONICAL_DIMENSION_NAMES[self.dimension_id]
        if self.dimension_name != expected_name:
            raise ValueError(
                f"dimension_name must be {expected_name!r} for {self.dimension_id.value}"
            )
        quote_ids = [quote.quote_id for quote in self.supporting_quotations]
        if len(set(quote_ids)) != len(quote_ids):
            raise ValueError("Supporting quotation IDs must be unique within a finding")

        if self.assessment_performed:
            if self.score is None or self.status is None or self.confidence is None:
                raise ValueError(
                    "Assessed dimensions require score, status, and confidence"
                )
            validate_score_status(self.score, self.status)
            if (
                self.status is DimensionStatus.DEMONSTRABLY_ABSENT
                and not self.supporting_quotations
            ):
                raise ValueError(
                    "demonstrably_absent requires a supporting quotation for verification"
                )
        else:
            if self.score is not None or self.status is not None:
                raise ValueError("Unassessed dimensions cannot have score or status")
            if self.confidence is not None:
                raise ValueError("Unassessed dimensions require confidence=None")
            if self.supporting_quotations:
                raise ValueError("Unassessed dimensions cannot assert supporting evidence")
            if self.identified_missing_elements or self.deficiency_types:
                raise ValueError(
                    "Unassessed dimensions cannot classify institutional deficiencies"
                )
        return self


class ProvisionalAuditResult(StrictDomainModel):
    """Structurally valid provider output that is not yet human-reviewable."""

    schema_version: VersionText
    analysis_id: UUID
    analysis_timestamp: datetime
    provider_type: AnalysisProviderType
    model_identifier: ShortText
    methodology_version: VersionText
    prompt_version: VersionText
    decision_metadata: DecisionMetadata
    analysis_status: AnalysisStatus
    dimension_findings: tuple[DimensionFinding, ...]
    overall_limitations: tuple[NarrativeText, ...] = ()
    insufficiency_reasons: tuple[NarrativeText, ...] = ()
    reviewer_status: ReviewerStatus

    @field_validator("analysis_timestamp")
    @classmethod
    def aware_analysis_timestamp(cls, value: datetime) -> datetime:
        checked = _require_aware(value, "analysis_timestamp")
        assert checked is not None
        return checked

    @field_validator(
        "schema_version", "model_identifier", "methodology_version", "prompt_version"
    )
    @classmethod
    def non_blank_versions(cls, value: str, info: object) -> str:
        return _require_non_blank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def validate_audit_invariants(self) -> Self:
        if self.reviewer_status is not ReviewerStatus.PENDING_REVIEW:
            raise ValueError("AI output must begin as pending_review")
        if len(self.dimension_findings) != len(CANONICAL_DIMENSION_IDS):
            raise ValueError("Audit results must contain exactly seven dimensions")
        ids = [finding.dimension_id for finding in self.dimension_findings]
        if len(set(ids)) != len(ids):
            raise ValueError("Audit results cannot contain duplicate dimensions")
        if set(ids) != set(CANONICAL_DIMENSION_IDS):
            raise ValueError("Audit results must contain all seven canonical dimensions")
        finding_ids = [finding.finding_id for finding in self.dimension_findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("Finding IDs must be unique within an audit")
        if self.analysis_status is AnalysisStatus.COMPLETED:
            if not all(finding.assessment_performed for finding in self.dimension_findings):
                raise ValueError("Completed audits require seven assessed dimensions")
            if self.insufficiency_reasons:
                raise ValueError("Completed audits cannot include insufficiency reasons")
        else:
            if any(finding.assessment_performed for finding in self.dimension_findings):
                raise ValueError(
                    "Insufficient-material audits require seven unassessed dimensions"
                )
            if not self.insufficiency_reasons:
                raise ValueError(
                    "Insufficient-material audits require an explicit insufficiency reason"
                )
        return self

class EvidenceValidatedDimensionFinding(StrictDomainModel):
    """Finding whose complete quotation set was verified against one source hash."""

    finding: DimensionFinding
    evidence_verifications: tuple[EvidenceVerificationResult, ...]
    source_text_sha256: Sha256Text

    @model_validator(mode="after")
    def validate_evidence_coverage(self) -> Self:
        quote_ids = [quote.quote_id for quote in self.finding.supporting_quotations]
        verification_ids = [item.quote.quote_id for item in self.evidence_verifications]
        if len(set(verification_ids)) != len(verification_ids):
            raise ValueError("Evidence verification quote IDs must be unique")
        if set(verification_ids) != set(quote_ids) or len(verification_ids) != len(
            quote_ids
        ):
            raise ValueError("Evidence verification coverage must be complete")
        quotes_by_id = {
            quote.quote_id: quote for quote in self.finding.supporting_quotations
        }
        for verification in self.evidence_verifications:
            if verification.quote != quotes_by_id[verification.quote.quote_id]:
                raise ValueError("Verification must retain the original quotation")
            if not verification.matched:
                raise ValueError("Validated findings cannot contain unmatched evidence")
            if verification.supplied_offsets_valid is False:
                raise ValueError("Validated findings cannot contain invalid offsets")
        if (
            self.finding.status is DimensionStatus.DEMONSTRABLY_ABSENT
            and not self.evidence_verifications
        ):
            raise ValueError(
                "demonstrably_absent requires successfully verified evidence"
            )
        return self

    @property
    def finding_id(self) -> UUID:
        return self.finding.finding_id

    @property
    def dimension_id(self) -> DimensionId:
        return self.finding.dimension_id


class ValidatedAuditResult(StrictDomainModel):
    """Serializable evidence-validation provenance; runtime admission is separate."""

    schema_version: VersionText
    provisional_result: ProvisionalAuditResult
    source_text_sha256: Sha256Text
    evidence_validated_at: datetime
    validated_findings: tuple[EvidenceValidatedDimensionFinding, ...]

    @field_validator("evidence_validated_at")
    @classmethod
    def aware_validation_timestamp(cls, value: datetime) -> datetime:
        checked = _require_aware(value, "evidence_validated_at")
        assert checked is not None
        return checked

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.evidence_validated_at < self.provisional_result.analysis_timestamp:
            raise ValueError("Evidence validation cannot predate the analysis")
        if self.schema_version != self.provisional_result.schema_version:
            raise ValueError("Validated and provisional schema versions must match")
        if len(self.validated_findings) != len(
            self.provisional_result.dimension_findings
        ):
            raise ValueError("Every provisional finding requires evidence validation")
        provisional_by_id = {
            finding.finding_id: finding
            for finding in self.provisional_result.dimension_findings
        }
        validated_ids = [item.finding_id for item in self.validated_findings]
        if len(set(validated_ids)) != len(validated_ids):
            raise ValueError("Validated findings must be unique")
        if set(validated_ids) != set(provisional_by_id):
            raise ValueError("Validated findings must cover every provisional finding")
        for item in self.validated_findings:
            if item.finding != provisional_by_id[item.finding_id]:
                raise ValueError("Validated finding must match provisional values")
            if item.source_text_sha256 != self.source_text_sha256:
                raise ValueError("Finding validation must use the audit source-text hash")
        return self

    @property
    def analysis_id(self) -> UUID:
        return self.provisional_result.analysis_id

    @property
    def analysis_status(self) -> AnalysisStatus:
        return self.provisional_result.analysis_status

    @property
    def methodology_version(self) -> str:
        return self.provisional_result.methodology_version


class HumanReview(StrictDomainModel):
    review_id: UUID
    analysis_id: UUID
    reviewer_label: ShortText | None = None
    status: ReviewStatus
    started_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    overall_reviewer_note: NarrativeText | None = None
    methodology_version: VersionText

    @field_validator("started_at", "updated_at", "confirmed_at")
    @classmethod
    def aware_review_timestamps(
        cls, value: datetime | None, info: object
    ) -> datetime | None:
        return _require_aware(value, getattr(info, "field_name", "timestamp"))

    @field_validator("reviewer_label", "overall_reviewer_note")
    @classmethod
    def non_blank_optional_review_text(
        cls, value: str | None, info: object
    ) -> str | None:
        if value is None:
            return None
        return _require_non_blank(value, getattr(info, "field_name", "value"))

    @model_validator(mode="after")
    def validate_review_timeline(self) -> Self:
        if self.updated_at < self.started_at:
            raise ValueError("updated_at cannot precede started_at")
        if self.status is ReviewStatus.CONFIRMED:
            if self.confirmed_at is None:
                raise ValueError("Confirmed reviews require confirmed_at")
            if self.confirmed_at != self.updated_at:
                raise ValueError("confirmed_at must equal the final updated_at")
        elif self.confirmed_at is not None:
            raise ValueError("Unconfirmed reviews require confirmed_at=None")
        return self


class ReviewedDimensionFinding(StrictDomainModel):
    reviewed_finding_id: UUID
    original_finding: EvidenceValidatedDimensionFinding
    review_action: ReviewAction
    final_finding: EvidenceValidatedDimensionFinding | None
    reviewer_note: NarrativeText | None = None

    @field_validator("reviewer_note")
    @classmethod
    def non_blank_reviewer_note(cls, value: str | None) -> str | None:
        return None if value is None else _require_non_blank(value, "reviewer_note")

    @model_validator(mode="after")
    def validate_review_action(self) -> Self:
        if self.review_action is ReviewAction.ACCEPTED:
            if self.final_finding != self.original_finding:
                raise ValueError("Accepted findings must preserve validated original values")
        elif self.review_action is ReviewAction.EDITED:
            if self.final_finding is None:
                raise ValueError("Edited findings require evidence-validated final values")
            if self.final_finding.dimension_id is not self.original_finding.dimension_id:
                raise ValueError("Edited findings cannot change dimension identity")
            if self.final_finding.finding_id != self.original_finding.finding_id:
                raise ValueError("Edited findings must retain the original finding ID")
            if (
                self.final_finding.finding.assessment_performed
                is not self.original_finding.finding.assessment_performed
            ):
                raise ValueError("Edited findings must preserve assessment state")
            if (
                self.final_finding.source_text_sha256
                != self.original_finding.source_text_sha256
            ):
                raise ValueError("Edited findings must use the original source-text hash")
            if self.final_finding == self.original_finding:
                raise ValueError("Edited findings must contain an actual change")
        elif self.final_finding is not None:
            raise ValueError(
                "Rejected findings preserve provenance but have no final finding"
            )
        return self


class ReviewedAuditResult(StrictDomainModel):
    """Serializable review provenance; it carries no workflow trust or eligibility."""

    schema_version: VersionText
    source_analysis: ValidatedAuditResult
    source_text_sha256: Sha256Text
    human_review: HumanReview
    reviewed_findings: tuple[ReviewedDimensionFinding, ...]
    final_overall_limitations: tuple[NarrativeText, ...] = ()

    @model_validator(mode="after")
    def validate_reviewed_result(self) -> Self:
        if self.human_review.analysis_id != self.source_analysis.analysis_id:
            raise ValueError("Review must reference the source analysis ID")
        if self.human_review.methodology_version != self.source_analysis.methodology_version:
            raise ValueError("Review methodology version must match the source analysis")
        if self.schema_version != self.source_analysis.schema_version:
            raise ValueError("Reviewed and source schema versions must match")
        if self.source_text_sha256 != self.source_analysis.source_text_sha256:
            raise ValueError("Reviewed result must retain the source-text hash")
        if self.human_review.started_at < self.source_analysis.evidence_validated_at:
            raise ValueError("Human review cannot begin before evidence validation")
        if len(self.reviewed_findings) != len(self.source_analysis.validated_findings):
            raise ValueError("Every source dimension requires a review action")
        source_by_id = {
            item.finding_id: item for item in self.source_analysis.validated_findings
        }
        reviewed_ids = [item.original_finding.finding_id for item in self.reviewed_findings]
        review_record_ids = [item.reviewed_finding_id for item in self.reviewed_findings]
        if len(set(review_record_ids)) != len(review_record_ids):
            raise ValueError("Reviewed finding record IDs must be unique")
        if len(set(reviewed_ids)) != len(reviewed_ids):
            raise ValueError("A source finding cannot be reviewed more than once")
        if set(reviewed_ids) != set(source_by_id):
            raise ValueError("Reviewed findings must correspond to every source finding")
        for reviewed in self.reviewed_findings:
            source = source_by_id[reviewed.original_finding.finding_id]
            if source != reviewed.original_finding:
                raise ValueError("Original reviewed values must match the validated source")
            if (
                reviewed.final_finding is not None
                and reviewed.final_finding.source_text_sha256 != self.source_text_sha256
            ):
                raise ValueError("Final findings must retain the source-text hash")
        return self

class ScoreCountEntry(StrictDomainModel):
    score: Score
    count: NonNegativeInt


class StatusCountEntry(StrictDomainModel):
    status: DimensionStatus
    count: NonNegativeInt


class InstitutionTypeCountEntry(StrictDomainModel):
    institution_type: InstitutionType
    count: NonNegativeInt


class DimensionAggregate(StrictDomainModel):
    dimension_id: DimensionId
    dimension_name: ShortText
    reviewed_decision_count: NonNegativeInt
    assessed_decision_count: NonNegativeInt
    rejected_or_unscored_count: NonNegativeInt
    mean_score: ScoreStatistic | None
    median_score: ScoreStatistic | None
    score_distribution: tuple[ScoreCountEntry, ...]
    status_distribution: tuple[StatusCountEntry, ...]
    serious_deficiency_count: NonNegativeInt
    serious_deficiency_percentage: Percentage | None
    institution_type_distribution: tuple[InstitutionTypeCountEntry, ...]
    limitations: tuple[NarrativeText, ...] = ()

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.dimension_name != CANONICAL_DIMENSION_NAMES[self.dimension_id]:
            raise ValueError("Aggregate dimension name must match canonical name")
        if self.reviewed_decision_count != (
            self.assessed_decision_count + self.rejected_or_unscored_count
        ):
            raise ValueError("Reviewed count must equal assessed plus rejected/unscored")

        score_keys = [entry.score for entry in self.score_distribution]
        if len(score_keys) != 5 or set(score_keys) != set(range(5)):
            raise ValueError("Score distribution must cover scores 0 through 4 exactly once")
        score_counts = {entry.score: entry.count for entry in self.score_distribution}
        if sum(score_counts.values()) != self.assessed_decision_count:
            raise ValueError("Score distribution total must equal assessed count")

        status_keys = [entry.status for entry in self.status_distribution]
        if len(status_keys) != 4 or set(status_keys) != set(DimensionStatus):
            raise ValueError("Status distribution must cover all statuses exactly once")
        if sum(entry.count for entry in self.status_distribution) != self.assessed_decision_count:
            raise ValueError("Status distribution total must equal assessed count")
        status_counts = {entry.status: entry.count for entry in self.status_distribution}
        if status_counts[DimensionStatus.DEMONSTRABLY_ABSENT] != score_counts[0]:
            raise ValueError("Aggregate score/status distributions violate the MVP matrix")
        if status_counts[DimensionStatus.NOT_IDENTIFIABLE] > score_counts[1]:
            raise ValueError("Aggregate score/status distributions violate the MVP matrix")
        partial_score_one = (
            score_counts[1] - status_counts[DimensionStatus.NOT_IDENTIFIABLE]
        )
        if partial_score_one > status_counts[DimensionStatus.PARTIALLY_IDENTIFIABLE]:
            raise ValueError("Aggregate score/status distributions violate the MVP matrix")
        if score_counts[4] > status_counts[DimensionStatus.CLEARLY_IDENTIFIABLE]:
            raise ValueError("Aggregate score/status distributions violate the MVP matrix")

        institution_keys = [
            entry.institution_type for entry in self.institution_type_distribution
        ]
        if len(set(institution_keys)) != len(institution_keys):
            raise ValueError("Institution distribution cannot contain duplicate types")
        if (
            sum(entry.count for entry in self.institution_type_distribution)
            != self.assessed_decision_count
        ):
            raise ValueError("Institution distribution total must equal assessed count")

        expected_serious = score_counts[0] + score_counts[1]
        if self.serious_deficiency_count != expected_serious:
            raise ValueError("Serious deficiency count must equal score-0 and score-1 counts")

        if self.assessed_decision_count == 0:
            if self.mean_score is not None or self.median_score is not None:
                raise ValueError("Zero assessed count requires null mean and median")
            if self.serious_deficiency_percentage is not None:
                raise ValueError("Zero assessed count requires null percentage")
        else:
            if self.mean_score is None or self.median_score is None:
                raise ValueError("Assessed results require mean and median")
            expanded_scores = tuple(
                score for score in range(5) for _ in range(score_counts[score])
            )
            expected_mean = sum(expanded_scores) / len(expanded_scores)
            expected_median = float(median(expanded_scores))
            if not math.isclose(
                self.mean_score, expected_mean, abs_tol=STATISTICAL_TOLERANCE
            ):
                raise ValueError("Mean score must match the score distribution")
            if not math.isclose(
                self.median_score, expected_median, abs_tol=STATISTICAL_TOLERANCE
            ):
                raise ValueError("Median score must match the score distribution")
            expected_percentage = _percentage(
                self.serious_deficiency_count, self.assessed_decision_count
            )
            if self.serious_deficiency_percentage is None or not math.isclose(
                self.serious_deficiency_percentage,
                expected_percentage,
                abs_tol=STATISTICAL_TOLERANCE,
            ):
                raise ValueError("Serious deficiency percentage must match its counts")
        return self


class RecurrencePattern(StrictDomainModel):
    pattern_id: ShortText
    dimension_ids: tuple[DimensionId, ...]
    deficiency_type: ShortText
    decision_count: NonNegativeInt
    assessed_denominator: NonNegativeInt
    percentage: Percentage | None
    institution_types_affected: tuple[InstitutionType, ...]
    institution_type_breadth: NonNegativeInt
    decision_ids: tuple[ShortText, ...]
    threshold_definition: NarrativeText
    plain_language_explanation: NarrativeText
    limitations: tuple[NarrativeText, ...] = ()

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if not self.dimension_ids:
            raise ValueError("Recurrence patterns require at least one dimension")
        if len(set(self.dimension_ids)) != len(self.dimension_ids):
            raise ValueError("Recurrence dimension IDs must be unique")
        if len(set(self.decision_ids)) != len(self.decision_ids):
            raise ValueError("Recurrence decision IDs must be unique")
        if self.decision_count != len(self.decision_ids):
            raise ValueError("Decision count must equal the number of decision IDs")
        if self.decision_count < PROVISIONAL_RECURRENCE_MINIMUM:
            raise ValueError("Recurrence requires at least two reviewed decisions")
        if self.decision_count > self.assessed_denominator:
            raise ValueError("Decision count cannot exceed assessed denominator")
        if len(set(self.institution_types_affected)) != len(
            self.institution_types_affected
        ):
            raise ValueError("Affected institution types must be unique")
        if self.institution_type_breadth != len(self.institution_types_affected):
            raise ValueError("Institution breadth must equal unique affected types")
        if not self.institution_types_affected or self.institution_type_breadth < 1:
            raise ValueError("Recurrence requires an affected institution type")
        if self.institution_type_breadth > self.decision_count:
            raise ValueError("Institution breadth cannot exceed decision count")
        expected = _percentage(self.decision_count, self.assessed_denominator)
        if self.percentage is None or not math.isclose(
            self.percentage, expected, abs_tol=STATISTICAL_TOLERANCE
        ):
            raise ValueError("Recurrence percentage must match its counts")
        return self

    @property
    def is_cross_institution(self) -> bool:
        return self.institution_type_breadth >= PROVISIONAL_CROSS_INSTITUTION_MINIMUM


_REQUIRED_DISCLAIMER = (
    "This prototype signal is based on a limited, non-representative sample. "
    "It identifies recurring patterns; it does not determine that a legal "
    "violation or systemic dysfunction exists."
)


class AggregateSignal(StrictDomainModel):
    schema_version: VersionText
    aggregate_id: UUID
    calculated_at: datetime
    methodology_version: VersionText
    formula_id: Literal["threshold_recurrence_v1"] = "threshold_recurrence_v1"
    reviewed_sample_size: NonNegativeInt
    institution_type_distribution: tuple[InstitutionTypeCountEntry, ...]
    dimension_aggregates: tuple[DimensionAggregate, ...]
    recurrence_patterns: tuple[RecurrencePattern, ...]
    sample_limitations: tuple[NarrativeText, ...]
    calculation_explanation: NarrativeText
    label: Literal["Prototype Systemic Signal"] = "Prototype Systemic Signal"
    disclaimer: Literal[_REQUIRED_DISCLAIMER] = _REQUIRED_DISCLAIMER

    @field_validator("calculated_at")
    @classmethod
    def aware_calculated_at(cls, value: datetime) -> datetime:
        checked = _require_aware(value, "calculated_at")
        assert checked is not None
        return checked

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        institution_keys = [
            entry.institution_type for entry in self.institution_type_distribution
        ]
        if len(set(institution_keys)) != len(institution_keys):
            raise ValueError("Sample institution distribution cannot contain duplicates")
        if (
            sum(entry.count for entry in self.institution_type_distribution)
            != self.reviewed_sample_size
        ):
            raise ValueError("Institution distribution must total reviewed sample size")
        if len(self.dimension_aggregates) != len(CANONICAL_DIMENSION_IDS):
            raise ValueError("Aggregate signal must contain exactly seven dimensions")
        ids = [aggregate.dimension_id for aggregate in self.dimension_aggregates]
        if len(set(ids)) != len(ids) or set(ids) != set(CANONICAL_DIMENSION_IDS):
            raise ValueError("Aggregate signal requires seven unique canonical dimensions")
        if any(
            aggregate.reviewed_decision_count > self.reviewed_sample_size
            for aggregate in self.dimension_aggregates
        ):
            raise ValueError("Dimension reviewed count cannot exceed sample size")
        return self
