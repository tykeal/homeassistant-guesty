# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for CachedToken dataclass and TokenStorage protocol."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.guesty.api.const import MAX_MESSAGE_LENGTH
from custom_components.guesty.api.models import (
    CachedToken,
    Conversation,
    MessageDeliveryResult,
    MessageRequest,
    TokenStorage,
)


def _make_token(**overrides: Any) -> CachedToken:
    """Create a CachedToken with sensible defaults.

    Args:
        **overrides: Fields to override on the default token.

    Returns:
        A CachedToken instance.
    """
    defaults: dict[str, Any] = {
        "access_token": "test-access-token",
        "token_type": "Bearer",
        "expires_in": 86400,
        "scope": "open-api",
        "issued_at": datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return CachedToken(**defaults)


class TestCachedTokenCreation:
    """Tests for CachedToken creation and validation."""

    def test_create_with_valid_data(self) -> None:
        """CachedToken can be created with valid parameters."""
        token = _make_token()
        assert token.access_token == "test-access-token"
        assert token.token_type == "Bearer"
        assert token.expires_in == 86400
        assert token.scope == "open-api"

    def test_empty_access_token_raises(self) -> None:
        """CachedToken raises ValueError for empty access_token."""
        with pytest.raises(ValueError, match="non-empty"):
            _make_token(access_token="")

    def test_negative_expires_in_raises(self) -> None:
        """CachedToken raises ValueError for non-positive expires_in."""
        with pytest.raises(ValueError, match="positive"):
            _make_token(expires_in=-1)

    def test_zero_expires_in_raises(self) -> None:
        """CachedToken raises ValueError for zero expires_in."""
        with pytest.raises(ValueError, match="positive"):
            _make_token(expires_in=0)

    def test_naive_datetime_raises(self) -> None:
        """CachedToken raises ValueError for naive issued_at."""
        with pytest.raises(ValueError, match="timezone-aware"):
            _make_token(issued_at=datetime(2025, 7, 18, 12, 0, 0))


class TestCachedTokenFrozen:
    """Tests for CachedToken immutability."""

    def test_frozen_immutability(self) -> None:
        """CachedToken fields cannot be modified after creation."""
        token = _make_token()
        with pytest.raises(AttributeError):
            token.access_token = "new-token"  # type: ignore[misc]

    def test_frozen_expires_in(self) -> None:
        """CachedToken expires_in cannot be modified."""
        token = _make_token()
        with pytest.raises(AttributeError):
            token.expires_in = 999  # type: ignore[misc]


class TestCachedTokenExpiresAt:
    """Tests for CachedToken.expires_at computed property."""

    def test_expires_at_computation(self) -> None:
        """expires_at equals issued_at + expires_in seconds."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued, expires_in=86400)
        expected = issued + timedelta(seconds=86400)
        assert token.expires_at == expected

    def test_expires_at_short_lifetime(self) -> None:
        """expires_at works for short token lifetimes."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued, expires_in=60)
        expected = issued + timedelta(seconds=60)
        assert token.expires_at == expected


class TestCachedTokenIsExpired:
    """Tests for CachedToken.is_expired method."""

    def test_not_expired_fresh_token(self) -> None:
        """A freshly issued token is not expired."""
        token = _make_token(
            issued_at=datetime.now(UTC),
            expires_in=86400,
        )
        assert token.is_expired() is False

    def test_expired_old_token(self) -> None:
        """A token issued more than expires_in ago is expired."""
        issued = datetime.now(UTC) - timedelta(seconds=86401)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired() is True

    def test_expired_with_buffer(self) -> None:
        """A token within the buffer window is considered expired."""
        # Token expires in 200 seconds, buffer is 300
        issued = datetime.now(UTC) - timedelta(seconds=86200)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired(buffer_seconds=300) is True

    def test_not_expired_outside_buffer(self) -> None:
        """A token outside the buffer window is not expired."""
        # Token expires in 600 seconds, buffer is 300
        issued = datetime.now(UTC) - timedelta(seconds=85800)
        token = _make_token(issued_at=issued, expires_in=86400)
        assert token.is_expired(buffer_seconds=300) is False


class TestCachedTokenSerialization:
    """Tests for CachedToken to_dict/from_dict round-trip."""

    def test_to_dict(self) -> None:
        """to_dict produces expected dictionary structure."""
        issued = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        token = _make_token(issued_at=issued)
        result = token.to_dict()
        assert result == {
            "access_token": "test-access-token",
            "token_type": "Bearer",
            "expires_in": 86400,
            "scope": "open-api",
            "issued_at": "2025-07-18T12:00:00+00:00",
        }

    def test_from_dict(self) -> None:
        """from_dict reconstructs a CachedToken from dictionary."""
        data = {
            "access_token": "restored-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "open-api",
            "issued_at": "2025-07-18T12:00:00+00:00",
        }
        token = CachedToken.from_dict(data)
        assert token.access_token == "restored-token"
        assert token.expires_in == 3600
        assert token.issued_at == datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)

    def test_round_trip(self) -> None:
        """to_dict followed by from_dict produces equal token."""
        original = _make_token()
        restored = CachedToken.from_dict(original.to_dict())
        assert restored == original


class TestTokenStorageProtocol:
    """Tests for the TokenStorage protocol interface."""

    async def test_protocol_structural_subtyping(self) -> None:
        """A class with matching methods satisfies TokenStorage."""

        class FakeStorage:
            """Fake storage for protocol test."""

            async def load_token(self) -> CachedToken | None:
                """Load token stub."""
                return None

            async def save_token(self, token: CachedToken) -> None:
                """Save token stub."""

            async def load_request_count(
                self,
            ) -> tuple[int, datetime | None]:
                """Load request count stub."""
                return (0, None)

            async def save_request_count(
                self,
                count: int,
                window_start: datetime,
            ) -> None:
                """Save request count stub."""

        storage: TokenStorage = FakeStorage()
        assert await storage.load_token() is None
        assert await storage.load_request_count() == (0, None)

    async def test_mock_satisfies_protocol(self) -> None:
        """AsyncMock can satisfy TokenStorage protocol methods."""
        mock_storage = AsyncMock(spec=TokenStorage)
        mock_storage.load_token.return_value = None
        mock_storage.load_request_count.return_value = (0, None)

        assert await mock_storage.load_token() is None
        assert await mock_storage.load_request_count() == (0, None)


class TestConversation:
    """Tests for Conversation frozen dataclass."""

    def test_create_with_valid_fields(self) -> None:
        """Conversation can be created with valid parameters."""
        conv = Conversation(
            id="conv-123",
            reservation_id="res-456",
            available_channels=("email", "sms"),
        )
        assert conv.id == "conv-123"
        assert conv.reservation_id == "res-456"
        assert conv.available_channels == ("email", "sms")

    def test_empty_id_raises(self) -> None:
        """Conversation raises ValueError for empty id."""
        with pytest.raises(ValueError, match="id must be non-empty"):
            Conversation(
                id="",
                reservation_id="res-456",
                available_channels=("email",),
            )

    def test_empty_reservation_id_raises(self) -> None:
        """Conversation raises ValueError for empty reservation_id."""
        with pytest.raises(ValueError, match="reservation_id"):
            Conversation(
                id="conv-123",
                reservation_id="",
                available_channels=("email",),
            )

    def test_empty_available_channels_raises(self) -> None:
        """Conversation raises for empty available_channels."""
        with pytest.raises(ValueError, match="available_channels"):
            Conversation(
                id="conv-123",
                reservation_id="res-456",
                available_channels=(),
            )

    def test_frozen_immutability(self) -> None:
        """Conversation fields cannot be modified after creation."""
        conv = Conversation(
            id="conv-123",
            reservation_id="res-456",
            available_channels=("email",),
        )
        with pytest.raises(AttributeError):
            conv.id = "new-id"  # type: ignore[misc]


class TestMessageRequest:
    """Tests for MessageRequest frozen dataclass."""

    def test_create_with_valid_fields(self) -> None:
        """MessageRequest can be created with valid parameters."""
        req = MessageRequest(
            conversation_id="conv-123",
            body="Hello, guest!",
        )
        assert req.conversation_id == "conv-123"
        assert req.body == "Hello, guest!"
        assert req.channel is None

    def test_create_with_channel(self) -> None:
        """MessageRequest accepts a valid channel."""
        req = MessageRequest(
            conversation_id="conv-123",
            body="Hello",
            channel="email",
        )
        assert req.channel == "email"

    def test_empty_conversation_id_raises(self) -> None:
        """MessageRequest raises for empty conversation_id."""
        with pytest.raises(ValueError, match="conversation_id"):
            MessageRequest(conversation_id="", body="Hello")

    def test_empty_body_raises(self) -> None:
        """MessageRequest raises ValueError for empty body."""
        with pytest.raises(
            ValueError,
            match="body must be non-empty",
        ):
            MessageRequest(
                conversation_id="conv-123",
                body="",
            )

    def test_body_exceeding_max_length_raises(self) -> None:
        """MessageRequest raises ValueError for oversized body."""
        long_body = "x" * (MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            MessageRequest(
                conversation_id="conv-123",
                body=long_body,
            )

    def test_unknown_channel_raises(self) -> None:
        """MessageRequest raises ValueError for unknown channel."""
        with pytest.raises(ValueError, match="unknown channel"):
            MessageRequest(
                conversation_id="conv-123",
                body="Hello",
                channel="carrier_pigeon",
            )

    def test_none_channel_accepted(self) -> None:
        """MessageRequest accepts None as channel."""
        req = MessageRequest(
            conversation_id="conv-123",
            body="Hello",
            channel=None,
        )
        assert req.channel is None

    def test_frozen_immutability(self) -> None:
        """MessageRequest fields cannot be modified."""
        req = MessageRequest(
            conversation_id="conv-123",
            body="Hello",
        )
        with pytest.raises(AttributeError):
            req.body = "new body"  # type: ignore[misc]


class TestMessageDeliveryResult:
    """Tests for MessageDeliveryResult frozen dataclass."""

    def test_success_result_with_message_id(self) -> None:
        """Successful result contains message_id."""
        result = MessageDeliveryResult(
            success=True,
            message_id="msg-789",
        )
        assert result.success is True
        assert result.message_id == "msg-789"
        assert result.error_details is None

    def test_failure_result_with_error_details(self) -> None:
        """Failed result has error_details and reservation_id."""
        result = MessageDeliveryResult(
            success=False,
            error_details="delivery timeout",
            reservation_id="res-456",
        )
        assert result.success is False
        assert result.error_details == "delivery timeout"
        assert result.reservation_id == "res-456"
        assert result.message_id is None

    def test_frozen_immutability(self) -> None:
        """MessageDeliveryResult fields cannot be modified."""
        result = MessageDeliveryResult(success=True)
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]
