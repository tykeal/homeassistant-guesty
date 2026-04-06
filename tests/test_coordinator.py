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
