# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty reservation sensor entities."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
    GuestyGuest,
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


class TestPrioritySelection:
    """Tests for reservation status priority selection logic."""

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
