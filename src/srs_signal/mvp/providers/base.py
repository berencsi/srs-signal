"""Provider protocol independent of Streamlit and provider implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from srs_signal.domain import AnalysisProviderType, ProvisionalAuditResult
from srs_signal.mvp.models import AuditRequest


@runtime_checkable
class AuditProvider(Protocol):
    @property
    def provider_type(self) -> AnalysisProviderType: ...

    def analyze(self, request: AuditRequest) -> ProvisionalAuditResult: ...
