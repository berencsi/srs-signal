"""Source-readmitted reviewed audit profile page."""

from __future__ import annotations

import streamlit as st

from srs_signal.domain import CANONICAL_DIMENSION_IDS, ReviewAction
from srs_signal.mvp import initialize_mvp_session
from srs_signal.mvp.reviews import ReviewWorkflowError, _readmit_reviewed_profile
from srs_signal.mvp.store import _list_reviewed_records
from srs_signal.mvp.ui import _render_page_frame


_PROFILE_CASE_KEY = "_srs_widget_profile_case"


def _show_finding(finding: object, *, prefix: str) -> None:
    st.write(f"{prefix} score: {finding.score if finding.score is not None else 'none'}")
    st.write(f"{prefix} status: {finding.status.value if finding.status else 'none'}")
    st.write(
        f"{prefix} confidence: {finding.confidence.value if finding.confidence else 'none'}"
    )
    st.write(f"{prefix} finding: {finding.concise_finding}")
    st.write(f"{prefix} reasoning: {finding.reasoning}")
    st.write(
        f"{prefix} limitations: " + ("; ".join(finding.limitations) or "none recorded")
    )
    st.write(
        f"{prefix} missing elements: "
        + (", ".join(finding.identified_missing_elements) or "none recorded")
    )
    st.write(
        f"{prefix} deficiency types: "
        + (", ".join(finding.deficiency_types) or "none recorded")
    )


def _show_evidence(validated: object) -> None:
    for verification in validated.evidence_verifications:
        st.markdown(f"**Exact evidence — {verification.quote.quote_id}**")
        st.code(verification.quote.text, language=None)
        st.caption(
            "Reported location — not independently verified: "
            f"{verification.quote.reported_document_location}"
        )
        for occurrence in verification.occurrences:
            st.caption(
                f"Verified span {occurrence.character_start}–{occurrence.character_end} "
                f"({occurrence.match_method.value})"
            )


def render() -> None:
    """Render only reviewed provenance reproduced by exact-source admission."""

    _render_page_frame(
        page_title="Reviewed Audit Profile",
        introduction=(
            "This profile displays confirmed human dispositions only after the full "
            "review provenance has been reproduced against the retained exact source."
        ),
    )
    session = initialize_mvp_session(st.session_state)
    listing = _list_reviewed_records(session.srs_mvp_store)
    for issue in listing.issues:
        st.warning(f"Ignored invalid reviewed entry `{issue.key}`: {issue.message}.")
    records = listing.records
    if not records:
        st.info("Finalize a seven-dimension human review before opening a profile.")
        return
    by_case = {record.analysis_record.case_id: record for record in records}
    labels = {
        case_id: record.analysis_record.decision_metadata.title
        for case_id, record in by_case.items()
    }
    selected_case = st.selectbox(
        "Finalized reviewed decision",
        options=tuple(by_case),
        format_func=labels.__getitem__,
        key=_PROFILE_CASE_KEY,
    )
    try:
        profile = _readmit_reviewed_profile(by_case[selected_case])
    except ReviewWorkflowError as exc:
        st.error(f"Reviewed profile could not be readmitted: {exc}")
        return
    record = profile.record
    analysis = record.analysis_record
    review = record.reviewed_result.human_review
    st.subheader(analysis.decision_metadata.title)
    st.write(f"Confirmed institution: {analysis.decision_metadata.institution_name}")
    st.write(f"Institution type: {analysis.decision_metadata.institution_type.value}")
    st.write(f"Decision ID: {analysis.decision_metadata.decision_id}")
    st.caption(f"Exact source SHA-256: {analysis.source_text_sha256}")
    st.write(f"Review status: {review.status.value}")
    st.write(f"Confirmed at: {review.confirmed_at.isoformat()}")
    st.write(
        "Reviewer: "
        + (
            f"{review.reviewer_label} — local label, identity not verified"
            if review.reviewer_label
            else "no local reviewer label supplied"
        )
    )
    if review.overall_reviewer_note:
        st.write(f"Overall reviewer note: {review.overall_reviewer_note}")

    reviewed_by_dimension = {
        item.original_finding.dimension_id: item
        for item in record.reviewed_result.reviewed_findings
    }
    eligible = set(profile.eligible_dimension_ids)
    for index, dimension_id in enumerate(CANONICAL_DIMENSION_IDS, start=1):
        item = reviewed_by_dimension[dimension_id]
        original = item.original_finding.finding
        with st.expander(
            f"{index}. {original.dimension_name} — {item.review_action.value}",
            expanded=False,
        ):
            st.write(f"Human disposition: **{item.review_action.value}**")
            if item.reviewer_note:
                st.write(f"Reviewer note: {item.reviewer_note}")
            if item.review_action is ReviewAction.REJECTED:
                st.write("Final reviewed score: none — rejected without replacement.")
                _show_finding(original, prefix="Original")
                st.write("Not eligible for future aggregation — rejected.")
                continue
            assert item.final_finding is not None
            if item.review_action is ReviewAction.EDITED:
                _show_finding(original, prefix="Original")
                st.markdown("**Human-edited final values**")
            _show_finding(item.final_finding.finding, prefix="Final reviewed")
            _show_evidence(item.final_finding)
            if dimension_id in eligible:
                st.success(
                    "Eligible for future aggregation after exact-source reviewed admission."
                )
            else:
                st.write("Not eligible for future aggregation — unassessed.")

    st.warning(
        "This profile performs no aggregation, recurrence, cross-institution "
        "comparison, weighted index, or Prototype Systemic Signal calculation. "
        "It is a prototype audit signal, not a legal determination."
    )
    st.info(
        "Overall auditability is the independently reviewed seventh dimension; it "
        "is not calculated as an average of the other six."
    )
    st.caption(
        "Exact-source-readmitted eligible findings may contribute to the separate "
        "Systemic Signals page. This profile performs no aggregation itself."
    )
