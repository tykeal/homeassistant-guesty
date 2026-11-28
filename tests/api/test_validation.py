# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure reservation record validator (feature 013)."""

from __future__ import annotations

import ast
import inspect
import logging
from typing import Any

import pytest

from custom_components.guesty.api import validation
from custom_components.guesty.api.const import V3_STATUS_MAP
from custom_components.guesty.api.models import GuestyReservation
from custom_components.guesty.api.validation import (
    RecordValidation,
    ValidationReason,
    assess_legacy_record,
    assess_v3_record,
    validate_record,
)
from tests.conftest import (
    make_reservation_dict,
    make_v3_reservation,
    make_v3_segment,
)

_SUPPORTED_ACTIONS = (
    "add_reservation_note",
    "set_reservation_status",
    "set_custom_field",
)


def _undated_segment(**overrides: Any) -> dict[str, Any]:
    """Build a stay segment with no dates of its own.

    Args:
        **overrides: Fields to override on the segment.

    Returns:
        A segment carrying only a listing and a party size.
    """
    segment: dict[str, Any] = {"listingId": "listing-001", "guestsCount": 2}
    segment.update(overrides)
    return segment


def _feature_010_record(**overrides: Any) -> dict[str, Any]:
    """Build the eligible feature 010 single-segment record.

    The only segment has no dates; the top level carries both
    localized dates and an ordered pair of instants.

    Args:
        **overrides: Top-level fields to override.

    Returns:
        A v3 search result dict.
    """
    record = make_v3_reservation(
        "res-010",
        segments=[_undated_segment()],
        checkIn="2025-08-01T15:00:00+00:00",
        checkOut="2025-08-05T11:00:00+00:00",
        checkInDateLocalized="2025-08-01",
        checkOutDateLocalized="2025-08-05",
    )
    record.update(overrides)
    return record


class TestAssessV3Record:
    """Record and segment assessment of a v3 search result (T002)."""

    @pytest.mark.parametrize(
        ("status", "expected_reason", "expected_status"),
        [
            pytest.param(None, ValidationReason.MALFORMED_STATUS, None, id="none"),
            pytest.param("", ValidationReason.MALFORMED_STATUS, None, id="empty"),
            pytest.param(7, ValidationReason.MALFORMED_STATUS, None, id="int"),
            pytest.param(
                ["confirmed"],
                ValidationReason.MALFORMED_STATUS,
                None,
                id="list",
            ),
            pytest.param(
                {"value": "confirmed"},
                ValidationReason.MALFORMED_STATUS,
                None,
                id="object",
            ),
            pytest.param(
                "inquiry",
                ValidationReason.UNRECOGNIZED_STATUS,
                None,
                id="unrecognized",
            ),
            pytest.param("confirmed", None, "confirmed", id="confirmed"),
            pytest.param(" Completed ", None, "checked_out", id="normalized"),
            pytest.param("CANCELLED", None, "canceled", id="upper"),
            pytest.param("alteration", None, "alteration", id="hold"),
        ],
    )
    def test_status_reasons(
        self,
        status: object,
        expected_reason: ValidationReason | None,
        expected_status: str | None,
    ) -> None:
        """The status is recognized only through the allowlist."""
        raw = make_v3_reservation()
        if status is None:
            del raw["status"]
        else:
            raw["status"] = status

        assessment = assess_v3_record(raw)

        assert assessment.reason == expected_reason
        assert assessment.status == expected_status
        assert assessment.accepted is (expected_reason is None)

    @pytest.mark.parametrize(
        "stay",
        [
            pytest.param(None, id="missing"),
            pytest.param([], id="empty"),
            pytest.param({"listingId": "listing-001"}, id="object"),
            pytest.param("listing-001", id="string"),
        ],
    )
    def test_no_stay(self, stay: object) -> None:
        """A missing, empty or non-list stay rejects the record."""
        raw = make_v3_reservation()
        if stay is None:
            del raw["stay"]
        else:
            raw["stay"] = stay

        assessment = assess_v3_record(raw)

        assert assessment.reason == ValidationReason.NO_STAY
        assert not assessment.accepted
        assert assessment.segments == ()

    @pytest.mark.parametrize(
        ("segment", "expected"),
        [
            pytest.param(
                "listing-001",
                ValidationReason.SEGMENT_MALFORMED,
                id="string",
            ),
            pytest.param(None, ValidationReason.SEGMENT_MALFORMED, id="null"),
            pytest.param(
                make_v3_segment(listingId=None),
                ValidationReason.SEGMENT_MISSING_LISTING,
                id="listing-null",
            ),
            pytest.param(
                make_v3_segment(listingId="  "),
                ValidationReason.SEGMENT_MISSING_LISTING,
                id="listing-blank",
            ),
            pytest.param(
                make_v3_segment(listingId=["listing-001"]),
                ValidationReason.SEGMENT_MISSING_LISTING,
                id="listing-list",
            ),
            pytest.param(
                make_v3_segment(parentListingId=["parent-1"]),
                ValidationReason.SEGMENT_MALFORMED_PARENT,
                id="parent-list",
            ),
            pytest.param(
                make_v3_segment(parentListingId={"_id": "parent-1"}),
                ValidationReason.SEGMENT_MALFORMED_PARENT,
                id="parent-object",
            ),
            pytest.param(
                make_v3_segment(parentListingId=""),
                ValidationReason.SEGMENT_MALFORMED_PARENT,
                id="parent-empty",
            ),
            pytest.param(
                make_v3_segment(parentListingId=" \t"),
                ValidationReason.SEGMENT_MALFORMED_PARENT,
                id="parent-blank",
            ),
            pytest.param(make_v3_segment(), None, id="parent-absent"),
            pytest.param(
                make_v3_segment(parentListingId=None),
                None,
                id="parent-null",
            ),
            pytest.param(
                make_v3_segment(parentListingId="parent-1"),
                None,
                id="parent-string",
            ),
            pytest.param(
                make_v3_segment(checkInDateLocalized=None),
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="check-in-missing",
            ),
            pytest.param(
                make_v3_segment(checkOutDateLocalized="2025-8-5"),
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="check-out-noncanonical",
            ),
            pytest.param(
                make_v3_segment(checkInDateLocalized=20250801),
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="check-in-int",
            ),
            pytest.param(
                make_v3_segment(
                    checkInDateLocalized="2025-08-05",
                    checkOutDateLocalized="2025-08-01",
                ),
                ValidationReason.SEGMENT_INVALID_DATES,
                id="reversed",
            ),
            pytest.param(
                make_v3_segment(
                    checkInDateLocalized="2025-08-05",
                    checkOutDateLocalized="2025-08-05",
                ),
                ValidationReason.SEGMENT_INVALID_DATES,
                id="zero-night",
            ),
        ],
    )
    def test_segment_reasons(
        self,
        segment: object,
        expected: ValidationReason | None,
    ) -> None:
        """Each segment rule reports its own fixed reason."""
        raw = make_v3_reservation(segments=[make_v3_segment(listingId="listing-ok")])
        raw["stay"] = [raw["stay"][0], segment]

        assessment = assess_v3_record(raw)

        assert assessment.reason is None
        assert len(assessment.segments) == 2
        assert assessment.segments[0].reason is None
        checked = assessment.segments[1]
        assert checked.raw_position == 1
        assert checked.reason == expected
        assert (checked.segment is None) is (expected is not None)

    def test_no_verified_segment(self) -> None:
        """A stay whose every segment is rejected rejects the record."""
        raw = make_v3_reservation(
            segments=[
                make_v3_segment(listingId=""),
                make_v3_segment(checkInDateLocalized=None),
            ],
        )

        assessment = assess_v3_record(raw)

        assert assessment.reason == ValidationReason.NO_VERIFIED_SEGMENT
        assert not assessment.accepted
        assert not assessment.partial
        assert not assessment.fully_verified
        assert [s.reason for s in assessment.segments] == [
            ValidationReason.SEGMENT_MISSING_LISTING,
            ValidationReason.SEGMENT_UNVERIFIED_DATES,
        ]

    def test_partial_record(self) -> None:
        """Verified segments survive beside a rejected one."""
        raw = make_v3_reservation(
            segments=[
                make_v3_segment(
                    listingId="listing-a",
                    parentListingId="parent-a",
                    guestsCount=3,
                ),
                make_v3_segment(listingId=""),
                make_v3_segment(
                    listingId="listing-b",
                    checkInDateLocalized="2025-08-05",
                    checkOutDateLocalized="2025-08-07",
                ),
            ],
        )

        assessment = assess_v3_record(raw)

        assert assessment.reason is None
        assert assessment.raw_stay_count == 3
        assert [s.raw_position for s in assessment.segments] == [0, 1, 2]
        assert assessment.accepted
        assert assessment.partial
        assert not assessment.fully_verified
        first = assessment.segments[0].segment
        assert first is not None
        assert first.listing_id == "listing-a"
        assert first.parent_listing_id == "parent-a"
        assert first.check_in_local == "2025-08-01"
        assert first.check_out_local == "2025-08-05"
        assert first.guests_count == 3
        assert first.raw_position == 0
        assert first.check_in is None
        assert first.check_out is None
        assert assessment.segments[1].segment is None
        third = assessment.segments[2].segment
        assert third is not None
        assert third.raw_position == 2

    def test_fully_verified_record(self) -> None:
        """A record with every segment verified is fully verified."""
        assessment = assess_v3_record(make_v3_reservation("res-9"))

        assert assessment.reservation_id == "res-9"
        assert assessment.status == "confirmed"
        assert assessment.raw_stay_count == 1
        assert assessment.accepted
        assert assessment.fully_verified
        assert not assessment.partial

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            pytest.param(
                {"status": "inquiry", "stay": []},
                ValidationReason.UNRECOGNIZED_STATUS,
                id="status-before-stay",
            ),
            pytest.param(
                {"status": 3, "stay": [make_v3_segment(listingId="")]},
                ValidationReason.MALFORMED_STATUS,
                id="status-before-segments",
            ),
            pytest.param(
                {"stay": "nope"},
                ValidationReason.NO_STAY,
                id="stay-before-segments",
            ),
            pytest.param(
                {"stay": [make_v3_segment(listingId="")]},
                ValidationReason.NO_VERIFIED_SEGMENT,
                id="segments-last",
            ),
        ],
    )
    def test_record_reasons_follow_evaluation_order(
        self,
        overrides: dict[str, Any],
        expected: ValidationReason,
    ) -> None:
        """A record reports only its first failing reason."""
        raw = make_v3_reservation(**overrides)

        assert assess_v3_record(raw).reason == expected

    @pytest.mark.parametrize(
        ("variant", "expected"),
        [
            pytest.param("eligible", None, id="eligible"),
            pytest.param(
                "two-segments",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="two-segments",
            ),
            pytest.param(
                "non-dict",
                ValidationReason.SEGMENT_MALFORMED,
                id="non-dict-segment",
            ),
            pytest.param(
                "no-listing",
                ValidationReason.SEGMENT_MISSING_LISTING,
                id="segment-without-listing",
            ),
            pytest.param(
                "own-check-in",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="segment-own-check-in",
            ),
            pytest.param(
                "own-check-out",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="segment-own-check-out",
            ),
            pytest.param(
                "bad-check-in",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="unparsable-top-check-in",
            ),
            pytest.param(
                "bad-check-out",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="unparsable-top-check-out",
            ),
            pytest.param(
                "not-after",
                ValidationReason.SEGMENT_UNVERIFIED_DATES,
                id="check-out-not-after-check-in",
            ),
        ],
    )
    def test_top_level_localized_fill(
        self,
        variant: str,
        expected: ValidationReason | None,
    ) -> None:
        """Top-level dates fill a segment only under the 010 predicate."""
        raw = _feature_010_record()
        if variant == "two-segments":
            raw["stay"] = [_undated_segment(), _undated_segment()]
        elif variant == "non-dict":
            raw["stay"] = ["listing-001"]
        elif variant == "no-listing":
            raw["stay"] = [_undated_segment(listingId=None)]
        elif variant == "own-check-in":
            raw["stay"] = [_undated_segment(checkIn="2025-08-01T15:00:00Z")]
        elif variant == "own-check-out":
            raw["stay"] = [_undated_segment(checkOut="2025-08-05T11:00:00Z")]
        elif variant == "bad-check-in":
            raw["checkIn"] = "not-a-date"
        elif variant == "bad-check-out":
            raw["checkOut"] = None
        elif variant == "not-after":
            raw["checkOut"] = raw["checkIn"]

        assessment = assess_v3_record(raw)

        reasons = [s.reason for s in assessment.segments]
        if expected is None:
            assert assessment.fully_verified
            segment = assessment.segments[0].segment
            assert segment is not None
            assert segment.check_in_local == "2025-08-01"
            assert segment.check_out_local == "2025-08-05"
        else:
            assert reasons == [expected] * len(reasons)
            assert all(s.segment is None for s in assessment.segments)
            assert assessment.reason == ValidationReason.NO_VERIFIED_SEGMENT

    @pytest.mark.parametrize(
        "segment_dates",
        [
            pytest.param({"checkInDateLocalized": "2025-8-01"}, id="check-in"),
            pytest.param({"checkOutDateLocalized": 20250805}, id="check-out"),
        ],
    )
    def test_top_level_never_replaces_a_malformed_segment_date(
        self,
        segment_dates: dict[str, Any],
    ) -> None:
        """Only a missing segment date is filled, never a malformed one."""
        raw = _feature_010_record()
        raw["stay"] = [_undated_segment(**segment_dates)]

        assessment = assess_v3_record(raw)

        assert [s.reason for s in assessment.segments] == [
            ValidationReason.SEGMENT_UNVERIFIED_DATES,
        ]
        assert assessment.reason == ValidationReason.NO_VERIFIED_SEGMENT

    def test_null_segment_date_is_filled(self) -> None:
        """Baseline control: a null segment date counts as missing."""
        raw = _feature_010_record()
        raw["stay"] = [
            _undated_segment(checkInDateLocalized=None, checkOutDateLocalized=None),
        ]

        assessment = assess_v3_record(raw)

        assert assessment.fully_verified

    def test_no_written_date_fallback(self) -> None:
        """Timestamp written dates never become localized dates."""
        raw = _feature_010_record(
            checkInDateLocalized=None,
            checkOutDateLocalized=None,
        )

        assessment = assess_v3_record(raw)

        assert [s.reason for s in assessment.segments] == [
            ValidationReason.SEGMENT_UNVERIFIED_DATES,
        ]
        assert assessment.reason == ValidationReason.NO_VERIFIED_SEGMENT
        assert not assessment.accepted

    @pytest.mark.parametrize(
        ("segment_overrides", "record_overrides", "check"),
        [
            pytest.param({"guestsCount": -1}, {}, "guests_count", id="guests-negative"),
            pytest.param({"guestsCount": True}, {}, "guests_count", id="guests-bool"),
            pytest.param({"guestsCount": "2"}, {}, "guests_count", id="guests-string"),
            pytest.param({}, {"nightsCount": "4"}, "nights_count", id="nights-string"),
            pytest.param({}, {"nightsCount": True}, "nights_count", id="nights-bool"),
            pytest.param({}, {"nightsCount": -4}, "nights_count", id="nights-negative"),
            pytest.param({}, {"nightsCount": 4.0}, "nights_count", id="nights-float"),
            pytest.param(
                {},
                {"plannedArrival": 1500},
                "planned_arrival",
                id="arrival-int",
            ),
            pytest.param(
                {},
                {"plannedDeparture": ["11:00"]},
                "planned_departure",
                id="departure-list",
            ),
            pytest.param({}, {"source": {"x": 1}}, "source", id="source-object"),
            pytest.param({}, {"notes": "free text"}, "note", id="notes-string"),
            pytest.param({}, {"notes": {"other": 5}}, "note", id="notes-other-int"),
            pytest.param(
                {},
                {"customFields": {"f": "v"}},
                "custom_fields",
                id="custom-fields-object",
            ),
            pytest.param({}, {"guestId": 42}, "guest", id="guest-id-int"),
        ],
    )
    def test_invalid_optional_fields_become_none(
        self,
        segment_overrides: dict[str, Any],
        record_overrides: dict[str, Any],
        check: str,
    ) -> None:
        """A bad optional field never rejects; it is never published.

        Updated for feature 013 review (data-model § Validation rules):
        a present but invalid ``nightsCount`` (string, bool, negative or
        float) now becomes None rather than being replaced by a count
        derived from the dates, because a wrongly typed value is not
        evidence of the night count.
        """
        raw = make_v3_reservation(
            segments=[make_v3_segment(**segment_overrides)],
            **record_overrides,
        )

        assessment = assess_v3_record(raw)
        model = GuestyReservation.from_v3_api_dict(raw)

        assert assessment.fully_verified
        assert model is not None
        if check == "guests_count":
            segment = assessment.segments[0].segment
            assert segment is not None
            assert segment.guests_count is None
            assert model.guests_count is None
        elif check == "custom_fields":
            assert dict(model.custom_fields) == {}
        else:
            assert getattr(model, check) is None

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            pytest.param({"nightsCount": 7}, 7, id="valid"),
            pytest.param({"nightsCount": 0}, 0, id="zero"),
            pytest.param({"nightsCount": None}, 4, id="null-derived"),
            pytest.param({}, 4, id="absent-derived"),
        ],
    )
    def test_nights_count_valid_or_derived_when_missing(
        self,
        overrides: dict[str, Any],
        expected: int,
    ) -> None:
        """A valid count passes; a missing one derives from verified dates."""
        model = GuestyReservation.from_v3_api_dict(make_v3_reservation(**overrides))

        assert model is not None
        assert model.nights_count == expected

    @pytest.mark.parametrize(
        ("status", "code", "segments", "expected"),
        [
            pytest.param("confirmed", "CODE-1", None, "CODE-1", id="accepted"),
            pytest.param(
                "inquiry",
                "CODE-1",
                None,
                "CODE-1",
                id="rejected-status",
            ),
            pytest.param(
                "confirmed",
                "CODE-1",
                [make_v3_segment(listingId="")],
                "CODE-1",
                id="rejected-segments",
            ),
            pytest.param("alteration", "CODE-1", None, None, id="hold"),
            pytest.param("confirmed", 12345, None, None, id="non-string"),
            pytest.param("confirmed", "", None, None, id="empty"),
            pytest.param("confirmed", None, None, None, id="missing"),
        ],
    )
    def test_conflict_code(
        self,
        status: str,
        code: object,
        segments: list[dict[str, Any]] | None,
        expected: str | None,
    ) -> None:
        """Only a non-hold, non-empty string code is a conflict code."""
        raw = make_v3_reservation(
            status=status,
            segments=segments,
            confirmationCode=code,
        )

        assert assess_v3_record(raw).conflict_code == expected

    def test_never_logs_or_echoes_values(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No payload value appears in a log or in a reason."""
        sentinel = "SENTINEL-7f3a"
        raw = make_v3_reservation(
            status=f"{sentinel}-status",
            confirmationCode=f"{sentinel}-code",
            notes={"other": f"{sentinel}-note"},
            segments=[
                make_v3_segment(listingId=f"{sentinel}-listing"),
                make_v3_segment(parentListingId=[sentinel]),
            ],
        )

        with caplog.at_level(logging.DEBUG):
            assessment = assess_v3_record(raw)
            result = validate_record(raw, "add_reservation_note")

        assert caplog.records == []
        reasons = [assessment.reason, result.reason]
        reasons.extend(s.reason for s in assessment.segments)
        for reason in reasons:
            assert sentinel not in str(reason)
            assert reason is None or reason in set(ValidationReason)


class TestValidateRecord:
    """The feature 014 by-ID validation interface (T003)."""

    @pytest.mark.parametrize(
        ("action_type", "valid"),
        [
            *[pytest.param(action, True, id=action) for action in _SUPPORTED_ACTIONS],
            pytest.param("send_message", False, id="send_message"),
            pytest.param("", False, id="empty"),
            pytest.param("ADD_RESERVATION_NOTE", False, id="case"),
        ],
    )
    def test_action_fact_table(self, action_type: str, valid: bool) -> None:
        """Only the three record actions are supported."""
        result = validate_record(make_v3_reservation(), action_type)

        assert result.valid is valid
        assert result.reason == (None if valid else ValidationReason.UNSUPPORTED_ACTION)

    @pytest.mark.parametrize(
        "reservation_id",
        [
            pytest.param(None, id="missing"),
            pytest.param(123, id="int"),
            pytest.param("", id="empty"),
        ],
    )
    def test_missing_identifier(self, reservation_id: object) -> None:
        """A record without a non-empty string ID is never valid."""
        raw = make_v3_reservation()
        if reservation_id is None:
            del raw["reservationId"]
        else:
            raw["reservationId"] = reservation_id

        result = validate_record(raw, "add_reservation_note")

        assert result == RecordValidation(
            valid=False,
            reason=ValidationReason.MISSING_IDENTIFIER,
        )

    def test_partial_record_is_partial_stay(self) -> None:
        """A partially valid record rejects every write."""
        raw = make_v3_reservation(
            segments=[make_v3_segment(), make_v3_segment(listingId="")],
        )

        result = validate_record(raw, "set_custom_field")

        assert result == RecordValidation(
            valid=False,
            reason=ValidationReason.PARTIAL_STAY,
        )

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            pytest.param(
                {"status": "inquiry"},
                ValidationReason.UNRECOGNIZED_STATUS,
                id="unrecognized",
            ),
            pytest.param(
                {"status": None},
                ValidationReason.MALFORMED_STATUS,
                id="malformed",
            ),
            pytest.param({"stay": []}, ValidationReason.NO_STAY, id="no-stay"),
            pytest.param(
                {"stay": [make_v3_segment(checkInDateLocalized=None)]},
                ValidationReason.NO_VERIFIED_SEGMENT,
                id="no-verified-segment",
            ),
        ],
    )
    def test_rejected_record_reports_its_reason(
        self,
        overrides: dict[str, Any],
        expected: ValidationReason,
    ) -> None:
        """A rejected record reports its assessment reason."""
        raw = make_v3_reservation(**overrides)

        result = validate_record(raw, "add_reservation_note")

        assert result == RecordValidation(valid=False, reason=expected)

    def test_rejected_record_reason_precedes_unsupported_action(self) -> None:
        """A record reason wins over an unsupported action."""
        raw = make_v3_reservation(status="inquiry")

        result = validate_record(raw, "send_message")

        assert result.reason == ValidationReason.UNRECOGNIZED_STATUS

    def test_status_reason_precedes_missing_identifier(self) -> None:
        """The status is evaluated before the identifier."""
        raw = make_v3_reservation(status="inquiry")
        del raw["reservationId"]

        result = validate_record(raw, "add_reservation_note")

        assert result.reason == ValidationReason.UNRECOGNIZED_STATUS

    def test_missing_identifier_precedes_stay_reasons(self) -> None:
        """A missing identifier is reported before a stay rejection."""
        raw = make_v3_reservation(stay=[])
        raw["reservationId"] = ""

        result = validate_record(raw, "add_reservation_note")

        assert result.reason == ValidationReason.MISSING_IDENTIFIER

    def test_hold_is_valid(self) -> None:
        """A verified hold is a valid record for every action."""
        raw = make_v3_reservation(status="alteration")

        for action in _SUPPORTED_ACTIONS:
            assert validate_record(raw, action) == RecordValidation(
                valid=True,
                reason=None,
            )

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(make_v3_reservation(), id="valid"),
            pytest.param(make_v3_reservation(status="inquiry"), id="rejected"),
            pytest.param(
                make_v3_reservation(
                    segments=[make_v3_segment(listingId=""), make_v3_segment()],
                ),
                id="partial",
            ),
        ],
    )
    def test_reason_is_none_iff_valid(self, raw: dict[str, Any]) -> None:
        """``reason`` is None exactly when the record is valid."""
        for action in (*_SUPPORTED_ACTIONS, "send_message"):
            result = validate_record(raw, action)
            assert (result.reason is None) is result.valid

    def test_no_reason_value_is_valid(self) -> None:
        """No reason value collides with 014's ``valid`` sentinel."""
        assert "valid" not in {reason.value for reason in ValidationReason}
        assert all(isinstance(reason, str) for reason in ValidationReason)

    @pytest.mark.parametrize(
        "code",
        [
            pytest.param(12345, id="int"),
            pytest.param({"code": "x"}, id="object"),
            pytest.param("", id="empty"),
            pytest.param(None, id="null"),
        ],
    )
    def test_non_string_code_is_not_a_rejection(self, code: object) -> None:
        """A bad confirmation code never invalidates the record."""
        raw = make_v3_reservation(confirmationCode=code)

        result = validate_record(raw, "add_reservation_note")

        assert result == RecordValidation(valid=True, reason=None)

    def test_pure(self, caplog: pytest.LogCaptureFixture) -> None:
        """The validator logs nothing and imports no I/O or HA code."""
        tree = ast.parse(inspect.getsource(validation))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith("homeassistant") for name in imported)
        assert "logging" not in imported
        assert "httpx" not in imported
        assert not hasattr(validation, "_LOGGER")

        with caplog.at_level(logging.DEBUG):
            validate_record(make_v3_reservation(status="inquiry"), "x")
            validate_record({}, "add_reservation_note")
            validate_record(make_v3_reservation(), "add_reservation_note")

        assert caplog.records == []

    def test_recognized_statuses_are_map_keys(self) -> None:
        """Every ``V3_STATUS_MAP`` key validates as recognized."""
        for raw_status in V3_STATUS_MAP:
            raw = make_v3_reservation(status=raw_status)
            assert validate_record(raw, "add_reservation_note").valid


class TestAssessLegacyRecord:
    """The legacy record verdict (research R5)."""

    def test_invalid_listing_precedes_unverified_instant(self) -> None:
        """A record failing both checks reports the listing reason."""
        raw = make_reservation_dict(listingId=123, checkIn="2025-08-01T15:00:00")

        assert assess_legacy_record(raw).reason is ValidationReason.INVALID_LISTING


class TestRetirementCheckOutBound:
    """The retirement-only bound of a rejected record (research R7)."""

    @pytest.mark.parametrize(
        ("check_out", "utc_representable"),
        [
            pytest.param("9999-12-31T23:00:00Z", True, id="utc-max-plus-one"),
            pytest.param("9999-12-31T20:00:00-05:00", False, id="negative-offset-max"),
            pytest.param("0001-01-01T00:00:00+05:00", False, id="positive-offset-min"),
        ],
    )
    def test_extreme_check_out_gives_no_candidate(
        self,
        check_out: str,
        utc_representable: bool,
    ) -> None:
        """An instant whose bound is unrepresentable gives no candidate."""
        assert validation.retirement_check_out_bound((), check_out) is None
        assert validation.explicit_offset_instant(check_out) is utc_representable

    def test_extreme_check_out_keeps_segment_candidate(self) -> None:
        """An unrepresentable top-level bound leaves the segment bound."""
        raw = make_v3_reservation(status="inquiry")

        segments = assess_v3_record(raw).segments

        assert (
            validation.retirement_check_out_bound(segments, "9999-12-31T23:00:00Z")
            == "2025-08-05"
        )

    @pytest.mark.parametrize(
        ("second", "expected"),
        [
            pytest.param(
                make_v3_segment(
                    listingId="",
                    checkInDateLocalized="2025-08-05",
                    checkOutDateLocalized="2025-08-09",
                ),
                "2025-08-09",
                id="missing-listing",
            ),
            pytest.param(
                make_v3_segment(
                    parentListingId=["parent"],
                    checkInDateLocalized="2025-08-05",
                    checkOutDateLocalized="2025-08-08",
                ),
                "2025-08-08",
                id="malformed-parent",
            ),
            pytest.param(
                make_v3_segment(checkOutDateLocalized="2025-8-9"),
                None,
                id="non-canonical-date",
            ),
            pytest.param(
                make_v3_segment(
                    checkInDateLocalized="2025-08-09",
                    checkOutDateLocalized="2025-08-05",
                ),
                None,
                id="reversed-dates",
            ),
            pytest.param("not-a-segment", None, id="non-dict"),
        ],
    )
    def test_segment_candidate_needs_only_date_level_intervals(
        self,
        second: object,
        expected: str | None,
    ) -> None:
        """Every raw segment needs a valid interval, not full verification."""
        raw = make_v3_reservation(status="inquiry", checkOut=None)
        raw["stay"] = [make_v3_segment(), second]

        segments = assess_v3_record(raw).segments

        assert validation.retirement_check_out_bound(segments, None) == expected

    def test_segment_candidate_uses_feature_010_fill(self) -> None:
        """A segment filled by the 010 rule supplies its date."""
        raw = _feature_010_record(status="inquiry")

        segments = assess_v3_record(raw).segments

        assert validation.retirement_check_out_bound(segments, None) == "2025-08-05"

    @pytest.mark.parametrize(
        ("check_out", "expected"),
        [
            pytest.param("2025-08-03T10:00:00Z", "2025-08-09", id="segments-later"),
            pytest.param("2025-08-20T10:00:00Z", "2025-08-21", id="top-level-later"),
        ],
    )
    def test_later_candidate_wins_for_partial_record(
        self,
        check_out: str,
        expected: str,
    ) -> None:
        """The later of segment and top-level candidates wins."""
        raw = make_v3_reservation(status="inquiry")
        raw["stay"] = [
            make_v3_segment(),
            make_v3_segment(
                listingId="",
                checkInDateLocalized="2025-08-05",
                checkOutDateLocalized="2025-08-09",
            ),
        ]

        segments = assess_v3_record(raw).segments

        assert validation.retirement_check_out_bound(segments, check_out) == expected
