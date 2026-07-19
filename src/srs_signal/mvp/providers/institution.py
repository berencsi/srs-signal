"""Institution-detector protocol independent of Streamlit."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from srs_signal.domain import AnalysisProviderType
from srs_signal.mvp.models import InstitutionDetectionResult


@runtime_checkable
class InstitutionDetector(Protocol):
    @property
    def provider_type(self) -> AnalysisProviderType: ...

    def detect(self, source_text: str) -> InstitutionDetectionResult: ...
