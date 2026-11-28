# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pure validation of Guesty reservation records.

The validator decides which parts of a raw reservation record are
reliably proven valid. It never logs, never performs I/O and never
carries a payload value in a reason, so its output is safe to report.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from custom_components.guesty.api.const import HOLD_STATUSES, V3_STATUS_MAP
from custom_components.guesty.api.models import (
    GuestyGuest,
    GuestyMoney,
    GuestyReservation,
    GuestyStaySegment,
    RejectedCode,
    ValidationSummary,
    _optional_str,
    _parse_custom_fields_array,
    _parse_iso_datetime,
    _valid_local_date,
)

_EXPLICIT_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:\d{2})\Z")

SUPPORTED_RECORD_ACTIONS: frozenset[str] = frozenset(
    {"add_reservation_note", "set_reservation_status", "set_custom_field"},
)


class ValidationReason(StrEnum):
    """Fixed categories explaining why data was not verified.

    A reason never carries a value from the payload. The record-level
    reasons are listed in evaluation order.
    """

    MALFORMED_STATUS = "malformed_status"
    UNRECOGNIZED_STATUS = "unrecognized_status"
    MISSING_IDENTIFIER = "missing_identifier"
    NO_STAY = "no_stay"
    NO_VERIFIED_SEGMENT = "no_verified_segment"
    PARTIAL_STAY = "partial_stay"
    INVALID_LISTING = "invalid_listing"
    UNVERIFIED_INSTANT = "unverified_instant"
    INVALID_INTERVAL = "invalid_interval"
    UNSUPPORTED_ACTION = "unsupported_action"
    SEGMENT_MALFORMED = "segment_malformed"
    SEGMENT_MISSING_LISTING = "segment_missing_listing"
    SEGMENT_MALFORMED_PARENT = "segment_malformed_parent"
    SEGMENT_UNVERIFIED_DATES = "segment_unverified_dates"
    SEGMENT_INVALID_DATES = "segment_invalid_dates"
    LISTING_TIMEZONE_UNVERIFIED = "listing_timezone_unverified"


_STATUS_REASONS = frozenset(
    {ValidationReason.MALFORMED_STATUS, ValidationReason.UNRECOGNIZED_STATUS},
)


@dataclass(frozen=True)
class SegmentAssessment:
    """The verdict on one raw ``stay[]`` entry.

    Attributes:
        raw_position: Index in the raw ``stay`` array.
        segment: The verified segment, present only when verified.
        reason: The rejection reason, present only when rejected.
        interval_check_out: The canonical local check-out date when the
            raw entry is a dict with a valid date-level interval, after
            the feature 010 fill, whatever its listing verdict. It is
            retirement evidence only, never published.
    """

    raw_position: int
    segment: GuestyStaySegment | None
    reason: ValidationReason | None
    interval_check_out: str | None = None


@dataclass(frozen=True)
class RecordAssessment:
    """The verdict on one raw v3 reservation record.

    Attributes:
        reservation_id: The record identifier when it is a non-empty
            string.
        status: The mapped recognized status, or None.
        raw_stay_count: Number of entries in the raw ``stay`` array.
        segments: One assessment per raw segment, in raw order.
        reason: The record-level rejection. Never ``partial_stay`` or
            ``unsupported_action``.
        conflict_code: A non-empty string confirmation code for a
            record with a verified identifier and no recognized hold
            status.
    """

    reservation_id: str | None
    status: str | None
    raw_stay_count: int
    segments: tuple[SegmentAssessment, ...]
    reason: ValidationReason | None
    conflict_code: str | None

    @property
    def accepted(self) -> bool:
        """Report whether the record has publishable verified data.

        Returns:
            True when there is no record-level rejection and at least
            one segment is verified.
        """
        return self.reason is None and any(
            item.segment is not None for item in self.segments
        )

    @property
    def fully_verified(self) -> bool:
        """Report whether every raw segment is verified.

        Returns:
            True when the record is accepted and no segment is rejected.
        """
        return self.accepted and all(item.segment is not None for item in self.segments)

    @property
    def partial(self) -> bool:
        """Report whether only some raw segments are verified.

        Returns:
            True when the record is accepted but not fully verified.
        """
        return self.accepted and not self.fully_verified

    @property
    def verified_segments(self) -> tuple[GuestyStaySegment, ...]:
        """Return the verified segments in raw order.

        Returns:
            The verified segments only.
        """
        return tuple(item.segment for item in self.segments if item.segment is not None)


@dataclass(frozen=True)
class RecordValidation:
    """The by-ID write validation verdict (feature 014 interface).

    Attributes:
        valid: Whether the record may be written for the action.
        reason: The rejection reason; None exactly when valid.
    """

    valid: bool
    reason: ValidationReason | None


def recognize_v3_status(raw: object) -> str | None:
    """Map a raw status through the allowlist without logging.

    Args:
        raw: The raw status value.

    Returns:
        The mapped status, or None when it is not recognized.
    """
    if not isinstance(raw, str):
        return None
    return V3_STATUS_MAP.get(raw.strip().lower())


def _status_verdict(raw: object) -> tuple[str | None, ValidationReason | None]:
    """Classify a raw status value.

    Args:
        raw: The raw status value.

    Returns:
        The mapped status and the rejection reason, one of them None.
    """
    if not isinstance(raw, str) or not raw:
        return None, ValidationReason.MALFORMED_STATUS
    mapped = recognize_v3_status(raw)
    if mapped is None:
        return None, ValidationReason.UNRECOGNIZED_STATUS
    return mapped, None


def _non_empty_str(value: object) -> str | None:
    """Return the value when it is a non-blank string.

    Args:
        value: Candidate value.

    Returns:
        The unchanged string, or None.
    """
    if isinstance(value, str) and value.strip():
        return value
    return None


def _conflict_code(
    raw: Mapping[str, Any],
    reservation_id: str | None,
    status: str | None,
) -> str | None:
    """Return the confirmation code usable as conflict evidence.

    Args:
        raw: The raw record.
        reservation_id: The verified identifier, or None.
        status: The mapped recognized status, or None.

    Returns:
        A non-empty string code, or None for a hold, a missing
        identifier or a code that is not a non-empty string.
    """
    if reservation_id is None or status in HOLD_STATUSES:
        return None
    code = raw.get("confirmationCode")
    return code if isinstance(code, str) and code else None


def non_negative_count(value: object) -> int | None:
    """Return a non-negative integer count such as a party size.

    A ``bool`` is not a count, although it is an ``int`` subclass.

    Args:
        value: The raw count value, such as ``guestsCount``.

    Returns:
        The count, or None when it is not a non-negative integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _feature_010_dates(
    raw: Mapping[str, Any],
    raw_stay: list[Any],
) -> tuple[str | None, str | None]:
    """Return top-level localized dates usable to fill a segment.

    The unchanged feature 010 predicate applies: exactly one raw
    segment that is a dict with a listing and without its own
    ``checkIn`` or ``checkOut``, and a parseable top-level interval
    with check-out after check-in. Timestamps never supply a date.

    Args:
        raw: The raw record.
        raw_stay: The raw ``stay`` list.

    Returns:
        The canonical top-level check-in and check-out dates, or None
        for each when the predicate fails or the date is not canonical.
    """
    if len(raw_stay) != 1:
        return None, None
    only = raw_stay[0]
    if (
        not isinstance(only, dict)
        or _non_empty_str(only.get("listingId")) is None
        or only.get("checkIn") is not None
        or only.get("checkOut") is not None
    ):
        return None, None
    check_in = _parse_iso_datetime(raw.get("checkIn"))
    check_out = _parse_iso_datetime(raw.get("checkOut"))
    if check_in is None or check_out is None or check_out <= check_in:
        return None, None
    return (
        _valid_local_date(raw.get("checkInDateLocalized")),
        _valid_local_date(raw.get("checkOutDateLocalized")),
    )


def _assess_segment(
    item: object,
    raw_position: int,
    fill: tuple[str | None, str | None],
) -> SegmentAssessment:
    """Apply the segment rules to one raw ``stay[]`` entry.

    Args:
        item: The raw segment.
        raw_position: Its index in the raw ``stay`` array.
        fill: Top-level dates usable for a missing (absent or null)
            segment date; never for a present malformed one.

    Returns:
        The segment assessment.
    """

    def rejected(reason: ValidationReason) -> SegmentAssessment:
        """Build a rejected segment assessment.

        Args:
            reason: The rejection reason.

        Returns:
            The rejected assessment.
        """
        return SegmentAssessment(raw_position, None, reason)

    if not isinstance(item, dict):
        return rejected(ValidationReason.SEGMENT_MALFORMED)
    raw_in = item.get("checkInDateLocalized")
    raw_out = item.get("checkOutDateLocalized")
    # Only a missing date is filled; a present malformed one stays
    # unverified (contract C6).
    check_in_local = fill[0] if raw_in is None else _valid_local_date(raw_in)
    check_out_local = fill[1] if raw_out is None else _valid_local_date(raw_out)
    interval_check_out = (
        check_out_local
        if check_in_local is not None
        and check_out_local is not None
        and check_in_local < check_out_local
        else None
    )

    def rejected_with_interval(reason: ValidationReason) -> SegmentAssessment:
        """Build a rejected assessment keeping its date-level interval.

        Args:
            reason: The rejection reason.

        Returns:
            The rejected assessment.
        """
        return SegmentAssessment(raw_position, None, reason, interval_check_out)

    listing_id = _non_empty_str(item.get("listingId"))
    if listing_id is None:
        return rejected_with_interval(ValidationReason.SEGMENT_MISSING_LISTING)
    parent = item.get("parentListingId")
    if parent is not None and _non_empty_str(parent) is None:
        return rejected_with_interval(ValidationReason.SEGMENT_MALFORMED_PARENT)
    if check_in_local is None or check_out_local is None:
        return rejected(ValidationReason.SEGMENT_UNVERIFIED_DATES)
    if check_in_local >= check_out_local:
        return rejected(ValidationReason.SEGMENT_INVALID_DATES)
    return SegmentAssessment(
        raw_position,
        GuestyStaySegment(
            listing_id=listing_id,
            check_in=None,
            check_out=None,
            parent_listing_id=parent,
            check_in_local=check_in_local,
            check_out_local=check_out_local,
            guests_count=non_negative_count(item.get("guestsCount")),
            raw_position=raw_position,
        ),
        None,
        interval_check_out,
    )


def assess_v3_record(raw: Mapping[str, Any]) -> RecordAssessment:
    """Assess one raw v3 search result.

    Record-level reasons are evaluated in order: status, stay, then
    segments. Segments are assessed whenever a stay list exists, even
    for a record rejected by its status.

    Args:
        raw: The raw v3 reservation record.

    Returns:
        The record assessment.
    """
    raw_id = raw.get("reservationId")
    reservation_id = raw_id if isinstance(raw_id, str) and raw_id else None
    status, reason = _status_verdict(raw.get("status"))
    raw_stay = raw.get("stay")
    segments: tuple[SegmentAssessment, ...] = ()
    raw_stay_count = 0
    if isinstance(raw_stay, list):
        raw_stay_count = len(raw_stay)
        fill = _feature_010_dates(raw, raw_stay)
        segments = tuple(
            _assess_segment(item, position, fill)
            for position, item in enumerate(raw_stay)
        )
    if reason is None and not segments:
        reason = ValidationReason.NO_STAY
    if reason is None and all(item.segment is None for item in segments):
        reason = ValidationReason.NO_VERIFIED_SEGMENT
    return RecordAssessment(
        reservation_id=reservation_id,
        status=status,
        raw_stay_count=raw_stay_count,
        segments=segments,
        reason=reason,
        conflict_code=_conflict_code(raw, reservation_id, status),
    )


def validate_record(
    raw: Mapping[str, object],
    action_type: str,
) -> RecordValidation:
    """Decide whether a by-ID v3 record may be written.

    Record reasons take precedence over the action check and follow
    the ``ValidationReason`` evaluation order: the status, a missing
    identifier, the remaining assessment reason, then a partial stay.
    An unsupported action is reported only for a fully verified
    record.

    Args:
        raw: The raw v3 reservation record.
        action_type: The requested write action.

    Returns:
        The validation verdict; ``reason`` is None exactly when valid.
    """
    assessment = assess_v3_record(raw)
    if assessment.reason in _STATUS_REASONS:
        return RecordValidation(False, assessment.reason)
    if assessment.reservation_id is None:
        return RecordValidation(False, ValidationReason.MISSING_IDENTIFIER)
    if assessment.reason is not None:
        return RecordValidation(False, assessment.reason)
    if assessment.partial:
        return RecordValidation(False, ValidationReason.PARTIAL_STAY)
    if action_type not in SUPPORTED_RECORD_ACTIONS:
        return RecordValidation(False, ValidationReason.UNSUPPORTED_ACTION)
    return RecordValidation(True, None)


@dataclass(frozen=True)
class LegacyAssessment:
    """Assessment of one legacy Open API reservation record.

    Attributes:
        reservation_id: The record's verified ``_id``.
        status: The mapped recognized status, or None.
        reason: The record-level rejection reason, or None if verified.
        conflict_code: Non-empty string code usable as conflict-only
            evidence, or None for a hold or a non-string code.
    """

    reservation_id: str
    status: str | None
    reason: ValidationReason | None
    conflict_code: str | None

    @property
    def accepted(self) -> bool:
        """Report whether the record is verified.

        Returns:
            True when no rule rejected the record.
        """
        return self.reason is None


def assess_legacy_record(raw: Mapping[str, Any]) -> LegacyAssessment:
    """Assess one legacy reservation record whose ``_id`` is verified.

    The status uses the same allowlist as v3, ``listingId`` must be a
    non-empty string, ``checkIn`` and ``checkOut`` must be parseable
    instants with an explicit offset (a naive value is never read as
    UTC) that can be expressed in UTC, and check-in must be strictly
    before check-out.

    Args:
        raw: The raw legacy record. Its ``_id`` must already be a
            non-empty string.

    Returns:
        The legacy assessment.
    """
    reservation_id = str(raw.get("_id"))
    status, reason = _status_verdict(raw.get("status"))
    if reason is None and _non_empty_str(raw.get("listingId")) is None:
        reason = ValidationReason.INVALID_LISTING
    if reason is None:
        raw_in, raw_out = raw.get("checkIn"), raw.get("checkOut")
        if not (explicit_offset_instant(raw_in) and explicit_offset_instant(raw_out)):
            reason = ValidationReason.UNVERIFIED_INSTANT
        else:
            check_in = _parse_iso_datetime(raw_in)
            check_out = _parse_iso_datetime(raw_out)
            if check_in is None or check_out is None or check_in >= check_out:
                reason = ValidationReason.INVALID_INTERVAL
    return LegacyAssessment(
        reservation_id=reservation_id,
        status=status,
        reason=reason,
        conflict_code=_conflict_code(raw, reservation_id, status),
    )


def _legacy_local_dates(
    raw: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Return the verified localized dates of a legacy record.

    Args:
        raw: The raw legacy record.

    Returns:
        Each canonical localized date, or None. When both are present
        but do not form a chronological interval, both are None.
    """
    check_in = _valid_local_date(raw.get("checkInDateLocalized"))
    check_out = _valid_local_date(raw.get("checkOutDateLocalized"))
    if check_in is not None and check_out is not None and check_in >= check_out:
        return None, None
    return check_in, check_out


def _finite_number(value: object) -> float | None:
    """Return a finite JSON number.

    Args:
        value: The raw amount value.

    Returns:
        The number, or None for a ``bool``, a non-number, a
        non-finite value or an integer too large for a float.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return None
    return value if finite else None


def _legacy_guest(value: object) -> GuestyGuest | None:
    """Return the legacy guest with only string fields kept.

    Args:
        value: The raw ``guest`` value.

    Returns:
        The guest, or None when it is not a dict or has no valid field.
    """
    if not isinstance(value, dict):
        return None
    guest = GuestyGuest(
        full_name=_optional_str(value.get("fullName")),
        phone=_optional_str(value.get("phone")),
        email=_optional_str(value.get("email")),
        guest_id=_optional_str(value.get("_id")),
    )
    return guest if guest != GuestyGuest() else None


def _legacy_money(value: object) -> GuestyMoney | None:
    """Return the legacy money summary with only valid fields kept.

    Args:
        value: The raw ``money`` value.

    Returns:
        The summary, or None when it is not a dict or has no valid
        field.
    """
    if not isinstance(value, dict):
        return None
    money = GuestyMoney(
        total_paid=_finite_number(value.get("totalPaid")),
        balance_due=_finite_number(value.get("balanceDue")),
        currency=_optional_str(value.get("currency")),
    )
    return money if money != GuestyMoney() else None


def build_legacy_reservation(
    raw: Mapping[str, Any],
    assessment: LegacyAssessment,
) -> GuestyReservation | None:
    """Build a legacy reservation from verified and sanitized values.

    Only an accepted assessment builds a model. The listing, status
    and instants are the verified values; localized dates must be
    canonical and chronological (contract C6); every optional field
    with an invalid type becomes None or empty, as on the v3 path. A
    missing or invalid ``nightsCount`` is None: the legacy path never
    derived it. The confirmation code passes through unchanged, as on
    the v3 path. ``GuestyReservation.from_api_dict`` is unchanged for
    its direct callers.

    Args:
        raw: The raw legacy record that was assessed.
        assessment: Its legacy assessment.

    Returns:
        The reservation, or None when the record was rejected.
    """
    check_in = _parse_iso_datetime(raw.get("checkIn"))
    check_out = _parse_iso_datetime(raw.get("checkOut"))
    listing_id = _non_empty_str(raw.get("listingId"))
    if (
        not assessment.accepted
        or assessment.status is None
        or listing_id is None
        or check_in is None
        or check_out is None
    ):
        return None
    check_in_local, check_out_local = _legacy_local_dates(raw)
    return GuestyReservation(
        id=assessment.reservation_id,
        listing_id=listing_id,
        status=assessment.status,
        check_in=check_in,
        check_out=check_out,
        confirmation_code=raw.get("confirmationCode"),
        check_in_local=check_in_local,
        check_out_local=check_out_local,
        planned_arrival=_optional_str(raw.get("plannedArrival")),
        planned_departure=_optional_str(raw.get("plannedDeparture")),
        nights_count=non_negative_count(raw.get("nightsCount")),
        guests_count=non_negative_count(raw.get("guestsCount")),
        source=_optional_str(raw.get("source")),
        note=_optional_str(raw.get("note")),
        guest=_legacy_guest(raw.get("guest")),
        money=_legacy_money(raw.get("money")),
        custom_fields=MappingProxyType(
            _parse_custom_fields_array(raw.get("customFields", [])),
        ),
    )


def _utc_instant(value: object) -> datetime | None:
    """Return a raw explicitly offset instant converted to UTC.

    Args:
        value: The raw instant value.

    Returns:
        The UTC instant, or None when the value has no explicit
        offset, does not parse or cannot be expressed in UTC.
    """
    if not isinstance(value, str) or _EXPLICIT_OFFSET.search(value) is None:
        return None
    instant = _parse_iso_datetime(value)
    if instant is None:
        return None
    try:
        return instant.astimezone(UTC)
    except OverflowError:
        return None


def explicit_offset_instant(value: object) -> bool:
    """Report whether a raw instant carries an explicit UTC offset.

    The offset (``Z`` or ``±HH:MM``) is checked on the raw string
    before parsing, because a naive value would otherwise be read as
    UTC. An instant outside the representable UTC range, such as
    ``9999-12-31T20:00:00-05:00``, is not verified.

    Args:
        value: The raw instant value.

    Returns:
        True when the value is a parseable, explicitly offset instant
        that can be expressed in UTC.
    """
    return _utc_instant(value) is not None


def retirement_check_out_bound(
    segments: Iterable[SegmentAssessment],
    raw_check_out: object,
) -> str | None:
    """Return the retirement-only check-out bound of a rejected record.

    The bound is the later of two candidates: the latest segment
    check-out date when every raw segment has a valid date-level
    interval (whatever its listing verdict), and
    ``utc_date(checkOut) + 1`` when the raw top-level ``checkOut`` has
    an explicit offset. A candidate that cannot be represented is no
    candidate, so an extreme date never fails the batch. It is never
    used for publication.

    Args:
        segments: The record's segment assessments, in raw order.
        raw_check_out: The raw top-level ``checkOut`` value.

    Returns:
        The ISO date bound, or None when neither candidate exists.
    """
    candidates: list[str] = []
    intervals = [item.interval_check_out for item in segments]
    if intervals and all(bound is not None for bound in intervals):
        candidates.extend(bound for bound in intervals if bound is not None)
    instant = _utc_instant(raw_check_out)
    if instant is not None and instant.date() < date.max:
        candidates.append((instant.date() + timedelta(days=1)).isoformat())
    return max(candidates) if candidates else None


@dataclass
class ValidationTally:
    """Accumulate the rejection report of one batch.

    Pure and in-memory: it collects identifiers, fixed reasons and
    counts, never a payload value other than a conflict code.

    Attributes:
        rejected_ids: Identifiers of rejected or partial records.
        rejected_codes: Conflict code pairs of those records.
        bounds: Retirement bound per coded rejected record.
        exclusions: ``(reason, reservation_id)`` keys.
        reasons: Occurrences of each reason.
        rejected_records: Count of record-level rejections.
        partial_records: Count of partially valid records.
        rejected_segments: Count of rejected raw segments.
    """

    rejected_ids: set[str] = field(default_factory=set)
    rejected_codes: set[RejectedCode] = field(default_factory=set)
    bounds: dict[str, str] = field(default_factory=dict)
    exclusions: set[tuple[ValidationReason, str]] = field(default_factory=set)
    reasons: Counter[ValidationReason] = field(default_factory=Counter)
    rejected_records: int = 0
    partial_records: int = 0
    rejected_segments: int = 0

    def add(
        self,
        reservation_id: str,
        *,
        reason: ValidationReason | None,
        partial: bool,
        segments: Iterable[SegmentAssessment],
        conflict_code: str | None,
        raw_check_out: object,
    ) -> None:
        """Record one assessed record with a verified identifier.

        Args:
            reservation_id: The batch-verified identifier.
            reason: The record-level rejection, or None.
            partial: Whether only some raw segments are verified.
            segments: The record's segment assessments.
            conflict_code: The conflict code, or None.
            raw_check_out: The raw top-level ``checkOut`` value.
        """
        assessed = tuple(segments)
        for item in assessed:
            if item.reason is not None:
                self.rejected_segments += 1
                self.reasons[item.reason] += 1
                self.exclusions.add((item.reason, reservation_id))
        record_reason = reason
        if record_reason is None and partial:
            record_reason = ValidationReason.PARTIAL_STAY
            self.partial_records += 1
        elif record_reason is not None:
            self.rejected_records += 1
        if record_reason is None:
            return
        self.reasons[record_reason] += 1
        self.exclusions.add((record_reason, reservation_id))
        self.rejected_ids.add(reservation_id)
        if conflict_code is None:
            return
        self.rejected_codes.add(RejectedCode(reservation_id, conflict_code))
        bound = retirement_check_out_bound(assessed, raw_check_out)
        if bound is not None:
            self.bounds[reservation_id] = bound

    def add_assessment(
        self,
        assessment: RecordAssessment,
        raw_check_out: object,
    ) -> None:
        """Record one v3 assessment with a verified identifier.

        Args:
            assessment: The record assessment.
            raw_check_out: The raw top-level ``checkOut`` value.
        """
        if assessment.reservation_id is None:
            return
        self.add(
            assessment.reservation_id,
            reason=assessment.reason,
            partial=assessment.partial,
            segments=assessment.segments,
            conflict_code=assessment.conflict_code,
            raw_check_out=raw_check_out,
        )

    def summary(self) -> ValidationSummary:
        """Return the counts-only summary.

        Returns:
            The validation summary.
        """
        return ValidationSummary(
            rejected_records=self.rejected_records,
            partial_records=self.partial_records,
            rejected_segments=self.rejected_segments,
            reasons=MappingProxyType(dict(self.reasons)),
        )

    def sorted_rejected_ids(self) -> tuple[str, ...]:
        """Return the sorted rejected identifiers.

        Returns:
            Sorted, unique identifiers.
        """
        return tuple(sorted(self.rejected_ids))

    def sorted_rejected_codes(self) -> tuple[RejectedCode, ...]:
        """Return the sorted conflict code pairs.

        Returns:
            Sorted, unique pairs.
        """
        return tuple(sorted(self.rejected_codes))

    def sorted_exclusions(self) -> tuple[tuple[ValidationReason, str], ...]:
        """Return the sorted exclusion keys.

        Returns:
            Sorted, unique ``(reason, reservation_id)`` keys.
        """
        return tuple(sorted(self.exclusions))

    def check_out_bounds(self) -> Mapping[str, str]:
        """Return the retirement bounds as a read-only mapping.

        Returns:
            Bound per coded rejected record.
        """
        return MappingProxyType(dict(sorted(self.bounds.items())))
