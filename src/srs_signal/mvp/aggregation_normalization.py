"""Private normalization rules shared by aggregation and report validation."""

from __future__ import annotations

import unicodedata


def _normalize_deficiency_label(value: str) -> tuple[str, str] | None:
    normalized_unicode = unicodedata.normalize("NFKC", value)
    display = " ".join(normalized_unicode.strip().split())
    if not display:
        return None
    return display.casefold(), display


def _select_recurrence_display_label(
    normalized_label: str,
    contributor_labels: tuple[
        tuple[str, tuple[tuple[str, str], ...]],
        ...,
    ],
) -> str:
    """Select the matching label from the smallest contributing observation ID."""

    if not contributor_labels:
        raise ValueError("Recurrence display selection requires contributors")
    matching: list[tuple[str, str]] = []
    for observation_id, labels in contributor_labels:
        displays = tuple(
            display for normalized, display in labels if normalized == normalized_label
        )
        if len(displays) != 1:
            raise ValueError(
                "Each recurrence contributor must carry one matching deficiency label"
            )
        matching.append((observation_id, displays[0]))
    return min(matching, key=lambda item: item[0])[1]
