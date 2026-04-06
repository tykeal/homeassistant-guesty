# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ListingsCoordinator."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyListing,
    GuestyReservation,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.guesty.coordinator import (
    CustomFieldsDefinitionCoordinator,
    ListingsCoordinator,
    ReservationsCoordinator,
)


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Guesty integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
        },
        unique_id="test-client-id",
        **overrides,  # type: ignore[arg-type]
    )


class TestListingsCoordinator:
    """Tests for ListingsCoordinator._async_update_data."""

    async def test_update_data_returns_dict_keyed_by_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """_async_update_data returns dict keyed by listing.id."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=[sample_listing])

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()
        assert isinstance(data, dict)
        assert sample_listing.id in data
        assert data[sample_listing.id] is sample_listing
        api_client.get_listings.assert_awaited_once()

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval matches scan_interval from entry.options."""
        entry = _make_entry(options={CONF_SCAN_INTERVAL: 10})
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.update_interval == timedelta(minutes=10)

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval uses DEFAULT_SCAN_INTERVAL when not set."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_SCAN_INTERVAL,
        )

    async def test_update_raises_update_failed_on_connection_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyConnectionError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_update_raises_update_failed_on_auth_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyAuthError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyAuthError("auth failed"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_update_raises_update_failed_on_response_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyResponseError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            side_effect=GuestyResponseError("malformed"),
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_listing_list_returns_empty_dict(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data returns empty dict for no listings."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=[])

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()
        assert data == {}


class TestDisappearedListings:
    """Tests for disappeared listing tracking (T028)."""

    async def test_disappeared_listing_ids_initially_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """disappeared_listing_ids is empty on init."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        assert coordinator.disappeared_listing_ids == set()

    async def test_disappeared_listing_detected(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Listing absent from current fetch is marked disappeared."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch — establishes baseline
        await coordinator._async_update_data()

        # Second fetch — listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()

        assert sample_listing.id in coordinator.disappeared_listing_ids

    async def test_disappeared_listing_cleared_on_reappear(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Disappeared listing ID cleared when listing reappears."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()
        assert sample_listing.id in coordinator.disappeared_listing_ids

        # Listing reappears
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )
        await coordinator._async_update_data()
        assert sample_listing.id not in coordinator.disappeared_listing_ids

    async def test_disappeared_listing_warning_logged(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning logged for each disappeared listing ID."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        caplog.clear()
        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        matching = [
            r
            for r in caplog.records
            if r.name == "custom_components.guesty.coordinator"
            and r.levelno == logging.WARNING
            and sample_listing.id in r.getMessage()
        ]
        assert len(matching) == 1

    async def test_last_known_good_data_retained_on_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Coordinator retains last-known-good data after error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # Successful first refresh through the public coordinator flow
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data == {sample_listing.id: sample_listing}

        # Now API fails during a coordinator refresh
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()

        # DataUpdateCoordinator preserves last-known-good data
        assert coordinator.last_update_success is False
        assert coordinator.data == {sample_listing.id: sample_listing}

    async def test_recovery_updates_data_after_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Data updated on recovery after API error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First successful refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API error during refresh
        api_client.get_listings = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Recovery with new listing
        new_listing = GuestyListing(
            id="listing-002",
            title="Mountain Cabin",
            nickname="cabin",
            status="active",
            address=None,
            property_type="house",
            room_type="entire_home",
            bedrooms=3,
            bathrooms=2.0,
            timezone="America/Denver",
            check_in_time="16:00",
            check_out_time="10:00",
            tags=(),
            custom_fields=MappingProxyType({}),
        )
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing, new_listing],
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert new_listing.id in coordinator.data
        assert sample_listing.id in coordinator.data

    async def test_disappeared_not_set_on_first_fetch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """No disappeared IDs on first successful fetch."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        await coordinator._async_update_data()
        assert coordinator.disappeared_listing_ids == set()

    async def test_disappeared_ids_preserved_on_api_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """disappeared_listing_ids unchanged after UpdateFailed."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(
            return_value=[sample_listing],
        )

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First fetch
        await coordinator._async_update_data()

        # Listing disappears
        api_client.get_listings = AsyncMock(return_value=[])
        await coordinator._async_update_data()
        assert sample_listing.id in coordinator.disappeared_listing_ids

        # API error — disappeared set should be unchanged
        api_client.get_listings = AsyncMock(
            side_effect=GuestyResponseError("bad response"),
        )
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

        assert sample_listing.id in coordinator.disappeared_listing_ids


class TestReservationsCoordinator:
    """Tests for ReservationsCoordinator._async_update_data."""

    async def test_update_data_returns_dict_keyed_by_listing_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """_async_update_data returns dict keyed by listing_id."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[sample_reservation],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert isinstance(data, dict)
        assert sample_listing.id in data
        assert data[sample_listing.id] == [sample_reservation]
        api_client.get_reservations.assert_awaited_once()

    async def test_groups_reservations_by_listing_id(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Reservations are grouped by listing_id."""
        from datetime import UTC, datetime

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_a = GuestyReservation(
            id="res-a",
            listing_id="listing-001",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        )
        listing_b = GuestyListing(
            id="listing-002",
            title="Mountain Cabin",
            nickname="cabin",
            status="active",
            address=None,
            property_type="house",
            room_type="entire_home",
            bedrooms=3,
            bathrooms=2.0,
            timezone="America/Denver",
            check_in_time="16:00",
            check_out_time="10:00",
            tags=(),
            custom_fields=MappingProxyType({}),
        )
        res_b = GuestyReservation(
            id="res-b",
            listing_id="listing-002",
            status="checked_in",
            check_in=datetime(2025, 8, 2, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 6, 11, 0, 0, tzinfo=UTC),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[res_a, res_b],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {
            sample_listing.id: sample_listing,
            listing_b.id: listing_b,
        }

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert "listing-001" in data
        assert "listing-002" in data
        assert len(data["listing-001"]) == 1
        assert len(data["listing-002"]) == 1

    async def test_filters_unknown_listing_ids(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Reservations for unknown listings are filtered out."""
        from datetime import UTC, datetime

        entry = _make_entry()
        entry.add_to_hass(hass)

        known_res = GuestyReservation(
            id="res-known",
            listing_id="listing-001",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        )
        unknown_res = GuestyReservation(
            id="res-unknown",
            listing_id="unknown-listing",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[known_res, unknown_res],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert "listing-001" in data
        assert "unknown-listing" not in data
        matching = [
            r
            for r in caplog.records
            if r.name == "custom_components.guesty.coordinator"
            and r.levelno == logging.WARNING
            and "unknown-listing" in r.getMessage()
        ]
        assert len(matching) == 1

    async def test_sorts_reservations_by_check_in(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Reservations per listing are sorted by check_in date."""
        from datetime import UTC, datetime

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_late = GuestyReservation(
            id="res-late",
            listing_id="listing-001",
            status="confirmed",
            check_in=datetime(2025, 9, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 9, 5, 11, 0, 0, tzinfo=UTC),
        )
        res_early = GuestyReservation(
            id="res-early",
            listing_id="listing-001",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[res_late, res_early],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        listing_res = data["listing-001"]
        assert listing_res[0].id == "res-early"
        assert listing_res[1].id == "res-late"

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval matches reservation_scan_interval option."""
        entry = _make_entry(
            options={CONF_RESERVATION_SCAN_INTERVAL: 10},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        assert coordinator.update_interval == timedelta(minutes=10)

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval uses default when option not set."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_RESERVATION_SCAN_INTERVAL,
        )

    async def test_raises_update_failed_on_connection_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on connection error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_auth_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on auth error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyAuthError("auth failed"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_response_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on response error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyResponseError("malformed"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_reservation_list_returns_empty_dict(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data returns empty dict for no reservations."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=[])
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert data == {}

    async def test_passes_date_range_to_api(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data passes configured date range to API."""
        from custom_components.guesty.const import (
            CONF_FUTURE_DAYS,
            CONF_PAST_DAYS,
        )

        entry = _make_entry(
            options={CONF_PAST_DAYS: 7, CONF_FUTURE_DAYS: 30},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=[])
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        await coordinator._async_update_data()
        api_client.get_reservations.assert_awaited_once_with(
            past_days=7,
            future_days=30,
        )

    async def test_uses_default_date_range(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data uses default date range when not set."""
        from custom_components.guesty.api.const import (
            DEFAULT_FUTURE_DAYS,
            DEFAULT_PAST_DAYS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=[])
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        await coordinator._async_update_data()
        api_client.get_reservations.assert_awaited_once_with(
            past_days=DEFAULT_PAST_DAYS,
            future_days=DEFAULT_FUTURE_DAYS,
        )

    async def test_empty_listings_skips_all_reservations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty listings data skips all reservations gracefully."""
        from datetime import UTC, datetime

        entry = _make_entry()
        entry.add_to_hass(hass)

        reservation = GuestyReservation(
            id="res-orphan",
            listing_id="listing-orphan",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[reservation],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = None

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        data = await coordinator._async_update_data()
        assert data == {}


class TestReservationsCoordinatorErrorResilience:
    """Tests for US5 — reservations coordinator error resilience (T024)."""

    async def test_raises_update_failed_on_rate_limit_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data raises UpdateFailed on GuestyRateLimitError."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyRateLimitError("rate limited"),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed, match="rate limited"):
            await coordinator._async_update_data()

    async def test_retains_last_known_good_data_after_error(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """Coordinator retains last-known-good data after failure (FR-014)."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[sample_reservation],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert sample_listing.id in coordinator.data

        # API fails during next refresh
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        await coordinator.async_refresh()

        # Last-known-good data retained
        assert coordinator.last_update_success is False
        assert coordinator.data is not None
        assert sample_listing.id in coordinator.data
        assert coordinator.data[sample_listing.id] == [
            sample_reservation,
        ]

    async def test_recovery_updates_data_on_successful_fetch(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """Recovery updates data on next successful fetch."""
        from datetime import UTC, datetime

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=[sample_reservation],
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API error during refresh
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyRateLimitError("rate limited"),
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Recovery with new reservation data
        new_reservation = GuestyReservation(
            id="res-new",
            listing_id="listing-001",
            status="checked_in",
            check_in=datetime(2025, 9, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 9, 5, 11, 0, 0, tzinfo=UTC),
        )
        api_client.get_reservations = AsyncMock(
            return_value=[new_reservation],
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert len(coordinator.data[sample_listing.id]) == 1
        assert coordinator.data[sample_listing.id][0].id == "res-new"

    async def test_error_logged_on_api_failure(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Error logged on API failure with error context."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(return_value=[])
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        # Successful first refresh
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # API failure with specific error context
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyResponseError("malformed JSON body"),
        )
        caplog.clear()
        with caplog.at_level(
            logging.ERROR,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is False
        matching = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "malformed JSON body" in r.getMessage()
        ]
        assert len(matching) >= 1


# ── CustomFieldsDefinitionCoordinator Tests (T016) ─────────────────


class TestCustomFieldsDefinitionCoordinator:
    """Tests for the CustomFieldsDefinitionCoordinator."""

    async def test_update_data_calls_get_definitions(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data calls get_definitions and returns defs."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        result = await coordinator._async_update_data()

        mock_client.get_definitions.assert_awaited_once()
        assert result == definitions
        assert len(result) == 3

    async def test_get_field_returns_matching_definition(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns the matching definition or None."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        assert coordinator.get_field("cf-text-001") is not None
        field = coordinator.get_field("cf-text-001")
        assert field is not None
        assert field.field_id == "cf-text-001"
        assert coordinator.get_field("nonexistent") is None

    async def test_get_fields_for_target_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target filters to listing-applicable."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        listing_fields = coordinator.get_fields_for_target("listing")
        # cf-text-001 (listing) + cf-bool-003 (both)
        assert len(listing_fields) == 2
        ids = {f.field_id for f in listing_fields}
        assert "cf-text-001" in ids
        assert "cf-bool-003" in ids

    async def test_get_fields_for_target_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target filters to reservation-applicable."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        res_fields = coordinator.get_fields_for_target("reservation")
        # cf-num-002 (reservation) + cf-bool-003 (both)
        assert len(res_fields) == 2
        ids = {f.field_id for f in res_fields}
        assert "cf-num-002" in ids
        assert "cf-bool-003" in ids

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval matches CONF_CF_SCAN_INTERVAL option."""
        from custom_components.guesty.const import CONF_CF_SCAN_INTERVAL
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry(
            options={CONF_CF_SCAN_INTERVAL: 30},
        )
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        assert coordinator.update_interval == timedelta(minutes=30)

    async def test_default_update_interval(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Default update_interval matches DEFAULT_CF_SCAN_INTERVAL."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_CF_SCAN_INTERVAL,
        )

    async def test_api_error_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """GuestyApiError raises UpdateFailed."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            side_effect=GuestyApiError("API failure"),
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        with pytest.raises(UpdateFailed, match="API failure"):
            await coordinator._async_update_data()

    async def test_empty_definitions_returns_empty_list(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty definitions returns empty list."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        result = await coordinator._async_update_data()
        assert result == []

    async def test_get_field_with_no_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns None when coordinator has no data."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        # Before first refresh, data is None
        assert coordinator.get_field("anything") is None

    async def test_get_fields_for_target_with_no_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target returns empty when no data."""
        from custom_components.guesty.coordinator import (
            CustomFieldsDefinitionCoordinator,
        )

        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        assert coordinator.get_fields_for_target("listing") == []


class TestFieldDiscovery:
    """Tests for custom field discovery (T028)."""

    async def test_definitions_expose_name_id_type_applicability(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator data has name, field_id, field_type, applicable_to."""
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data is not None
        for defn in coordinator.data:
            assert defn.field_id is not None
            assert defn.name is not None
            assert defn.field_type is not None
            assert isinstance(defn.applicable_to, frozenset)
            assert len(defn.applicable_to) > 0

    async def test_listing_filter_excludes_reservation_only(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('listing') excludes reservation-only."""
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        listing_fields = coordinator.get_fields_for_target("listing")
        ids = {f.field_id for f in listing_fields}
        assert "cf-text-001" in ids
        assert "cf-bool-003" in ids
        # cf-num-002 is reservation-only
        assert "cf-num-002" not in ids

    async def test_reservation_filter_excludes_listing_only(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('reservation') excludes listing-only."""
        from tests.conftest import sample_custom_field_definitions

        definitions = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=definitions,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        res_fields = coordinator.get_fields_for_target("reservation")
        ids = {f.field_id for f in res_fields}
        assert "cf-num-002" in ids
        assert "cf-bool-003" in ids
        # cf-text-001 is listing-only
        assert "cf-text-001" not in ids

    async def test_both_target_appears_in_both_queries(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Field applicable to 'both' appears in both target queries."""
        both_field = GuestyCustomFieldDefinition(
            field_id="cf-both-999",
            name="Shared Field",
            field_type="text",
            applicable_to=frozenset({"listing", "reservation"}),
        )
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=[both_field],
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        listing_ids = {f.field_id for f in coordinator.get_fields_for_target("listing")}
        res_ids = {f.field_id for f in coordinator.get_fields_for_target("reservation")}
        assert "cf-both-999" in listing_ids
        assert "cf-both-999" in res_ids

    async def test_empty_definitions_returns_empty_no_error(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty definitions returns empty list without error."""
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data == []
        assert coordinator.get_fields_for_target("listing") == []
        assert coordinator.get_fields_for_target("reservation") == []


class TestDefinitionRefresh:
    """Tests for custom field definition refresh (T029)."""

    async def test_new_field_appears_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """New field added in Guesty appears after coordinator refresh."""
        from tests.conftest import sample_custom_field_definitions

        original = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=original,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

        new_field = GuestyCustomFieldDefinition(
            field_id="cf-new-004",
            name="New Field",
            field_type="text",
            applicable_to=frozenset({"listing"}),
        )
        updated = [*original, new_field]
        mock_client.get_definitions.return_value = updated
        await coordinator.async_refresh()

        assert len(coordinator.data) == 4
        ids = {d.field_id for d in coordinator.data}
        assert "cf-new-004" in ids

    async def test_deleted_field_removed_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Deleted field removed after coordinator refresh."""
        from tests.conftest import sample_custom_field_definitions

        original = sample_custom_field_definitions()
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(
            return_value=original,
        )
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

        # Remove first field
        reduced = original[1:]
        mock_client.get_definitions.return_value = reduced
        await coordinator.async_refresh()

        assert len(coordinator.data) == 2
        ids = {d.field_id for d in coordinator.data}
        assert "cf-text-001" not in ids

    async def test_update_interval_respects_cf_scan_interval(
        self,
        hass: HomeAssistant,
    ) -> None:
        """update_interval respects configured CONF_CF_SCAN_INTERVAL."""
        mock_client = AsyncMock()
        mock_client.get_definitions = AsyncMock(return_value=[])
        entry = _make_entry(
            options={CONF_CF_SCAN_INTERVAL: 45},
        )
        entry.add_to_hass(hass)

        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=mock_client,
        )
        assert coordinator.update_interval == timedelta(minutes=45)
