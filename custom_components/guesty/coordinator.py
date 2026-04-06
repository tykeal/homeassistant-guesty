# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinator for Guesty listings."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.guesty.api.exceptions import GuestyApiError
from custom_components.guesty.api.models import GuestyListing
from custom_components.guesty.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient

_LOGGER = logging.getLogger(__name__)


class ListingsCoordinator(
    DataUpdateCoordinator[dict[str, GuestyListing]],
):
    """Coordinator that fetches Guesty listings periodically.

    Wraps the API client's ``get_listings()`` call inside a
    ``DataUpdateCoordinator`` so sensor entities receive automatic
    updates. The poll interval is configurable via options flow.

    Attributes:
        api_client: The Guesty API client instance.
        config_entry: The integration config entry.
        disappeared_listing_ids: IDs of listings previously present
            but absent in the most recent successful fetch.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: GuestyApiClient,
    ) -> None:
        """Initialize the listings coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Guesty API client for fetching listings.
        """
        self.api_client = api_client
        self.disappeared_listing_ids: set[str] = set()
        self._previous_listing_ids: set[str] | None = None
        interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_listings",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[str, GuestyListing]:
        """Fetch all listings and return as a dict keyed by ID.

        Compares fetched listing IDs against previous data to track
        disappeared listings. IDs present before but absent now are
        added to ``disappeared_listing_ids``; IDs that reappear are
        removed from the set. A warning is logged per disappeared ID.

        Returns:
            Dictionary mapping listing ID to GuestyListing.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        try:
            listings = await self.api_client.get_listings()
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching listings: {exc.message}",
            ) from exc

        new_data = {listing.id: listing for listing in listings}
        current_ids = set(new_data.keys())

        if self._previous_listing_ids is not None:
            newly_disappeared = self._previous_listing_ids - current_ids
            for lid in newly_disappeared:
                _LOGGER.warning(
                    "Listing %s disappeared from API response",
                    lid,
                )
            self.disappeared_listing_ids = (
                self.disappeared_listing_ids | newly_disappeared
            ) - current_ids

        self._previous_listing_ids = current_ids
        return new_data
