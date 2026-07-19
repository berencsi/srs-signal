"""Source-backed deterministic MVP analysis orchestration."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from srs_signal.domain import ProvisionalAuditResult, admit_validated_audit
from srs_signal.domain.validation import source_text_sha256, validate_audit_evidence
from srs_signal.mvp.models import AuditRequest, MvpDecisionRecord
from srs_signal.mvp.providers.base import AuditProvider


class AuditWorkflowError(ValueError):
    """Raised when provider output cannot enter the validated workflow."""


class AuditMetadataMismatchError(AuditWorkflowError):
    """Raised when a provider substitutes decision metadata."""


class AuditProviderMismatchError(AuditWorkflowError):
    """Raised when output declares a provider different from the provider used."""


def analyze_and_validate(
    request: AuditRequest,
    provider: AuditProvider,
    *,
    validated_at: datetime | None = None,
) -> MvpDecisionRecord:
    """Run the only approved provisional-to-admitted Commit 2 path.

    The runtime admission handle is deliberately discarded after admission.
    Only exact source text and serializable provenance enter the returned record.
    """

    try:
        request = AuditRequest.model_validate(request.model_dump())
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise AuditWorkflowError(
            "Audit request failed structural and confirmation revalidation"
        ) from exc
    submitted = provider.analyze(request)
    if type(submitted) is not ProvisionalAuditResult:
        raise AuditWorkflowError(
            "Audit providers must return an exact ProvisionalAuditResult record"
        )
    try:
        provisional = ProvisionalAuditResult.model_validate(submitted.model_dump())
    except ValidationError as exc:
        raise AuditWorkflowError(
            "Provider output failed structural revalidation"
        ) from exc
    if provisional.provider_type is not provider.provider_type:
        raise AuditProviderMismatchError(
            "Provider output does not match the selected provider type"
        )
    if provisional.decision_metadata != request.decision_metadata:
        raise AuditMetadataMismatchError(
            "Provider output substituted or changed the supplied decision metadata"
        )
    validation_time = validated_at or datetime.now(UTC)
    validated = validate_audit_evidence(
        provisional,
        request.source_text,
        validated_at=validation_time,
    )
    admitted = admit_validated_audit(validated, request.source_text)
    if admitted.record != validated:
        raise AuditWorkflowError(
            "Source-backed admission did not retain the validated provenance"
        )
    case_id = request.bundled_case_id or request.decision_metadata.decision_id
    return MvpDecisionRecord(
        case_id=case_id,
        source_text=request.source_text,
        source_text_sha256=source_text_sha256(request.source_text),
        decision_metadata=request.decision_metadata,
        institution_confirmation=request.institution_confirmation,
        provisional_result=provisional,
        validated_result=validated,
        provider_type=provider.provider_type,
        created_at=validation_time,
    )
