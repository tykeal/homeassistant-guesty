# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Legacy and v3 differential tests for one shared account (T090).

One synthetic account is expressed twice, once in each reservation
generation's payload shape, and published through the real sources
so that the comparison exercises the parsers rather than a mock.
Every entity id, state and attribute key set must match, except for
the three deprecated financial sensors, which are named here
explicitly rather than tolerated by a loose comparison (SC-002,
NFR-003, NFR-004, FR-049).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import respx
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import (
    BASE_URL,
    GUESTS_CRUD_PATH,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATIONS_API,
    DOMAIN,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    make_fetch_result,
    make_guest_dict,
    make_reservation_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
)

_LISTING_ID = "listing-001"
_GUEST_ID = "guest-001"

# The one declared loss of the v3 migration (FR-043 to FR-049).
FINANCIAL_ENTITY_IDS = (
    "sensor.beach_house_reservation_total",
    "sensor.beach_house_reservation_balance",
    "sensor.beach_house_reservation_currency",
)


def _moment(days: int, hour: int) -> datetime:
    """Build an in-window instant relative to now.

    Args:
        days: Day offset from today.
        hour: Hour of day, UTC.

    Returns:
        The offset instant, in UTC.
    """
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )


@dataclass(frozen=True)
class _Booking:
    """One booking of the shared fixture account.

    Attributes:
        id: Guesty reservation identifier.
        status: Reservation status, in legacy spelling.
        code: Confirmation code.
        check_in: Arrival instant.
        check_out: Departure instant.
    """

    id: str
    status: str
    code: str
    check_in: datetime
    check_out: datetime


_BOOKINGS: tuple[_Booking, ...] = (
    _Booking(
        id="res-001",
        status="checked_in",
        code="CONF-AAA",
        check_in=_moment(-1, 15),
        check_out=_moment(2, 11),
    ),
    _Booking(
        id="res-002",
        status="confirmed",
        code="CONF-BBB",
        check_in=_moment(4, 15),
        check_out=_moment(7, 11),
    ),
)


def _legacy_results() -> list[dict[str, Any]]:
    """Express the shared account in the legacy payload shape.

    Returns:
        The ``results`` entries of a legacy reservations page.
    """
    return [
        make_reservation_dict(
            _id=booking.id,
            listingId=_LISTING_ID,
            status=booking.status,
            checkIn=booking.check_in.isoformat(),
            checkOut=booking.check_out.isoformat(),
            checkInDateLocalized=booking.check_in.date().isoformat(),
            checkOutDateLocalized=booking.check_out.date().isoformat(),
            confirmationCode=booking.code,
            guest=make_guest_dict(_id=_GUEST_ID),
        )
        for booking in _BOOKINGS
    ]


def _v3_results() -> list[dict[str, Any]]:
    """Express the same account in the v3 payload shape.

    Returns:
        The ``results`` entries of a v3 search page.
    """
    return [
        make_v3_reservation(
            booking.id,
            status=booking.status,
            guest_id=_GUEST_ID,
            confirmationCode=booking.code,
            segments=[
                make_v3_segment(
                    listingId=_LISTING_ID,
                    checkIn=booking.check_in.isoformat(),
                    checkOut=booking.check_out.isoformat(),
                    checkInDateLocalized=(booking.check_in.date().isoformat()),
                    checkOutDateLocalized=(booking.check_out.date().isoformat()),
                ),
            ],
        )
        for booking in _BOOKINGS
    ]


def _snapshot(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    """Capture every published entity of one config entry.

    Args:
        hass: Home Assistant instance.
        entry: The loaded config entry.

    Returns:
        State and attribute mapping by entity id.
    """
    registry = er.async_get(hass)
    captured: dict[str, Any] = {}
    for registered in er.async_entries_for_config_entry(
        registry,
        entry.entry_id,
    ):
        state = hass.states.get(registered.entity_id)
        captured[registered.entity_id] = {
            "unique_id": registered.unique_id,
            "state": state.state if state else None,
            "attributes": dict(state.attributes) if state else {},
        }
    return captured


async def _publish_on(hass: HomeAssistant, path: str, listing: Any) -> dict[str, Any]:
    """Load the shared account on one path and snapshot it.

    The entry is removed afterwards so that the other path starts
    from an empty entity registry and cannot inherit an entity id.

    Args:
        hass: Home Assistant instance.
        path: The reservation path to publish through.
        listing: The listing the listings coordinator reports.

    Returns:
        The snapshot of every entity the path published.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        options={CONF_RESERVATIONS_API: path},
        unique_id=FAKE_CLIENT_ID,
    )
    entry.add_to_hass(hass)
    legacy_page = {
        "results": _legacy_results(),
        "count": len(_BOOKINGS),
        "limit": 100,
        "skip": 0,
    }
    with (
        respx.mock(assert_all_called=False) as router,
        patch(
            "custom_components.guesty.GuestyApiClient.test_connection",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_account_id",
            new_callable=AsyncMock,
            return_value="account-001",
        ),
        patch(
            "custom_components.guesty.GuestyApiClient.get_listings",
            new_callable=AsyncMock,
            return_value=[listing],
        ),
        patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "custom_components.guesty.identity.LegacyReservationSource",
        ) as bootstrap_source,
    ):
        bootstrap_source.return_value.fetch = AsyncMock(
            return_value=make_fetch_result([]),
        )
        router.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json=make_token_response()),
        )
        router.get(f"{BASE_URL}{RESERVATIONS_ENDPOINT}").mock(
            return_value=httpx.Response(200, json=legacy_page),
        )
        router.get(f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}").mock(
            return_value=httpx.Response(
                200,
                json=make_v3_search_page(_v3_results()),
            ),
        )
        router.get(
            f"{BASE_URL}{GUESTS_CRUD_PATH.format(guest_id=_GUEST_ID)}",
        ).mock(
            return_value=httpx.Response(
                200,
                json=make_guest_dict(_id=_GUEST_ID),
            ),
        )
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        captured = _snapshot(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    return captured


class TestLegacyV3Parity:
    """One account, two generations, one published result."""

    async def test_entity_ids_differ_only_by_the_financial_three(
        self,
        hass: HomeAssistant,
        sample_listing: Any,
    ) -> None:
        """Nothing else is added, renamed or lost (NFR-003)."""
        on_legacy = await _publish_on(
            hass,
            RESERVATIONS_API_LEGACY,
            sample_listing,
        )
        on_v3 = await _publish_on(hass, RESERVATIONS_API_V3, sample_listing)

        assert set(on_legacy) - set(on_v3) == set(FINANCIAL_ENTITY_IDS)
        assert set(on_v3) - set(on_legacy) == set()

    async def test_states_and_attribute_keys_match(
        self,
        hass: HomeAssistant,
        sample_listing: Any,
    ) -> None:
        """Every surviving entity reads the same (SC-002)."""
        on_legacy = await _publish_on(
            hass,
            RESERVATIONS_API_LEGACY,
            sample_listing,
        )
        on_v3 = await _publish_on(hass, RESERVATIONS_API_V3, sample_listing)

        for entity_id, v3_entity in on_v3.items():
            legacy_entity = on_legacy[entity_id]
            assert v3_entity["unique_id"] == legacy_entity["unique_id"]
            assert v3_entity["state"] == legacy_entity["state"], entity_id
            assert set(v3_entity["attributes"]) == set(
                legacy_entity["attributes"],
            ), entity_id

    async def test_reservation_attributes_match_in_value(
        self,
        hass: HomeAssistant,
        sample_listing: Any,
    ) -> None:
        """The reservation sensor publishes the same booking."""
        on_legacy = await _publish_on(
            hass,
            RESERVATIONS_API_LEGACY,
            sample_listing,
        )
        on_v3 = await _publish_on(hass, RESERVATIONS_API_V3, sample_listing)

        entity_id = "sensor.beach_house_reservation_status"
        legacy_attrs = on_legacy[entity_id]["attributes"]
        v3_attrs = on_v3[entity_id]["attributes"]
        for key in (
            "reservation_id",
            "check_in",
            "check_out",
            "check_in_local",
            "check_out_local",
            "confirmation_code",
            "guest_name",
            "guest_phone",
            "guest_email",
            "guests_count",
            "source",
            "listing_id",
            "upcoming_reservations",
        ):
            assert v3_attrs[key] == legacy_attrs[key], key

    async def test_the_financial_three_exist_only_on_legacy(
        self,
        hass: HomeAssistant,
        sample_listing: Any,
    ) -> None:
        """Their ids are still compared, and named here (FR-049)."""
        on_legacy = await _publish_on(
            hass,
            RESERVATIONS_API_LEGACY,
            sample_listing,
        )
        on_v3 = await _publish_on(hass, RESERVATIONS_API_V3, sample_listing)

        for entity_id in FINANCIAL_ENTITY_IDS:
            assert entity_id in on_legacy
            assert entity_id not in on_v3
