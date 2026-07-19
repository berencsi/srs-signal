"""Strict application-layer contracts for the hackathon MVP workflow."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from srs_signal.domain import (
    AnalysisProviderType,
    ConfidenceLevel,
    DecisionMetadata,
    InstitutionType,
    ProvisionalAuditResult,
    ValidatedAuditResult,
)
from srs_signal.domain.limits import MAX_SOURCE_TEXT_CHARACTERS
from srs_signal.domain.validation import source_text_sha256


CaseId = Annotated[str, StringConstraints(min_length=1, max_length=100)]
SourceText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_SOURCE_TEXT_CHARACTERS),
]
Sha256Text = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64),
]
ShortApplicationText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
ApplicationNarrative = Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]


class _StrictMvpModel(BaseModel):
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


class IssuerDetectionEvidence(_StrictMvpModel):
    """Exact source span supporting the detected issuing institution."""

    quotation: ApplicationNarrative
    character_start: NonNegativeInt
    character_end: NonNegativeInt

    @field_validator("quotation")
    @classmethod
    def non_blank_quotation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("quotation must not be blank")
        return value

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if self.character_end <= self.character_start:
            raise ValueError("character_end must be greater than character_start")
        return self


class InstitutionDetectionResult(_StrictMvpModel):
    """Advisory issuer and institution-type suggestion bound to one source."""

    source_text_sha256: Sha256Text
    detected_institution_name: ShortApplicationText
    suggested_institution_type: InstitutionType
    institution_type_confidence: ConfidenceLevel
    institution_type_reason: ApplicationNarrative
    issuer_evidence: IssuerDetectionEvidence
    detector_provider_type: AnalysisProviderType
    detector_identifier: ShortApplicationText

    @field_validator(
        "detected_institution_name",
        "institution_type_reason",
        "detector_identifier",
    )
    @classmethod
    def non_blank_detection_text(cls, value: str, info: object) -> str:
        if not value.strip():
            raise ValueError(f"{getattr(info, 'field_name', 'value')} must not be blank")
        return value

    @model_validator(mode="after")
    def issuer_name_occurs_in_evidence(self) -> Self:
        if self.detected_institution_name not in self.issuer_evidence.quotation:
            raise ValueError(
                "detected_institution_name must occur in issuer evidence"
            )
        return self


class InstitutionConfirmationStatus(StrEnum):
    CONFIRMED_SUGGESTION = "confirmed_suggestion"
    OVERRIDDEN = "overridden"


class InstitutionConfirmation(_StrictMvpModel):
    """Explicit human confirmation or override of one detector suggestion."""

    detection_result: InstitutionDetectionResult
    confirmed_institution_name: ShortApplicationText
    confirmed_institution_type: InstitutionType
    status: InstitutionConfirmationStatus

    @field_validator("confirmed_institution_name")
    @classmethod
    def non_blank_confirmed_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("confirmed_institution_name must not be blank")
        return value

    @model_validator(mode="after")
    def status_matches_values(self) -> Self:
        matches_suggestion = (
            self.confirmed_institution_name
            == self.detection_result.detected_institution_name
            and self.confirmed_institution_type
            is self.detection_result.suggested_institution_type
        )
        if (
            self.status is InstitutionConfirmationStatus.CONFIRMED_SUGGESTION
            and not matches_suggestion
        ):
            raise ValueError(
                "confirmed_suggestion requires the detected name and suggested type"
            )
        if self.status is InstitutionConfirmationStatus.OVERRIDDEN and matches_suggestion:
            raise ValueError("overridden requires at least one changed institution value")
        return self


def _validate_confirmation_source(
    source_text: str,
    confirmation: InstitutionConfirmation,
) -> None:
    actual_hash = source_text_sha256(source_text)
    detection = confirmation.detection_result
    if detection.source_text_sha256 != actual_hash:
        raise ValueError("Institution confirmation does not match the exact source")
    evidence = detection.issuer_evidence
    if evidence.character_end > len(source_text):
        raise ValueError("Issuer evidence offsets exceed the exact source")
    if source_text[evidence.character_start : evidence.character_end] != evidence.quotation:
        raise ValueError("Issuer evidence does not match the exact source span")


class AuditRequest(_StrictMvpModel):
    """Exact source and already supplied metadata sent to an audit provider."""

    source_text: SourceText
    decision_metadata: DecisionMetadata
    institution_confirmation: InstitutionConfirmation
    bundled_case_id: CaseId | None = None

    @field_validator("bundled_case_id")
    @classmethod
    def non_blank_case_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("bundled_case_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_confirmed_metadata(self) -> Self:
        _validate_confirmation_source(self.source_text, self.institution_confirmation)
        if (
            self.decision_metadata.institution_name
            != self.institution_confirmation.confirmed_institution_name
        ):
            raise ValueError("Decision metadata must use the confirmed institution name")
        if (
            self.decision_metadata.institution_type
            is not self.institution_confirmation.confirmed_institution_type
        ):
            raise ValueError("Decision metadata must use the confirmed institution type")
        return self


class MvpDecisionRecord(_StrictMvpModel):
    """Serializable MVP provenance; it never contains an admitted handle."""

    record_schema_version: Literal["mvp-decision-record-v1"] = (
        "mvp-decision-record-v1"
    )
    case_id: CaseId
    source_text: SourceText
    source_text_sha256: Sha256Text
    decision_metadata: DecisionMetadata
    institution_confirmation: InstitutionConfirmation
    provisional_result: ProvisionalAuditResult
    validated_result: ValidatedAuditResult
    provider_type: AnalysisProviderType
    created_at: datetime

    @field_validator("case_id")
    @classmethod
    def non_blank_case_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("case_id must not be blank")
        return value

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if source_text_sha256(self.source_text) != self.source_text_sha256:
            raise ValueError("source_text_sha256 must match the exact source text")
        _validate_confirmation_source(self.source_text, self.institution_confirmation)
        if (
            self.decision_metadata.institution_name
            != self.institution_confirmation.confirmed_institution_name
            or self.decision_metadata.institution_type
            is not self.institution_confirmation.confirmed_institution_type
        ):
            raise ValueError("Record metadata must retain confirmed institution values")
        if self.provisional_result.decision_metadata != self.decision_metadata:
            raise ValueError("Provisional metadata must match record metadata")
        if self.validated_result.provisional_result != self.provisional_result:
            raise ValueError("Validated provenance must retain the provisional result")
        if self.validated_result.source_text_sha256 != self.source_text_sha256:
            raise ValueError("Validated provenance must use the exact source hash")
        if self.provider_type is not self.provisional_result.provider_type:
            raise ValueError("Provider type must match the provisional result")
        if self.created_at < self.validated_result.evidence_validated_at:
            raise ValueError("Record creation cannot predate evidence validation")
        return self
