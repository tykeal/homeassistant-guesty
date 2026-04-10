# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for API data models including tokens and listing models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.guesty.api.const import MAX_MESSAGE_LENGTH
from custom_components.guesty.api.models import (
    ActionResult,
    CachedToken,
    Conversation,
    GuestyAddress,
    GuestyCustomFieldDefinition,
    GuestyCustomFieldResult,
    GuestyCustomFieldUpdate,
    GuestyGuest,
    GuestyListing,
    GuestyListingsResponse,
    GuestyMoney,
    GuestyReservation,
    GuestyReservationsResponse,
    MessageDeliveryResult,
    MessageRequest,
    TokenStorage,
    _parse_custom_fields_array,
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


# ── GuestyAddress Tests (T003) ──────────────────────────────────────


def _make_full_address_dict() -> dict[str, str]:
    """Create a complete Guesty API address dictionary.

    Returns:
        Dictionary matching the Guesty API address format.
    """
    return {
        "full": "123 Beach Rd, Miami, FL 33139, USA",
        "street": "123 Beach Rd",
        "city": "Miami",
        "state": "FL",
        "zipcode": "33139",
        "country": "USA",
    }


class TestGuestyAddressFromApiDict:
    """Tests for GuestyAddress.from_api_dict class method."""

    def test_full_address(self) -> None:
        """from_api_dict parses a complete address dictionary."""
        data = _make_full_address_dict()
        addr = GuestyAddress.from_api_dict(data)
        assert addr is not None
        assert addr.full == "123 Beach Rd, Miami, FL 33139, USA"
        assert addr.street == "123 Beach Rd"
        assert addr.city == "Miami"
        assert addr.state == "FL"
        assert addr.zipcode == "33139"
        assert addr.country == "USA"

    def test_partial_address(self) -> None:
        """from_api_dict handles partial address with some None."""
        data = {"city": "Miami", "state": "FL"}
        addr = GuestyAddress.from_api_dict(data)
        assert addr is not None
        assert addr.full is None
        assert addr.street is None
        assert addr.city == "Miami"
        assert addr.state == "FL"
        assert addr.zipcode is None
        assert addr.country is None

    def test_none_input_returns_none(self) -> None:
        """from_api_dict returns None when input is None."""
        assert GuestyAddress.from_api_dict(None) is None

    def test_empty_dict_returns_none(self) -> None:
        """from_api_dict returns None when input is empty dict."""
        assert GuestyAddress.from_api_dict({}) is None

    def test_non_dict_truthy_returns_none(self) -> None:
        """from_api_dict returns None for non-dict truthy value."""
        assert GuestyAddress.from_api_dict("123 Main St") is None  # type: ignore[arg-type]


class TestGuestyAddressFormatted:
    """Tests for GuestyAddress.formatted method."""

    def test_returns_full_when_present(self) -> None:
        """formatted() returns full address when available."""
        addr = GuestyAddress.from_api_dict(_make_full_address_dict())
        assert addr is not None
        assert addr.formatted() == "123 Beach Rd, Miami, FL 33139, USA"

    def test_joins_components_when_full_absent(self) -> None:
        """formatted() joins non-empty components with comma."""
        data = {"street": "123 Beach Rd", "city": "Miami"}
        addr = GuestyAddress.from_api_dict(data)
        assert addr is not None
        assert addr.formatted() == "123 Beach Rd, Miami"

    def test_returns_none_when_all_empty(self) -> None:
        """formatted() returns None when all components empty."""
        addr = GuestyAddress(
            full=None,
            street=None,
            city=None,
            state=None,
            zipcode=None,
            country=None,
        )
        assert addr.formatted() is None


class TestGuestyAddressFrozen:
    """Tests for GuestyAddress immutability."""

    def test_frozen(self) -> None:
        """GuestyAddress fields cannot be modified."""
        addr = GuestyAddress.from_api_dict(_make_full_address_dict())
        assert addr is not None
        with pytest.raises(AttributeError):
            addr.full = "new"  # type: ignore[misc]


# ── _parse_custom_fields_array Tests ─────────────────────────────────


class TestParseCustomFieldsArray:
    """Tests for _parse_custom_fields_array helper."""

    def test_valid_array(self) -> None:
        """Parses a valid array of custom field objects."""
        raw = [
            {"fieldId": "f1", "value": "val1"},
            {"fieldId": "f2", "value": "val2"},
        ]
        assert _parse_custom_fields_array(raw) == {
            "f1": "val1",
            "f2": "val2",
        }

    def test_empty_list(self) -> None:
        """Empty list returns empty dict."""
        assert _parse_custom_fields_array([]) == {}

    def test_non_list_returns_empty(self) -> None:
        """Non-list input returns empty dict."""
        assert _parse_custom_fields_array("not-a-list") == {}

    def test_dict_input_returns_empty(self) -> None:
        """Dict input returns empty dict (old format)."""
        assert _parse_custom_fields_array({"k": "v"}) == {}

    def test_none_input_returns_empty(self) -> None:
        """None input returns empty dict."""
        assert _parse_custom_fields_array(None) == {}

    def test_non_dict_items_skipped(self) -> None:
        """Non-dict items in the array are skipped."""
        raw = [
            {"fieldId": "f1", "value": "v1"},
            "not-a-dict",
            42,
            {"fieldId": "f2", "value": "v2"},
        ]
        assert _parse_custom_fields_array(raw) == {
            "f1": "v1",
            "f2": "v2",
        }

    def test_missing_field_id_skipped(self) -> None:
        """Items without fieldId are skipped."""
        raw = [
            {"value": "orphan"},
            {"fieldId": "f1", "value": "v1"},
        ]
        assert _parse_custom_fields_array(raw) == {"f1": "v1"}

    def test_empty_field_id_skipped(self) -> None:
        """Items with empty fieldId are skipped."""
        raw = [
            {"fieldId": "", "value": "v0"},
            {"fieldId": "f1", "value": "v1"},
        ]
        assert _parse_custom_fields_array(raw) == {"f1": "v1"}

    def test_non_string_field_id_skipped(self) -> None:
        """Items with non-string fieldId are skipped."""
        raw = [
            {"fieldId": 123, "value": "v0"},
            {"fieldId": "f1", "value": "v1"},
        ]
        assert _parse_custom_fields_array(raw) == {"f1": "v1"}

    def test_none_value_becomes_empty_string(self) -> None:
        """None value is converted to empty string."""
        raw = [{"fieldId": "f1", "value": None}]
        assert _parse_custom_fields_array(raw) == {"f1": ""}

    def test_missing_value_becomes_empty_string(self) -> None:
        """Missing value key results in empty string."""
        raw = [{"fieldId": "f1"}]
        assert _parse_custom_fields_array(raw) == {"f1": ""}

    def test_numeric_value_coerced(self) -> None:
        """Numeric values are coerced to strings."""
        raw = [{"fieldId": "f1", "value": 42}]
        assert _parse_custom_fields_array(raw) == {"f1": "42"}

    def test_bool_value_coerced(self) -> None:
        """Boolean values are coerced to strings."""
        raw = [{"fieldId": "f1", "value": True}]
        assert _parse_custom_fields_array(raw) == {"f1": "True"}


# ── GuestyListing Tests (T004) ──────────────────────────────────────


def _make_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Create a complete Guesty API listing dictionary.

    Args:
        **overrides: Fields to override on the default listing.

    Returns:
        Dictionary matching the Guesty API listing format.
    """
    defaults: dict[str, Any] = {
        "_id": "507f1f77bcf86cd799439011",
        "title": "Beach House",
        "nickname": "Beach Alt Name",
        "listed": True,
        "active": True,
        "address": _make_full_address_dict(),
        "propertyType": "apartment",
        "roomType": "Entire home/apartment",
        "type": "SINGLE",
        "bedrooms": 2,
        "bathrooms": 1.5,
        "accommodates": 5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckOutTime": "11:00",
        "tags": ["pet-friendly", "beachfront"],
        "customFields": [
            {"fieldId": "cf_maintenance_status", "value": "good"},
            {"fieldId": "cf_region", "value": "southeast"},
        ],
    }
    defaults.update(overrides)
    return defaults


class TestGuestyListingFromApiDict:
    """Tests for GuestyListing.from_api_dict class method."""

    def test_complete_data(self) -> None:
        """from_api_dict parses all fields from complete data."""
        listing = GuestyListing.from_api_dict(_make_listing_dict())
        assert listing is not None
        assert listing.id == "507f1f77bcf86cd799439011"
        assert listing.title == "Beach House"
        assert listing.nickname == "Beach Alt Name"
        assert listing.status == "active"
        assert listing.address is not None
        assert listing.property_type == "apartment"
        assert listing.room_type == "Entire home/apartment"
        assert listing.listing_type == "SINGLE"
        assert listing.bedrooms == 2
        assert listing.bathrooms == 1.5
        assert listing.accommodates == 5
        assert listing.timezone == "America/New_York"
        assert listing.check_in_time == "15:00"
        assert listing.check_out_time == "11:00"
        assert listing.tags == ("pet-friendly", "beachfront")
        assert listing.custom_fields == {
            "cf_maintenance_status": "good",
            "cf_region": "southeast",
        }

    def test_missing_id_returns_none(self) -> None:
        """from_api_dict returns None when _id is missing."""
        data = _make_listing_dict()
        del data["_id"]
        assert GuestyListing.from_api_dict(data) is None

    def test_empty_id_returns_none(self) -> None:
        """from_api_dict returns None when _id is empty."""
        assert (
            GuestyListing.from_api_dict(
                _make_listing_dict(_id=""),
            )
            is None
        )


class TestGuestyListingStatusDerivation:
    """Tests for listing status derivation logic."""

    def test_active_status(self) -> None:
        """listed=true and active=true yields active status."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(listed=True, active=True),
        )
        assert listing is not None
        assert listing.status == "active"

    def test_inactive_listed_false(self) -> None:
        """listed=false yields inactive status."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(listed=False, active=True),
        )
        assert listing is not None
        assert listing.status == "inactive"

    def test_inactive_active_false(self) -> None:
        """active=false yields inactive status."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(listed=True, active=False),
        )
        assert listing is not None
        assert listing.status == "inactive"

    def test_archived_status(self) -> None:
        """Explicit archive indicator yields archived status."""
        data = _make_listing_dict()
        data["pms"] = {"active": False}
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.status == "archived"

    def test_default_listed_true(self) -> None:
        """Missing listed field defaults to true."""
        data = _make_listing_dict()
        del data["listed"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.status == "active"

    def test_default_active_true(self) -> None:
        """Missing active field defaults to true."""
        data = _make_listing_dict()
        del data["active"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.status == "active"


class TestGuestyListingTitleFallback:
    """Tests for listing title fallback chain."""

    def test_title_present(self) -> None:
        """Title is used when present."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(title="My Title"),
        )
        assert listing is not None
        assert listing.title == "My Title"

    def test_falls_back_to_nickname(self) -> None:
        """Title falls back to nickname when absent."""
        data = _make_listing_dict(nickname="Alt Name")
        del data["title"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.title == "Alt Name"

    def test_falls_back_to_unknown(self) -> None:
        """Title falls back to Unknown when both absent."""
        data = _make_listing_dict()
        del data["title"]
        del data["nickname"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.title == "Unknown"


class TestGuestyListingDefaults:
    """Tests for GuestyListing default value handling."""

    def test_timezone_defaults_to_utc(self) -> None:
        """Missing timezone defaults to UTC."""
        data = _make_listing_dict()
        del data["timezone"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.timezone == "UTC"

    def test_null_timezone_defaults_to_utc(self) -> None:
        """Null timezone defaults to UTC."""
        data = _make_listing_dict()
        data["timezone"] = None
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.timezone == "UTC"

    def test_empty_string_timezone_defaults_to_utc(self) -> None:
        """Empty string timezone defaults to UTC."""
        data = _make_listing_dict()
        data["timezone"] = ""
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.timezone == "UTC"

    def test_tags_coerced_to_tuple(self) -> None:
        """Tags list is coerced to immutable tuple."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(tags=["a", "b"]),
        )
        assert listing is not None
        assert listing.tags == ("a", "b")
        assert isinstance(listing.tags, tuple)

    def test_empty_tags(self) -> None:
        """Missing tags default to empty tuple."""
        data = _make_listing_dict()
        del data["tags"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.tags == ()

    def test_custom_fields_coerced_to_strings(self) -> None:
        """Custom field values are coerced to strings."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(
                customFields=[
                    {"fieldId": "cf_count", "value": 42},
                    {"fieldId": "cf_ok", "value": True},
                ],
            ),
        )
        assert listing is not None
        assert listing.custom_fields == {
            "cf_count": "42",
            "cf_ok": "True",
        }

    def test_bedrooms_none_when_absent(self) -> None:
        """Bedrooms is None when not in API data."""
        data = _make_listing_dict()
        del data["bedrooms"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.bedrooms is None

    def test_bathrooms_none_when_absent(self) -> None:
        """Bathrooms is None when not in API data."""
        data = _make_listing_dict()
        del data["bathrooms"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.bathrooms is None

    def test_accommodates_none_when_absent(self) -> None:
        """Accommodates is None when not in API data."""
        data = _make_listing_dict()
        del data["accommodates"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.accommodates is None

    def test_no_address(self) -> None:
        """Missing address yields None."""
        data = _make_listing_dict()
        del data["address"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.address is None


class TestGuestyListingFrozen:
    """Tests for GuestyListing immutability."""

    def test_frozen(self) -> None:
        """GuestyListing fields cannot be modified."""
        listing = GuestyListing.from_api_dict(_make_listing_dict())
        assert listing is not None
        with pytest.raises(AttributeError):
            listing.title = "new"  # type: ignore[misc]

    def test_custom_fields_immutable(self) -> None:
        """custom_fields mapping cannot be mutated."""
        listing = GuestyListing.from_api_dict(_make_listing_dict())
        assert listing is not None
        with pytest.raises(TypeError):
            listing.custom_fields["new_key"] = "val"  # type: ignore[index]


class TestGuestyListingDefensiveValidation:
    """Tests for defensive type validation in from_api_dict."""

    def test_non_list_custom_fields_yields_empty(self) -> None:
        """Non-list customFields degrades to empty mapping."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(customFields="not-a-list"),
        )
        assert listing is not None
        assert dict(listing.custom_fields) == {}

    def test_non_list_tags_yields_empty(self) -> None:
        """Non-list tags degrades to empty tuple."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(tags="not-a-list"),
        )
        assert listing is not None
        assert listing.tags == ()

    def test_non_string_tags_filtered(self) -> None:
        """Non-string tag entries are filtered out."""
        listing = GuestyListing.from_api_dict(
            _make_listing_dict(tags=["ok", 42, None, "good"]),
        )
        assert listing is not None
        assert listing.tags == ("ok", "good")


# ── GuestyListingsResponse Tests (T005) ─────────────────────────────


def _make_listings_response(
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
    listings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a Guesty API listings response dictionary.

    Args:
        count: Total count from API metadata.
        limit: Page size used.
        skip: Offset used.
        listings: Override the results array.

    Returns:
        Dictionary matching the listings endpoint format.
    """
    if listings is None:
        listings = [_make_listing_dict()]
    return {
        "results": listings,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


class TestGuestyListingsResponseFromApiDict:
    """Tests for GuestyListingsResponse.from_api_dict."""

    def test_parses_valid_listings(self) -> None:
        """from_api_dict parses valid listings array."""
        data = _make_listings_response(count=1)
        resp = GuestyListingsResponse.from_api_dict(data)
        assert len(resp.results) == 1
        assert resp.results[0].id == "507f1f77bcf86cd799439011"

    def test_filters_none_entries(self) -> None:
        """from_api_dict filters listings with missing _id."""
        listings = [
            _make_listing_dict(),
            {"title": "No ID listing"},
        ]
        data = _make_listings_response(
            listings=listings,
            count=2,
        )
        resp = GuestyListingsResponse.from_api_dict(data)
        assert len(resp.results) == 1

    def test_preserves_pagination_fields(self) -> None:
        """count, limit, skip fields are preserved."""
        data = _make_listings_response(
            count=42,
            limit=100,
            skip=200,
        )
        resp = GuestyListingsResponse.from_api_dict(data)
        assert resp.count == 42
        assert resp.limit == 100
        assert resp.skip == 200

    def test_empty_results(self) -> None:
        """Empty results array yields empty tuple."""
        data = _make_listings_response(
            listings=[],
            count=0,
        )
        resp = GuestyListingsResponse.from_api_dict(data)
        assert resp.results == ()
        assert resp.count == 0


class TestGuestyListingsResponseFrozen:
    """Tests for GuestyListingsResponse immutability."""

    def test_frozen(self) -> None:
        """GuestyListingsResponse fields cannot be modified."""
        resp = GuestyListingsResponse.from_api_dict(
            _make_listings_response(),
        )
        with pytest.raises(AttributeError):
            resp.count = 99  # type: ignore[misc]


class TestGuestyListingsResponseDefensive:
    """Tests for defensive validation in from_api_dict."""

    def test_non_dict_items_skipped(self) -> None:
        """Non-dict items in results are skipped."""
        data = {
            "results": [
                _make_listing_dict(),
                "not-a-dict",
                42,
            ],
            "count": 3,
            "limit": 100,
            "skip": 0,
        }
        resp = GuestyListingsResponse.from_api_dict(data)
        assert len(resp.results) == 1

    def test_non_list_results_yields_empty(self) -> None:
        """Non-list results field degrades to empty tuple."""
        data = {
            "results": "not-a-list",
            "count": 0,
            "limit": 100,
            "skip": 0,
        }
        resp = GuestyListingsResponse.from_api_dict(data)
        assert resp.results == ()


# ── GuestyGuest Tests (T001) ────────────────────────────────────────


def _make_guest_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API guest dictionary with defaults.

    Args:
        **overrides: Fields to override on the default guest.

    Returns:
        Dictionary matching the Guesty API guest format.
    """
    defaults: dict[str, Any] = {
        "fullName": "Jane Smith",
        "phone": "+1-555-0123",
        "email": "jane@example.com",
        "_id": "guest-001",
    }
    defaults.update(overrides)
    return defaults


class TestGuestyGuestFromApiDict:
    """Tests for GuestyGuest.from_api_dict class method."""

    def test_full_guest_data(self) -> None:
        """from_api_dict parses a complete guest dictionary."""
        guest = GuestyGuest.from_api_dict(_make_guest_dict())
        assert guest is not None
        assert guest.full_name == "Jane Smith"
        assert guest.phone == "+1-555-0123"
        assert guest.email == "jane@example.com"
        assert guest.guest_id == "guest-001"

    def test_none_input_returns_none(self) -> None:
        """from_api_dict returns None when input is None."""
        assert GuestyGuest.from_api_dict(None) is None

    def test_empty_dict_returns_none(self) -> None:
        """from_api_dict returns None when input is empty dict."""
        assert GuestyGuest.from_api_dict({}) is None

    def test_partial_guest_missing_phone_email(self) -> None:
        """from_api_dict handles missing optional fields."""
        data = {"fullName": "John Doe"}
        guest = GuestyGuest.from_api_dict(data)
        assert guest is not None
        assert guest.full_name == "John Doe"
        assert guest.phone is None
        assert guest.email is None
        assert guest.guest_id is None

    def test_non_dict_input_returns_none(self) -> None:
        """from_api_dict returns None for non-dict input."""
        assert GuestyGuest.from_api_dict("not-a-dict") is None  # type: ignore[arg-type]


class TestGuestyGuestFrozen:
    """Tests for GuestyGuest immutability."""

    def test_frozen(self) -> None:
        """GuestyGuest fields cannot be modified."""
        guest = GuestyGuest.from_api_dict(_make_guest_dict())
        assert guest is not None
        with pytest.raises(AttributeError):
            guest.full_name = "new"  # type: ignore[misc]


# ── GuestyMoney Tests (T001) ────────────────────────────────────────


def _make_money_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API money dictionary with defaults.

    Args:
        **overrides: Fields to override on the default money.

    Returns:
        Dictionary matching the Guesty API money format.
    """
    defaults: dict[str, Any] = {
        "totalPaid": 1250.00,
        "balanceDue": 0.00,
        "currency": "USD",
    }
    defaults.update(overrides)
    return defaults


class TestGuestyMoneyFromApiDict:
    """Tests for GuestyMoney.from_api_dict class method."""

    def test_full_money_data(self) -> None:
        """from_api_dict parses a complete money dictionary."""
        money = GuestyMoney.from_api_dict(_make_money_dict())
        assert money is not None
        assert money.total_paid == 1250.00
        assert money.balance_due == 0.00
        assert money.currency == "USD"

    def test_none_input_returns_none(self) -> None:
        """from_api_dict returns None when input is None."""
        assert GuestyMoney.from_api_dict(None) is None

    def test_empty_dict_returns_none(self) -> None:
        """from_api_dict returns None when input is empty dict."""
        assert GuestyMoney.from_api_dict({}) is None

    def test_partial_money_missing_fields(self) -> None:
        """from_api_dict handles missing optional fields."""
        data = {"totalPaid": 500.0}
        money = GuestyMoney.from_api_dict(data)
        assert money is not None
        assert money.total_paid == 500.0
        assert money.balance_due is None
        assert money.currency is None

    def test_non_dict_input_returns_none(self) -> None:
        """from_api_dict returns None for non-dict input."""
        assert GuestyMoney.from_api_dict("not-a-dict") is None  # type: ignore[arg-type]


class TestGuestyMoneyFrozen:
    """Tests for GuestyMoney immutability."""

    def test_frozen(self) -> None:
        """GuestyMoney fields cannot be modified."""
        money = GuestyMoney.from_api_dict(_make_money_dict())
        assert money is not None
        with pytest.raises(AttributeError):
            money.total_paid = 999.0  # type: ignore[misc]


# ── GuestyReservation Tests (T002) ──────────────────────────────────


def _make_reservation_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API reservation dictionary with defaults.

    Args:
        **overrides: Fields to override on the default reservation.

    Returns:
        Dictionary matching the Guesty API reservation format.
    """
    defaults: dict[str, Any] = {
        "_id": "res-001",
        "listingId": "listing-001",
        "status": "confirmed",
        "checkIn": "2025-08-17T15:00:00.000Z",
        "checkOut": "2025-08-22T11:00:00.000Z",
        "confirmationCode": "GY-h5SdcsBL",
        "checkInDateLocalized": "2025-08-17",
        "checkOutDateLocalized": "2025-08-22",
        "plannedArrival": "16:00",
        "plannedDeparture": "10:00",
        "nightsCount": 5,
        "guestsCount": 3,
        "guest": _make_guest_dict(),
        "money": _make_money_dict(),
        "source": "airbnb",
        "note": "Late check-in requested",
    }
    defaults.update(overrides)
    return defaults


class TestGuestyReservationFromApiDict:
    """Tests for GuestyReservation.from_api_dict class method."""

    def test_complete_data(self) -> None:
        """from_api_dict parses all fields from complete data."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert res is not None
        assert res.id == "res-001"
        assert res.listing_id == "listing-001"
        assert res.status == "confirmed"
        assert res.check_in == datetime(
            2025,
            8,
            17,
            15,
            0,
            0,
            tzinfo=UTC,
        )
        assert res.check_out == datetime(
            2025,
            8,
            22,
            11,
            0,
            0,
            tzinfo=UTC,
        )
        assert res.confirmation_code == "GY-h5SdcsBL"
        assert res.check_in_local == "2025-08-17"
        assert res.check_out_local == "2025-08-22"
        assert res.planned_arrival == "16:00"
        assert res.planned_departure == "10:00"
        assert res.nights_count == 5
        assert res.guests_count == 3
        assert res.source == "airbnb"
        assert res.note == "Late check-in requested"
        assert res.guest is not None
        assert res.guest.full_name == "Jane Smith"
        assert res.money is not None
        assert res.money.total_paid == 1250.00

    def test_missing_id_returns_none(self) -> None:
        """from_api_dict returns None when _id is missing."""
        data = _make_reservation_dict()
        del data["_id"]
        assert GuestyReservation.from_api_dict(data) is None

    def test_empty_id_returns_none(self) -> None:
        """from_api_dict returns None when _id is empty."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(_id=""),
            )
            is None
        )

    def test_missing_listing_id_returns_none(self) -> None:
        """from_api_dict returns None when listingId is missing."""
        data = _make_reservation_dict()
        del data["listingId"]
        assert GuestyReservation.from_api_dict(data) is None

    def test_empty_listing_id_returns_none(self) -> None:
        """from_api_dict returns None for empty listingId."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(listingId=""),
            )
            is None
        )

    def test_missing_status_returns_none(self) -> None:
        """from_api_dict returns None when status is missing."""
        data = _make_reservation_dict()
        del data["status"]
        assert GuestyReservation.from_api_dict(data) is None

    def test_empty_status_returns_none(self) -> None:
        """from_api_dict returns None for empty status."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(status=""),
            )
            is None
        )

    def test_unparsable_check_in_returns_none(self) -> None:
        """from_api_dict returns None for unparsable checkIn."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(checkIn="not-a-date"),
            )
            is None
        )

    def test_missing_check_in_returns_none(self) -> None:
        """from_api_dict returns None when checkIn is missing."""
        data = _make_reservation_dict()
        del data["checkIn"]
        assert GuestyReservation.from_api_dict(data) is None

    def test_unparsable_check_out_returns_none(self) -> None:
        """from_api_dict returns None for unparsable checkOut."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(checkOut="not-a-date"),
            )
            is None
        )

    def test_missing_check_out_returns_none(self) -> None:
        """from_api_dict returns None when checkOut is missing."""
        data = _make_reservation_dict()
        del data["checkOut"]
        assert GuestyReservation.from_api_dict(data) is None

    def test_unknown_status_passed_through(self) -> None:
        """Unknown statuses are accepted per FR-025."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(status="future_unknown"),
        )
        assert res is not None
        assert res.status == "future_unknown"

    def test_optional_fields_default_to_none(self) -> None:
        """Optional fields default to None when absent."""
        data = {
            "_id": "res-minimal",
            "listingId": "listing-001",
            "status": "confirmed",
            "checkIn": "2025-08-17T15:00:00.000Z",
            "checkOut": "2025-08-22T11:00:00.000Z",
        }
        res = GuestyReservation.from_api_dict(data)
        assert res is not None
        assert res.confirmation_code is None
        assert res.check_in_local is None
        assert res.check_out_local is None
        assert res.planned_arrival is None
        assert res.planned_departure is None
        assert res.nights_count is None
        assert res.guests_count is None
        assert res.source is None
        assert res.note is None
        assert res.guest is None
        assert res.money is None

    def test_nested_guest_parsed(self) -> None:
        """Nested guest object is parsed via from_api_dict."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert res is not None
        assert res.guest is not None
        assert res.guest.full_name == "Jane Smith"
        assert res.guest.phone == "+1-555-0123"

    def test_nested_money_parsed(self) -> None:
        """Nested money object is parsed via from_api_dict."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert res is not None
        assert res.money is not None
        assert res.money.total_paid == 1250.00
        assert res.money.currency == "USD"

    def test_null_check_in_returns_none(self) -> None:
        """from_api_dict returns None when checkIn is None."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(checkIn=None),
            )
            is None
        )

    def test_null_check_out_returns_none(self) -> None:
        """from_api_dict returns None when checkOut is None."""
        assert (
            GuestyReservation.from_api_dict(
                _make_reservation_dict(checkOut=None),
            )
            is None
        )

    def test_naive_datetime_gets_utc(self) -> None:
        """Naive datetime strings are assigned UTC timezone."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(
                checkIn="2025-08-17T15:00:00",
                checkOut="2025-08-22T11:00:00",
            ),
        )
        assert res is not None
        assert res.check_in.tzinfo == UTC
        assert res.check_out.tzinfo == UTC


class TestReservationCustomFields:
    """Tests for GuestyReservation custom_fields parsing."""

    def test_custom_fields_parsed(self) -> None:
        """from_api_dict parses customFields into custom_fields."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(
                customFields=[
                    {"fieldId": "cf_door_code", "value": "1234"},
                    {"fieldId": "cf_wifi", "value": "secret"},
                ],
            ),
        )
        assert res is not None
        assert res.custom_fields == {
            "cf_door_code": "1234",
            "cf_wifi": "secret",
        }

    def test_custom_fields_coerced_to_strings(self) -> None:
        """Custom field values are coerced to strings."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(
                customFields=[
                    {"fieldId": "cf_count", "value": 42},
                    {"fieldId": "cf_ok", "value": True},
                ],
            ),
        )
        assert res is not None
        assert res.custom_fields == {
            "cf_count": "42",
            "cf_ok": "True",
        }

    def test_custom_fields_empty_when_absent(self) -> None:
        """Missing customFields yields empty mapping."""
        data = _make_reservation_dict()
        data.pop("customFields", None)
        res = GuestyReservation.from_api_dict(data)
        assert res is not None
        assert dict(res.custom_fields) == {}

    def test_non_list_custom_fields_yields_empty(self) -> None:
        """Non-list customFields degrades to empty mapping."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(customFields="not-a-list"),
        )
        assert res is not None
        assert dict(res.custom_fields) == {}

    def test_custom_fields_immutable(self) -> None:
        """custom_fields mapping cannot be mutated."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(
                customFields=[
                    {"fieldId": "cf_door_code", "value": "1234"},
                ],
            ),
        )
        assert res is not None
        with pytest.raises(TypeError):
            res.custom_fields["new"] = "val"  # type: ignore[index]

    def test_default_custom_fields_empty(self) -> None:
        """Default custom_fields is an empty MappingProxyType."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert res is not None
        assert len(res.custom_fields) == 0


class TestGuestyReservationFrozen:
    """Tests for GuestyReservation immutability."""

    def test_frozen(self) -> None:
        """GuestyReservation fields cannot be modified."""
        res = GuestyReservation.from_api_dict(
            _make_reservation_dict(),
        )
        assert res is not None
        with pytest.raises(AttributeError):
            res.status = "checked_in"  # type: ignore[misc]


# ── GuestyReservationsResponse Tests (T003) ─────────────────────────


def _make_reservations_response(
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
    reservations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a Guesty API reservations response dictionary.

    Args:
        count: Total count from API metadata.
        limit: Page size used.
        skip: Offset used.
        reservations: Override the results array.

    Returns:
        Dictionary matching the reservations endpoint format.
    """
    if reservations is None:
        reservations = [_make_reservation_dict()]
    return {
        "results": reservations,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


class TestGuestyReservationsResponseFromApiDict:
    """Tests for GuestyReservationsResponse.from_api_dict."""

    def test_parses_valid_reservations(self) -> None:
        """from_api_dict parses valid reservations array."""
        data = _make_reservations_response(count=1)
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert len(resp.results) == 1
        assert resp.results[0].id == "res-001"

    def test_filters_none_entries(self) -> None:
        """from_api_dict filters invalid reservations."""
        reservations = [
            _make_reservation_dict(),
            {"status": "confirmed"},  # missing _id
        ]
        data = _make_reservations_response(
            reservations=reservations,
            count=2,
        )
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert len(resp.results) == 1

    def test_preserves_pagination_fields(self) -> None:
        """count, limit, skip fields are preserved."""
        data = _make_reservations_response(
            count=42,
            limit=100,
            skip=200,
        )
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert resp.count == 42
        assert resp.limit == 100
        assert resp.skip == 200

    def test_empty_results(self) -> None:
        """Empty results array yields empty tuple."""
        data = _make_reservations_response(
            reservations=[],
            count=0,
        )
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert resp.results == ()
        assert resp.count == 0

    def test_non_dict_items_skipped(self) -> None:
        """Non-dict items in results are skipped."""
        data = {
            "results": [
                _make_reservation_dict(),
                "not-a-dict",
                42,
            ],
            "count": 3,
            "limit": 100,
            "skip": 0,
        }
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert len(resp.results) == 1

    def test_non_list_results_yields_empty(self) -> None:
        """Non-list results field degrades to empty tuple."""
        data = {
            "results": "not-a-list",
            "count": 0,
            "limit": 100,
            "skip": 0,
        }
        resp = GuestyReservationsResponse.from_api_dict(data)
        assert resp.results == ()


class TestGuestyReservationsResponseFrozen:
    """Tests for GuestyReservationsResponse immutability."""

    def test_frozen(self) -> None:
        """GuestyReservationsResponse fields cannot be modified."""
        resp = GuestyReservationsResponse.from_api_dict(
            _make_reservations_response(),
        )
        with pytest.raises(AttributeError):
            resp.count = 99  # type: ignore[misc]


# --- Phase 1 (Feature 004) Custom Field Model Tests ---


def _make_custom_field_definition_dict(
    **overrides: Any,
) -> dict[str, Any]:
    """Create a Guesty API custom field definition dict.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty custom-fields endpoint.
    """
    defaults: dict[str, Any] = {
        "fieldId": "637bad36abcdef123456",
        "key": "Door Code",
        "type": "text",
        "object": "reservation",
        "displayName": "door_code",
        "isPublic": False,
        "isRequired": False,
        "options": [],
    }
    defaults.update(overrides)
    return defaults


class TestGuestyCustomFieldDefinitionFromApiDict:
    """Tests for GuestyCustomFieldDefinition.from_api_dict."""

    def test_full_data_returns_populated_instance(self) -> None:
        """from_api_dict with full data returns populated instance."""
        data = _make_custom_field_definition_dict()
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.field_id == "637bad36abcdef123456"
        assert result.name == "Door Code"
        assert result.field_type == "text"
        assert result.applicable_to == frozenset({"reservation"})
        assert result.display_name == "door_code"
        assert result.is_public is False
        assert result.is_required is False
        assert result.options == ()

    def test_maps_string_type_to_text(self) -> None:
        """from_api_dict maps Guesty type 'string' to 'text'."""
        data = _make_custom_field_definition_dict(type="string")
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.field_type == "text"

    def test_unknown_type_preserved_as_is(self) -> None:
        """from_api_dict preserves unknown type values as-is."""
        data = _make_custom_field_definition_dict(type="multiline")
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.field_type == "multiline"

    def test_number_type_preserved(self) -> None:
        """from_api_dict preserves 'number' type value."""
        data = _make_custom_field_definition_dict(type="number")
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.field_type == "number"

    def test_boolean_type_preserved(self) -> None:
        """from_api_dict preserves 'boolean' type value."""
        data = _make_custom_field_definition_dict(type="boolean")
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.field_type == "boolean"

    def test_missing_field_id_returns_none(self) -> None:
        """from_api_dict returns None when 'fieldId' is missing."""
        data = _make_custom_field_definition_dict()
        del data["fieldId"]
        assert GuestyCustomFieldDefinition.from_api_dict(data) is None

    def test_missing_key_returns_none(self) -> None:
        """from_api_dict returns None when 'key' is missing."""
        data = _make_custom_field_definition_dict()
        del data["key"]
        assert GuestyCustomFieldDefinition.from_api_dict(data) is None

    def test_missing_type_returns_none(self) -> None:
        """from_api_dict returns None when 'type' is missing."""
        data = _make_custom_field_definition_dict()
        del data["type"]
        assert GuestyCustomFieldDefinition.from_api_dict(data) is None

    def test_unhashable_type_returns_none(self) -> None:
        """from_api_dict returns None when 'type' is not a string."""
        data = _make_custom_field_definition_dict()
        data["type"] = ["list", "value"]
        assert GuestyCustomFieldDefinition.from_api_dict(data) is None

    def test_missing_object_type_defaults_empty(self) -> None:
        """from_api_dict defaults applicable_to to frozenset()."""
        data = _make_custom_field_definition_dict()
        del data["object"]
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.applicable_to == frozenset()

    def test_object_type_both_produces_listing_reservation(
        self,
    ) -> None:
        """object 'both' maps to frozenset of both targets."""
        data = _make_custom_field_definition_dict(
            object="both",
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.applicable_to == frozenset(
            {"listing", "reservation"},
        )

    def test_object_type_listing_maps_correctly(self) -> None:
        """object 'listing' maps to frozenset({'listing'})."""
        data = _make_custom_field_definition_dict(
            object="listing",
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.applicable_to == frozenset({"listing"})

    def test_non_string_object_type_defaults_empty(self) -> None:
        """Non-string object defaults to frozenset()."""
        data = _make_custom_field_definition_dict(
            object=["listing", "reservation"],
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.applicable_to == frozenset()

    def test_is_public_true(self) -> None:
        """from_api_dict parses isPublic=true correctly."""
        data = _make_custom_field_definition_dict(isPublic=True)
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.is_public is True

    def test_is_required_true(self) -> None:
        """from_api_dict parses isRequired=true correctly."""
        data = _make_custom_field_definition_dict(isRequired=True)
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.is_required is True

    def test_options_parsed_as_tuple(self) -> None:
        """from_api_dict parses options list as tuple."""
        data = _make_custom_field_definition_dict(
            options=["a", "b", "c"],
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.options == ("a", "b", "c")

    def test_options_non_string_filtered(self) -> None:
        """from_api_dict filters non-string options."""
        data = _make_custom_field_definition_dict(
            options=["valid", 123, "also_valid"],
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.options == ("valid", "also_valid")

    def test_options_non_list_defaults_empty(self) -> None:
        """from_api_dict defaults options to () for non-list."""
        data = _make_custom_field_definition_dict(
            options="not-a-list",
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.options == ()

    def test_missing_display_name_defaults_empty(self) -> None:
        """from_api_dict defaults display_name to empty string."""
        data = _make_custom_field_definition_dict()
        del data["displayName"]
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.display_name == ""

    def test_null_display_name_defaults_empty(self) -> None:
        """from_api_dict defaults display_name for None value."""
        data = _make_custom_field_definition_dict(
            displayName=None,
        )
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.display_name == ""

    def test_non_bool_is_public_defaults_false(self) -> None:
        """from_api_dict defaults is_public to False for non-bool."""
        data = _make_custom_field_definition_dict(isPublic="yes")
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.is_public is False

    def test_non_bool_is_required_defaults_false(self) -> None:
        """from_api_dict defaults is_required to False for non-bool."""
        data = _make_custom_field_definition_dict(isRequired=1)
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        assert result.is_required is False


class TestGuestyCustomFieldDefinitionFrozen:
    """Tests for GuestyCustomFieldDefinition immutability."""

    def test_frozen(self) -> None:
        """GuestyCustomFieldDefinition fields cannot be modified."""
        data = _make_custom_field_definition_dict()
        result = GuestyCustomFieldDefinition.from_api_dict(data)
        assert result is not None
        with pytest.raises(AttributeError):
            result.field_id = "new-id"  # type: ignore[misc]


class TestGuestyCustomFieldUpdate:
    """Tests for GuestyCustomFieldUpdate construction and API dict."""

    def test_construction_with_string_value(self) -> None:
        """GuestyCustomFieldUpdate accepts string value."""
        update = GuestyCustomFieldUpdate(
            field_id="field-1",
            value="hello",
        )
        assert update.field_id == "field-1"
        assert update.value == "hello"

    def test_construction_with_int_value(self) -> None:
        """GuestyCustomFieldUpdate accepts int value."""
        update = GuestyCustomFieldUpdate(
            field_id="field-2",
            value=42,
        )
        assert update.value == 42

    def test_construction_with_float_value(self) -> None:
        """GuestyCustomFieldUpdate accepts float value."""
        update = GuestyCustomFieldUpdate(
            field_id="field-3",
            value=3.14,
        )
        assert update.value == 3.14

    def test_construction_with_bool_value(self) -> None:
        """GuestyCustomFieldUpdate accepts bool value."""
        update = GuestyCustomFieldUpdate(
            field_id="field-4",
            value=True,
        )
        assert update.value is True

    def test_to_api_dict(self) -> None:
        """to_api_dict returns expected API payload."""
        update = GuestyCustomFieldUpdate(
            field_id="cf-abc",
            value="test-value",
        )
        assert update.to_api_dict() == {
            "fieldId": "cf-abc",
            "value": "test-value",
        }

    def test_to_api_dict_with_numeric_value(self) -> None:
        """to_api_dict includes numeric value correctly."""
        update = GuestyCustomFieldUpdate(
            field_id="cf-num",
            value=99,
        )
        assert update.to_api_dict() == {
            "fieldId": "cf-num",
            "value": 99,
        }


class TestGuestyCustomFieldUpdateFrozen:
    """Tests for GuestyCustomFieldUpdate immutability."""

    def test_frozen(self) -> None:
        """GuestyCustomFieldUpdate fields cannot be modified."""
        update = GuestyCustomFieldUpdate(
            field_id="f1",
            value="v1",
        )
        with pytest.raises(AttributeError):
            update.field_id = "f2"  # type: ignore[misc]

    def test_empty_field_id_raises_value_error(self) -> None:
        """GuestyCustomFieldUpdate rejects empty field_id."""
        with pytest.raises(ValueError, match="field_id must not be empty"):
            GuestyCustomFieldUpdate(field_id="", value="val")


class TestGuestyCustomFieldResult:
    """Tests for GuestyCustomFieldResult construction."""

    def test_success_result_all_fields(self) -> None:
        """Success result has all fields populated."""
        result = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="lst-123",
            field_id="cf-abc",
        )
        assert result.success is True
        assert result.target_type == "listing"
        assert result.target_id == "lst-123"
        assert result.field_id == "cf-abc"
        assert result.error_details is None

    def test_failure_result_with_error_details(self) -> None:
        """Failure result stores error_details."""
        result = GuestyCustomFieldResult(
            success=False,
            target_type="reservation",
            target_id="res-456",
            field_id="cf-def",
            error_details="Field not found",
        )
        assert result.success is False
        assert result.error_details == "Field not found"

    def test_success_false_when_error_present(self) -> None:
        """success=False when error_details is set."""
        result = GuestyCustomFieldResult(
            success=False,
            target_type="listing",
            target_id="lst-789",
            field_id="cf-ghi",
            error_details="Invalid value",
        )
        assert result.success is False
        assert result.error_details is not None


class TestGuestyCustomFieldResultFrozen:
    """Tests for GuestyCustomFieldResult immutability."""

    def test_frozen(self) -> None:
        """GuestyCustomFieldResult fields cannot be modified."""
        result = GuestyCustomFieldResult(
            success=True,
            target_type="listing",
            target_id="lst-1",
            field_id="cf-1",
        )
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


# ── ActionResult ────────────────────────────────────────────────────


class TestActionResult:
    """Tests for ActionResult creation and validation."""

    def test_create_with_valid_data(self) -> None:
        """ActionResult can be created with valid parameters."""
        result = ActionResult(
            success=True,
            target_id="res-001",
        )
        assert result.success is True
        assert result.target_id == "res-001"
        assert result.error is None

    def test_create_with_error(self) -> None:
        """ActionResult stores error details."""
        result = ActionResult(
            success=False,
            target_id="res-001",
            error="Something went wrong",
        )
        assert result.success is False
        assert result.error == "Something went wrong"

    def test_empty_target_id_raises(self) -> None:
        """ActionResult raises ValueError for empty target_id."""
        with pytest.raises(ValueError, match="target_id"):
            ActionResult(success=True, target_id="")

    def test_false_without_error_raises(self) -> None:
        """ActionResult raises ValueError when False with no error."""
        with pytest.raises(ValueError, match="error must be"):
            ActionResult(success=False, target_id="res-001")

    def test_false_with_empty_error_raises(self) -> None:
        """ActionResult raises ValueError with empty error string."""
        with pytest.raises(ValueError, match="error must be"):
            ActionResult(
                success=False,
                target_id="res-001",
                error="",
            )

    def test_false_with_whitespace_error_raises(self) -> None:
        """ActionResult raises ValueError with whitespace error."""
        with pytest.raises(ValueError, match="error must be"):
            ActionResult(
                success=False,
                target_id="res-001",
                error="   ",
            )


class TestActionResultFrozen:
    """Tests for ActionResult immutability."""

    def test_frozen(self) -> None:
        """ActionResult fields cannot be modified."""
        result = ActionResult(
            success=True,
            target_id="res-001",
        )
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]
