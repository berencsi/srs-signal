"""Namespaced, framework-neutral session-state initialization."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import cast

from pydantic import BaseModel, ConfigDict
from srs_signal.domain import AnalysisProviderType


class _MvpSessionValues(BaseModel):
    """Private strict validator for the seven application-owned values."""

    model_config = ConfigDict(extra="forbid", strict=True)

    srs_mvp_store: dict[str, object]
    srs_mvp_working: dict[str, object]
    srs_mvp_provider_mode: AnalysisProviderType
    srs_mvp_review_draft: dict[str, object]
    srs_mvp_current_decision_id: str | None
    srs_mvp_notice: str | None
    srs_mvp_seed_loaded: bool


def _validate_session_values(session: MutableMapping[str, object]) -> None:
    """Validate current values without using the resulting Pydantic copies."""

    _MvpSessionValues.model_validate(
        {
            "srs_mvp_store": session["srs_mvp_store"],
            "srs_mvp_working": session["srs_mvp_working"],
            "srs_mvp_provider_mode": session["srs_mvp_provider_mode"],
            "srs_mvp_review_draft": session["srs_mvp_review_draft"],
            "srs_mvp_current_decision_id": session["srs_mvp_current_decision_id"],
            "srs_mvp_notice": session["srs_mvp_notice"],
            "srs_mvp_seed_loaded": session["srs_mvp_seed_loaded"],
        }
    )


@dataclass(frozen=True, slots=True)
class MvpSessionState:
    """Validated live view over one supplied Streamlit session mapping.

    The mutable containers are intentionally created per initialization call.
    The store contains serialized provenance records, but never admitted handles.
    Source-bound detection and confirmation JSON lives inside the working mapping,
    not in new top-level session keys. Properties always read from the original
    mapping; no returned container is a detached copy.
    """

    _session: MutableMapping[str, object] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_session_values(self._session)

    @property
    def srs_mvp_store(self) -> dict[str, object]:
        return cast(dict[str, object], self._session["srs_mvp_store"])

    @property
    def srs_mvp_working(self) -> dict[str, object]:
        return cast(dict[str, object], self._session["srs_mvp_working"])

    @property
    def srs_mvp_provider_mode(self) -> AnalysisProviderType:
        return cast(
            AnalysisProviderType,
            self._session["srs_mvp_provider_mode"],
        )

    @property
    def srs_mvp_review_draft(self) -> dict[str, object]:
        return cast(dict[str, object], self._session["srs_mvp_review_draft"])

    @property
    def srs_mvp_current_decision_id(self) -> str | None:
        return cast(str | None, self._session["srs_mvp_current_decision_id"])

    @property
    def srs_mvp_notice(self) -> str | None:
        return cast(str | None, self._session["srs_mvp_notice"])

    @property
    def srs_mvp_seed_loaded(self) -> bool:
        return cast(bool, self._session["srs_mvp_seed_loaded"])


def initialize_mvp_session(
    session: MutableMapping[str, object],
) -> MvpSessionState:
    """Add missing MVP keys while preserving all existing session values.

    This function deliberately accepts an ordinary mutable mapping so its
    behavior does not depend on Streamlit, secrets, environment variables, or
    API credentials. Widget-specific state is not initialized here; future UI
    widget keys must use the ``_srs_widget_`` prefix.
    """

    if "srs_mvp_store" not in session:
        session["srs_mvp_store"] = {}
    if "srs_mvp_working" not in session:
        session["srs_mvp_working"] = {}
    if "srs_mvp_provider_mode" not in session:
        session["srs_mvp_provider_mode"] = AnalysisProviderType.DETERMINISTIC_DEMO
    if "srs_mvp_review_draft" not in session:
        session["srs_mvp_review_draft"] = {}
    if "srs_mvp_current_decision_id" not in session:
        session["srs_mvp_current_decision_id"] = None
    if "srs_mvp_notice" not in session:
        session["srs_mvp_notice"] = None
    if "srs_mvp_seed_loaded" not in session:
        session["srs_mvp_seed_loaded"] = False

    return MvpSessionState(session)
