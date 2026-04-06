# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ListingsCoordinator."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from custom_components.guesty.coordinator import ListingsCoordinator
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
