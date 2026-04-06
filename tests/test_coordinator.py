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
    GuestyResponseError,
)
from custom_components.guesty.api.models import GuestyListing
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.guesty.coordinator import ListingsCoordinator


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
