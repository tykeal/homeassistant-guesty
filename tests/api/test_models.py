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
    CachedToken,
    Conversation,
    GuestyAddress,
    GuestyListing,
    GuestyListingsResponse,
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
        "numberOfBedrooms": 2,
        "numberOfBathrooms": 1.5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckoutTime": "11:00",
        "tags": ["pet-friendly", "beachfront"],
        "customFields": {
            "maintenance_status": "good",
            "region": "southeast",
        },
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
        assert listing.bedrooms == 2
        assert listing.bathrooms == 1.5
        assert listing.timezone == "America/New_York"
        assert listing.check_in_time == "15:00"
        assert listing.check_out_time == "11:00"
        assert listing.tags == ("pet-friendly", "beachfront")
        assert listing.custom_fields == {
            "maintenance_status": "good",
            "region": "southeast",
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
                customFields={"count": 42, "ok": True},
            ),
        )
        assert listing is not None
        assert listing.custom_fields == {
            "count": "42",
            "ok": "True",
        }

    def test_bedrooms_none_when_absent(self) -> None:
        """Bedrooms is None when not in API data."""
        data = _make_listing_dict()
        del data["numberOfBedrooms"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.bedrooms is None

    def test_bathrooms_none_when_absent(self) -> None:
        """Bathrooms is None when not in API data."""
        data = _make_listing_dict()
        del data["numberOfBathrooms"]
        listing = GuestyListing.from_api_dict(data)
        assert listing is not None
        assert listing.bathrooms is None

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
        """Empty results array yields empty list."""
        data = _make_listings_response(
            listings=[],
            count=0,
        )
        resp = GuestyListingsResponse.from_api_dict(data)
        assert resp.results == []
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
