# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty API exception hierarchy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyCustomFieldError,
    GuestyMessageError,
    GuestyRateLimitError,
    GuestyResponseError,
)


class TestGuestyApiError:
    """Tests for the GuestyApiError base exception."""

    def test_is_exception(self) -> None:
        """GuestyApiError inherits from Exception."""
        assert issubclass(GuestyApiError, Exception)

    def test_message_attribute(self) -> None:
        """GuestyApiError stores the message attribute."""
        error = GuestyApiError("something failed")
        assert error.message == "something failed"

    def test_str_representation(self) -> None:
        """GuestyApiError string representation is the message."""
        error = GuestyApiError("test error")
        assert str(error) == "test error"

    def test_can_be_raised_and_caught(self) -> None:
        """GuestyApiError can be raised and caught."""
        with pytest.raises(GuestyApiError, match="boom"):
            raise GuestyApiError("boom")


class TestGuestyAuthError:
    """Tests for the GuestyAuthError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyAuthError is a subclass of GuestyApiError."""
        assert issubclass(GuestyAuthError, GuestyApiError)

    def test_message_attribute(self) -> None:
        """GuestyAuthError stores the message attribute."""
        error = GuestyAuthError("invalid credentials")
        assert error.message == "invalid credentials"

    def test_caught_as_base(self) -> None:
        """GuestyAuthError can be caught as GuestyApiError."""
        with pytest.raises(GuestyApiError):
            raise GuestyAuthError("auth failed")


class TestGuestyRateLimitError:
    """Tests for the GuestyRateLimitError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyRateLimitError is a subclass of GuestyApiError."""
        assert issubclass(GuestyRateLimitError, GuestyApiError)

    def test_retry_after_attribute(self) -> None:
        """GuestyRateLimitError stores retry_after."""
        error = GuestyRateLimitError("rate limited", retry_after=60.0)
        assert error.retry_after == 60.0
        assert error.reset_at is None

    def test_reset_at_attribute(self) -> None:
        """GuestyRateLimitError stores reset_at."""
        reset = datetime(2025, 7, 19, 12, 0, 0, tzinfo=UTC)
        error = GuestyRateLimitError(
            "rate limited",
            reset_at=reset,
        )
        assert error.reset_at == reset
        assert error.retry_after is None

    def test_both_attributes(self) -> None:
        """GuestyRateLimitError stores both attributes."""
        reset = datetime(2025, 7, 19, 12, 0, 0, tzinfo=UTC)
        error = GuestyRateLimitError(
            "rate limited",
            retry_after=30.0,
            reset_at=reset,
        )
        assert error.retry_after == 30.0
        assert error.reset_at == reset

    def test_defaults_to_none(self) -> None:
        """GuestyRateLimitError defaults attributes to None."""
        error = GuestyRateLimitError("rate limited")
        assert error.retry_after is None
        assert error.reset_at is None

    def test_message_attribute(self) -> None:
        """GuestyRateLimitError stores message from base."""
        error = GuestyRateLimitError("too many requests")
        assert error.message == "too many requests"


class TestGuestyConnectionError:
    """Tests for the GuestyConnectionError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyConnectionError is a subclass of GuestyApiError."""
        assert issubclass(GuestyConnectionError, GuestyApiError)

    def test_message_attribute(self) -> None:
        """GuestyConnectionError stores the message attribute."""
        error = GuestyConnectionError("connection refused")
        assert error.message == "connection refused"


class TestGuestyResponseError:
    """Tests for the GuestyResponseError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyResponseError is a subclass of GuestyApiError."""
        assert issubclass(GuestyResponseError, GuestyApiError)

    def test_message_attribute(self) -> None:
        """GuestyResponseError stores the message attribute."""
        error = GuestyResponseError("unexpected format")
        assert error.message == "unexpected format"


class TestGuestyMessageError:
    """Tests for the GuestyMessageError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyMessageError is a subclass of GuestyApiError."""
        assert issubclass(GuestyMessageError, GuestyApiError)

    def test_construction_with_message_only(self) -> None:
        """GuestyMessageError stores message with None defaults."""
        error = GuestyMessageError("delivery failed")
        assert error.message == "delivery failed"
        assert error.reservation_id is None
        assert error.available_channels is None

    def test_construction_with_reservation_id(self) -> None:
        """GuestyMessageError stores reservation_id context."""
        error = GuestyMessageError(
            "not found",
            reservation_id="res-123",
        )
        assert error.reservation_id == "res-123"

    def test_construction_with_available_channels(self) -> None:
        """GuestyMessageError stores available_channels context."""
        error = GuestyMessageError(
            "channel unavailable",
            available_channels=("email", "sms"),
        )
        assert error.available_channels == ("email", "sms")

    def test_attributes_accessible_after_construction(self) -> None:
        """All attributes are accessible after construction."""
        error = GuestyMessageError(
            "test error",
            reservation_id="res-456",
            available_channels=("email",),
        )
        assert error.message == "test error"
        assert error.reservation_id == "res-456"
        assert error.available_channels == ("email",)
        assert str(error) == "test error"

    def test_caught_as_base(self) -> None:
        """GuestyMessageError can be caught as GuestyApiError."""
        with pytest.raises(GuestyApiError):
            raise GuestyMessageError("test")


class TestGuestyCustomFieldError:
    """Tests for the GuestyCustomFieldError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyCustomFieldError is a subclass of GuestyApiError."""
        assert issubclass(GuestyCustomFieldError, GuestyApiError)

    def test_construction_with_message_only(self) -> None:
        """GuestyCustomFieldError stores message with None defaults."""
        error = GuestyCustomFieldError("field update failed")
        assert error.message == "field update failed"
        assert error.target_type is None
        assert error.target_id is None
        assert error.field_id is None

    def test_construction_with_context_attributes(self) -> None:
        """GuestyCustomFieldError stores context attributes."""
        error = GuestyCustomFieldError(
            "invalid value",
            target_type="listing",
            target_id="lst-123",
            field_id="cf-abc",
        )
        assert error.target_type == "listing"
        assert error.target_id == "lst-123"
        assert error.field_id == "cf-abc"

    def test_attributes_accessible_after_construction(self) -> None:
        """All attributes are accessible after construction."""
        error = GuestyCustomFieldError(
            "test error",
            target_type="reservation",
            target_id="res-456",
            field_id="cf-def",
        )
        assert error.message == "test error"
        assert error.target_type == "reservation"
        assert error.target_id == "res-456"
        assert error.field_id == "cf-def"
        assert str(error) == "test error"

    def test_caught_as_base(self) -> None:
        """GuestyCustomFieldError can be caught as GuestyApiError."""
        with pytest.raises(GuestyApiError):
            raise GuestyCustomFieldError("test")


class TestGuestyActionError:
    """Tests for the GuestyActionError exception."""

    def test_inherits_from_base(self) -> None:
        """GuestyActionError is a subclass of GuestyApiError."""
        assert issubclass(GuestyActionError, GuestyApiError)

    def test_construction_with_message_only(self) -> None:
        """GuestyActionError stores message with None defaults."""
        error = GuestyActionError("action failed")
        assert error.message == "action failed"
        assert error.target_id is None
        assert error.action_type is None

    def test_construction_with_context(self) -> None:
        """GuestyActionError stores context attributes."""
        error = GuestyActionError(
            "not found",
            target_id="res-123",
            action_type="add_reservation_note",
        )
        assert error.target_id == "res-123"
        assert error.action_type == "add_reservation_note"

    def test_attributes_accessible_after_construction(self) -> None:
        """All attributes are accessible after construction."""
        error = GuestyActionError(
            "test error",
            target_id="lst-456",
            action_type="set_listing_status",
        )
        assert error.message == "test error"
        assert error.target_id == "lst-456"
        assert error.action_type == "set_listing_status"
        assert str(error) == "test error"

    def test_caught_as_base(self) -> None:
        """GuestyActionError can be caught as GuestyApiError."""
        with pytest.raises(GuestyApiError):
            raise GuestyActionError("test")
