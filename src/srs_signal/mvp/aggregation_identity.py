"""Private deterministic identities shared by aggregation and report validation."""

from __future__ import annotations

from hashlib import sha256

from srs_signal.domain import DimensionId


def _length_delimited_sha256(*components: str) -> str:
    digest = sha256()
    for component in components:
        encoded = component.encode("utf-8", errors="strict")
        digest.update(len(encoded).to_bytes(8, byteorder="big", signed=False))
        digest.update(encoded)
    return digest.hexdigest()


def _dimension_pattern_id(
    dimension_id: DimensionId,
    observation_ids: tuple[str, ...],
) -> str:
    if not observation_ids:
        raise ValueError("Dimension pattern identity requires observations")
    return _length_delimited_sha256(
        dimension_id.value,
        *sorted(observation_ids),
    )


def _label_recurrence_id(
    dimension_id: DimensionId,
    normalized_label: str,
    observation_ids: tuple[str, ...],
) -> str:
    if not observation_ids:
        raise ValueError("Label recurrence identity requires observations")
    return _length_delimited_sha256(
        dimension_id.value,
        normalized_label,
        *sorted(observation_ids),
    )
