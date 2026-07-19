"""Private exact-source catalog shared by deterministic MVP providers."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

from pydantic import ValidationError

from srs_signal.domain import AnalysisProviderType, ProvisionalAuditResult
from srs_signal.domain.validation import source_text_sha256


_DEMO_PACKAGE = "srs_signal.mvp.demo"


class _DemoCatalogError(ValueError):
    """Raised when bundled assets or exact-source lookup fail."""


class _UnknownDemoCaseError(_DemoCatalogError):
    """Raised when no exact bundled source or case exists."""


@dataclass(frozen=True, slots=True)
class _LoadedDemoCase:
    case_id: str
    display_name: str
    source_text: str
    provisional_result: ProvisionalAuditResult
    source_text_sha256: str


@dataclass(frozen=True, slots=True)
class _CaseDefinition:
    case_id: str
    display_name: str
    decision_filename: str
    analysis_filename: str


_CASE_DEFINITIONS = (
    _CaseDefinition(
        "fictional-civic-chamber",
        "Fictional Civic Chamber",
        "fictional_civic_chamber.txt",
        "fictional_civic_chamber.json",
    ),
    _CaseDefinition(
        "fictional-services-authority",
        "Fictional Services Authority",
        "fictional_services_authority.txt",
        "fictional_services_authority.json",
    ),
    _CaseDefinition(
        "fictional-review-board",
        "Fictional Review Board",
        "fictional_review_board.txt",
        "fictional_review_board.json",
    ),
)


def _read_asset(directory: str, filename: str) -> str:
    try:
        return (
            resources.files(_DEMO_PACKAGE)
            .joinpath(directory, filename)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError, UnicodeError) as exc:
        raise _DemoCatalogError(
            "A required deterministic demonstration asset could not be loaded"
        ) from exc


@lru_cache(maxsize=1)
def _loaded_demo_cases() -> tuple[_LoadedDemoCase, ...]:
    loaded: list[_LoadedDemoCase] = []
    seen_hashes: set[str] = set()
    seen_case_ids: set[str] = set()
    for definition in _CASE_DEFINITIONS:
        source_text = _read_asset("decisions", definition.decision_filename)
        try:
            provisional = ProvisionalAuditResult.model_validate_json(
                _read_asset("analyses", definition.analysis_filename)
            )
        except (ValidationError, ValueError) as exc:
            raise _DemoCatalogError(
                "A deterministic analysis asset failed structural validation"
            ) from exc
        if provisional.provider_type is not AnalysisProviderType.DETERMINISTIC_DEMO:
            raise _DemoCatalogError(
                "A deterministic analysis asset declares the wrong provider type"
            )
        if (
            provisional.decision_metadata.decision_id != definition.case_id
            or provisional.decision_metadata.institution_name != definition.display_name
        ):
            raise _DemoCatalogError(
                "A deterministic analysis asset does not match its case definition"
            )
        metadata = provisional.decision_metadata
        expected_source_declarations = (
            f"Issuing institution: {metadata.institution_name}",
            "Institution type supplied by fixture: "
            + metadata.institution_type.value.replace("_", " "),
            f"Decision title: {metadata.title}",
        )
        if any(item not in source_text for item in expected_source_declarations):
            raise _DemoCatalogError(
                "A deterministic source does not match its analysis metadata"
            )
        source_hash = source_text_sha256(source_text)
        if source_hash in seen_hashes or definition.case_id in seen_case_ids:
            raise _DemoCatalogError(
                "Deterministic decision assets must have unique identities and hashes"
            )
        seen_hashes.add(source_hash)
        seen_case_ids.add(definition.case_id)
        loaded.append(
            _LoadedDemoCase(
                case_id=definition.case_id,
                display_name=definition.display_name,
                source_text=source_text,
                provisional_result=provisional,
                source_text_sha256=source_hash,
            )
        )
    return tuple(loaded)


def _demo_case_by_id(case_id: str) -> _LoadedDemoCase:
    for case in _loaded_demo_cases():
        if case.case_id == case_id:
            return case
    raise _UnknownDemoCaseError(
        "The requested deterministic case identifier is unknown"
    )


def _demo_case_for_source(source_text: str) -> _LoadedDemoCase:
    requested_hash = source_text_sha256(source_text)
    for case in _loaded_demo_cases():
        if case.source_text_sha256 == requested_hash:
            if case.source_text != source_text:
                raise _UnknownDemoCaseError(
                    "Deterministic processing requires the exact bundled source text"
                )
            return case
    raise _UnknownDemoCaseError(
        "Deterministic processing is unavailable for unknown or modified source text"
    )
