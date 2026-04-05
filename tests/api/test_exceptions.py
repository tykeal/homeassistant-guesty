# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty API exception hierarchy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
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
