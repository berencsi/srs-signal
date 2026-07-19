"""Exact-source deterministic issuing-institution detector."""

from __future__ import annotations

from srs_signal.domain import AnalysisProviderType, ConfidenceLevel
from srs_signal.mvp.demo.catalog import _DemoCatalogError, _demo_case_for_source
from srs_signal.mvp.institutions import validate_detection_against_source
from srs_signal.mvp.models import InstitutionDetectionResult, IssuerDetectionEvidence


_DETECTOR_IDENTIFIER = "deterministic-fixture-issuer-detector-v1"


class DeterministicInstitutionDetectionError(ValueError):
    """Raised when deterministic issuer detection is unavailable or invalid."""


class DeterministicInstitutionDetector:
    """Suggest only the explicitly declared issuer of an exact demo source."""

    @property
    def provider_type(self) -> AnalysisProviderType:
        return AnalysisProviderType.DETERMINISTIC_DEMO

    def detect(self, source_text: str) -> InstitutionDetectionResult:
        try:
            case = _demo_case_for_source(source_text)
        except _DemoCatalogError as exc:
            raise DeterministicInstitutionDetectionError(
                "Issuer detection is unavailable for unknown or modified source text"
            ) from exc
        metadata = case.provisional_result.decision_metadata
        issuer_line = f"Issuing institution: {metadata.institution_name}"
        type_line = (
            "Institution type supplied by fixture: "
            + metadata.institution_type.value.replace("_", " ")
        )
        evidence_text = f"{issuer_line}\n{type_line}"
        start = source_text.find(evidence_text)
        if start < 0 or source_text.find(evidence_text, start + 1) >= 0:
            raise DeterministicInstitutionDetectionError(
                "The bundled decision does not contain one explicit issuer declaration"
            )
        end = start + len(evidence_text)
        result = InstitutionDetectionResult(
            source_text_sha256=case.source_text_sha256,
            detected_institution_name=metadata.institution_name,
            suggested_institution_type=metadata.institution_type,
            institution_type_confidence=ConfidenceLevel.HIGH,
            institution_type_reason=(
                "The document explicitly labels this institution as the issuing "
                "institution and supplies the fixture institution type on the "
                "immediately adjacent line."
            ),
            issuer_evidence=IssuerDetectionEvidence(
                quotation=evidence_text,
                character_start=start,
                character_end=end,
            ),
            detector_provider_type=self.provider_type,
            detector_identifier=_DETECTOR_IDENTIFIER,
        )
        return validate_detection_against_source(result, source_text)
