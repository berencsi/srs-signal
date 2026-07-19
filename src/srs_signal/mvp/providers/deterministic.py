"""Exact-source deterministic audit provider for fictional decisions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid5

from pydantic import ValidationError

from srs_signal.domain import (
    AnalysisProviderType,
    DecisionMetadata,
    ProvisionalAuditResult,
)
from srs_signal.mvp.demo.catalog import (
    _DemoCatalogError,
    _LoadedDemoCase,
    _UnknownDemoCaseError,
    _demo_case_by_id,
    _demo_case_for_source,
    _loaded_demo_cases,
)
from srs_signal.mvp.institutions import (
    InstitutionConfirmationError,
    metadata_with_confirmed_institution,
    validate_confirmation_against_source,
)
from srs_signal.mvp.models import AuditRequest, InstitutionConfirmation
from srs_signal.mvp.providers.deterministic_institution import (
    DeterministicInstitutionDetector,
)


class DeterministicProviderError(ValueError):
    """Base error for deterministic asset resolution and loading."""


class UnknownDeterministicCaseError(DeterministicProviderError):
    """Raised when exact source text has no bundled deterministic analysis."""


class DeterministicAssetError(DeterministicProviderError):
    """Raised when a bundled asset violates the reviewed fixture contract."""


@dataclass(frozen=True, slots=True)
class BundledCaseSummary:
    case_id: str
    display_name: str
    source_text_sha256: str


_PROTECTED_METADATA_FIELDS = (
    "decision_id",
    "title",
    "decision_type",
    "jurisdiction_or_domain",
    "decision_date",
    "source_type",
    "contextual_note",
)


def _translate_catalog_error(exc: _DemoCatalogError) -> DeterministicProviderError:
    if isinstance(exc, _UnknownDemoCaseError):
        return UnknownDeterministicCaseError(str(exc))
    return DeterministicAssetError(str(exc))


def _case_by_id(case_id: str) -> _LoadedDemoCase:
    try:
        return _demo_case_by_id(case_id)
    except _DemoCatalogError as exc:
        raise _translate_catalog_error(exc) from exc


def _case_for_source(source_text: str) -> _LoadedDemoCase:
    try:
        return _demo_case_for_source(source_text)
    except _DemoCatalogError as exc:
        translated = _translate_catalog_error(exc)
        if isinstance(translated, UnknownDeterministicCaseError):
            raise UnknownDeterministicCaseError(
                "Deterministic analysis is unavailable for unknown or modified "
                "source text"
            ) from exc
        raise translated from exc


def _protected_metadata_equal(
    submitted: DecisionMetadata,
    fixture: DecisionMetadata,
) -> bool:
    return all(
        getattr(submitted, field_name) == getattr(fixture, field_name)
        for field_name in _PROTECTED_METADATA_FIELDS
    )


def _require_deterministic_confirmation(
    confirmation: InstitutionConfirmation,
    source_text: str,
) -> InstitutionConfirmation:
    try:
        verified = validate_confirmation_against_source(confirmation, source_text)
        expected_detection = DeterministicInstitutionDetector().detect(source_text)
    except (InstitutionConfirmationError, ValueError) as exc:
        raise DeterministicProviderError(
            "The institution confirmation does not match the exact source text"
        ) from exc
    if verified.detection_result != expected_detection:
        raise DeterministicProviderError(
            "The institution confirmation does not reproduce deterministic detection"
        )
    return verified


class DeterministicAuditProvider:
    """Materialize a deterministic audit only from confirmed exact-source input."""

    @property
    def provider_type(self) -> AnalysisProviderType:
        return AnalysisProviderType.DETERMINISTIC_DEMO

    def list_cases(self) -> tuple[BundledCaseSummary, ...]:
        try:
            cases = _loaded_demo_cases()
        except _DemoCatalogError as exc:
            raise _translate_catalog_error(exc) from exc
        return tuple(
            BundledCaseSummary(
                case_id=case.case_id,
                display_name=case.display_name,
                source_text_sha256=case.source_text_sha256,
            )
            for case in cases
        )

    def load_source(self, case_id: str) -> str:
        return _case_by_id(case_id).source_text

    def load_request(
        self,
        case_id: str,
        *,
        institution_confirmation: InstitutionConfirmation,
    ) -> AuditRequest:
        case = _case_by_id(case_id)
        return self.request_for_source(
            case.source_text,
            institution_confirmation=institution_confirmation,
            bundled_case_id=case_id,
        )

    def request_for_source(
        self,
        source_text: str,
        *,
        institution_confirmation: InstitutionConfirmation,
        bundled_case_id: str | None = None,
    ) -> AuditRequest:
        case = _case_for_source(source_text)
        if bundled_case_id is not None and bundled_case_id != case.case_id:
            raise UnknownDeterministicCaseError(
                "The supplied case identifier does not match the exact source text"
            )
        verified_confirmation = _require_deterministic_confirmation(
            institution_confirmation,
            source_text,
        )
        metadata = metadata_with_confirmed_institution(
            case.provisional_result.decision_metadata,
            verified_confirmation,
            source_text,
        )
        return AuditRequest(
            source_text=source_text,
            decision_metadata=metadata,
            institution_confirmation=verified_confirmation,
            bundled_case_id=case.case_id,
        )

    def analyze(self, request: AuditRequest) -> ProvisionalAuditResult:
        try:
            request = AuditRequest.model_validate(request.model_dump())
        except (AttributeError, TypeError, ValueError, ValidationError) as exc:
            raise DeterministicProviderError(
                "Deterministic audit request failed structural revalidation"
            ) from exc
        case = _case_for_source(request.source_text)
        if request.bundled_case_id != case.case_id:
            raise UnknownDeterministicCaseError(
                "The supplied case identifier does not match the exact source text"
            )
        confirmation = _require_deterministic_confirmation(
            request.institution_confirmation,
            request.source_text,
        )
        fixture_metadata = case.provisional_result.decision_metadata
        if not _protected_metadata_equal(request.decision_metadata, fixture_metadata):
            raise DeterministicProviderError(
                "Deterministic audit metadata changed a protected fixture field"
            )
        if (
            request.decision_metadata.institution_name
            != confirmation.confirmed_institution_name
            or request.decision_metadata.institution_type
            is not confirmation.confirmed_institution_type
        ):
            raise DeterministicProviderError(
                "Deterministic audit metadata does not match human confirmation"
            )
        identity_name = (
            f"{confirmation.confirmed_institution_name}\0"
            f"{confirmation.confirmed_institution_type.value}"
        )
        analysis_id = uuid5(case.provisional_result.analysis_id, identity_name)
        return case.provisional_result.model_copy(
            update={
                "analysis_id": analysis_id,
                "decision_metadata": request.decision_metadata,
            }
        )
