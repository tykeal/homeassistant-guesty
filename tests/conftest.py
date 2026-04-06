# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Guesty integration test suite."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    CachedToken,
    GuestyAddress,
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyMoney,
    GuestyReservation,
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


@pytest.fixture(autouse=True)
def _auto_mock_cf_definitions(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Auto-mock custom field definitions for integration tests.

    Prevents real API calls during setup. Skipped for tests in the
    api/ subdirectory. Tests that need specific definitions can
    override with their own patch.

    Args:
        request: Pytest fixture request for path inspection.

    Yields:
        None after applying the patch.
    """
    # Skip only for API-level tests under tests/api/
    path_parts = request.path.parts
    if "tests" in path_parts:
        idx = path_parts.index("tests")
        if len(path_parts) > idx + 1 and path_parts[idx + 1] == "api":
            yield
            return

    with patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        yield


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


# ── Reservation test helpers ────────────────────────────────────────


def make_guest_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API guest dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API guest object format.
    """
    defaults: dict[str, Any] = {
        "fullName": "John Doe",
        "phone": "+15551234567",
        "email": "john@example.com",
        "_id": "guest-001",
    }
    defaults.update(overrides)
    return defaults


def make_money_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API money dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API money object format.
    """
    defaults: dict[str, Any] = {
        "totalPaid": 500.00,
        "balanceDue": 100.00,
        "currency": "USD",
    }
    defaults.update(overrides)
    return defaults


def make_reservation_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API reservation dict with sensible defaults.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty API reservation response.
    """
    defaults: dict[str, Any] = {
        "_id": "res-001",
        "listingId": "listing-001",
        "status": "confirmed",
        "checkIn": "2025-08-01T15:00:00+00:00",
        "checkOut": "2025-08-05T11:00:00+00:00",
        "confirmationCode": "CONF-ABC",
        "checkInDateLocalized": "2025-08-01",
        "checkOutDateLocalized": "2025-08-05",
        "plannedArrival": "15:00",
        "plannedDeparture": "11:00",
        "nightsCount": 4,
        "guestsCount": 2,
        "source": "airbnb",
        "note": "Late arrival",
        "guest": make_guest_dict(),
        "money": make_money_dict(),
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def sample_reservation() -> GuestyReservation:
    """Provide a valid GuestyReservation instance for tests.

    Returns:
        A GuestyReservation with default test values.
    """
    return GuestyReservation(
        id="res-001",
        listing_id="listing-001",
        status="confirmed",
        check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
        check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        confirmation_code="CONF-ABC",
        check_in_local="2025-08-01",
        check_out_local="2025-08-05",
        planned_arrival="15:00",
        planned_departure="11:00",
        nights_count=4,
        guests_count=2,
        source="airbnb",
        note="Late arrival",
        guest=GuestyGuest(
            full_name="John Doe",
            phone="+15551234567",
            email="john@example.com",
            guest_id="guest-001",
        ),
        money=GuestyMoney(
            total_paid=500.00,
            balance_due=100.00,
            currency="USD",
        ),
    )


@pytest.fixture
def mock_reservations_coordinator(
    hass: HomeAssistant,
    sample_listing: GuestyListing,
    sample_reservation: GuestyReservation,
) -> AsyncMock:
    """Provide a mocked ReservationsCoordinator with sample data.

    Args:
        hass: Home Assistant instance.
        sample_listing: The sample listing for coordinator data.
        sample_reservation: The sample reservation for data.

    Returns:
        An AsyncMock mimicking ReservationsCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = {
        sample_listing.id: [sample_reservation],
    }
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
    coordinator.last_update_success = True
    return coordinator


# ── Custom field test helpers ───────────────────────────────────────


def make_custom_field_definition_dict(
    **overrides: Any,
) -> dict[str, Any]:
    """Create a Guesty API custom field definition dict.

    Args:
        **overrides: Fields to override on the defaults.

    Returns:
        Dictionary matching the Guesty custom field definition format.
    """
    defaults: dict[str, Any] = {
        "id": "cf-door-code",
        "name": "Door Code",
        "type": "string",
        "objectType": "reservation",
    }
    defaults.update(overrides)
    return defaults


def sample_custom_field_definitions() -> list[GuestyCustomFieldDefinition]:
    """Provide a list of sample custom field definitions.

    Returns:
        List with listing, reservation, and both-target fields.
    """
    return [
        GuestyCustomFieldDefinition(
            field_id="cf-region",
            name="Region",
            field_type="text",
            applicable_to=frozenset({"listing"}),
        ),
        GuestyCustomFieldDefinition(
            field_id="cf-door-code",
            name="Door Code",
            field_type="text",
            applicable_to=frozenset({"reservation"}),
        ),
        GuestyCustomFieldDefinition(
            field_id="cf-priority",
            name="Priority",
            field_type="number",
            applicable_to=frozenset({"listing", "reservation"}),
        ),
    ]


@pytest.fixture
def mock_cf_definitions() -> list[GuestyCustomFieldDefinition]:
    """Provide sample custom field definitions.

    Returns:
        List of sample GuestyCustomFieldDefinition instances.
    """
    return sample_custom_field_definitions()


@pytest.fixture
def mock_cf_coordinator(
    hass: HomeAssistant,
) -> AsyncMock:
    """Provide a mocked CustomFieldsDefinitionCoordinator.

    Args:
        hass: Home Assistant instance.

    Returns:
        An AsyncMock mimicking CustomFieldsDefinitionCoordinator.
    """
    coordinator = AsyncMock()
    coordinator.data = sample_custom_field_definitions()
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
    coordinator.last_update_success = True

    def _get_field(
        field_id: str,
    ) -> GuestyCustomFieldDefinition | None:
        """Look up a field by ID from mock data."""
        data: list[GuestyCustomFieldDefinition] = coordinator.data
        for f in data:
            if f.field_id == field_id:
                return f
        return None

    def _get_fields_for_target(
        target_type: str,
    ) -> list[GuestyCustomFieldDefinition]:
        """Filter fields by target type from mock data."""
        data: list[GuestyCustomFieldDefinition] = coordinator.data
        return [f for f in data if target_type in f.applicable_to]

    coordinator.get_field = _get_field
    coordinator.get_fields_for_target = _get_fields_for_target
    return coordinator


@pytest.fixture
def mock_cf_client() -> AsyncMock:
    """Provide a mock GuestyCustomFieldsClient.

    Returns:
        An AsyncMock configured as a GuestyCustomFieldsClient.
    """
    client = AsyncMock()
    client.get_definitions = AsyncMock(
        return_value=sample_custom_field_definitions(),
    )
    client.set_field = AsyncMock()
    client.validate_value = lambda v, t: None
    return client


# ── Action test helpers ─────────────────────────────────────────────


@pytest.fixture
def mock_actions_client() -> AsyncMock:
    """Provide a mock GuestyActionsClient for action tests.

    Returns:
        An AsyncMock configured as a GuestyActionsClient stand-in.
    """
    from custom_components.guesty.api.models import ActionResult

    client = AsyncMock()
    client.add_reservation_note = AsyncMock(
        return_value=ActionResult(success=True, target_id="res-001"),
    )
    client.set_listing_status = AsyncMock(
        return_value=ActionResult(success=True, target_id="listing-001"),
    )
    client.create_task = AsyncMock(
        return_value=ActionResult(success=True, target_id="task-001"),
    )
    client.set_calendar_availability = AsyncMock(
        return_value=ActionResult(
            success=True,
            target_id="listing-001",
        ),
    )
    client.update_reservation_custom_field = AsyncMock(
        return_value=ActionResult(success=True, target_id="res-001"),
    )
    return client
