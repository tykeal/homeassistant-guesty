# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ListingsCoordinator."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import cast
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyFilterRejectedError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
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
from tests.conftest import (
    FakeTokenStorage,
    make_fetch_result,
    make_listing_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
)


def _rel(days: int, hour: int, minute: int = 0) -> datetime:
    """Build a datetime offset from now, so fixtures stay in window.

    The coordinator trims reservations outside the configured
    instant window, so fixture dates must track the clock.

    Args:
        days: Day offset from today.
        hour: Hour of day, UTC.
        minute: Minute of hour, UTC.

    Returns:
        The offset datetime, in UTC.
    """
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
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


class TestListingsCoordinatorFiltering:
    """Tests for ListingsCoordinator selected-listings filtering."""

    @staticmethod
    def _models_from_dicts(
        dicts: list[dict[str, object]],
    ) -> list[GuestyListing]:
        """Convert multi-listing API dicts to GuestyListing models.

        Args:
            dicts: Raw API listing dictionaries.

        Returns:
            List of parsed GuestyListing instances.
        """
        models: list[GuestyListing] = []
        for d in dicts:
            model = GuestyListing.from_api_dict(d)
            assert model is not None
            models.append(model)
        return models

    async def test_returns_only_selected_listings(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns only selected listings (T005).

        When CONF_SELECTED_LISTINGS is set in options, only listings
        whose IDs appear in that list are included in coordinator
        data.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "lst_tampa_bay"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert set(data.keys()) == {"lst_miami_beach", "lst_tampa_bay"}
        assert len(data) == 2

    async def test_returns_all_when_selected_absent(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns all listings when option absent (T006).

        When CONF_SELECTED_LISTINGS is not present in entry options
        the coordinator must return every listing from the API
        (backward-compatible default).
        """
        models = self._models_from_dicts(multi_listing_dicts)

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        expected_ids = {m.id for m in models}
        assert set(data.keys()) == expected_ids
        assert len(data) == 5

    async def test_ignores_nonexistent_selected_ids(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator silently ignores unknown selected IDs (T007).

        IDs in CONF_SELECTED_LISTINGS that do not appear in the API
        response are silently dropped — no error is raised.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "nonexistent_id"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert set(data.keys()) == {"lst_miami_beach"}
        assert len(data) == 1

    async def test_empty_result_all_selected_missing(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
    ) -> None:
        """Coordinator returns empty dict when all IDs missing (T008).

        When every ID in CONF_SELECTED_LISTINGS is absent from the
        API response, coordinator data is an empty dict with no error.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["nonexistent1", "nonexistent2"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        data = await coordinator._async_update_data()

        assert data == {}

    async def test_filter_does_not_trigger_disappeared(
        self,
        hass: HomeAssistant,
        multi_listing_dicts: list[dict[str, object]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Filtering does not mark unselected listings disappeared.

        Listings still present in the API but excluded by the
        selected-listings filter must not appear in
        ``disappeared_listing_ids`` or produce warnings.
        """
        models = self._models_from_dicts(multi_listing_dicts)
        selected = ["lst_miami_beach", "lst_tampa_bay"]

        entry = _make_entry(
            options={CONF_SELECTED_LISTINGS: selected},
        )
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_listings = AsyncMock(return_value=models)

        coordinator = ListingsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
        )

        # First call sets _previous_listing_ids
        await coordinator._async_update_data()

        # Second call should not mark unselected as disappeared
        with caplog.at_level(logging.WARNING):
            data = await coordinator._async_update_data()

        assert set(data.keys()) == set(selected)
        assert coordinator.disappeared_listing_ids == set()
        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "disappeared" in r.getMessage().lower()
        ]


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
            listing_type="SINGLE",
            bedrooms=3,
            bathrooms=2.0,
            accommodates=6,
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
            return_value=make_fetch_result([sample_reservation]),
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

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_a = GuestyReservation(
            id="res-a",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )
        listing_b = GuestyListing(
            id="listing-002",
            title="Mountain Cabin",
            nickname="cabin",
            status="active",
            address=None,
            property_type="house",
            room_type="entire_home",
            listing_type="SINGLE",
            bedrooms=3,
            bathrooms=2.0,
            accommodates=6,
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
            check_in=_rel(1, 15),
            check_out=_rel(5, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([res_a, res_b]),
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

        entry = _make_entry()
        entry.add_to_hass(hass)

        known_res = GuestyReservation(
            id="res-known",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )
        unknown_res = GuestyReservation(
            id="res-unknown",
            listing_id="unknown-listing",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([known_res, unknown_res]),
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

        entry = _make_entry()
        entry.add_to_hass(hass)

        res_late = GuestyReservation(
            id="res-late",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(31, 15),
            check_out=_rel(35, 11),
        )
        res_early = GuestyReservation(
            id="res-early",
            listing_id="listing-001",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([res_late, res_early]),
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
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
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
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
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
            candidates={},
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
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
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
            candidates={},
        )

    async def test_empty_listings_skips_all_reservations(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty listings data skips all reservations gracefully."""

        entry = _make_entry()
        entry.add_to_hass(hass)

        reservation = GuestyReservation(
            id="res-orphan",
            listing_id="listing-orphan",
            status="confirmed",
            check_in=_rel(0, 15),
            check_out=_rel(4, 11),
        )

        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([reservation]),
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

    async def test_filter_rejection_surfaces_detail(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A rejected filter reaches the user with its detail (FR-008)."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            side_effect=GuestyFilterRejectedError(
                "Guesty rejected the reservation search filters",
                endpoint="/reservations-v3/search",
                status_code=400,
                body_excerpt="unsupported filter checkIn",
            ),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )

        with pytest.raises(UpdateFailed) as excinfo:
            await coordinator._async_update_data()

        detail = str(excinfo.value)
        assert "/reservations-v3/search" in detail
        assert "400" in detail
        assert "unsupported filter checkIn" in detail
        api_client.get_reservations.assert_awaited_once()

    async def test_filter_rejection_retains_last_known_good(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        sample_reservation: GuestyReservation,
    ) -> None:
        """A rejected filter leaves last-known-good data intact."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
        )
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {sample_listing.id: sample_listing}

        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=api_client,
            listings_coordinator=listings_coordinator,
        )
        await coordinator.async_refresh()

        api_client.get_reservations = AsyncMock(
            side_effect=GuestyFilterRejectedError(
                "Guesty rejected the reservation search filters",
                endpoint="/reservations-v3/search",
                status_code=400,
            ),
        )
        await coordinator.async_refresh()

        assert coordinator.last_update_success is False
        assert coordinator.data is not None
        assert coordinator.data[sample_listing.id] == [sample_reservation]

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
            return_value=make_fetch_result([sample_reservation]),
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

        entry = _make_entry()
        entry.add_to_hass(hass)
        api_client = AsyncMock()
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([sample_reservation]),
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
            check_in=_rel(31, 15),
            check_out=_rel(35, 11),
        )
        api_client.get_reservations = AsyncMock(
            return_value=make_fetch_result([new_reservation]),
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
        api_client.get_reservations = AsyncMock(return_value=make_fetch_result([]))
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


# ── T016 + T028 + T029: CustomFieldsDefinitionCoordinator Tests ─────

_SAMPLE_DEFS = [
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


class TestCustomFieldsDefinitionCoordinator:
    """Tests for CustomFieldsDefinitionCoordinator (T016)."""

    async def test_update_data_calls_get_definitions(
        self,
        hass: HomeAssistant,
    ) -> None:
        """_async_update_data calls get_definitions."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data == _SAMPLE_DEFS
        cf_client.get_definitions.assert_awaited_once()

    async def test_definitions_expose_name_id_type_applicability(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Coordinator data has name, field_id, field_type, applicable_to."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data is not None
        for defn in coordinator.data:
            assert defn.field_id is not None
            assert defn.name is not None
            assert defn.field_type is not None
            assert isinstance(defn.applicable_to, frozenset)
            assert len(defn.applicable_to) > 0

    async def test_get_field_returns_matching(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns matching definition."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        field = coordinator.get_field("cf-region")
        assert field is not None
        assert field.name == "Region"

    async def test_get_field_returns_none_not_found(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns None when not found."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.get_field("cf-nonexistent") is None

    async def test_get_field_none_data_returns_none(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_field returns None when data is None."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        # Don't refresh — data is None
        assert coordinator.get_field("cf-region") is None

    async def test_get_fields_for_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('listing') filters correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        listing_fields = coordinator.get_fields_for_target("listing")
        ids = [f.field_id for f in listing_fields]
        assert "cf-region" in ids
        assert "cf-priority" in ids
        assert "cf-door-code" not in ids

    async def test_get_fields_for_reservation(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target('reservation') filters correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        res_fields = coordinator.get_fields_for_target("reservation")
        ids = [f.field_id for f in res_fields]
        assert "cf-door-code" in ids
        assert "cf-priority" in ids
        assert "cf-region" not in ids

    async def test_both_target_appears_in_both(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Field applicable to 'both' appears in both queries."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        listing_ids = [f.field_id for f in coordinator.get_fields_for_target("listing")]
        res_ids = [f.field_id for f in coordinator.get_fields_for_target("reservation")]
        assert "cf-priority" in listing_ids
        assert "cf-priority" in res_ids

    async def test_empty_definitions_returns_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Empty definitions returns empty list without error."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(return_value=[])
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()

        assert coordinator.data == []
        assert coordinator.get_fields_for_target("listing") == []

    async def test_get_fields_none_data_returns_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """get_fields_for_target returns empty when data is None."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.get_fields_for_target("listing") == []

    async def test_update_interval_default(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Update interval defaults to DEFAULT_CF_SCAN_INTERVAL."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_CF_SCAN_INTERVAL,
        )

    async def test_update_interval_from_options(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Update interval respects CONF_CF_SCAN_INTERVAL."""
        entry = _make_entry(
            options={CONF_CF_SCAN_INTERVAL: 30},
        )
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        assert coordinator.update_interval == timedelta(minutes=30)

    async def test_api_error_raises_update_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """GuestyApiError raises UpdateFailed."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            side_effect=GuestyConnectionError("network down"),
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False


class TestCustomFieldDefinitionRefresh:
    """Tests for definition refresh behavior (T028-T029)."""

    async def test_new_field_appears_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """New field added in Guesty appears after refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS[:1],
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 1

        # Simulate new field added
        cf_client.get_definitions.return_value = _SAMPLE_DEFS
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

    async def test_deleted_field_removed_after_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Deleted field removed after coordinator refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        cf_client = AsyncMock()
        cf_client.get_definitions = AsyncMock(
            return_value=_SAMPLE_DEFS,
        )
        coordinator = CustomFieldsDefinitionCoordinator(
            hass=hass,
            entry=entry,
            cf_client=cf_client,
        )
        await coordinator.async_refresh()
        assert len(coordinator.data) == 3

        # Simulate field deleted
        cf_client.get_definitions.return_value = _SAMPLE_DEFS[:1]
        await coordinator.async_refresh()
        assert len(coordinator.data) == 1


# ── Phase 4: attribution, projection, state and trim (T041-T047) ────

_NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


def _listing(listing_id: str) -> GuestyListing:
    """Build a GuestyListing with the given identifier.

    Args:
        listing_id: The listing identifier.

    Returns:
        A parsed GuestyListing for the identifier.
    """
    listing = GuestyListing.from_api_dict(
        make_listing_dict(_id=listing_id),
    )
    assert listing is not None
    return listing


def _seg(
    listing_id: str,
    start_days: float,
    end_days: float,
    *,
    parent: str | None = None,
    guests: int | None = 2,
) -> GuestyStaySegment:
    """Build a stay segment relative to the frozen test instant.

    Args:
        listing_id: Listing the interval belongs to.
        start_days: Days from ``_NOW`` the interval starts.
        end_days: Days from ``_NOW`` the interval ends.
        parent: Parent listing identifier, when any.
        guests: Party size for the interval.

    Returns:
        A GuestyStaySegment for the interval.
    """
    check_in = _NOW + timedelta(days=start_days)
    check_out = _NOW + timedelta(days=end_days)
    return GuestyStaySegment(
        listing_id=listing_id,
        check_in=check_in,
        check_out=check_out,
        parent_listing_id=parent,
        check_in_local=check_in.date().isoformat(),
        check_out_local=check_out.date().isoformat(),
        guests_count=guests,
    )


def _v3_res(
    res_id: str,
    segments: list[GuestyStaySegment],
    *,
    status: str = "confirmed",
    **overrides: object,
) -> GuestyReservation:
    """Build a v3-shaped reservation carrying stay segments.

    Args:
        res_id: Reservation identifier.
        segments: The stay segments of the reservation.
        status: Reservation-level status.
        **overrides: Further reservation fields.

    Returns:
        A GuestyReservation with the segments attached.
    """
    fields: dict[str, object] = {
        "confirmation_code": f"CONF-{res_id}",
        "source": "airbnb",
        "guests_count": segments[0].guests_count,
        "nights_count": 1,
    }
    fields.update(overrides)
    return GuestyReservation(
        id=res_id,
        listing_id=segments[0].listing_id,
        status=status,
        check_in=segments[0].check_in,
        check_out=segments[-1].check_out,
        check_in_local=segments[0].check_in_local,
        check_out_local=segments[-1].check_out_local,
        stay=tuple(segments),
        **fields,  # type: ignore[arg-type]
    )


def _legacy_res(
    res_id: str,
    listing_id: str,
    start_days: float,
    end_days: float,
    *,
    status: str = "confirmed",
) -> GuestyReservation:
    """Build a legacy-shaped reservation without stay segments.

    Args:
        res_id: Reservation identifier.
        listing_id: Listing the reservation belongs to.
        start_days: Days from ``_NOW`` the stay starts.
        end_days: Days from ``_NOW`` the stay ends.
        status: Reservation status.

    Returns:
        A GuestyReservation with an empty ``stay``.
    """
    return GuestyReservation(
        id=res_id,
        listing_id=listing_id,
        status=status,
        check_in=_NOW + timedelta(days=start_days),
        check_out=_NOW + timedelta(days=end_days),
    )


def _build_coordinator(
    hass: HomeAssistant,
    reservations: list[GuestyReservation],
    known_listing_ids: list[str],
    *,
    entry: MockConfigEntry | None = None,
    fetch_counters: dict[str, object] | None = None,
    guest_result: GuestLookupResult | None = None,
    guest_error: Exception | None = None,
) -> ReservationsCoordinator:
    """Build a ReservationsCoordinator over canned reservations.

    Passing ``guest_result`` or ``guest_error`` also selects the v3
    path, because guest lookups exist only there.

    Args:
        hass: Home Assistant instance.
        reservations: Reservations the API returns.
        known_listing_ids: Listing IDs the listings coordinator knows.
        entry: Config entry to use; a default one when None.
        fetch_counters: Diagnostics counters for the fetch result.
        guest_result: Result the guest cache returns, if any.
        guest_error: Error the guest lookup raises, if any.

    Returns:
        A ReservationsCoordinator wired to mocks.
    """
    if entry is None:
        entry = _make_entry()
        entry.add_to_hass(hass)
    api_client = AsyncMock()
    api_client.get_reservations = AsyncMock(
        return_value=make_fetch_result(
            reservations,
            **(fetch_counters or {}),
        ),
    )
    if guest_result is not None or guest_error is not None:
        api_client.reservations_api = RESERVATIONS_API_V3
        api_client.resolve_guests = AsyncMock(
            return_value=guest_result,
            side_effect=guest_error,
        )
    listings_coordinator = AsyncMock()
    listings_coordinator.data = {
        listing_id: _listing(listing_id) for listing_id in known_listing_ids
    }
    return ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=listings_coordinator,
    )


class TestSegmentAttribution:
    """Tests for stay-segment attribution (T041, T045, T046)."""

    async def test_single_segment_attributed_to_its_listing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A single-segment reservation lands on its listing."""
        freezer.move_to(_NOW)
        reservation = _v3_res("res-1", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_two_segments_attributed_to_both_listings(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A mid-stay move is visible on origin and destination."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -2, 0), _seg("listing-002", 0, 3)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert set(data) == {"listing-001", "listing-002"}
        assert data["listing-001"][0].id == "res-move"
        assert data["listing-002"][0].id == "res-move"

    async def test_segment_resolves_through_parent_listing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An unknown sub-unit resolves through its parent."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-sub",
            [_seg("sub-unit-9", -1, 2, parent="listing-001")],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        assert data["listing-001"][0].listing_id == "listing-001"

    async def test_partly_unknown_reservation_is_published(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An unknown segment is logged at debug, not dropped."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-partial",
            [_seg("listing-001", -2, 0), _seg("listing-999", 0, 3)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert list(data) == ["listing-001"]
        debug_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "custom_components.guesty.coordinator"
            and record.levelno == logging.DEBUG
        ]
        assert any("listing-999" in message for message in debug_messages)
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        ]
        assert not any("res-partial" in message for message in warnings)

    async def test_wholly_unknown_reservation_is_skipped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A reservation with no known segment is skipped."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-orphan",
            [_seg("listing-998", -1, 2), _seg("listing-999", 2, 4)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert data == {}
        assert any(
            "res-orphan" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    async def test_reservation_without_stay_uses_listing_id(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A legacy reservation keeps its top-level attribution."""
        freezer.move_to(_NOW)
        reservation = _legacy_res("res-legacy", "listing-001", -1, 2)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"] == [reservation]

    async def test_reservation_without_stay_unknown_is_skipped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A stay-less reservation off a known listing is skipped."""
        freezer.move_to(_NOW)
        reservation = _legacy_res("res-nostay", "listing-999", -1, 2)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.WARNING,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert data == {}
        assert any(
            "res-nostay" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    async def test_alteration_reservation_is_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An alteration never reaches the published data (FR-024)."""
        freezer.move_to(_NOW)
        published = _v3_res("res-ok", [_seg("listing-001", -1, 2)])
        alteration = _v3_res(
            "res-alt",
            [_seg("listing-001", 1, 4)],
            status="alteration",
        )
        coordinator = _build_coordinator(
            hass,
            [published, alteration],
            ["listing-001"],
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-ok"]
        assert any(
            "res-alt" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )
        assert any(
            "alteration_drops=1" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.DEBUG
        )

    async def test_filtered_out_listing_publishes_nothing(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Feature 007 filtering alone decides tracking (FR-014)."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-filtered",
            [_seg("listing-001", -1, 2), _seg("listing-002", 2, 4)],
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-002"],
        )

        data = await coordinator._async_update_data()

        assert list(data) == ["listing-002"]
        fetch = cast("AsyncMock", coordinator.api_client.get_reservations)
        fetch.assert_awaited_once_with(
            past_days=DEFAULT_PAST_DAYS,
            future_days=DEFAULT_FUTURE_DAYS,
            candidates={},
        )


class TestPerListingProjection:
    """Tests for the per-listing projection (T042)."""

    async def test_single_segment_projects_to_itself(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A single-segment projection keeps every own field."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-single",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(full_name="Jane", guest_id="guest-1"),
            custom_fields=MappingProxyType({"cf_a": "A"}),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.id == reservation.id
        assert projection.listing_id == "listing-001"
        assert projection.status == reservation.status
        assert projection.check_in == reservation.check_in
        assert projection.check_out == reservation.check_out
        assert projection.check_in_local == reservation.check_in_local
        assert projection.check_out_local == reservation.check_out_local
        assert projection.guests_count == reservation.guests_count
        assert projection.nights_count == reservation.nights_count
        assert projection.confirmation_code == reservation.confirmation_code
        assert projection.source == reservation.source
        assert projection.guest == reservation.guest
        assert projection.custom_fields == reservation.custom_fields
        assert projection.stay == ()

    async def test_multi_segment_uses_that_listings_segment(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Each listing gets its own dates, guests and nights."""
        freezer.move_to(_NOW)
        first = _seg("listing-001", -2, 0, guests=2)
        second = _seg("listing-002", 0, 3, guests=4)
        reservation = _v3_res(
            "res-multi",
            [first, second],
            status="checked_in",
            guest=GuestyGuest(full_name="Jane", guest_id="guest-1"),
            custom_fields=MappingProxyType({"cf_a": "A"}),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()
        origin = data["listing-001"][0]
        destination = data["listing-002"][0]

        assert origin.check_in == first.check_in
        assert origin.check_out == first.check_out
        assert origin.check_in_local == first.check_in_local
        assert origin.check_out_local == first.check_out_local
        assert origin.guests_count == 2
        assert origin.nights_count == 2
        assert destination.check_in == second.check_in
        assert destination.check_out == second.check_out
        assert destination.guests_count == 4
        assert destination.nights_count == 3
        for projection in (origin, destination):
            assert projection.id == "res-multi"
            assert projection.confirmation_code == "CONF-res-multi"
            assert projection.source == "airbnb"
            assert projection.guest == reservation.guest
            assert projection.custom_fields == reservation.custom_fields
            assert projection.stay == ()

    async def test_repeat_listing_selects_current_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An A → B → A stay keeps A's intervals disjoint."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", -6, -4)
        stay_b = _seg("listing-002", -4, -1)
        second_a = _seg("listing-001", -1, 2)
        reservation = _v3_res(
            "res-aba",
            [first_a, stay_b, second_a],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == second_a.check_in
        assert projection.check_out == second_a.check_out

    async def test_repeat_listing_selects_earliest_future_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """With no current interval the earliest future one wins."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", 2, 4)
        stay_b = _seg("listing-002", 4, 6)
        second_a = _seg("listing-001", 6, 8)
        reservation = _v3_res("res-future", [first_a, stay_b, second_a])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == first_a.check_in
        assert projection.check_out == first_a.check_out

    async def test_repeat_listing_selects_latest_past_interval(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """With only past intervals the latest one wins."""
        freezer.move_to(_NOW)
        first_a = _seg("listing-001", -8, -6)
        stay_b = _seg("listing-002", -6, -4)
        second_a = _seg("listing-001", -4, -2)
        reservation = _v3_res(
            "res-past",
            [first_a, stay_b, second_a],
            status="checked_out",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        projection = (await coordinator._async_update_data())["listing-001"][0]

        assert projection.check_in == second_a.check_in
        assert projection.check_out == second_a.check_out


class TestSegmentAwareState:
    """Tests for segment-aware status derivation (T043)."""

    async def test_before_move_origin_in_destination_upcoming(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Before the move the guest is only in the origin."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -1, 1), _seg("listing-002", 1, 4)],
            status="checked_in",
        )
        occupant = _v3_res(
            "res-occupant",
            [_seg("listing-002", -2, 1)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation, occupant],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "checked_in"
        moving = next(r for r in data["listing-002"] if r.id == "res-move")
        current = next(r for r in data["listing-002"] if r.id == "res-occupant")
        assert moving.status == "confirmed"
        assert current.status == "checked_in"

    async def test_after_move_origin_checked_out(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """After the move the origin is past and destination live."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-move",
            [_seg("listing-001", -4, -1), _seg("listing-002", -1, 2)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "checked_out"
        assert data["listing-002"][0].status == "checked_in"

    async def test_canceled_multi_segment_stays_canceled(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A canceled stay is canceled on every listing it touches."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-canceled",
            [_seg("listing-001", -1, 1), _seg("listing-002", 1, 4)],
            status="canceled",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001", "listing-002"],
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].status == "canceled"
        assert data["listing-002"][0].status == "canceled"


class TestWindowTrim:
    """Tests for the instant-based window trim (T044)."""

    async def test_padded_boundary_results_are_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Results outside the configured window are not published."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        too_old = _v3_res(
            "res-old",
            [_seg("listing-001", -6, -3)],
            status="checked_out",
        )
        too_far = _v3_res("res-far", [_seg("listing-001", 6, 8)])
        inside = _v3_res("res-inside", [_seg("listing-001", -1, 1)])
        coordinator = _build_coordinator(
            hass,
            [too_old, too_far, inside],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-inside"]

    async def test_in_progress_stay_is_never_dropped(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A long in-progress stay survives however old (FR-004)."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        long_stay = _v3_res(
            "res-long",
            [_seg("listing-001", -400, 1)],
            status="checked_in",
        )
        coordinator = _build_coordinator(
            hass,
            [long_stay],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-long"]

    async def test_trim_applies_to_legacy_shaped_reservations(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The trim also narrows the legacy path (FR-002a)."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        too_old = _legacy_res(
            "res-old",
            "listing-001",
            -9,
            -6,
            status="checked_out",
        )
        inside = _legacy_res("res-inside", "listing-001", -1, 1)
        coordinator = _build_coordinator(
            hass,
            [too_old, inside],
            ["listing-001"],
            entry=entry,
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-inside"]


class TestRefreshDiagnostics:
    """Tests for the per-refresh DEBUG diagnostics (T052)."""

    async def test_debug_line_carries_counters_without_guest_data(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The refresh line reports counters and no personal data."""
        freezer.move_to(_NOW)
        entry = _make_entry(
            options={CONF_PAST_DAYS: 2, CONF_FUTURE_DAYS: 5},
        )
        entry.add_to_hass(hass)
        guest = GuestyGuest(
            full_name="Jane Traveller",
            phone="+15551234567",
            email="jane@example.com",
            guest_id="guest-1",
        )
        published = _v3_res(
            "res-ok",
            [_seg("listing-001", -1, 1), _seg("listing-999", 1, 3)],
            guest=guest,
        )
        alteration = _v3_res(
            "res-alt",
            [_seg("listing-001", 1, 2)],
            status="alteration",
        )
        trimmed = _v3_res(
            "res-far",
            [_seg("listing-001", 9, 11)],
        )
        coordinator = _build_coordinator(
            hass,
            [published, alteration, trimmed],
            ["listing-001"],
            entry=entry,
            fetch_counters={
                "pages": 2,
                "status_counts": MappingProxyType(
                    {"confirmed": 2, "alteration": 1},
                ),
                "segment_histogram": MappingProxyType({1: 2, 2: 1}),
            },
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.name == "custom_components.guesty.coordinator"
            and record.levelno == logging.DEBUG
            and "pages=2" in record.getMessage()
        ]
        assert len(lines) == 1
        line = lines[0]
        assert "'confirmed': 2" in line
        assert "segments=" in line
        assert "attributed=1" in line
        assert "partially_attributed=1" in line
        assert "skipped=0" in line
        assert "alteration_drops=1" in line
        assert "trim_drops=1" in line
        for secret in ("Jane Traveller", "+15551234567", "jane@example.com"):
            assert secret not in caplog.text


class TestGuestLookupScope:
    """Tests for which reservations cost a guest lookup (T055)."""

    async def test_only_attributed_reservations_are_looked_up(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """Untracked, dropped and guest-less stays cost nothing."""
        freezer.move_to(_NOW)
        tracked = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        untracked = _v3_res(
            "res-2",
            [_seg("listing-999", -1, 3)],
            guest=GuestyGuest(guest_id="guest-2"),
        )
        altered = _v3_res(
            "res-3",
            [_seg("listing-001", 1, 2)],
            status="alteration",
            guest=GuestyGuest(guest_id="guest-3"),
        )
        far_away = _v3_res(
            "res-4",
            [_seg("listing-001", 400, 402)],
            guest=GuestyGuest(guest_id="guest-4"),
        )
        no_guest = _v3_res("res-5", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [tracked, untracked, altered, far_away, no_guest],
            ["listing-001"],
            guest_result=GuestLookupResult(
                guests={"guest-1": GuestyGuest(guest_id="guest-1")},
            ),
        )

        await coordinator._async_update_data()

        resolve = cast(AsyncMock, coordinator.api_client.resolve_guests)
        resolve.assert_awaited_once()
        assert resolve.await_args is not None
        assert set(resolve.await_args.args[0]) == {"guest-1"}

    async def test_no_lookup_when_no_reservation_has_a_guest(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A refresh with no guest ids issues no lookup at all."""
        freezer.move_to(_NOW)
        reservation = _v3_res("res-1", [_seg("listing-001", -1, 3)])
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(),
        )

        await coordinator._async_update_data()

        cast(
            AsyncMock,
            coordinator.api_client.resolve_guests,
        ).assert_not_awaited()

    async def test_resolved_guest_is_published(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A resolved guest replaces the id-only placeholder."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        resolved = GuestyGuest(
            full_name="Jane Traveller",
            phone="+15551234567",
            email="jane@example.com",
            guest_id="guest-1",
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(guests={"guest-1": resolved}),
        )

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].guest == resolved

    async def test_unresolved_guest_publishes_the_reservation(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """A deferred or missing guest still publishes the stay."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(deferrals=1),
        )

        data = await coordinator._async_update_data()

        published = data["listing-001"][0]
        assert published.id == "res-1"
        assert published.guest == GuestyGuest(guest_id="guest-1")

    async def test_guest_lookup_error_never_fails_the_refresh(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An exception from the cache degrades only the guest."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_error=GuestyConnectionError("offline"),
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_untranslated_guest_error_is_contained(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """An error the client never wraps still cannot escape."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_error=httpx.ReadError("connection reset"),
        )

        data = await coordinator._async_update_data()

        assert [r.id for r in data["listing-001"]] == ["res-1"]

    async def test_legacy_path_issues_no_guest_lookup(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
    ) -> None:
        """The legacy path keeps its embedded guest untouched."""
        freezer.move_to(_NOW)
        embedded = GuestyGuest(
            full_name="Jane Traveller",
            guest_id="guest-1",
        )
        reservation = _legacy_res("res-1", "listing-001", -1, 3)
        reservation = replace(reservation, guest=embedded)
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
        )
        cast(
            AsyncMock,
            coordinator.api_client,
        ).reservations_api = RESERVATIONS_API_LEGACY

        data = await coordinator._async_update_data()

        assert data["listing-001"][0].guest == embedded
        cast(
            AsyncMock,
            coordinator.api_client.resolve_guests,
        ).assert_not_awaited()

    async def test_diagnostics_report_guest_counters(
        self,
        hass: HomeAssistant,
        freezer: FrozenDateTimeFactory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The FR-042 line carries the guest cache counters."""
        freezer.move_to(_NOW)
        reservation = _v3_res(
            "res-1",
            [_seg("listing-001", -1, 3)],
            guest=GuestyGuest(guest_id="guest-1"),
        )
        coordinator = _build_coordinator(
            hass,
            [reservation],
            ["listing-001"],
            guest_result=GuestLookupResult(
                guests={
                    "guest-1": GuestyGuest(
                        full_name="Jane Traveller",
                        phone="+15551234567",
                        email="jane@example.com",
                        guest_id="guest-1",
                    ),
                },
                hits=3,
                lookups=1,
                failures=2,
                deferrals=4,
            ),
        )

        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.coordinator",
        ):
            await coordinator._async_update_data()

        lines = [
            record.getMessage()
            for record in caplog.records
            if "guest_hits=" in record.getMessage()
        ]
        assert len(lines) == 1
        assert "guest_hits=3" in lines[0]
        assert "guest_lookups=1" in lines[0]
        assert "guest_failures=2" in lines[0]
        assert "guest_deferrals=4" in lines[0]
        for secret in ("Jane Traveller", "+15551234567", "jane@example.com"):
            assert secret not in caplog.text


class TestNoFolioOrPaymentsRequests:
    """No financial resource is ever contacted (T088, FR-043)."""

    @staticmethod
    def _client(path: str) -> GuestyApiClient:
        """Build a real API client on one reservation path.

        Args:
            path: The reservation path to select.

        Returns:
            A client whose HTTP calls respx can observe.
        """
        http = httpx.AsyncClient()
        return GuestyApiClient(
            token_manager=GuestyTokenManager(
                client_id="test-client-id",
                client_secret="test-client-secret",
                http_client=http,
                storage=FakeTokenStorage(),
                refresh_buffer=0,
            ),
            http_client=http,
            reservations_api=path,
        )

    async def _refresh(
        self,
        hass: HomeAssistant,
        path: str,
        page: dict[str, object],
        endpoint: str,
    ) -> list[str]:
        """Run one full refresh and report the URLs it requested.

        Args:
            hass: Home Assistant instance.
            path: The reservation path to run.
            page: The reservation page the endpoint answers with.
            endpoint: The reservation endpoint of that path.

        Returns:
            Every URL the refresh requested, as a string.
        """
        entry = _make_entry()
        entry.add_to_hass(hass)
        listings_coordinator = AsyncMock()
        listings_coordinator.data = {"listing-001": _listing("listing-001")}
        coordinator = ReservationsCoordinator(
            hass=hass,
            entry=entry,
            api_client=self._client(path),
            listings_coordinator=listings_coordinator,
        )
        with respx.mock(assert_all_called=False) as router:
            router.post(TOKEN_URL).mock(
                return_value=httpx.Response(200, json=make_token_response()),
            )
            router.get(f"{BASE_URL}{endpoint}").mock(
                return_value=httpx.Response(200, json=page),
            )
            catch_all = router.route().mock(
                return_value=httpx.Response(200, json={}),
            )
            await coordinator._async_update_data()
            assert not catch_all.called
            return [str(call.request.url) for call in router.calls]

    async def test_v3_refresh_touches_no_financial_resource(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The v3 path asks for no folio and no payments."""
        urls = await self._refresh(
            hass,
            RESERVATIONS_API_V3,
            make_v3_search_page([make_v3_reservation("res-001")]),
            RESERVATIONS_V3_SEARCH_ENDPOINT,
        )

        assert urls
        for url in urls:
            assert "guest-folio" not in url
            assert "payments" not in url

    async def test_legacy_refresh_touches_no_financial_resource(
        self,
        hass: HomeAssistant,
    ) -> None:
        """The rollback target asks for nothing extra either."""
        urls = await self._refresh(
            hass,
            RESERVATIONS_API_LEGACY,
            {"results": [], "count": 0, "limit": 100, "skip": 0},
            RESERVATIONS_ENDPOINT,
        )

        assert urls
        for url in urls:
            assert "guest-folio" not in url
            assert "payments" not in url
