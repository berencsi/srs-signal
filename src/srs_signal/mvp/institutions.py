"""Source-bound institution detection and confirmation helpers."""

from __future__ import annotations

from pydantic import ValidationError

from srs_signal.domain import DecisionMetadata, InstitutionType
from srs_signal.domain.validation import source_text_sha256
from srs_signal.mvp.models import (
    InstitutionConfirmation,
    InstitutionConfirmationStatus,
    InstitutionDetectionResult,
)


class InstitutionWorkflowError(ValueError):
    """Base error for source-bound institution workflow failures."""


class InstitutionDetectionValidationError(InstitutionWorkflowError):
    """Raised when a detector result does not match its exact source."""


class InstitutionConfirmationError(InstitutionWorkflowError):
    """Raised when a confirmation cannot be bound to its exact source."""


def validate_detection_against_source(
    result: InstitutionDetectionResult,
    source_text: str,
) -> InstitutionDetectionResult:
    try:
        structural = InstitutionDetectionResult.model_validate(result.model_dump())
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise InstitutionDetectionValidationError(
            "Institution detection failed structural validation"
        ) from exc
    if structural.source_text_sha256 != source_text_sha256(source_text):
        raise InstitutionDetectionValidationError(
            "Institution detection does not match the exact source"
        )
    evidence = structural.issuer_evidence
    if evidence.character_end > len(source_text):
        raise InstitutionDetectionValidationError(
            "Institution detection evidence offsets exceed the source"
        )
    if source_text[evidence.character_start : evidence.character_end] != evidence.quotation:
        raise InstitutionDetectionValidationError(
            "Institution detection evidence does not match the exact source span"
        )
    return structural


def create_institution_confirmation(
    detection: InstitutionDetectionResult,
    source_text: str,
    *,
    confirmed_institution_name: str,
    confirmed_institution_type: InstitutionType,
) -> InstitutionConfirmation:
    verified = validate_detection_against_source(detection, source_text)
    name = confirmed_institution_name
    matches = (
        name == verified.detected_institution_name
        and confirmed_institution_type is verified.suggested_institution_type
    )
    try:
        return InstitutionConfirmation(
            detection_result=verified,
            confirmed_institution_name=name,
            confirmed_institution_type=confirmed_institution_type,
            status=(
                InstitutionConfirmationStatus.CONFIRMED_SUGGESTION
                if matches
                else InstitutionConfirmationStatus.OVERRIDDEN
            ),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise InstitutionConfirmationError(
            "Institution confirmation is invalid"
        ) from exc


def validate_confirmation_against_source(
    confirmation: InstitutionConfirmation,
    source_text: str,
) -> InstitutionConfirmation:
    try:
        structural = InstitutionConfirmation.model_validate(confirmation.model_dump())
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise InstitutionConfirmationError(
            "Institution confirmation failed structural validation"
        ) from exc
    try:
        validate_detection_against_source(structural.detection_result, source_text)
    except InstitutionDetectionValidationError as exc:
        raise InstitutionConfirmationError(
            "Institution confirmation does not match the exact source"
        ) from exc
    return structural


def metadata_with_confirmed_institution(
    fixture_metadata: DecisionMetadata,
    confirmation: InstitutionConfirmation,
    source_text: str,
) -> DecisionMetadata:
    verified = validate_confirmation_against_source(confirmation, source_text)
    return fixture_metadata.model_copy(
        update={
            "institution_name": verified.confirmed_institution_name,
            "institution_type": verified.confirmed_institution_type,
        }
    )
