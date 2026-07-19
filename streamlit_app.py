"""Streamlit entry point for the SRS Signal hackathon MVP."""

from __future__ import annotations

import streamlit as st

from srs_signal.mvp import initialize_mvp_session
from srs_signal.mvp.ui import analyze, profile, review, signals


st.set_page_config(
    page_title="SRS Signal",
    page_icon="🔎",
    layout="wide",
)
initialize_mvp_session(st.session_state)

selected_page = st.navigation(
    [
        st.Page(
            analyze.render,
            title="Analyze Decision",
            icon="📄",
            url_path="analyze",
            default=True,
        ),
        st.Page(
            review.render,
            title="Human Review",
            icon="✅",
            url_path="review",
        ),
        st.Page(
            profile.render,
            title="Reviewed Audit Profile",
            icon="📋",
            url_path="profile",
        ),
        st.Page(
            signals.render,
            title="Systemic Signals",
            icon="📊",
            url_path="signals",
        ),
    ]
)
selected_page.run()
