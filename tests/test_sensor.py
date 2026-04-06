# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyListingSensor and GuestyEntity base class."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory
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
    create_custom_field_description,
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


# ── Detail sensor keys for parameterised tests ──────────────────────

DETAIL_SENSOR_KEYS = (
    "name",
    "nickname",
    "address",
    "property_type",
    "room_type",
    "bedrooms",
    "bathrooms",
    "timezone",
    "check_in_time",
    "check_out_time",
)


class TestPropertyDetailSensors:
    """Tests for the 10 property detail sensors (T025)."""

    @pytest.mark.parametrize("key", DETAIL_SENSOR_KEYS)
    def test_entity_category_diagnostic(self, key: str) -> None:
        """All detail sensors have entity_category=DIAGNOSTIC."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == key)
        assert desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_name_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Name sensor returns listing.title."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "name")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.title

    def test_nickname_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Nickname sensor returns listing.nickname."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "nickname")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.nickname

    def test_address_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Address sensor returns listing.address.formatted()."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "address")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sample_listing.address is not None
        assert sensor.native_value == sample_listing.address.formatted()

    def test_address_sensor_none_when_no_address(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Address sensor returns None when listing has no address."""
        listing = GuestyListing(
            id="no-addr",
            title="No Address",
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
        mock_coordinator.data = {"no-addr": listing}
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "address")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="no-addr",
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value is None

    def test_property_type_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Property type sensor returns listing.property_type."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "property_type")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.property_type

    def test_room_type_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Room type sensor returns listing.room_type."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "room_type")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.room_type

    def test_bedrooms_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Bedrooms sensor returns listing.bedrooms."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "bedrooms")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.bedrooms

    def test_bathrooms_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Bathrooms sensor returns listing.bathrooms."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "bathrooms")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.bathrooms

    def test_timezone_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Timezone sensor returns listing.timezone."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "timezone")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.timezone

    def test_check_in_time_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Check-in time sensor returns listing.check_in_time."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "check_in_time")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.check_in_time

    def test_check_out_time_sensor_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Check-out time sensor returns listing.check_out_time."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "check_out_time")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == sample_listing.check_out_time

    @pytest.mark.parametrize(
        "key",
        [
            "nickname",
            "property_type",
            "room_type",
            "bedrooms",
            "bathrooms",
            "check_in_time",
            "check_out_time",
        ],
    )
    def test_optional_field_none_when_missing(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
        key: str,
    ) -> None:
        """Optional fields return None when absent from listing."""
        listing = GuestyListing(
            id="minimal",
            title="Minimal",
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
        mock_coordinator.data = {"minimal": listing}
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == key)
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="minimal",
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value is None


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


class TestSensorAvailability:
    """Tests for sensor entity availability (T029)."""

    def test_available_true_when_listing_present(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """available returns True when listing in coordinator data."""
        mock_coordinator.disappeared_listing_ids = set()
        mock_coordinator.last_update_success = True
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        assert sensor.available is True

    def test_available_false_when_listing_disappeared(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """available returns False when listing in disappeared set."""
        mock_coordinator.disappeared_listing_ids = {sample_listing.id}
        mock_coordinator.last_update_success = True
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        assert sensor.available is False

    def test_available_false_when_listing_absent(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """available returns False when listing not in data at all."""
        mock_coordinator.disappeared_listing_ids = set()
        mock_coordinator.last_update_success = True
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="nonexistent",
            entry=entry,
            description=desc,
        )

        assert sensor.available is False

    def test_available_false_when_data_none(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """available returns False when coordinator data is None."""
        mock_coordinator.data = None
        mock_coordinator.disappeared_listing_ids = set()
        mock_coordinator.last_update_success = True
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="anything",
            entry=entry,
            description=desc,
        )

        assert sensor.available is False

    def test_available_regained_on_reappear(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Entity regains availability when listing reappears."""
        mock_coordinator.disappeared_listing_ids = {sample_listing.id}
        mock_coordinator.last_update_success = True
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        # Currently unavailable
        assert sensor.available is False

        # Listing reappears: remove from disappeared
        mock_coordinator.disappeared_listing_ids = set()
        assert sensor.available is True

    def test_available_false_when_coordinator_unhealthy(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """available returns False when coordinator update failed."""
        mock_coordinator.disappeared_listing_ids = set()
        mock_coordinator.last_update_success = False
        entry = mock_coordinator.config_entry
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "status")

        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=entry,
            description=desc,
        )

        assert sensor.available is False


class TestTagsSensor:
    """Tests for tags sensor (T032)."""

    def test_tags_description_exists(self) -> None:
        """Tags sensor description in LISTING_SENSOR_DESCRIPTIONS."""
        desc = next(
            (d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "tags"),
            None,
        )
        assert desc is not None

    def test_tags_entity_category_diagnostic(self) -> None:
        """Tags sensor entity_category is DIAGNOSTIC."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "tags")
        assert desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_tags_native_value_comma_separated(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Tags sensor returns comma-separated tag string."""
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "tags")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == "premium, beachfront"

    def test_tags_native_value_empty_string_for_no_tags(
        self,
        hass: HomeAssistant,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Tags sensor returns empty string when no tags."""
        listing = GuestyListing(
            id="no-tags",
            title="No Tags",
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
        mock_coordinator.data = {"no-tags": listing}
        desc = next(d for d in LISTING_SENSOR_DESCRIPTIONS if d.key == "tags")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id="no-tags",
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == ""


class TestCustomFieldSensors:
    """Tests for dynamic custom field sensors (T032)."""

    def test_custom_field_description_unique_id_slug(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Custom field sensor unique_id includes custom_{slug}."""
        desc = create_custom_field_description("region")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.unique_id is not None
        assert "custom_region" in sensor.unique_id

    def test_custom_field_description_entity_category(
        self,
    ) -> None:
        """Custom field sensor entity_category is DIAGNOSTIC."""
        desc = create_custom_field_description("region")
        assert desc.entity_category == EntityCategory.DIAGNOSTIC

    def test_custom_field_native_value(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
        mock_coordinator: AsyncMock,
    ) -> None:
        """Custom field sensor returns the field value."""
        desc = create_custom_field_description("region")
        sensor = GuestyListingSensor(
            coordinator=mock_coordinator,
            listing_id=sample_listing.id,
            entry=mock_coordinator.config_entry,
            description=desc,
        )
        assert sensor.native_value == "southeast"

    def test_custom_field_translation_key(self) -> None:
        """Custom field sensor has listing_custom_field key."""
        desc = create_custom_field_description("region")
        assert desc.translation_key == "listing_custom_field"

    def test_custom_field_name_with_spaces(self) -> None:
        """Custom field with spaces gets slugified key."""
        desc = create_custom_field_description("My Field Name")
        assert desc.key == "custom_my_field_name"

    def test_custom_field_name_set_to_field_name(self) -> None:
        """Custom field description name matches field name."""
        desc = create_custom_field_description("region")
        assert desc.name == "region"

    async def test_no_custom_field_sensors_when_empty(
        self,
        hass: HomeAssistant,
    ) -> None:
        """No custom field sensors when custom_fields is empty."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        listing = GuestyListing(
            id="no-cf",
            title="No Custom Fields",
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
        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = {"no-cf": listing}
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
            """Capture entities.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)

        custom_sensors = [
            e
            for e in added_entities
            if isinstance(e, GuestyListingSensor)
            and e.entity_description.key.startswith("custom_")
        ]
        assert len(custom_sensors) == 0

    async def test_custom_field_sensors_created_in_setup(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """Custom field sensors created during setup."""
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
            """Capture entities.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)

        custom_sensors = [
            e
            for e in added_entities
            if isinstance(e, GuestyListingSensor)
            and e.entity_description.key.startswith("custom_")
        ]
        assert len(custom_sensors) == 1
        assert custom_sensors[0].entity_description.key == "custom_region"

    async def test_multiple_custom_fields_create_multiple_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Multiple custom fields create one sensor each."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        listing = GuestyListing(
            id="multi-cf",
            title="Multi CF",
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
            custom_fields=MappingProxyType({"region": "west", "color": "blue"}),
        )
        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = {"multi-cf": listing}
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
            """Capture entities.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)

        custom_sensors = [
            e
            for e in added_entities
            if isinstance(e, GuestyListingSensor)
            and e.entity_description.key.startswith("custom_")
        ]
        assert len(custom_sensors) == 2
        keys = {s.entity_description.key for s in custom_sensors}
        assert keys == {"custom_region", "custom_color"}

    async def test_custom_field_discovery_on_new_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Custom field sensors created when new listing discovered."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        listing1 = GuestyListing(
            id="listing-a",
            title="Listing A",
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
        coordinator = AsyncMock(spec=DataUpdateCoordinator)
        coordinator.data = {"listing-a": listing1}
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
            """Capture entities.

            Args:
                new_entities: Entities to add.
                update_before_add: Whether to update first.
            """
            added_entities.extend(list(new_entities))

        await async_setup_entry(hass, entry, mock_add_entities)
        initial_count = len(added_entities)

        # New listing with custom fields
        listing2 = GuestyListing(
            id="listing-b",
            title="Listing B",
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
            custom_fields=MappingProxyType({"pool_type": "heated"}),
        )
        coordinator.data = {
            "listing-a": listing1,
            "listing-b": listing2,
        }

        listener_call = coordinator.async_add_listener.call_args
        assert listener_call is not None
        listener_fn = listener_call[0][0]
        listener_fn()

        new_entities = added_entities[initial_count:]
        custom_sensors = [
            e
            for e in new_entities
            if isinstance(e, GuestyListingSensor)
            and e.entity_description.key.startswith("custom_")
        ]
        assert len(custom_sensors) == 1
        assert custom_sensors[0].entity_description.key == "custom_pool_type"
