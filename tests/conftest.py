# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Guesty integration test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    CachedToken,
    GuestyAddress,
    GuestyListing,
    TokenStorage,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)

# Common test constants
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret"
FAKE_TOKEN = "test-access-token-jwt"
FAKE_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
FAKE_BASE_URL = "https://open-api.guesty.com/v1"


def make_token_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Guesty token endpoint response.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching the Guesty token endpoint response format.
    """
    defaults: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": FAKE_TOKEN,
        "expires_in": 86400,
        "scope": "open-api",
    }
    defaults.update(overrides)
    return defaults


class FakeTokenStorage:
    """In-memory TokenStorage implementation for tests.

    Implements the TokenStorage protocol without any HA dependencies,
    suitable for unit testing the API layer in isolation.
    """

    def __init__(self) -> None:
        """Initialize with empty state."""
        self._token: CachedToken | None = None
        self._count: int = 0
        self._window: datetime | None = None

    async def load_token(self) -> CachedToken | None:
        """Load the stored token.

        Returns:
            The stored CachedToken, or None if not set.
        """
        return self._token

    async def save_token(self, token: CachedToken) -> None:
        """Store a token.

        Args:
            token: The CachedToken to store.
        """
        self._token = token

    async def load_request_count(self) -> tuple[int, datetime | None]:
        """Load the token request counter.

        Returns:
            Tuple of (count, window_start).
        """
        return (self._count, self._window)

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Store the token request counter.

        Args:
            count: Number of token requests in current window.
            window_start: Start time of the current window.
        """
        self._count = count
        self._window = window_start


# Verify FakeTokenStorage satisfies the protocol at module level
_storage_check: TokenStorage = FakeTokenStorage()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Automatically enable custom integrations for all tests.

    This fixture ensures HA's loader discovers the custom_components/
    directory for integration tests.
    """


@pytest.fixture
def fake_storage() -> FakeTokenStorage:
    """Provide a clean FakeTokenStorage instance.

    Returns:
        A new FakeTokenStorage for the test.
    """
    return FakeTokenStorage()


@pytest.fixture
def mock_token() -> CachedToken:
    """Provide a valid CachedToken for tests.

    Returns:
        A CachedToken with default test values.
    """
    return CachedToken(
        access_token=FAKE_TOKEN,
        token_type="Bearer",
        expires_in=86400,
        scope="open-api",
        issued_at=datetime.now(UTC),
    )


# ── Listing test helpers ────────────────────────────────────────────


def make_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API listing dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API listing response format.
    """
    defaults: dict[str, Any] = {
        "_id": "listing-001",
        "title": "Beach House",
        "nickname": "beach",
        "listed": True,
        "active": True,
        "address": {
            "full": "123 Ocean Dr, Miami, FL 33139, US",
            "street": "123 Ocean Dr",
            "city": "Miami",
            "state": "FL",
            "zipcode": "33139",
            "country": "US",
        },
        "propertyType": "apartment",
        "roomType": "entire_home",
        "numberOfBedrooms": 2,
        "numberOfBathrooms": 1.5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckoutTime": "11:00",
        "tags": ["premium", "beachfront"],
        "customFields": {"region": "southeast"},
    }
    defaults.update(overrides)
    return defaults


def make_listings_page_response(
    listings: list[dict[str, Any]],
    count: int,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty API listings page response dict.

    Args:
        listings: List of listing dictionaries.
        count: Total count reported by API.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the Guesty listings endpoint response.
    """
    return {
        "results": listings,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


@pytest.fixture
def sample_listing() -> GuestyListing:
    """Provide a valid GuestyListing instance for tests.

    Returns:
        A GuestyListing with default test values.
    """
    return GuestyListing(
        id="listing-001",
        title="Beach House",
        nickname="beach",
        status="active",
        address=GuestyAddress(
            full="123 Ocean Dr, Miami, FL 33139, US",
            street="123 Ocean Dr",
            city="Miami",
            state="FL",
            zipcode="33139",
            country="US",
        ),
        property_type="apartment",
        room_type="entire_home",
        bedrooms=2,
        bathrooms=1.5,
        timezone="America/New_York",
        check_in_time="15:00",
        check_out_time="11:00",
        tags=("premium", "beachfront"),
        custom_fields=MappingProxyType({"region": "southeast"}),
    )


@pytest.fixture
def mock_coordinator(
    hass: HomeAssistant,
    sample_listing: GuestyListing,
) -> AsyncMock:
    """Provide a mocked ListingsCoordinator with sample data.

    Args:
        hass: Home Assistant instance.
        sample_listing: The sample listing for coordinator data.

    Returns:
        An AsyncMock mimicking ListingsCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = {sample_listing.id: sample_listing}
    coordinator.hass = hass
    coordinator.config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    return coordinator


@pytest.fixture
def mock_messaging_client() -> AsyncMock:
    """Provide a mock GuestyMessagingClient for notify tests.

    Returns:
        An AsyncMock configured as a GuestyMessagingClient stand-in.
    """
    client = AsyncMock()
    client.send_message = AsyncMock()
    client.resolve_conversation = AsyncMock()
    client.render_template = AsyncMock()
    return client
