"""Explicit seven-dimension human-review page."""

from __future__ import annotations

from pydantic import ValidationError
import streamlit as st

from srs_signal.domain import (
    CANONICAL_DIMENSION_IDS,
    ConfidenceLevel,
    DimensionFinding,
    DimensionStatus,
    ReviewAction,
)
from srs_signal.mvp import initialize_mvp_session
from srs_signal.mvp.review_models import (
    _MvpDimensionReviewDraft,
    _MvpFindingEdit,
    _MvpReviewDraft,
)
from srs_signal.mvp.reviews import (
    ReviewDraftMismatchError,
    ReviewWorkflowError,
    _create_review_draft,
    _finalize_review_draft,
    _validate_draft_for_analysis,
)
from srs_signal.mvp.store import (
    MvpStoreError,
    _list_analysis_records,
    _load_reviewed,
    _reviewed_key,
    _save_reviewed,
)
from srs_signal.mvp.ui import _render_page_frame


_CASE_KEY = "_srs_widget_review_case"
_LABEL_KEY = "_srs_widget_reviewer_label"
_OVERALL_NOTE_KEY = "_srs_widget_overall_reviewer_note"


def _field_key(analysis_id: object, dimension: object, field: str) -> str:
    return f"_srs_widget_review_{analysis_id}_{dimension}_{field}"


def _lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


def _text_lines(values: tuple[str, ...]) -> str:
    return "\n".join(values)


def _render_evidence(item: object) -> None:
    for verification in item.evidence_verifications:
        st.markdown(f"**Evidence quotation — {verification.quote.quote_id}**")
        st.code(verification.quote.text, language=None)
        st.caption(
            "Reported location — not independently verified: "
            f"{verification.quote.reported_document_location}"
        )
        st.write(
            "Evidence validation: exact supplied source; primary method "
            f"`{verification.primary_match_method.value}`."
        )
        for occurrence in verification.occurrences:
            st.caption(
                f"Verified source span {occurrence.character_start}–"
                f"{occurrence.character_end} ({occurrence.match_method.value})"
            )


def _load_or_create_draft(session: object, record: object) -> _MvpReviewDraft:
    payload = session.srs_mvp_review_draft.get("payload")
    if payload is not None:
        if not isinstance(payload, str):
            session.srs_mvp_review_draft.clear()
            raise ReviewDraftMismatchError("Stored review draft is malformed")
        try:
            draft = _MvpReviewDraft.model_validate_json(payload)
            return _validate_draft_for_analysis(draft, record)
        except (ValidationError, ValueError, ReviewWorkflowError) as exc:
            session.srs_mvp_review_draft.clear()
            raise ReviewDraftMismatchError("Stored review draft was discarded") from exc
    draft = _create_review_draft(record)
    session.srs_mvp_review_draft["payload"] = draft.model_dump_json()
    return draft


def _render_original(original: object) -> None:
    finding = original.finding
    st.write(f"Dimension ID: `{finding.dimension_id.value}`")
    st.write(f"Assessment performed: {'yes' if finding.assessment_performed else 'no'}")
    st.write(f"Original score: {finding.score if finding.score is not None else 'none'}")
    st.write(f"Original status: {finding.status.value if finding.status else 'none'}")
    st.write(f"Original confidence: {finding.confidence.value if finding.confidence else 'none'}")
    st.write(f"Original finding: {finding.concise_finding}")
    st.write(f"Original reasoning: {finding.reasoning}")
    st.write("Limitations: " + ("; ".join(finding.limitations) or "none recorded"))
    st.write(
        "Identified missing elements: "
        + (", ".join(finding.identified_missing_elements) or "none recorded")
    )
    st.write(
        "Deficiency types: "
        + (", ".join(finding.deficiency_types) or "none recorded")
    )
    _render_evidence(original)


def _render_edit_controls(original: object, prefix: str) -> tuple[_MvpFindingEdit | None, str | None]:
    finding = original.finding
    score = None
    status = None
    confidence = None
    if finding.assessment_performed:
        score = st.selectbox(
            "Edited score",
            options=(0, 1, 2, 3, 4),
            index=(0, 1, 2, 3, 4).index(finding.score),
            key=f"{prefix}_score",
        )
        status = st.selectbox(
            "Edited status",
            options=tuple(DimensionStatus),
            index=tuple(DimensionStatus).index(finding.status),
            format_func=lambda item: item.value,
            key=f"{prefix}_status",
        )
        confidence = st.selectbox(
            "Edited confidence",
            options=tuple(ConfidenceLevel),
            index=tuple(ConfidenceLevel).index(finding.confidence),
            format_func=lambda item: item.value,
            key=f"{prefix}_confidence",
        )
    concise = st.text_area(
        "Edited concise finding",
        value=finding.concise_finding,
        key=f"{prefix}_concise",
    )
    reasoning = st.text_area(
        "Edited reasoning",
        value=finding.reasoning,
        key=f"{prefix}_reasoning",
    )
    limitations = st.text_area(
        "Edited limitations — one per line",
        value=_text_lines(finding.limitations),
        key=f"{prefix}_limitations",
    )
    missing = st.text_area(
        "Edited missing elements — one per line",
        value=_text_lines(finding.identified_missing_elements),
        key=f"{prefix}_missing",
    )
    deficiency = st.text_area(
        "Edited deficiency types — one per line",
        value=_text_lines(finding.deficiency_types),
        key=f"{prefix}_deficiency",
    )
    try:
        edit = _MvpFindingEdit(
            score=score,
            status=status,
            concise_finding=concise,
            reasoning=reasoning,
            confidence=confidence,
            limitations=_lines(limitations),
            identified_missing_elements=_lines(missing),
            deficiency_types=_lines(deficiency),
        )
        values = finding.model_dump()
        values.update(edit.model_dump())
        changed = DimensionFinding.model_validate(values)
        if changed == finding:
            raise ValueError("Edited disposition requires at least one changed value")
        return edit, None
    except (ValidationError, ValueError) as exc:
        return None, str(exc)


def render() -> None:
    """Render explicit review controls over exact-source-admitted findings."""

    _render_page_frame(
        page_title="Human Review",
        introduction=(
            "Review every evidence-validated dimension explicitly. Accept means "
            "accepting a prototype audit finding, not declaring a decision lawful, "
            "valid, democratic, or factually correct."
        ),
    )
    st.info(
        "Evidence quotations and verified spans are read-only. Rejection excludes "
        "only that dimension; aggregation is not performed on this page."
    )
    session = initialize_mvp_session(st.session_state)
    listing = _list_analysis_records(session.srs_mvp_store)
    for issue in listing.issues:
        st.warning(f"Ignored invalid analysis entry `{issue.key}`: {issue.message}.")
    records = listing.records
    if not records:
        st.info("Analyze and validate a fictional decision before starting human review.")
        return
    by_case = {record.case_id: record for record in records}
    labels = {case_id: record.decision_metadata.title for case_id, record in by_case.items()}
    selected_case = st.selectbox(
        "Evidence-validated decision",
        options=tuple(by_case),
        format_func=labels.__getitem__,
        key=_CASE_KEY,
    )
    prior_case = session.srs_mvp_current_decision_id
    if prior_case != selected_case:
        session.srs_mvp_review_draft.clear()
        st.session_state.pop(_LABEL_KEY, None)
        st.session_state.pop(_OVERALL_NOTE_KEY, None)
        st.session_state["srs_mvp_current_decision_id"] = selected_case
    record = by_case[selected_case]
    reviewed_key = _reviewed_key(selected_case)
    if reviewed_key in session.srs_mvp_store:
        try:
            _load_reviewed(session.srs_mvp_store, selected_case)
        except MvpStoreError as exc:
            st.warning(
                f"Ignored invalid or stale reviewed entry `{reviewed_key}`: {exc}. "
                "It does not block a replacement review."
            )
        else:
            session.srs_mvp_review_draft.clear()
            st.info(
                "A finalized immutable review already exists for this analysis. Open "
                "Reviewed Audit Profile to inspect it. A replacement analysis is "
                "required before a new MVP review can be created."
            )
            return
    try:
        draft = _load_or_create_draft(session, record)
    except ReviewWorkflowError as exc:
        st.error(f"Review draft could not be loaded: {exc}")
        return

    st.subheader(record.decision_metadata.title)
    st.write(f"Confirmed institution: {record.decision_metadata.institution_name}")
    st.write(
        "Confirmed institution type: "
        f"{record.decision_metadata.institution_type.value}"
    )
    st.caption(f"Exact source SHA-256: {record.source_text_sha256}")
    reviewer_label = st.text_input(
        "Optional local reviewer label — identity is not verified",
        value=draft.reviewer_label or "",
        key=_LABEL_KEY,
    )
    overall_note = st.text_area(
        "Optional overall reviewer note",
        value=draft.overall_reviewer_note or "",
        key=_OVERALL_NOTE_KEY,
    )

    originals = {
        item.dimension_id: item for item in record.validated_result.validated_findings
    }
    prior_dimensions = {item.dimension_id: item for item in draft.dimensions}
    dimension_drafts: list[_MvpDimensionReviewDraft] = []
    edit_errors: list[str] = []
    action_options = (None, *tuple(ReviewAction))
    action_labels = {
        None: "Not reviewed",
        ReviewAction.ACCEPTED: "Accept",
        ReviewAction.EDITED: "Edit",
        ReviewAction.REJECTED: "Reject",
    }
    for index, dimension_id in enumerate(CANONICAL_DIMENSION_IDS, start=1):
        original = originals[dimension_id]
        prior = prior_dimensions[dimension_id]
        with st.expander(
            f"{index}. {original.finding.dimension_name}", expanded=False
        ):
            _render_original(original)
            action_key = _field_key(draft.analysis_id, dimension_id.value, "action")
            if action_key not in st.session_state:
                st.session_state[action_key] = prior.action
            action = st.selectbox(
                "Human disposition",
                options=action_options,
                format_func=action_labels.__getitem__,
                key=action_key,
            )
            edit = None
            if action is ReviewAction.EDITED:
                edit, error = _render_edit_controls(original, action_key)
                if error is not None:
                    edit_errors.append(f"{original.finding.dimension_name}: {error}")
                    st.error("Edited score/status or text is not domain-valid.")
            note_key = _field_key(draft.analysis_id, dimension_id.value, "note")
            note = st.text_area(
                "Optional reviewer note",
                value=prior.reviewer_note or "",
                key=note_key,
            )
            try:
                dimension_drafts.append(
                    _MvpDimensionReviewDraft(
                        dimension_id=dimension_id,
                        original_finding_id=original.finding_id,
                        reviewed_finding_id=prior.reviewed_finding_id,
                        action=action,
                        edit=edit,
                        reviewer_note=note.strip() or None,
                    )
                )
            except ValidationError:
                edit_errors.append(f"{original.finding.dimension_name}: invalid edit")
                dimension_drafts.append(prior)

    try:
        draft = _MvpReviewDraft(
            review_id=draft.review_id,
            case_id=draft.case_id,
            source_text_sha256=draft.source_text_sha256,
            analysis_id=draft.analysis_id,
            evidence_validated_at=draft.evidence_validated_at,
            institution_confirmation=draft.institution_confirmation,
            started_at=draft.started_at,
            reviewer_label=reviewer_label.strip() or None,
            overall_reviewer_note=overall_note.strip() or None,
            dimensions=tuple(dimension_drafts),
        )
        session.srs_mvp_review_draft["payload"] = draft.model_dump_json()
    except ValidationError as exc:
        st.error(f"Review draft is invalid: {exc}")
        return

    complete = draft.is_complete and not edit_errors
    completed_count = sum(item.action is not None for item in draft.dimensions)
    st.write(f"Review completeness: {completed_count}/7 dimensions")
    if not complete:
        st.info("Select one valid disposition for every dimension before finalizing.")
    if st.button(
        "Finalize reviewed audit",
        disabled=not complete,
    ):
        try:
            reviewed = _finalize_review_draft(record, draft)
            _save_reviewed(session.srs_mvp_store, reviewed)
        except (MvpStoreError, ReviewWorkflowError, ValidationError) as exc:
            st.error(f"Review finalization failed: {exc}")
        else:
            session.srs_mvp_review_draft.clear()
            st.success(
                "Confirmed review passed exact-source reviewed admission and was "
                "stored as serializable provenance. Open Reviewed Audit Profile."
            )
