# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty reservation sensor entities."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import (
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


class TestGuestInformation:
    """Tests for US3 — guest information edge cases (T023)."""

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
        assert len(upcoming) == 2
        assert upcoming[0]["guest_name"] == "Future Guest"
        assert upcoming[1]["guest_name"] is None

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
