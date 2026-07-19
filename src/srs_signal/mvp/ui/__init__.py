"""Internal rendering helpers for the Streamlit MVP shell."""

from __future__ import annotations

import streamlit as st


_APP_TITLE = "SRS Signal"
_APP_SUBTITLE = "An Early-Warning System for Systemic Institutional Dysfunction"
_CORE_STATEMENT = (
    "We do not ask an AI model to decide whether a system is democratic. "
    "We examine whether public power can explain, document, and correct itself."
)
_LIMITATION_NOTICE = (
    "This research prototype will display prototype audit signals, not legal "
    "determinations. It does not decide whether a decision is lawful, unlawful, "
    "democratic, undemocratic, valid, or invalid."
)
_REVIEW_NOTICE = (
    "This MVP uses wholly fictional demonstration data. Human review "
    "is required before any finding may contribute to systemic signals."
)


def _render_page_frame(*, page_title: str, introduction: str) -> None:
    """Render shared identity and non-negotiable safeguards on every page."""

    st.title(_APP_TITLE)
    st.caption(_APP_SUBTITLE)
    st.markdown(f"> **{_CORE_STATEMENT}**")
    st.header(page_title)
    st.write(introduction)
    st.info(_LIMITATION_NOTICE)
    st.warning(_REVIEW_NOTICE)
