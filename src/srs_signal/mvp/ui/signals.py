"""Reviewed-only threshold Systemic Signals dashboard."""

from __future__ import annotations

import streamlit as st

from pydantic import ValidationError

from srs_signal.mvp import (
    MvpAggregationReport,
    PrototypeSignalStatus,
    build_systemic_signal_report,
    initialize_mvp_session,
)
from srs_signal.mvp.aggregation import AggregationWorkflowError
from srs_signal.mvp.ui import _render_page_frame


def _render_overall_status(report: MvpAggregationReport) -> None:
    if report.prototype_signal_status is PrototypeSignalStatus.NO_RECURRENCE:
        st.info("No recurrence threshold met.")
    elif report.prototype_signal_status is PrototypeSignalStatus.RECURRING_DEFICIENCY:
        st.warning("Recurring deficiency pattern within one institution type.")
    else:
        st.error("Prototype Systemic Signal — cross-institution threshold met.")


def _render_patterns(report: MvpAggregationReport) -> None:
    st.subheader("Contributing patterns")
    if not report.recurrence_patterns:
        st.write("No canonical dimension currently reaches the two-decision threshold.")
        return
    decision_by_case = {item.case_id: item for item in report.reviewed_decisions}
    for pattern in report.recurrence_patterns:
        label = (
            f"{pattern.dimension_name} — "
            + (
                "cross-institution recurrence"
                if pattern.cross_institution
                else "recurrence"
            )
        )
        with st.expander(label, expanded=True):
            st.write(pattern.threshold_explanation)
            for contributor in pattern.contributors:
                decision = decision_by_case[contributor.case_id]
                st.markdown(f"**{decision.decision_title}**")
                st.write(
                    f"Institution type: {contributor.institution_type.value}; "
                    f"score: {contributor.score}; status: {contributor.status.value}; "
                    f"review action: {contributor.review_action.value}."
                )
                st.caption(contributor.concise_finding)
            if pattern.recurring_deficiency_labels:
                st.write(
                    "Recurring deficiency labels: "
                    + ", ".join(
                        item.display_label
                        for item in pattern.recurring_deficiency_labels
                    )
                )
            else:
                st.write(
                    "The dimension recurs even though no normalized deficiency label "
                    "reaches the two-decision threshold."
                )


def _render_dimension_overview(report: MvpAggregationReport) -> None:
    st.subheader("Seven-dimension overview")
    rows = [
        "| Dimension | Eligible decisions | Serious decisions | Recurrence | Cross-institution |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    rows.extend(
        "| "
        + " | ".join(
            (
                summary.dimension_name,
                str(summary.eligible_reviewed_decision_count),
                str(summary.serious_reviewed_decision_count),
                "yes" if summary.recurrence else "no",
                "yes" if summary.cross_institution_recurrence else "no",
            )
        )
        + " |"
        for summary in report.dimension_summaries
    )
    st.markdown("\n".join(rows))


def render() -> None:
    """Recalculate and render the current reviewed-only threshold report."""

    _render_page_frame(
        page_title="Systemic Signals",
        introduction=(
            "This dashboard recalculates transparent recurrence thresholds only from "
            "current, exact-source-readmitted human-reviewed findings."
        ),
    )
    session = initialize_mvp_session(st.session_state)
    try:
        report = build_systemic_signal_report(session.srs_mvp_store)
        report = MvpAggregationReport.model_validate(report.model_dump())
    except (AggregationWorkflowError, ValidationError, ValueError) as exc:
        st.error(f"Systemic signal report could not be produced: {exc}")
        return

    for issue in report.excluded_entries:
        st.warning(f"Excluded reviewed entry `{issue.store_key}`: {issue.reason}.")

    st.subheader("Dataset readiness")
    st.markdown(
        "\n".join(
            (
                "| Measure | Value |",
                "| --- | ---: |",
                f"| Valid reviewed decisions | {report.valid_reviewed_decision_count} |",
                "| Minimum for recurrence | 2 |",
                f"| Institution types | {len(report.represented_institution_types)} |",
                f"| Serious observations | {report.serious_observation_count} |",
                f"| Recurrence patterns | {len(report.recurrence_patterns)} |",
            )
        )
    )
    st.caption(
        f"Excluded invalid or stale reviewed entries: "
        f"{report.excluded_reviewed_entry_count}."
    )
    if report.valid_reviewed_decision_count == 0:
        st.info(
            "Finalize and retain at least two current reviewed decisions to test "
            "the recurrence threshold."
        )

    st.subheader("Overall status")
    _render_overall_status(report)
    st.write(report.threshold_explanation)
    if report.considered_case_ids:
        st.caption("Considered reviewed cases: " + ", ".join(report.considered_case_ids))
    if report.signal_contributing_case_ids:
        st.caption(
            "Signal-contributing cases: "
            + ", ".join(report.signal_contributing_case_ids)
        )

    _render_patterns(report)
    _render_dimension_overview(report)

    st.subheader("Methodological limitations")
    for limitation in report.methodological_limitations:
        st.write(f"- {limitation}")
    st.warning(
        "This prototype signal is based on a limited, non-representative sample. "
        "It identifies recurring patterns; it does not determine that a legal "
        "violation or systemic dysfunction exists."
    )
    st.caption(f"Current admitted-input fingerprint: {report.input_fingerprint}")
