# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyListingSensor and GuestyEntity base class."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    GuestyListing,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)
from custom_components.guesty.entity import GuestyEntity
from custom_components.guesty.sensor import (
    LISTING_SENSOR_DESCRIPTIONS,
    GuestyListingSensor,
    async_setup_entry,
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


class TestGuestyEntityDeviceInfo:
    """Tests for GuestyEntity.device_info."""

    def test_device_info_identifiers(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info has identifiers={(DOMAIN, listing_id)}."""
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
        )

        info = entity.device_info
        assert info is not None
        assert (DOMAIN, sample_listing.id) in info["identifiers"]

    def test_device_info_name(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info name matches listing.title."""
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
        )

        info = entity.device_info
        assert info is not None
        assert info["name"] == sample_listing.title

    def test_device_info_manufacturer(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info manufacturer is 'Guesty'."""
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
        )

        info = entity.device_info
        assert info is not None
        assert info["manufacturer"] == "Guesty"

    def test_device_info_model_from_property_type(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info model uses listing.property_type."""
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
        )

        info = entity.device_info
        assert info is not None
        assert info["model"] == sample_listing.property_type

    def test_device_info_model_fallback(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info model falls back to 'Listing'."""
        listing = GuestyListing(
            id="no-type",
            title="No Type",
            nickname=None,
            status="active",
            address=None,
            property_type=None,
            room_type=None,
            bedrooms=None,
            bathrooms=None,
            timezone="UTC",
            check_in_time=None,
            check_out_time=None,
            tags=(),
            custom_fields=MappingProxyType({}),
        )
        mock_coordinator.data = {"no-type": listing}
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id="no-type",
            entry=entry,
        )

        info = entity.device_info
        assert info is not None
        assert info["model"] == "Listing"


class TestGuestyListingSensor:
    """Tests for GuestyListingSensor."""

    def test_status_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Status sensor native_value returns listing.status."""
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        assert sensor.native_value == sample_listing.status

    def test_unique_id_format(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """unique_id is {entry_unique_id}_{listing_id}_{key}."""
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        expected = f"{entry.unique_id}_{sample_listing.id}_status"
        assert sensor.unique_id == expected

    def test_entity_category_none_for_status(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Status sensor entity_category is None."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")
        assert desc.entity_category is None


class TestSensorPlatformSetup:
    """Tests for sensor platform async_setup_entry."""

    async def test_setup_creates_sensors_for_listings(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """async_setup_entry creates sensors for each listing."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = {sample_listing.id: sample_listing}
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
        }

        added_entities: list[Entity] = []

        def mock_add_entities(
            new_entities: Iterable[Entity],
            update_before_add: bool = False,
        ) -> None:
            """Capture entities added by the platform.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)

        status_sensors = [
            e
            for e in added_entities
            if isinstance(e, GuestyListingSensor)
            and e.entity_description.key == "status"
        ]
        assert len(status_sensors) == 1


class TestNewListingDiscovery:
    """Tests for new-listing discovery on coordinator update (T016a)."""

    async def test_new_listing_discovered_on_update(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """New listing discovered without reload."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = {sample_listing.id: sample_listing}
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
        }

        added_entities: list[Entity] = []

        def mock_add_entities(
            new_entities: Iterable[Entity],
            update_before_add: bool = False,
        ) -> None:
            """Capture entities added by the platform.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)
        initial_count = len(added_entities)

        # Simulate new listing appearing in coordinator data
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
        coordinator.data = {
            sample_listing.id: sample_listing,
            new_listing.id: new_listing,
        }

        # Call the listener that was registered
        listener_call = coordinator.async_add_listener.call_args
        assert listener_call is not None
        listener_fn = listener_call[0][0]
        listener_fn()

        new_entities = added_entities[initial_count:]
        assert len(new_entities) > 0
        new_ids = {
            e.unique_id
            for e in new_entities
            if isinstance(e, GuestyListingSensor) and e.unique_id is not None
        }
        assert any("listing-002" in uid for uid in new_ids)


class TestEdgeCases:
    """Tests for edge cases: None data, missing listings."""

    def test_entity_device_info_none_when_data_missing(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """device_info returns None when coordinator.data is None."""
        mock_coordinator.data = None
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id="nonexistent",
            entry=entry,
        )

        assert entity.device_info is None

    def test_entity_listing_none_when_data_missing(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """_listing returns None when coordinator.data is None."""
        mock_coordinator.data = None
        entry = mock_coordinator.config_entry

        entity = GuestyEntity(
            coordinator=mock_coordinator,
            listing_id="nonexistent",
            entry=entry,
        )

        assert entity._listing is None

    def test_sensor_native_value_none_when_listing_absent(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """native_value returns None when listing not in data."""
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="nonexistent",
            entry=entry,
            description=desc,
        )

        assert sensor.native_value is None

    async def test_discovery_listener_noop_when_data_none(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Discovery listener does nothing when data is None."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = None
        coordinator.async_add_listener = MagicMock(
            return_value=MagicMock(),
        )

        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
        }

        added_entities: list[Entity] = []

        def mock_add_entities(
            new_entities: Iterable[Entity],
            update_before_add: bool = False,
        ) -> None:
            """Capture entities.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)

        # No entities created since data is None
        assert len(added_entities) == 0

        # Trigger listener with None data
        listener_call = coordinator.async_add_listener.call_args
        assert listener_call is not None
        listener_fn = listener_call[0][0]
        listener_fn()

        # Still no entities since data is still None
        assert len(added_entities) == 0
