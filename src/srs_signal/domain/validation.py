"""Source-backed evidence validation with deterministic resource limits."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from weakref import WeakSet

from srs_signal.domain.enums import EvidenceMatchMethod
from srs_signal.domain.limits import (
    MAX_INDIVIDUAL_QUOTATION_CHARACTERS,
    MAX_NORMALIZATION_SEGMENT_CHARACTERS,
    MAX_NORMALIZATION_WORK_UNITS,
    MAX_NORMALIZED_CANDIDATE_POSITIONS_PER_QUOTATION,
    MAX_OCCURRENCES_PER_QUOTATION,
    MAX_QUOTATIONS_PER_AUDIT,
    MAX_SOURCE_TEXT_CHARACTERS,
    MAX_TOTAL_QUOTATION_CHARACTERS_PER_AUDIT,
)
from srs_signal.domain.schemas import (
    DimensionFinding,
    EvidenceOccurrence,
    EvidenceQuote,
    EvidenceValidatedDimensionFinding,
    EvidenceVerificationResult,
    ProvisionalAuditResult,
    ValidatedAuditResult,
)


class EvidenceVerificationError(ValueError):
    """Raised when evidence cannot cross the validation boundary safely."""


class ResourceLimitError(EvidenceVerificationError):
    """Raised before normalization when untrusted input exceeds an MVP limit."""


@dataclass(frozen=True, slots=True)
class _ResourceLimitMetadata:
    """Configured limits and observed source measurements."""

    source_character_count: int
    longest_normalization_segment: int
    maximum_source_text_characters: int = MAX_SOURCE_TEXT_CHARACTERS
    maximum_normalization_segment_characters: int = (
        MAX_NORMALIZATION_SEGMENT_CHARACTERS
    )
    maximum_quotations_per_audit: int = MAX_QUOTATIONS_PER_AUDIT
    maximum_individual_quotation_characters: int = (
        MAX_INDIVIDUAL_QUOTATION_CHARACTERS
    )
    maximum_total_quotation_characters_per_audit: int = (
        MAX_TOTAL_QUOTATION_CHARACTERS_PER_AUDIT
    )
    maximum_occurrences_per_quotation: int = MAX_OCCURRENCES_PER_QUOTATION
    maximum_normalized_candidate_positions_per_quotation: int = (
        MAX_NORMALIZED_CANDIDATE_POSITIONS_PER_QUOTATION
    )
    estimated_normalization_work_units: int = 0
    maximum_normalization_work_units: int = MAX_NORMALIZATION_WORK_UNITS


class _PreparationCapability:
    __slots__ = ()


_PREPARATION_CAPABILITY = _PreparationCapability()


class _PreparedSource:
    """Guarded internal source representation derived from exact source text."""

    __slots__ = (
        "__weakref__",
        "normalized_source_text",
        "resource_limits",
        "source_ends",
        "source_starts",
        "source_text",
        "source_text_sha256",
    )

    def __init__(
        self,
        capability: object,
        *,
        source_text: str,
        source_text_sha256: str,
        normalized_source_text: str,
        source_starts: tuple[int, ...],
        source_ends: tuple[int, ...],
        resource_limits: _ResourceLimitMetadata,
    ) -> None:
        if capability is not _PREPARATION_CAPABILITY:
            raise TypeError("Prepared sources are created from exact source text")
        object.__setattr__(self, "source_text", source_text)
        object.__setattr__(self, "source_text_sha256", source_text_sha256)
        object.__setattr__(self, "normalized_source_text", normalized_source_text)
        object.__setattr__(self, "source_starts", source_starts)
        object.__setattr__(self, "source_ends", source_ends)
        object.__setattr__(self, "resource_limits", resource_limits)
        _PREPARED_SOURCES.add(self)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Prepared sources are immutable")


@dataclass(frozen=True, slots=True)
class _NormalizedText:
    text: str
    source_starts: tuple[int, ...]
    source_ends: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ValidSourceSpan:
    character_start: int
    character_end: int
    source_text: str
    match_method: EvidenceMatchMethod


_PREPARED_SOURCES: WeakSet[_PreparedSource] = WeakSet()


def source_text_sha256(source_text: str) -> str:
    _require_exact_source_text(source_text)
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def _require_exact_source_text(source_text: object) -> str:
    if not isinstance(source_text, str):
        raise TypeError("Evidence validation requires exact source text as str")
    return source_text


def _segment_boundaries(text: str) -> tuple[tuple[int, int], ...]:
    if not text:
        return ()
    boundaries: list[tuple[int, int]] = []
    start = 0
    whitespace = text[0].isspace()
    for index in range(1, len(text)):
        current = text[index].isspace()
        if current != whitespace:
            boundaries.append((start, index))
            start = index
            whitespace = current
    boundaries.append((start, len(text)))
    return tuple(boundaries)


def _validate_source_limits(
    source_text: str,
) -> tuple[tuple[tuple[int, int], ...], _ResourceLimitMetadata]:
    _require_exact_source_text(source_text)
    source_length = len(source_text)
    if source_length > MAX_SOURCE_TEXT_CHARACTERS:
        raise ResourceLimitError(
            f"Source text exceeds {MAX_SOURCE_TEXT_CHARACTERS} characters"
        )
    boundaries = _segment_boundaries(source_text)
    longest_segment = max((end - start for start, end in boundaries), default=0)
    if longest_segment > MAX_NORMALIZATION_SEGMENT_CHARACTERS:
        raise ResourceLimitError(
            "Uninterrupted normalization segment exceeds "
            f"{MAX_NORMALIZATION_SEGMENT_CHARACTERS} characters"
        )
    # Each bounded prefix mapper normalizes and compares prefixes. This estimate
    # deliberately over-approximates those character-level operations and adds
    # one whole-source pass. It is checked before normalization begins.
    estimated_work = source_length + sum(
        segment_length * (segment_length + 1)
        for start, end in boundaries
        if (segment_length := end - start)
    )
    if estimated_work > MAX_NORMALIZATION_WORK_UNITS:
        raise ResourceLimitError(
            "Estimated normalization work exceeds "
            f"{MAX_NORMALIZATION_WORK_UNITS} units"
        )
    return boundaries, _ResourceLimitMetadata(
        source_character_count=source_length,
        longest_normalization_segment=longest_segment,
        estimated_normalization_work_units=estimated_work,
    )


def _validate_quote_limits(quotes: tuple[EvidenceQuote, ...]) -> None:
    if len(quotes) > MAX_QUOTATIONS_PER_AUDIT:
        raise ResourceLimitError(
            f"Audit exceeds {MAX_QUOTATIONS_PER_AUDIT} quotations"
        )
    total_characters = 0
    for quote in quotes:
        quote_length = len(quote.text)
        if quote_length > MAX_INDIVIDUAL_QUOTATION_CHARACTERS:
            raise ResourceLimitError(
                "Quotation exceeds "
                f"{MAX_INDIVIDUAL_QUOTATION_CHARACTERS} characters"
            )
        total_characters += quote_length
    if total_characters > MAX_TOTAL_QUOTATION_CHARACTERS_PER_AUDIT:
        raise ResourceLimitError(
            "Audit quotation text exceeds "
            f"{MAX_TOTAL_QUOTATION_CHARACTERS_PER_AUDIT} total characters"
        )


def _audit_quotes(
    provisional_result: ProvisionalAuditResult,
) -> tuple[EvidenceQuote, ...]:
    return tuple(
        quote
        for finding in provisional_result.dimension_findings
        for quote in finding.supporting_quotations
    )


def _incremental_nfkc_map(text: str, base_offset: int) -> _NormalizedText:
    """Map NFKC prefixes within one already bounded uninterrupted segment."""

    previous = ""
    starts: list[int] = []
    ends: list[int] = []
    for relative_index in range(len(text)):
        current = unicodedata.normalize("NFKC", text[: relative_index + 1])
        common_prefix = 0
        limit = min(len(previous), len(current))
        while common_prefix < limit and previous[common_prefix] == current[common_prefix]:
            common_prefix += 1
        changed_start = (
            min(starts[common_prefix:])
            if common_prefix < len(starts)
            else base_offset + relative_index
        )
        del starts[common_prefix:]
        del ends[common_prefix:]
        for _ in current[common_prefix:]:
            starts.append(changed_start)
            ends.append(base_offset + relative_index + 1)
        previous = current
    return _NormalizedText(previous, tuple(starts), tuple(ends))


def _nfkc_with_offsets(
    text: str, boundaries: tuple[tuple[int, int], ...]
) -> _NormalizedText:
    mapped_segments = tuple(
        _incremental_nfkc_map(text[start:end], start) for start, end in boundaries
    )
    joined_text = "".join(segment.text for segment in mapped_segments)
    whole_text = unicodedata.normalize("NFKC", text)
    if joined_text != whole_text:
        if len(text) > MAX_NORMALIZATION_SEGMENT_CHARACTERS:
            raise ResourceLimitError(
                "Whole-string normalization would exceed the bounded mapper limit"
            )
        # The complete fallback is allowed only within the same segment bound.
        return _incremental_nfkc_map(text, 0)
    return _NormalizedText(
        joined_text,
        tuple(value for segment in mapped_segments for value in segment.source_starts),
        tuple(value for segment in mapped_segments for value in segment.source_ends),
    )


def _collapse_whitespace_with_offsets(mapped: _NormalizedText) -> _NormalizedText:
    characters: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for character, start, end in zip(
        mapped.text, mapped.source_starts, mapped.source_ends, strict=True
    ):
        if character.isspace():
            if characters and characters[-1] == " ":
                starts[-1] = min(starts[-1], start)
                ends[-1] = max(ends[-1], end)
                continue
            character = " "
        characters.append(character)
        starts.append(start)
        ends.append(end)
    return _NormalizedText("".join(characters), tuple(starts), tuple(ends))


def _normalize_with_offsets(
    text: str, boundaries: tuple[tuple[int, int], ...]
) -> _NormalizedText:
    return _collapse_whitespace_with_offsets(_nfkc_with_offsets(text, boundaries))


def normalize_evidence_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    characters: list[str] = []
    for character in normalized:
        if character.isspace():
            if characters and characters[-1] == " ":
                continue
            character = " "
        characters.append(character)
    return "".join(characters)


def _prepare_source(source_text: str) -> _PreparedSource:
    """Reject over-limit input, then normalize the exact source once."""

    _require_exact_source_text(source_text)
    boundaries, limit_metadata = _validate_source_limits(source_text)
    normalized = _normalize_with_offsets(source_text, boundaries)
    return _PreparedSource(
        _PREPARATION_CAPABILITY,
        source_text=source_text,
        source_text_sha256=source_text_sha256(source_text),
        normalized_source_text=normalized.text,
        source_starts=normalized.source_starts,
        source_ends=normalized.source_ends,
        resource_limits=limit_metadata,
    )


def _prepare_audit_source(
    provisional_result: ProvisionalAuditResult, source_text: str
) -> _PreparedSource:
    """Check all quote limits before preparing the source."""

    _validate_quote_limits(_audit_quotes(provisional_result))
    return _prepare_source(source_text)


def _iter_match_positions(haystack: str, needle: str) -> Iterator[int]:
    if not needle:
        return
    start = 0
    while True:
        position = haystack.find(needle, start)
        if position < 0:
            return
        yield position
        start = position + 1


def _add_valid_source_span(
    by_span: dict[tuple[int, int], _ValidSourceSpan],
    candidate: _ValidSourceSpan,
) -> None:
    span = (candidate.character_start, candidate.character_end)
    existing = by_span.get(span)
    if existing is not None:
        if (
            candidate.match_method is EvidenceMatchMethod.EXACT
            and existing.match_method is not EvidenceMatchMethod.EXACT
        ):
            by_span[span] = candidate
        return
    if len(by_span) >= MAX_OCCURRENCES_PER_QUOTATION:
        raise ResourceLimitError(
            "Quotation exceeds the ambiguity limit of "
            f"{MAX_OCCURRENCES_PER_QUOTATION} valid unique source spans"
        )
    by_span[span] = candidate


def _collect_exact_source_spans(
    prepared_source: _PreparedSource,
    quote_text: str,
    by_span: dict[tuple[int, int], _ValidSourceSpan],
) -> bool:
    found = False
    for start in _iter_match_positions(prepared_source.source_text, quote_text):
        found = True
        end = start + len(quote_text)
        _add_valid_source_span(
            by_span,
            _ValidSourceSpan(
                character_start=start,
                character_end=end,
                source_text=prepared_source.source_text[start:end],
                match_method=EvidenceMatchMethod.EXACT,
            ),
        )
    return found


def _collect_normalized_source_spans(
    prepared_source: _PreparedSource,
    quote_text: str,
    by_span: dict[tuple[int, int], _ValidSourceSpan],
) -> None:
    normalized_quote = normalize_evidence_text(quote_text)
    if not normalized_quote:
        return
    seen_candidate_spans: set[tuple[int, int]] = set()
    for candidate_number, normalized_start in enumerate(
        _iter_match_positions(
            prepared_source.normalized_source_text, normalized_quote
        ),
        start=1,
    ):
        if candidate_number > MAX_NORMALIZED_CANDIDATE_POSITIONS_PER_QUOTATION:
            raise ResourceLimitError(
                "Normalized quotation search exceeds the raw candidate safety limit of "
                f"{MAX_NORMALIZED_CANDIDATE_POSITIONS_PER_QUOTATION} positions"
            )
        normalized_end = normalized_start + len(normalized_quote)
        source_start = prepared_source.source_starts[normalized_start]
        source_end = prepared_source.source_ends[normalized_end - 1]
        span = (source_start, source_end)
        if span in seen_candidate_spans:
            continue
        seen_candidate_spans.add(span)
        source_slice = prepared_source.source_text[source_start:source_end]
        if normalize_evidence_text(source_slice) != normalized_quote:
            continue
        _add_valid_source_span(
            by_span,
            _ValidSourceSpan(
                character_start=source_start,
                character_end=source_end,
                source_text=source_slice,
                match_method=(
                    EvidenceMatchMethod.EXACT
                    if source_slice == quote_text
                    else EvidenceMatchMethod.NORMALIZED
                ),
            ),
        )


def _require_prepared_source(value: object) -> _PreparedSource:
    if (
        type(value) is not _PreparedSource
        or value not in _PREPARED_SOURCES
    ):
        raise TypeError("Internal evidence processing requires a prepared source")
    return value


def _verify_prepared_evidence_quote(
    prepared_source: _PreparedSource, quote: EvidenceQuote
) -> EvidenceVerificationResult:
    """Verify one bounded quote using a once-prepared source."""

    prepared_source = _require_prepared_source(prepared_source)
    _validate_quote_limits((quote,))
    by_span: dict[tuple[int, int], _ValidSourceSpan] = {}
    exact_found = _collect_exact_source_spans(
        prepared_source, quote.text, by_span
    )
    _collect_normalized_source_spans(prepared_source, quote.text, by_span)
    occurrences = tuple(
        EvidenceOccurrence(
            character_start=item.character_start,
            character_end=item.character_end,
            source_text=item.source_text,
            match_method=item.match_method,
        )
        for item in (by_span[span] for span in sorted(by_span))
    )
    primary_method = (
        EvidenceMatchMethod.EXACT
        if exact_found
        else EvidenceMatchMethod.NORMALIZED
        if occurrences
        else EvidenceMatchMethod.NO_MATCH
    )
    supplied_offsets_valid: bool | None = None
    if quote.character_start is not None and quote.character_end is not None:
        supplied_offsets_valid = any(
            item.character_start == quote.character_start
            and item.character_end == quote.character_end
            for item in occurrences
        )
    return EvidenceVerificationResult(
        quote=quote,
        matched=bool(occurrences),
        primary_match_method=primary_method,
        occurrences=occurrences,
        supplied_offsets_valid=supplied_offsets_valid,
        ambiguous=len(occurrences) > 1,
    )


def verify_evidence_quote(
    source_text: str, quote: EvidenceQuote
) -> EvidenceVerificationResult:
    _validate_quote_limits((quote,))
    return _verify_prepared_evidence_quote(_prepare_source(source_text), quote)


def require_verified_evidence(
    source_text: str,
    quotes: tuple[EvidenceQuote, ...],
) -> tuple[EvidenceVerificationResult, ...]:
    """Verify quotations from exact source text; prepared objects are not accepted."""

    _validate_quote_limits(quotes)
    prepared = _prepare_source(source_text)
    return _require_verified_evidence(prepared, quotes)


def _require_verified_evidence(
    prepared_source: _PreparedSource,
    quotes: tuple[EvidenceQuote, ...],
) -> tuple[EvidenceVerificationResult, ...]:
    prepared = _require_prepared_source(prepared_source)
    _validate_quote_limits(quotes)
    results = tuple(_verify_prepared_evidence_quote(prepared, quote) for quote in quotes)
    for result in results:
        if not result.matched:
            raise EvidenceVerificationError(
                f"Evidence quote {result.quote.quote_id!r} does not occur in the source"
            )
        if result.supplied_offsets_valid is False:
            raise EvidenceVerificationError(
                f"Evidence quote {result.quote.quote_id!r} has invalid source offsets"
            )
    return results


def _validate_finding_against_prepared_source(
    finding: DimensionFinding,
    prepared_source: _PreparedSource,
) -> EvidenceValidatedDimensionFinding:
    prepared = _require_prepared_source(prepared_source)
    verifications = _require_verified_evidence(
        prepared, finding.supporting_quotations
    )
    return EvidenceValidatedDimensionFinding(
        finding=finding,
        evidence_verifications=verifications,
        source_text_sha256=prepared.source_text_sha256,
    )


def validate_finding_evidence(
    finding: DimensionFinding,
    source_text: str,
    *,
    expected_source_text_sha256: str | None = None,
) -> EvidenceValidatedDimensionFinding:
    _validate_quote_limits(finding.supporting_quotations)
    prepared = _prepare_source(source_text)
    if (
        expected_source_text_sha256 is not None
        and prepared.source_text_sha256 != expected_source_text_sha256
    ):
        raise EvidenceVerificationError(
            "Edited evidence must be validated against the original source text"
        )
    return _validate_finding_against_prepared_source(finding, prepared)


def _build_validated_audit_from_prepared_source(
    provisional_result: ProvisionalAuditResult,
    prepared_source: _PreparedSource,
    *,
    validated_at: datetime,
) -> ValidatedAuditResult:
    prepared = _require_prepared_source(prepared_source)
    _validate_quote_limits(_audit_quotes(provisional_result))
    validated_findings = tuple(
        _validate_finding_against_prepared_source(finding, prepared)
        for finding in provisional_result.dimension_findings
    )
    return ValidatedAuditResult(
        schema_version=provisional_result.schema_version,
        provisional_result=provisional_result,
        source_text_sha256=prepared.source_text_sha256,
        evidence_validated_at=validated_at,
        validated_findings=validated_findings,
    )


def validate_audit_evidence(
    provisional_result: ProvisionalAuditResult,
    source_text: str,
    *,
    validated_at: datetime,
) -> ValidatedAuditResult:
    prepared = _prepare_audit_source(provisional_result, source_text)
    return _build_validated_audit_from_prepared_source(
        provisional_result, prepared, validated_at=validated_at
    )
