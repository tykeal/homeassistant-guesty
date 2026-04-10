# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty reservation sensor entities."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyMoney,
    GuestyReservation,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
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


def _make_reservation(
    *,
    res_id: str = "res-001",
    listing_id: str = "listing-001",
    status: str = "confirmed",
    check_in: datetime | None = None,
    check_out: datetime | None = None,
    **kwargs: object,
) -> GuestyReservation:
    """Create a GuestyReservation with sensible defaults.

    Args:
        res_id: Reservation ID.
        listing_id: Parent listing ID.
        status: Reservation status.
        check_in: Check-in datetime.
        check_out: Check-out datetime.
        **kwargs: Additional reservation fields.

    Returns:
        A GuestyReservation instance.
    """
    return GuestyReservation(
        id=res_id,
        listing_id=listing_id,
        status=status,
        check_in=check_in or datetime(2025, 8, 1, 15, 0, 0, tzinfo=UTC),
        check_out=check_out or datetime(2025, 8, 5, 11, 0, 0, tzinfo=UTC),
        **kwargs,  # type: ignore[arg-type]
    )


class TestReservationSensorEnumConfig:
    """Tests for reservation sensor ENUM device class config."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_device_class_is_enum(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservation sensor has ENUM device class."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.attributes["device_class"] == SensorDeviceClass.ENUM

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_list(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservation sensor options match expected statuses."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        expected = [
            "no_reservation",
            "awaiting_checkin",
            "checked_in",
            "checked_out",
            "canceled",
        ]
        assert state.attributes["options"] == expected

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_includes_unknown_status(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Options dynamically include unknown statuses."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="future_status"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "future_status"
        assert "future_status" in state.attributes["options"]


class TestPrioritySelection:
    """Tests for reservation status priority selection logic."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_checked_in_highest_priority(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """checked_in takes priority over all other statuses."""
        mock_listings.return_value = [sample_listing]

        reservations = [
            _make_reservation(res_id="r1", status="confirmed"),
            _make_reservation(res_id="r2", status="checked_in"),
            _make_reservation(res_id="r3", status="checked_out"),
            _make_reservation(res_id="r4", status="canceled"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_in"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_confirmed_becomes_awaiting_checkin(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """confirmed status maps to awaiting_checkin."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="confirmed"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "awaiting_checkin"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_checked_out_lower_than_awaiting(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """checked_out has lower priority than awaiting_checkin."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="confirmed"),
            _make_reservation(res_id="r2", status="checked_out"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "awaiting_checkin"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_checked_out_only(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Only checked_out reservation shows checked_out state."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="checked_out"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_out"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_canceled_only(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Only canceled reservation shows canceled state."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="canceled"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "canceled"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_canceled_lower_than_checked_out(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """canceled has lower priority than checked_out."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="checked_out"),
            _make_reservation(res_id="r2", status="canceled"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_out"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_reservation_state(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """No reservations shows no_reservation state."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "no_reservation"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unknown_status_passed_through(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Unknown status is passed through as-is (FR-025)."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(res_id="r1", status="future_status"),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "future_status"


class TestSensorAttributes:
    """Tests for reservation sensor extra_state_attributes."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_full_attributes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Sensor exposes all expected attributes."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-full",
            status="checked_in",
            confirmation_code="CONF-123",
            check_in_local="2025-08-01",
            check_out_local="2025-08-05",
            planned_arrival="15:00",
            planned_departure="11:00",
            nights_count=4,
            guests_count=2,
            source="airbnb",
            guest=GuestyGuest(
                full_name="Jane Doe",
                phone="+15559876543",
                email="jane@example.com",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes

        assert attrs["reservation_id"] == "res-full"
        assert attrs["check_in"] == "2025-08-01T15:00:00+00:00"
        assert attrs["check_out"] == "2025-08-05T11:00:00+00:00"
        assert attrs["check_in_local"] == "2025-08-01"
        assert attrs["check_out_local"] == "2025-08-05"
        assert attrs["planned_arrival"] == "15:00"
        assert attrs["planned_departure"] == "11:00"
        assert attrs["guest_name"] == "Jane Doe"
        assert attrs["guest_phone"] == "+15559876543"
        assert attrs["guest_email"] == "jane@example.com"
        assert attrs["confirmation_code"] == "CONF-123"
        assert attrs["guests_count"] == 2
        assert attrs["nights_count"] == 4
        assert attrs["source"] == "airbnb"
        assert attrs["listing_id"] == "listing-001"
        assert "upcoming_reservations" in attrs

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_reservation_attributes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """No reservation shows None attributes."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["reservation_id"] is None
        assert attrs["guest_name"] is None
        assert attrs["listing_id"] == "listing-001"
        assert attrs["upcoming_reservations"] == []

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_upcoming_reservations_limited_to_10(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """upcoming_reservations attribute limited to 10 entries."""
        mock_listings.return_value = [sample_listing]
        # The first is checked_in (selected), the rest are upcoming
        reservations = [
            _make_reservation(res_id="r-active", status="checked_in"),
        ]
        for i in range(15):
            reservations.append(
                _make_reservation(
                    res_id=f"r-upcoming-{i}",
                    status="confirmed",
                    check_in=datetime(
                        2025,
                        9,
                        i + 1,
                        15,
                        0,
                        0,
                        tzinfo=UTC,
                    ),
                    check_out=datetime(
                        2025,
                        9,
                        i + 5,
                        11,
                        0,
                        0,
                        tzinfo=UTC,
                    ),
                ),
            )
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        upcoming = state.attributes["upcoming_reservations"]
        assert len(upcoming) <= 10

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_guest_attributes_when_guest_none(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Missing guest shows None for guest attributes."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="r1",
            status="checked_in",
            guest=None,
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.attributes["guest_name"] is None
        assert state.attributes["guest_phone"] is None
        assert state.attributes["guest_email"] is None


class TestSensorEntitySetup:
    """Tests for reservation sensor entity registration."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_one_status_sensor_per_listing(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """One reservation status sensor created per listing."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unique_id_format(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Unique ID follows {entry_unique_id}_{listing_id}_{key}."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        from homeassistant.helpers import entity_registry as er

        entity_reg = er.async_get(hass)
        entity_entry = entity_reg.async_get(
            "sensor.beach_house_reservation_status",
        )
        assert entity_entry is not None
        expected_uid = "test-client-id_listing-001_reservation_status"
        assert entity_entry.unique_id == expected_uid

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_device_info_links_to_listing(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Sensor device_info links to the listing device."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        from homeassistant.helpers import device_registry as dr

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, "listing-001")},
        )
        assert device is not None


class TestGuestInformation:
    """Tests for US3 — guest information edge cases (T023)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_missing_guest_phone_shows_none(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Guest with no phone shows None for guest_phone."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-no-phone",
            status="checked_in",
            guest=GuestyGuest(
                full_name="Bob Jones",
                phone=None,
                email="bob@example.com",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["guest_name"] == "Bob Jones"
        assert attrs["guest_phone"] is None
        assert attrs["guest_email"] == "bob@example.com"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_missing_guest_email_shows_none(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Guest with no email shows None for guest_email."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-no-email",
            status="checked_in",
            guest=GuestyGuest(
                full_name="Carol White",
                phone="+15551234567",
                email=None,
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["guest_name"] == "Carol White"
        assert attrs["guest_phone"] == "+15551234567"
        assert attrs["guest_email"] is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_upcoming_reservations_include_guest_names(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Upcoming reservation summaries include guest names (FR-009)."""
        mock_listings.return_value = [sample_listing]
        active = _make_reservation(
            res_id="r-active",
            status="checked_in",
            guest=GuestyGuest(full_name="Current Guest"),
        )
        upcoming_with_guest = _make_reservation(
            res_id="r-upcoming-1",
            status="confirmed",
            check_in=datetime(2025, 9, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 9, 5, 11, 0, 0, tzinfo=UTC),
            guest=GuestyGuest(full_name="Future Guest"),
        )
        upcoming_no_guest = _make_reservation(
            res_id="r-upcoming-2",
            status="confirmed",
            check_in=datetime(2025, 10, 1, 15, 0, 0, tzinfo=UTC),
            check_out=datetime(2025, 10, 5, 11, 0, 0, tzinfo=UTC),
            guest=None,
        )
        mock_reservations.return_value = [
            active,
            upcoming_with_guest,
            upcoming_no_guest,
        ]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        upcoming = state.attributes["upcoming_reservations"]
        assert len(upcoming) == 3
        assert upcoming[0]["guest_name"] == "Current Guest"
        assert upcoming[1]["guest_name"] == "Future Guest"
        assert upcoming[2]["guest_name"] is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_partial_guest_data_name_only(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Name-only guest exposes name while phone/email are None."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-name-only",
            status="checked_in",
            guest=GuestyGuest(
                full_name="Dan Brown",
                phone=None,
                email=None,
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["guest_name"] == "Dan Brown"
        assert attrs["guest_phone"] is None
        assert attrs["guest_email"] is None


class TestSensorErrorResilience:
    """Tests for US5 — sensor availability during API failures (T025)."""

    def test_sensor_stale_data_accessible_when_coordinator_fails(
        self,
    ) -> None:
        """Sensor retains data access when coordinator has stale data."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        reservation = _make_reservation(
            res_id="res-stale",
            status="checked_in",
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": [reservation]}
        res_coordinator.last_update_success = False
        listings_coordinator = MagicMock()
        listings_coordinator.data = {"listing-001": MagicMock()}

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )

        # Data is still accessible via _reservations
        assert sensor._reservations == [reservation]
        # But sensor reports unavailable to HA
        assert sensor.available is False

    def test_sensor_recovers_with_fresh_data(
        self,
    ) -> None:
        """Sensors recover to fresh data when API succeeds (US5-S3)."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        old_reservation = _make_reservation(
            res_id="res-old",
            status="checked_in",
        )
        new_reservation = _make_reservation(
            res_id="res-new",
            status="confirmed",
        )

        res_coordinator = MagicMock()
        listings_coordinator = MagicMock()
        listings_coordinator.data = {"listing-001": MagicMock()}

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )

        # Simulate failure: stale data, unavailable
        res_coordinator.data = {"listing-001": [old_reservation]}
        res_coordinator.last_update_success = False
        assert sensor.available is False
        assert sensor.native_value == "checked_in"

        # Simulate recovery: fresh data, available
        res_coordinator.data = {"listing-001": [new_reservation]}
        res_coordinator.last_update_success = True
        assert sensor.available is True
        assert sensor.native_value == "awaiting_checkin"

    def test_unavailable_during_outage_prevents_misfire(
        self,
    ) -> None:
        """Unavailable state during outage prevents automation misfire."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        reservation = _make_reservation(
            res_id="res-stale",
            status="checked_in",
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": [reservation]}
        res_coordinator.last_update_success = False
        listings_coordinator = MagicMock()
        listings_coordinator.data = {"listing-001": MagicMock()}

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )

        # Sensor is unavailable — HA will not use its value
        assert sensor.available is False
        # native_value still computes from stale data
        assert sensor.native_value == "checked_in"
        # extra_state_attributes still populated from stale data
        attrs = sensor.extra_state_attributes
        assert attrs["reservation_id"] == "res-stale"


class TestReservationSensorEdgeCases:
    """Tests for GuestyReservationSensor edge case branches."""

    def test_device_info_none_when_listings_data_none(
        self,
        sample_listing: object,
    ) -> None:
        """device_info returns None when listings coordinator data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        listings_coordinator = MagicMock()
        listings_coordinator.data = None

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor.device_info is None

    def test_device_info_none_when_listing_missing(
        self,
        sample_listing: object,
    ) -> None:
        """device_info returns None when listing not found."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        listings_coordinator = MagicMock()
        listings_coordinator.data = {}

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor.device_info is None

    def test_available_false_when_coordinator_unhealthy(
        self,
    ) -> None:
        """available returns False when coordinator is unhealthy."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": []}
        # CoordinatorEntity.available checks last_update_success
        res_coordinator.last_update_success = False
        listings_coordinator = MagicMock()

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )

        assert sensor.available is False

    def test_reservations_empty_when_data_none(self) -> None:
        """_reservations returns empty list when data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = None
        listings_coordinator = MagicMock()

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor._reservations == []

    def test_available_false_when_listing_disappeared(
        self,
    ) -> None:
        """available returns False when listing is not in listings data."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()
        listings_coordinator.data = {"other-listing": MagicMock()}

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor.available is False

    def test_available_false_when_listings_data_none(
        self,
    ) -> None:
        """available returns False when listings coordinator data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": []}
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()
        listings_coordinator.data = None

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor.available is False

    def test_available_false_when_data_none(
        self,
    ) -> None:
        """available returns False when coordinator data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            GuestyReservationSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = None
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()

        entry = _make_entry()
        sensor = GuestyReservationSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
        )
        assert sensor.available is False


class TestFinancialDiagnosticSensors:
    """Tests for financial diagnostic sensors (US4)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_total_returns_total_paid(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """reservation_total native_value returns money.total_paid."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=1500.50,
                balance_due=200.00,
                currency="USD",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_total",
        )
        assert state is not None
        assert float(state.state) == 1500.50
        assert state.attributes["listing_id"] == "listing-001"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_balance_returns_balance_due(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """reservation_balance returns money.balance_due."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=1500.50,
                balance_due=200.00,
                currency="USD",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_balance",
        )
        assert state is not None
        assert float(state.state) == 200.00

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_reservation_currency_returns_currency(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """reservation_currency returns money.currency."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=1500.50,
                balance_due=200.00,
                currency="USD",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_currency",
        )
        assert state is not None
        assert state.state == "USD"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensors_are_diagnostic(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """All financial sensors have EntityCategory.DIAGNOSTIC."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=100.0,
                balance_due=0.0,
                currency="EUR",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_reg = er.async_get(hass)
        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            entity_id = f"sensor.beach_house_{sensor_key}"
            entity_entry = entity_reg.async_get(entity_id)
            assert entity_entry is not None, f"{entity_id} not found"
            assert entity_entry.entity_category == (EntityCategory.DIAGNOSTIC), (
                f"{entity_id} not DIAGNOSTIC"
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensors_unavailable_no_money(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Financial sensors unavailable when no money data."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-no-fin",
            status="checked_in",
            money=None,
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            state = hass.states.get(
                f"sensor.beach_house_{sensor_key}",
            )
            assert state is not None, f"sensor.beach_house_{sensor_key} missing"
            assert state.state == "unavailable", f"{sensor_key} should be unavailable"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensors_unavailable_no_reservation(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Financial sensors unavailable when no reservation."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            state = hass.states.get(
                f"sensor.beach_house_{sensor_key}",
            )
            assert state is not None, f"sensor.beach_house_{sensor_key} missing"
            assert state.state == "unavailable", f"{sensor_key} should be unavailable"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensors_unavailable_partial_money(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Financial sensors unavailable when field is None."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-partial",
            status="checked_in",
            money=GuestyMoney(
                total_paid=None,
                balance_due=None,
                currency=None,
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            state = hass.states.get(
                f"sensor.beach_house_{sensor_key}",
            )
            assert state is not None, f"sensor.beach_house_{sensor_key} missing"
            assert state.state == "unavailable", f"{sensor_key} should be unavailable"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensor_unique_id_format(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Financial sensor unique_id includes sensor key."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=100.0,
                balance_due=0.0,
                currency="EUR",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_reg = er.async_get(hass)
        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            entity_id = f"sensor.beach_house_{sensor_key}"
            entity_entry = entity_reg.async_get(entity_id)
            assert entity_entry is not None
            expected = f"test-client-id_listing-001_{sensor_key}"
            assert entity_entry.unique_id == expected

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_financial_sensor_device_info(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Financial sensors attach to listing device."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-fin",
            status="checked_in",
            money=GuestyMoney(
                total_paid=100.0,
                balance_due=0.0,
                currency="EUR",
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        from homeassistant.helpers import device_registry as dr

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, "listing-001")},
        )
        assert device is not None

        entity_reg = er.async_get(hass)
        for sensor_key in (
            "reservation_total",
            "reservation_balance",
            "reservation_currency",
        ):
            entity_id = f"sensor.beach_house_{sensor_key}"
            entity_entry = entity_reg.async_get(entity_id)
            assert entity_entry is not None
            assert entity_entry.device_id == device.id

    def test_financial_sensor_device_info_none_data_none(
        self,
    ) -> None:
        """Financial sensor device_info None when data None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        listings_coordinator = MagicMock()
        listings_coordinator.data = None

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.device_info is None

    def test_financial_sensor_device_info_none_missing(
        self,
    ) -> None:
        """Financial sensor device_info None when listing gone."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        listings_coordinator = MagicMock()
        listings_coordinator.data = {}

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.device_info is None

    def test_financial_sensor_available_false_unhealthy(
        self,
    ) -> None:
        """Financial sensor unavailable when coordinator unhealthy."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": []}
        res_coordinator.last_update_success = False
        listings_coordinator = MagicMock()

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.available is False

    def test_financial_sensor_available_false_data_none(
        self,
    ) -> None:
        """Financial sensor unavailable when data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = None
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.available is False

    def test_financial_sensor_available_false_listings_none(
        self,
    ) -> None:
        """Financial sensor unavailable when listings data None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {"listing-001": []}
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()
        listings_coordinator.data = None

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.available is False

    def test_financial_sensor_available_false_listing_gone(
        self,
    ) -> None:
        """Financial sensor unavailable when listing disappeared."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {}
        res_coordinator.last_update_success = True
        listings_coordinator = MagicMock()
        listings_coordinator.data = {"other": MagicMock()}

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.available is False

    def test_native_value_none_when_money_none(
        self,
    ) -> None:
        """native_value returns None when no money data."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = {
            "listing-001": [
                _make_reservation(
                    res_id="r1",
                    status="checked_in",
                    money=None,
                ),
            ],
        }
        listings_coordinator = MagicMock()

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor.native_value is None

    def test_money_none_when_data_none(
        self,
    ) -> None:
        """_money returns None when coordinator data is None."""
        from unittest.mock import MagicMock

        from custom_components.guesty.sensor import (
            RESERVATION_FINANCIAL_DESCRIPTIONS,
            GuestyFinancialSensor,
        )

        res_coordinator = MagicMock()
        res_coordinator.data = None
        listings_coordinator = MagicMock()

        entry = _make_entry()
        desc = RESERVATION_FINANCIAL_DESCRIPTIONS[0]
        sensor = GuestyFinancialSensor(
            coordinator=res_coordinator,
            listings_coordinator=listings_coordinator,
            listing_id="listing-001",
            entry=entry,
            description=desc,
        )
        assert sensor._money is None


class TestEdgeCaseSameDayTurnover:
    """Tests for same-day turnover edge cases (FR-018)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_same_day_turnover_picks_active(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Same-day turnover selects checked_in as primary."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(
                res_id="r-outgoing",
                status="checked_out",
                check_in=datetime(2025, 7, 28, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 8, 1, 11, 0, tzinfo=UTC),
            ),
            _make_reservation(
                res_id="r-incoming",
                status="checked_in",
                check_in=datetime(2025, 8, 1, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 8, 5, 11, 0, tzinfo=UTC),
            ),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_in"
        assert state.attributes["reservation_id"] == "r-incoming"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_same_day_turnover_upcoming_has_others(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Same-day turnover includes all confirmed/checked_in."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(
                res_id="r-active",
                status="checked_in",
                check_in=datetime(2025, 8, 1, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 8, 5, 11, 0, tzinfo=UTC),
            ),
            _make_reservation(
                res_id="r-next",
                status="confirmed",
                check_in=datetime(2025, 8, 5, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 8, 8, 11, 0, tzinfo=UTC),
            ),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_in"
        upcoming = state.attributes["upcoming_reservations"]
        assert len(upcoming) == 2
        assert upcoming[0]["reservation_id"] == "r-active"
        assert upcoming[0]["status"] == "checked_in"
        assert upcoming[1]["reservation_id"] == "r-next"
        assert upcoming[1]["status"] == "confirmed"


class TestEdgeCaseMissingOptionalFields:
    """Tests for missing optional fields handled gracefully (FR-019)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_guest_with_partial_fields(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Guest with phone=None and email=None shows None."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="r1",
            status="checked_in",
            guest=GuestyGuest(
                full_name="Jane Doe",
                phone=None,
                email=None,
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.attributes["guest_name"] == "Jane Doe"
        assert state.attributes["guest_phone"] is None
        assert state.attributes["guest_email"] is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_missing_financial_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservation with money=None is handled gracefully."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="r1",
            status="checked_in",
            money=None,
            note=None,
            confirmation_code=None,
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "checked_in"
        assert state.attributes["confirmation_code"] is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_all_optional_fields_none(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservation with all optional fields None is valid."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="r1",
            status="confirmed",
            guest=None,
            money=None,
            note=None,
            confirmation_code=None,
            check_in_local=None,
            check_out_local=None,
            planned_arrival=None,
            planned_departure=None,
            nights_count=None,
            guests_count=None,
            source=None,
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "awaiting_checkin"
        assert state.attributes["guest_name"] is None
        assert state.attributes["guest_phone"] is None
        assert state.attributes["guest_email"] is None
        assert state.attributes["confirmation_code"] is None
        assert state.attributes["source"] is None


class TestEdgeCaseSingleListingReservations:
    """Tests for reservation edge cases within a single listing."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_listing_without_reservations_shows_no_res(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Listing with zero reservations shows no_reservation."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        assert state.state == "no_reservation"
        assert state.attributes["reservation_id"] is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_multiple_reservations_per_listing(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Multiple reservations: highest priority selected."""
        mock_listings.return_value = [sample_listing]
        reservations = [
            _make_reservation(
                res_id="r-past",
                status="checked_out",
                check_in=datetime(2025, 7, 20, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 7, 25, 11, 0, tzinfo=UTC),
            ),
            _make_reservation(
                res_id="r-active",
                status="confirmed",
                check_in=datetime(2025, 8, 1, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 8, 5, 11, 0, tzinfo=UTC),
            ),
            _make_reservation(
                res_id="r-future",
                status="confirmed",
                check_in=datetime(2025, 9, 1, 15, 0, tzinfo=UTC),
                check_out=datetime(2025, 9, 5, 11, 0, tzinfo=UTC),
            ),
        ]
        mock_reservations.return_value = reservations

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        # confirmed has higher priority than checked_out
        assert state.state == "awaiting_checkin"
        upcoming = state.attributes["upcoming_reservations"]
        # Both confirmed reservations now included
        assert len(upcoming) == 2
        assert upcoming[0]["reservation_id"] == "r-active"
        assert upcoming[1]["reservation_id"] == "r-future"


class TestStateTransitionEvents:
    """Integration tests for state change events (FR-015, T030)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_confirmed_to_checked_in_fires_event(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """confirmed→checked_in transition fires state_changed."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="confirmed",
            ),
        ]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.beach_house_reservation_status"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "awaiting_checkin"

        # Track state change events
        events: list[Event] = []

        @callback
        def _capture(event: Event) -> None:
            """Capture state_changed events for our entity."""
            if event.data.get("entity_id") == entity_id:
                events.append(event)

        hass.bus.async_listen("state_changed", _capture)

        # Simulate transition: guest checks in
        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="checked_in",
            ),
        ]
        coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        await coord.async_refresh()
        await hass.async_block_till_done()

        assert len(events) == 1
        event_data = events[0].data
        assert event_data["old_state"].state == "awaiting_checkin"
        assert event_data["new_state"].state == "checked_in"
        # Verify automation context includes attributes
        new_attrs = event_data["new_state"].attributes
        assert new_attrs["reservation_id"] == "r1"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_checked_in_to_checked_out_fires_event(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """checked_in→checked_out transition fires state_changed."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="checked_in",
            ),
        ]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.beach_house_reservation_status"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "checked_in"

        events: list[Event] = []

        @callback
        def _capture(event: Event) -> None:
            """Capture state_changed events for our entity."""
            if event.data.get("entity_id") == entity_id:
                events.append(event)

        hass.bus.async_listen("state_changed", _capture)

        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="checked_out",
            ),
        ]
        coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        await coord.async_refresh()
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["old_state"].state == "checked_in"
        assert events[0].data["new_state"].state == "checked_out"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_confirmed_to_canceled_fires_event(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """confirmed→canceled transition fires state_changed."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="confirmed",
            ),
        ]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.beach_house_reservation_status"
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "awaiting_checkin"

        events: list[Event] = []

        @callback
        def _capture(event: Event) -> None:
            """Capture state_changed events for our entity."""
            if event.data.get("entity_id") == entity_id:
                events.append(event)

        hass.bus.async_listen("state_changed", _capture)

        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="canceled",
            ),
        ]
        coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        await coord.async_refresh()
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["old_state"].state == "awaiting_checkin"
        assert events[0].data["new_state"].state == "canceled"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_event_context_includes_guest_attrs(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """State change event includes guest context attributes."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="confirmed",
                guest=GuestyGuest(
                    full_name="Alice Smith",
                    phone="+15550001111",
                    email="alice@example.com",
                ),
            ),
        ]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.beach_house_reservation_status"
        events: list[Event] = []

        @callback
        def _capture(event: Event) -> None:
            """Capture state_changed events for our entity."""
            if event.data.get("entity_id") == entity_id:
                events.append(event)

        hass.bus.async_listen("state_changed", _capture)

        mock_reservations.return_value = [
            _make_reservation(
                res_id="r1",
                status="checked_in",
                guest=GuestyGuest(
                    full_name="Alice Smith",
                    phone="+15550001111",
                    email="alice@example.com",
                ),
            ),
        ]
        coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        await coord.async_refresh()
        await hass.async_block_till_done()

        assert len(events) == 1
        new_attrs = events[0].data["new_state"].attributes
        assert new_attrs["guest_name"] == "Alice Smith"
        assert new_attrs["guest_phone"] == "+15550001111"
        assert new_attrs["guest_email"] == "alice@example.com"
        assert new_attrs["reservation_id"] == "r1"


class TestReservationCustomFieldAttributes:
    """Tests for reservation custom_fields in extra_state_attributes."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_custom_fields_in_attributes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservation custom fields appear in attributes."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-cf",
            status="checked_in",
            custom_fields=MappingProxyType(
                {"cf_door_code": "1234", "cf_wifi_pass": "secret"},
            ),
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["custom_fields"] == {
            "cf_door_code": "1234",
            "cf_wifi_pass": "secret",
        }

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_empty_custom_fields_in_attributes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Empty custom fields show empty dict in attributes."""
        mock_listings.return_value = [sample_listing]
        reservation = _make_reservation(
            res_id="res-no-cf",
            status="checked_in",
        )
        mock_reservations.return_value = [reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["custom_fields"] == {}

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_reservation_custom_fields_empty(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """No reservation shows empty custom_fields."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = []

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        state = hass.states.get(
            "sensor.beach_house_reservation_status",
        )
        assert state is not None
        attrs = state.attributes
        assert attrs["custom_fields"] == {}


class TestReservationCFNameResolution:
    """Tests for custom field name resolution in attributes."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_resolved_to_display_names(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Custom field IDs resolved to display names."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_door_code",
                name="Door Code",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="door_code",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-resolve",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {"cf_door_code": "1234"},
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            attrs = state.attributes
            assert attrs["custom_fields"] == {
                "door_code": "1234",
            }

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_unknown_field_uses_id_as_key(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Unknown custom field uses fieldId as key."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_door_code",
                name="Door Code",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="door_code",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-unknown",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {"cf_unknown_field": "mystery"},
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            attrs = state.attributes
            assert attrs["custom_fields"] == {
                "cf_unknown_field": "mystery",
            }

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_falls_back_to_name_when_no_display(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """CF with empty display_name uses name as key."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_region",
                name="Region",
                field_type="text",
                applicable_to=frozenset({"listing"}),
                display_name="",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-nodisp",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {"cf_region": "southeast"},
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            attrs = state.attributes
            assert attrs["custom_fields"] == {
                "Region": "southeast",
            }

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_collision_falls_back_to_field_id(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Duplicate display names fall back to fieldId."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_code_1",
                name="Code",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="code",
                is_public=False,
                is_required=False,
                options=(),
            ),
            GuestyCustomFieldDefinition(
                field_id="cf_code_2",
                name="Code Alt",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="code",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-dup",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {
                        "cf_code_1": "1234",
                        "cf_code_2": "5678",
                    },
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            attrs = state.attributes
            cf = attrs["custom_fields"]
            assert cf["code"] == "1234"
            assert cf["cf_code_2"] == "5678"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_lookup_returns_none_non_dict_entry(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """CF lookup returns None when entry data is not a dict."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_door_code",
                name="Door Code",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="door_code",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-nondict",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {"cf_door_code": "9999"},
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(
                entry.entry_id,
            )
            await hass.async_block_till_done()

            # Save refs before corruption
            original = hass.data[DOMAIN][entry.entry_id]
            res_coord = original["reservations_coordinator"]

            # Corrupt entry data to non-dict
            hass.data[DOMAIN][entry.entry_id] = "bad"

            # Trigger state recalculation
            res_coord.async_set_updated_data(
                {"listing-001": [reservation]},
            )
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            attrs = state.attributes
            # No resolution → raw field IDs as keys
            assert attrs["custom_fields"] == {
                "cf_door_code": "9999",
            }

            # Restore for clean teardown
            hass.data[DOMAIN][entry.entry_id] = original

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_cf_collision_field_id_equals_display(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Field ID matching another display name gets suffix."""
        cf_defs = [
            GuestyCustomFieldDefinition(
                field_id="cf_a",
                name="A",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="code",
                is_public=False,
                is_required=False,
                options=(),
            ),
            GuestyCustomFieldDefinition(
                field_id="code",
                name="Code",
                field_type="text",
                applicable_to=frozenset({"reservation"}),
                display_name="code",
                is_public=False,
                is_required=False,
                options=(),
            ),
        ]
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=cf_defs,
        ):
            mock_listings.return_value = [sample_listing]
            reservation = _make_reservation(
                res_id="res-cf-overlap",
                status="checked_in",
                custom_fields=MappingProxyType(
                    {
                        "cf_a": "AAA",
                        "code": "BBB",
                    },
                ),
            )
            mock_reservations.return_value = [reservation]

            entry = _make_entry()
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(
                entry.entry_id,
            )
            await hass.async_block_till_done()

            state = hass.states.get(
                "sensor.beach_house_reservation_status",
            )
            assert state is not None
            cf = state.attributes["custom_fields"]
            assert cf["code"] == "AAA"
            assert cf["code_1"] == "BBB"
