# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Guesty listings and reservations."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
)
from custom_components.guesty.api.exceptions import GuestyApiError
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyListing,
    GuestyReservation,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.custom_fields import (
        GuestyCustomFieldsClient,
    )

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


class ReservationsCoordinator(
    DataUpdateCoordinator[dict[str, list[GuestyReservation]]],
):
    """Coordinator that fetches Guesty reservations periodically.

    Groups reservations by listing ID and filters out reservations
    for listings not tracked by the ``ListingsCoordinator``. Each
    listing's reservations are sorted by check-in date.

    Attributes:
        api_client: The Guesty API client instance.
        config_entry: The integration config entry.
        listings_coordinator: Reference to the listings coordinator
            for known listing ID validation.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: GuestyApiClient,
        listings_coordinator: ListingsCoordinator,
    ) -> None:
        """Initialize the reservations coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Guesty API client for fetching reservations.
            listings_coordinator: The listings coordinator for
                known listing ID validation.
        """
        self.api_client = api_client
        self.listings_coordinator = listings_coordinator
        interval_minutes = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_reservations",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[str, list[GuestyReservation]]:
        """Fetch reservations and group by listing ID.

        Calls the API client with configured date range parameters,
        groups results by listing ID, filters out reservations for
        unknown listings, and sorts each listing's reservations by
        check-in date.

        Returns:
            Dictionary mapping listing ID to sorted reservation list.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        past_days = self.config_entry.options.get(
            CONF_PAST_DAYS,
            DEFAULT_PAST_DAYS,
        )
        future_days = self.config_entry.options.get(
            CONF_FUTURE_DAYS,
            DEFAULT_FUTURE_DAYS,
        )

        try:
            reservations = await self.api_client.get_reservations(
                past_days=past_days,
                future_days=future_days,
            )
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching reservations: {exc.message}",
            ) from exc

        # Group by listing ID
        grouped: dict[str, list[GuestyReservation]] = defaultdict(list)
        for reservation in reservations:
            grouped[reservation.listing_id].append(reservation)

        # Filter unknown listing IDs (FR-017)
        known_ids = set()
        if self.listings_coordinator.data:
            known_ids = set(self.listings_coordinator.data.keys())

        result: dict[str, list[GuestyReservation]] = {}
        if not known_ids and grouped:
            _LOGGER.debug(
                "No known listings yet; skipping %d reservation(s)",
                len(reservations),
            )
            return result

        for listing_id, listing_reservations in grouped.items():
            if listing_id not in known_ids:
                _LOGGER.warning(
                    "Skipping reservations for unknown listing %s",
                    listing_id,
                )
                continue
            # Sort by check-in date
            result[listing_id] = sorted(
                listing_reservations,
                key=lambda r: r.check_in,
            )

        return result


class CustomFieldsDefinitionCoordinator(
    DataUpdateCoordinator[list[GuestyCustomFieldDefinition]],
):
    """Coordinator that fetches custom field definitions periodically.

    Wraps the custom fields client's ``get_definitions()`` call inside
    a ``DataUpdateCoordinator`` so the service handler can validate
    field IDs and types against cached definitions. The poll interval
    is configurable via options flow.

    Attributes:
        cf_client: The custom fields client instance.
        config_entry: The integration config entry.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cf_client: GuestyCustomFieldsClient,
    ) -> None:
        """Initialize the custom fields definition coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            cf_client: Custom fields client for fetching definitions.
        """
        self.cf_client = cf_client
        interval_minutes = entry.options.get(
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_custom_fields",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> list[GuestyCustomFieldDefinition]:
        """Fetch all custom field definitions.

        Returns:
            List of custom field definitions from the API.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        try:
            return await self.cf_client.get_definitions()
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching custom field definitions: {exc.message}",
            ) from exc

    def get_field(
        self,
        field_id: str,
    ) -> GuestyCustomFieldDefinition | None:
        """Look up a custom field definition by ID.

        Args:
            field_id: The custom field identifier to look up.

        Returns:
            The matching definition, or None if not found.
        """
        if self.data is None:
            return None
        for field in self.data:
            if field.field_id == field_id:
                return field
        return None

    def get_fields_for_target(
        self,
        target_type: str,
    ) -> list[GuestyCustomFieldDefinition]:
        """Filter definitions applicable to a target type.

        Args:
            target_type: Entity type to filter by
                ('listing' or 'reservation').

        Returns:
            List of definitions applicable to the target type.
        """
        if self.data is None:
            return []
        return [f for f in self.data if target_type in f.applicable_to]
