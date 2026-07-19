"""Source-bound institution confirmation and deterministic audit page."""

from __future__ import annotations

from pydantic import ValidationError
import streamlit as st

from srs_signal.domain import (
    AdmissionError,
    InstitutionType,
    admit_validated_audit,
)
from srs_signal.domain.validation import (
    EvidenceVerificationError,
    source_text_sha256,
)
from srs_signal.mvp import (
    DeterministicAuditProvider,
    DeterministicInstitutionDetector,
    InstitutionConfirmation,
    InstitutionConfirmationStatus,
    InstitutionDetectionResult,
    MvpDecisionRecord,
    analyze_and_validate,
    initialize_mvp_session,
)
from srs_signal.mvp.institutions import (
    InstitutionConfirmationError,
    InstitutionDetectionValidationError,
    create_institution_confirmation,
    validate_confirmation_against_source,
    validate_detection_against_source,
)
from srs_signal.mvp.providers.deterministic import DeterministicProviderError
from srs_signal.mvp.providers.deterministic_institution import (
    DeterministicInstitutionDetectionError,
)
from srs_signal.mvp.state import MvpSessionState
from srs_signal.mvp.store import _invalidate_case_review
from srs_signal.mvp.ui import _render_page_frame
from srs_signal.mvp.workflow import AuditWorkflowError


_SOURCE_WIDGET_KEY = "_srs_widget_source_text"
_CASE_WIDGET_KEY = "_srs_widget_case_selector"
_NAME_WIDGET_KEY = "_srs_widget_confirmed_institution_name"
_TYPE_WIDGET_KEY = "_srs_widget_confirmed_institution_type"


def _institution_type_label(value: InstitutionType) -> str:
    return value.value.replace("_", " ")


def _clear_confirmation_widgets() -> None:
    st.session_state.pop(_NAME_WIDGET_KEY, None)
    st.session_state.pop(_TYPE_WIDGET_KEY, None)


def _bind_working_state_to_source(
    session: MvpSessionState,
    source_text: str,
) -> str | None:
    current_hash = source_text_sha256(source_text) if source_text else None
    prior_hash = session.srs_mvp_working.get("current_source_sha256")
    if prior_hash != current_hash:
        session.srs_mvp_working["current_source_sha256"] = current_hash
        session.srs_mvp_working.pop("institution_detection_json", None)
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        _clear_confirmation_widgets()
    return current_hash


def _load_detection(
    session: MvpSessionState,
    source_text: str,
    detector: DeterministicInstitutionDetector,
) -> InstitutionDetectionResult | None:
    payload = session.srs_mvp_working.get("institution_detection_json")
    if payload is None:
        return None
    if not isinstance(payload, str):
        session.srs_mvp_working.pop("institution_detection_json", None)
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        st.error("Stored institution detection is malformed and was discarded.")
        return None
    try:
        result = InstitutionDetectionResult.model_validate_json(payload)
        verified = validate_detection_against_source(result, source_text)
        if detector.detect(source_text) != verified:
            raise InstitutionDetectionValidationError(
                "Stored detection does not reproduce the deterministic suggestion"
            )
        return verified
    except (
        InstitutionDetectionValidationError,
        ValidationError,
        ValueError,
    ):
        session.srs_mvp_working.pop("institution_detection_json", None)
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        st.error("Stored institution detection is invalid and was discarded.")
        return None


def _load_confirmation(
    session: MvpSessionState,
    source_text: str,
    detection: InstitutionDetectionResult,
) -> InstitutionConfirmation | None:
    payload = session.srs_mvp_working.get("institution_confirmation_json")
    if payload is None:
        return None
    if not isinstance(payload, str):
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        st.error("Stored institution confirmation is malformed and was discarded.")
        return None
    try:
        confirmation = InstitutionConfirmation.model_validate_json(payload)
        verified = validate_confirmation_against_source(confirmation, source_text)
    except (InstitutionConfirmationError, ValidationError, ValueError):
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        st.error("Stored institution confirmation is invalid and was discarded.")
        return None
    if verified.detection_result != detection:
        session.srs_mvp_working.pop("institution_confirmation_json", None)
        session.srs_mvp_working.pop("active_record_id", None)
        st.error("Stored institution confirmation does not match the current detection.")
        return None
    return verified


def _render_detection(result: InstitutionDetectionResult) -> None:
    st.info("Suggested — human confirmation required.")
    st.write(f"Detected issuing institution: {result.detected_institution_name}")
    st.write(
        "Suggested institution type: "
        f"{_institution_type_label(result.suggested_institution_type)}"
    )
    st.write(f"Confidence: {result.institution_type_confidence.value}")
    st.write(f"Reason: {result.institution_type_reason}")
    st.markdown("**Exact issuer evidence**")
    st.code(result.issuer_evidence.quotation, language=None)
    st.caption(
        "Verified source span: "
        f"{result.issuer_evidence.character_start}–"
        f"{result.issuer_evidence.character_end}"
    )
    st.caption(
        "Detector provenance: "
        f"{result.detector_identifier} ({result.detector_provider_type.value})"
    )


def _render_validated_record(record: MvpDecisionRecord) -> None:
    st.success(
        "Deterministic provisional output passed exact-source evidence validation "
        "and runtime admission. It remains pending human review."
    )
    st.write(
        "Confirmed issuing institution used for audit: "
        f"{record.decision_metadata.institution_name}"
    )
    st.write(
        "Confirmed institution type used for audit: "
        f"{_institution_type_label(record.decision_metadata.institution_type)}"
    )
    st.caption(f"Exact source SHA-256: {record.source_text_sha256}")
    st.warning(
        "This is not a reviewed result. No finding shown here is eligible for a "
        "systemic signal."
    )
    for validated_finding in record.validated_result.validated_findings:
        finding = validated_finding.finding
        score_text = "unassessed" if finding.score is None else f"{finding.score}/4"
        with st.expander(f"{finding.dimension_name} — {score_text}"):
            st.write(f"Status: {finding.status.value if finding.status else 'unassessed'}")
            st.write(
                f"Confidence: {finding.confidence.value if finding.confidence else 'none'}"
            )
            st.write(f"Finding: {finding.concise_finding}")
            st.write(f"Reasoning: {finding.reasoning}")
            st.write(
                "Deficiency types: "
                + (", ".join(finding.deficiency_types) or "none recorded")
            )
            st.write(
                "Limitations: " + ("; ".join(finding.limitations) or "none recorded")
            )
            st.write(
                "Identified missing elements: "
                + (", ".join(finding.identified_missing_elements) or "none recorded")
            )
            for verification in validated_finding.evidence_verifications:
                st.markdown(f"**Evidence quotation — {verification.quote.quote_id}**")
                st.code(verification.quote.text, language=None)
                st.caption(
                    "Reported location — not independently verified: "
                    f"{verification.quote.reported_document_location}"
                )
                st.write(
                    "Evidence verification: matched against the exact supplied "
                    f"source; primary method `{verification.primary_match_method.value}`."
                )
                for occurrence in verification.occurrences:
                    st.write(
                        "Verified source span: "
                        f"{occurrence.character_start}–{occurrence.character_end} "
                        f"({occurrence.match_method.value})"
                    )


def _load_stored_record(
    payload: object,
    *,
    current_source_text: str,
    current_confirmation: InstitutionConfirmation,
) -> MvpDecisionRecord | None:
    if not isinstance(payload, str):
        return None
    record = MvpDecisionRecord.model_validate_json(payload)
    if (
        record.source_text != current_source_text
        or record.institution_confirmation != current_confirmation
    ):
        return None
    admitted = admit_validated_audit(record.validated_result, record.source_text)
    if admitted.record != record.validated_result:
        raise AdmissionError("Stored MVP validation could not be reproduced")
    return record


def render() -> None:
    """Render source-bound issuer confirmation and deterministic analysis."""

    _render_page_frame(
        page_title="Analyze Decision",
        introduction=(
            "Load or paste one of three wholly fictional bundled decisions. The "
            "prototype suggests the issuing institution and type, but a human must "
            "confirm or override them before the seven-dimension audit can run."
        ),
    )
    st.warning(
        "Every bundled decision is wholly fictional and was created only for this "
        "research-prototype demonstration. No real person, institution, proceeding, "
        "or event is represented. Detection is advisory and is not a legal or "
        "democratic-quality determination."
    )
    session = initialize_mvp_session(st.session_state)
    audit_provider = DeterministicAuditProvider()
    detector = DeterministicInstitutionDetector()
    cases = audit_provider.list_cases()
    display_names = {case.case_id: case.display_name for case in cases}
    selected_case_id = st.selectbox(
        "Bundled fictional decision",
        options=tuple(display_names),
        format_func=display_names.__getitem__,
        key=_CASE_WIDGET_KEY,
    )
    if st.button("Load selected fictional decision"):
        st.session_state[_SOURCE_WIDGET_KEY] = audit_provider.load_source(
            selected_case_id
        )
        session.srs_mvp_working["selected_case_id"] = selected_case_id

    source_text = st.text_area(
        "Decision text",
        height=360,
        key=_SOURCE_WIDGET_KEY,
        help=(
            "Deterministic detection and analysis are available only when this text "
            "exactly matches one bundled fictional source."
        ),
    )
    _bind_working_state_to_source(session, source_text)

    detection = (
        _load_detection(session, source_text, detector) if source_text else None
    )
    if source_text and detection is None:
        try:
            detection = detector.detect(source_text)
        except DeterministicInstitutionDetectionError as exc:
            session.srs_mvp_working.pop("institution_detection_json", None)
            session.srs_mvp_working.pop("institution_confirmation_json", None)
            session.srs_mvp_working.pop("active_record_id", None)
            st.error(str(exc))
        else:
            session.srs_mvp_working[
                "institution_detection_json"
            ] = detection.model_dump_json()
            st.session_state[_NAME_WIDGET_KEY] = detection.detected_institution_name
            st.session_state[_TYPE_WIDGET_KEY] = detection.suggested_institution_type

    confirmation = None
    if detection is not None:
        _render_detection(detection)
        if _NAME_WIDGET_KEY not in st.session_state:
            st.session_state[_NAME_WIDGET_KEY] = detection.detected_institution_name
        if _TYPE_WIDGET_KEY not in st.session_state:
            st.session_state[_TYPE_WIDGET_KEY] = detection.suggested_institution_type
        confirmed_name = st.text_input(
            "Confirmed issuing institution",
            key=_NAME_WIDGET_KEY,
        )
        confirmed_type = st.selectbox(
            "Confirmed institution type",
            options=tuple(InstitutionType),
            format_func=_institution_type_label,
            key=_TYPE_WIDGET_KEY,
        )
        confirmation = _load_confirmation(session, source_text, detection)
        if confirmation is not None and (
            confirmed_name != confirmation.confirmed_institution_name
            or confirmed_type is not confirmation.confirmed_institution_type
        ):
            session.srs_mvp_working.pop("institution_confirmation_json", None)
            session.srs_mvp_working.pop("active_record_id", None)
            confirmation = None
            st.warning("Institution values changed; confirm them again before auditing.")

        if st.button("Confirm institution metadata"):
            session.srs_mvp_working.pop("institution_confirmation_json", None)
            session.srs_mvp_working.pop("active_record_id", None)
            try:
                confirmation = create_institution_confirmation(
                    detection,
                    source_text,
                    confirmed_institution_name=confirmed_name,
                    confirmed_institution_type=confirmed_type,
                )
            except InstitutionConfirmationError as exc:
                st.error(f"Institution confirmation failed: {exc}")
                confirmation = None
            else:
                session.srs_mvp_working[
                    "institution_confirmation_json"
                ] = confirmation.model_dump_json()

        if confirmation is not None:
            if (
                confirmation.status
                is InstitutionConfirmationStatus.CONFIRMED_SUGGESTION
            ):
                st.success("Institution metadata confirmed from the suggestion.")
            else:
                st.success("Human override confirmed for institution metadata.")
            st.caption(
                "Only these confirmed institution values will enter DecisionMetadata."
            )

    request = None
    if source_text and confirmation is not None:
        try:
            request = audit_provider.request_for_source(
                source_text,
                institution_confirmation=confirmation,
            )
        except (DeterministicProviderError, ValidationError, ValueError) as exc:
            session.srs_mvp_working.pop("institution_confirmation_json", None)
            session.srs_mvp_working.pop("active_record_id", None)
            confirmation = None
            st.error(f"Confirmed audit request is invalid: {exc}")

    if st.button("Run deterministic audit", disabled=request is None):
        assert request is not None
        session.srs_mvp_working.pop("active_record_id", None)
        try:
            record = analyze_and_validate(request, audit_provider)
        except (
            AdmissionError,
            AuditWorkflowError,
            DeterministicProviderError,
            EvidenceVerificationError,
            ValidationError,
        ) as exc:
            st.session_state["srs_mvp_notice"] = str(exc)
            st.error(f"Deterministic audit failed: {exc}")
        else:
            _invalidate_case_review(
                session.srs_mvp_store,
                session.srs_mvp_review_draft,
                record.case_id,
            )
            if session.srs_mvp_current_decision_id == record.case_id:
                st.session_state["srs_mvp_current_decision_id"] = None
            session.srs_mvp_store[record.case_id] = record.model_dump_json()
            session.srs_mvp_working["active_record_id"] = record.case_id
            st.session_state["srs_mvp_notice"] = None

    active_record_id = session.srs_mvp_working.get("active_record_id")
    stored_payload = (
        session.srs_mvp_store.get(active_record_id)
        if isinstance(active_record_id, str)
        else None
    )
    stored_record = None
    if confirmation is not None:
        try:
            stored_record = _load_stored_record(
                stored_payload,
                current_source_text=source_text,
                current_confirmation=confirmation,
            )
        except (AdmissionError, ValidationError) as exc:
            st.error(f"Stored deterministic provenance could not be readmitted: {exc}")

    if stored_record is not None:
        _render_validated_record(stored_record)
    elif not source_text:
        st.info(
            "Load a bundled fictional decision to begin. No audit result has been "
            "created."
        )
    elif detection is not None and confirmation is None:
        st.info("Confirm or override the suggested institution before auditing.")
