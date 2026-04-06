# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sensor platform for Guesty listings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.typing import StateType

from custom_components.guesty.api.models import GuestyListing
from custom_components.guesty.const import DOMAIN
from custom_components.guesty.entity import GuestyEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.guesty.coordinator import ListingsCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class GuestyListingSensorEntityDescription(SensorEntityDescription):
    """Describe a Guesty listing sensor with a value function.

    Attributes:
        value_fn: Callable extracting the sensor value from a listing.
    """

    value_fn: Callable[[GuestyListing], StateType]


LISTING_SENSOR_DESCRIPTIONS: tuple[GuestyListingSensorEntityDescription, ...] = (
    GuestyListingSensorEntityDescription(
        key="status",
        translation_key="listing_status",
        entity_category=None,
        value_fn=lambda listing: listing.status,
    ),
    GuestyListingSensorEntityDescription(
        key="name",
        translation_key="listing_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.title,
    ),
    GuestyListingSensorEntityDescription(
        key="nickname",
        translation_key="listing_nickname",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.nickname,
    ),
    GuestyListingSensorEntityDescription(
        key="address",
        translation_key="listing_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: (
            listing.address.formatted() if listing.address else None
        ),
    ),
    GuestyListingSensorEntityDescription(
        key="property_type",
        translation_key="listing_property_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.property_type,
    ),
    GuestyListingSensorEntityDescription(
        key="room_type",
        translation_key="listing_room_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.room_type,
    ),
    GuestyListingSensorEntityDescription(
        key="bedrooms",
        translation_key="listing_bedrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bedrooms,
    ),
    GuestyListingSensorEntityDescription(
        key="bathrooms",
        translation_key="listing_bathrooms",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.bathrooms,
    ),
    GuestyListingSensorEntityDescription(
        key="timezone",
        translation_key="listing_timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.timezone,
    ),
    GuestyListingSensorEntityDescription(
        key="check_in_time",
        translation_key="listing_check_in_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.check_in_time,
    ),
    GuestyListingSensorEntityDescription(
        key="check_out_time",
        translation_key="listing_check_out_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda listing: listing.check_out_time,
    ),
)


class GuestyListingSensor(GuestyEntity, SensorEntity):
    """Sensor entity for a Guesty listing attribute.

    Uses the description's ``value_fn`` to extract the native
    value from the listing in the coordinator's data dict.

    Attributes:
        entity_description: The sensor entity description.
    """

    entity_description: GuestyListingSensorEntityDescription

    def __init__(
        self,
        coordinator: ListingsCoordinator,
        listing_id: str,
        entry: ConfigEntry,
        description: GuestyListingSensorEntityDescription,
    ) -> None:
        """Initialize the listing sensor.

        Args:
            coordinator: The listings coordinator.
            listing_id: The Guesty listing ID.
            entry: The config entry.
            description: The sensor entity description.
        """
        super().__init__(coordinator, listing_id, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{listing_id}_{description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the sensor value from the listing.

        Returns:
            The sensor state value, or None if listing unavailable.
        """
        listing = self._listing
        if listing is None:
            return None
        return self.entity_description.value_fn(listing)

    @property
    def available(self) -> bool:
        """Return True only when the listing is present and not disappeared.

        Combines the parent coordinator health check with listing-level
        presence: the entity is unavailable if the coordinator itself is
        unhealthy, the listing ID is absent from coordinator data, or the
        listing ID is in the coordinator's disappeared set.

        Returns:
            True when the listing is available, False otherwise.
        """
        if self.coordinator.data is None:
            return False
        if self._listing_id in self.coordinator.disappeared_listing_ids:
            return False
        return self._listing_id in self.coordinator.data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Guesty listing sensors from a config entry.

    Creates one sensor per listing per description. Registers a
    coordinator listener for discovering new listings at runtime.

    Args:
        hass: Home Assistant instance.
        entry: The config entry.
        async_add_entities: Callback to add entities.
    """
    coordinator: ListingsCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    known_ids: set[str] = set()

    def _create_sensors(listing_ids: set[str]) -> list[GuestyListingSensor]:
        """Create sensor entities for the given listing IDs.

        Args:
            listing_ids: Set of listing IDs to create sensors for.

        Returns:
            List of new sensor entities.
        """
        entities: list[GuestyListingSensor] = []
        for listing_id in listing_ids:
            for desc in LISTING_SENSOR_DESCRIPTIONS:
                entities.append(
                    GuestyListingSensor(
                        coordinator=coordinator,
                        listing_id=listing_id,
                        entry=entry,
                        description=desc,
                    )
                )
        return entities

    def _on_coordinator_update() -> None:
        """Handle coordinator data updates for new listings."""
        if coordinator.data is None:
            return
        current_ids = set(coordinator.data.keys())
        new_ids = current_ids - known_ids
        if new_ids:
            _LOGGER.debug(
                "Discovered %d new listing(s): %s",
                len(new_ids),
                new_ids,
            )
            known_ids.update(new_ids)
            async_add_entities(_create_sensors(new_ids))

    # Initial entity creation
    if coordinator.data:
        initial_ids = set(coordinator.data.keys())
        known_ids.update(initial_ids)
        async_add_entities(_create_sensors(initial_ids))

    # Register listener for runtime discovery and ensure it is removed
    # when the config entry is unloaded.
    entry.async_on_unload(
        coordinator.async_add_listener(_on_coordinator_update),
    )
