"""Private typed helpers for transient MVP session provenance."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from pydantic import ValidationError

from srs_signal.mvp.models import MvpDecisionRecord
from srs_signal.mvp.review_models import MvpReviewedDecisionRecord, _MvpReviewDraft


_REVIEWED_PREFIX = "reviewed::"


class MvpStoreError(ValueError):
    """Raised when transient session provenance is malformed or misclassified."""


_RecordT = TypeVar("_RecordT", MvpDecisionRecord, MvpReviewedDecisionRecord)


@dataclass(frozen=True, slots=True)
class _StoreIssue:
    key: str
    namespace: Literal["analysis", "reviewed"]
    message: str


@dataclass(frozen=True, slots=True)
class _StoreEnumeration(Generic[_RecordT]):
    records: tuple[_RecordT, ...]
    issues: tuple[_StoreIssue, ...]


def _reviewed_key(case_id: str) -> str:
    return f"{_REVIEWED_PREFIX}{case_id}"


def _analysis_keys(store: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted(key for key in store if not key.startswith(_REVIEWED_PREFIX)))


def _reviewed_keys(store: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted(key for key in store if key.startswith(_REVIEWED_PREFIX)))


def _parse_analysis(payload: object) -> MvpDecisionRecord:
    if not isinstance(payload, str):
        raise MvpStoreError("Stored analysis provenance must be serialized JSON")
    try:
        return MvpDecisionRecord.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise MvpStoreError("Stored analysis provenance is malformed") from exc


def _parse_reviewed(payload: object) -> MvpReviewedDecisionRecord:
    if not isinstance(payload, str):
        raise MvpStoreError("Stored reviewed provenance must be serialized JSON")
    try:
        return MvpReviewedDecisionRecord.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise MvpStoreError("Stored reviewed provenance is malformed") from exc


def _load_analysis(store: Mapping[str, object], case_id: str) -> MvpDecisionRecord:
    if case_id.startswith(_REVIEWED_PREFIX):
        raise MvpStoreError("Reviewed keys cannot be loaded as analysis provenance")
    try:
        payload = store[case_id]
    except KeyError as exc:
        raise MvpStoreError("Stored analysis provenance was not found") from exc
    record = _parse_analysis(payload)
    if record.case_id != case_id:
        raise MvpStoreError("Analysis store key does not match the record case")
    return record


def _load_reviewed(
    store: Mapping[str, object], case_id: str
) -> MvpReviewedDecisionRecord:
    current_analysis = _load_analysis(store, case_id)
    key = _reviewed_key(case_id)
    try:
        payload = store[key]
    except KeyError as exc:
        raise MvpStoreError("Stored reviewed provenance was not found") from exc
    record = _parse_reviewed(payload)
    if record.analysis_record.case_id != case_id:
        raise MvpStoreError("Reviewed store key does not match the record case")
    if record.analysis_record != current_analysis:
        raise MvpStoreError(
            "Stored reviewed provenance does not match the current analysis"
        )
    try:
        from srs_signal.mvp.reviews import _readmit_reviewed_profile

        _readmit_reviewed_profile(record)
    except (ValueError, ValidationError) as exc:
        raise MvpStoreError(
            "Stored reviewed provenance failed exact-source readmission"
        ) from exc
    return record


def _list_analysis_records(
    store: Mapping[str, object],
) -> _StoreEnumeration[MvpDecisionRecord]:
    records: list[MvpDecisionRecord] = []
    issues: list[_StoreIssue] = []
    for key in _analysis_keys(store):
        try:
            records.append(_load_analysis(store, key))
        except MvpStoreError as exc:
            issues.append(
                _StoreIssue(
                    key=key,
                    namespace="analysis",
                    message=str(exc),
                )
            )
    return _StoreEnumeration(records=tuple(records), issues=tuple(issues))


def _list_reviewed_records(
    store: Mapping[str, object],
) -> _StoreEnumeration[MvpReviewedDecisionRecord]:
    records: list[MvpReviewedDecisionRecord] = []
    issues: list[_StoreIssue] = []
    for key in _reviewed_keys(store):
        try:
            records.append(
                _load_reviewed(store, key.removeprefix(_REVIEWED_PREFIX))
            )
        except MvpStoreError as exc:
            issues.append(
                _StoreIssue(
                    key=key,
                    namespace="reviewed",
                    message=str(exc),
                )
            )
    return _StoreEnumeration(records=tuple(records), issues=tuple(issues))


def _save_reviewed(
    store: MutableMapping[str, object], record: MvpReviewedDecisionRecord
) -> str:
    checked = MvpReviewedDecisionRecord.model_validate(record.model_dump())
    current_analysis = _load_analysis(store, checked.analysis_record.case_id)
    if current_analysis != checked.analysis_record:
        raise MvpStoreError(
            "Reviewed provenance does not match the current stored analysis"
        )
    # Keep the private UI store path aligned with the workflow boundary. The
    # record remains untrusted after storage and will be readmitted again on load.
    from srs_signal.mvp.reviews import _readmit_reviewed_profile

    _readmit_reviewed_profile(checked)
    key = _reviewed_key(checked.analysis_record.case_id)
    store[key] = checked.model_dump_json()
    return key


def _invalidate_case_review(
    store: MutableMapping[str, object],
    review_draft: MutableMapping[str, object],
    case_id: str,
) -> None:
    store.pop(_reviewed_key(case_id), None)
    payload = review_draft.get("payload")
    if payload is None:
        return
    if not isinstance(payload, str):
        review_draft.clear()
        return
    try:
        draft = _MvpReviewDraft.model_validate_json(payload)
    except (ValidationError, ValueError):
        review_draft.clear()
        return
    if draft.case_id == case_id:
        review_draft.clear()
