# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the config entry diagnostics download (FR-042).

The download is the second of the two diagnostics surfaces the
legacy not-found fallback has to report through: a user asking why
a reservation is still visible, or why a write was refused, needs
to see the fallback counters and the served identifiers without
turning on DEBUG logging.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import RESERVATIONS_API_V3
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    FallbackCounters,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATIONS_API,
    DOMAIN,
)
from custom_components.guesty.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.guesty.identity import (
    IdentityComparison,
    IdentityConflict,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    make_fetch_result,
    make_identity_reservations,
    stub_guesty_network,
)

_PATCH_TEST = "custom_components.guesty.GuestyApiClient.test_connection"
_PATCH_LISTINGS = "custom_components.guesty.GuestyApiClient.get_listings"
_PATCH_RESERVATIONS = "custom_components.guesty.GuestyApiClient.get_reservations"

_WRITE_TARGETS = ("res-001", "res-002")


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Load a config entry with its network calls stubbed.

    Args:
        hass: Home Assistant instance.

    Returns:
        The loaded config entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    entry.add_to_hass(hass)
    with (
        patch(_PATCH_TEST, new_callable=AsyncMock, return_value=True),
        patch(_PATCH_LISTINGS, new_callable=AsyncMock, return_value=[]),
        patch(
            _PATCH_RESERVATIONS,
            new_callable=AsyncMock,
            return_value=make_fetch_result(
                make_identity_reservations(*_WRITE_TARGETS),
            ),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


class TestReservationDiagnostics:
    """The reservations block of the download (T120)."""

    async def test_reports_the_fallback_counters_and_served_ids(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Both the counts and the served identifiers appear."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.last_fallback_counters = FallbackCounters(
            attempted=3,
            served=2,
            succeeded=1,
            confirmed_absent=1,
            failed=1,
            deferred=4,
        )
        coordinator.fallback_served_ids = frozenset({"res-001"})
        coordinator.fallback_sourced_ids = frozenset({"res-001", "res-002"})

        result = await async_get_config_entry_diagnostics(hass, entry)

        fallback: dict[str, Any] = result["reservations"]["fallback"]
        assert fallback["attempted"] == 3
        assert fallback["served"] == 2
        assert fallback["succeeded"] == 1
        assert fallback["confirmed_absent"] == 1
        assert fallback["failed"] == 1
        assert fallback["deferred"] == 4
        assert fallback["served_ids"] == ["res-001"]
        assert fallback["sourced_ids"] == ["res-001", "res-002"]
        assert result["reservations"]["path"] == coordinator.reservations_path
        assert result["reservations"]["continuity"] is not None

    async def test_carries_no_personal_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Identifiers only: no names, contacts or codes."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.fallback_served_ids = frozenset({"res-001"})

        rendered = repr(await async_get_config_entry_diagnostics(hass, entry))

        # Guest lookup *counts* are required by FR-042, so the guard
        # is on guest detail rather than on the word "guest".
        for forbidden in ("CONF-", "@", "full_name", "phone", "email"):
            assert forbidden not in rendered

    async def test_unloaded_entry_reports_nothing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A download must not fail on an unloaded entry."""
        entry = MockConfigEntry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result == {"reservations": {"loaded": False}}


class TestLegacyWriteDiagnostics:
    """Legacy-routed write counters are downloadable (T131)."""

    async def test_counters_appear_in_the_download(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Every operation and outcome is reported by name."""
        entry = await _setup_entry(hass)
        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        coordinator.record_legacy_write("note", "succeeded")
        coordinator.record_legacy_write("status", "failed")
        coordinator.record_legacy_write("custom_field", "refused")

        result = await async_get_config_entry_diagnostics(hass, entry)

        legacy_writes: dict[str, Any] = result["reservations"]["legacy_writes"]
        assert legacy_writes["note_succeeded"] == 1
        assert legacy_writes["status_failed"] == 1
        assert legacy_writes["custom_field_refused"] == 1
        assert legacy_writes["total"] == 3
        assert legacy_writes["routing_available"] is True

    async def test_retired_routing_is_reported(
        self,
        hass: HomeAssistant,
    ) -> None:
        """An operator can see routing has self-disabled."""
        entry = await _setup_entry(hass)
        api_client = hass.data[DOMAIN][entry.entry_id]["api_client"]
        with patch.object(
            type(api_client),
            "legacy_write_routing_available",
            property(lambda _self: False),
        ):
            result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["reservations"]["legacy_writes"]["routing_available"] is False


class TestRefreshDiagnostics:
    """The FR-042 refresh counters in the download (T091)."""

    @staticmethod
    def _reservation(
        reservation_id: str,
        listing_id: str,
        *,
        days_out: int,
        guest_id: str | None = None,
    ) -> GuestyReservation:
        """Build a reservation with one stay segment.

        Args:
            reservation_id: Guesty reservation identifier.
            listing_id: Listing the stay segment names.
            days_out: Check-in offset from now, in days.
            guest_id: Guest identifier to resolve, if any.

        Returns:
            A reservation the coordinator can attribute.
        """
        now = datetime.now(UTC)
        check_in = now + timedelta(days=days_out)
        check_out = check_in + timedelta(days=2)
        return GuestyReservation(
            id=reservation_id,
            listing_id=listing_id,
            status="confirmed",
            check_in=check_in,
            check_out=check_out,
            confirmation_code=f"CONF-{reservation_id}",
            guest=(
                GuestyGuest(full_name="", guest_id=guest_id)
                if guest_id is not None
                else None
            ),
            stay=(
                GuestyStaySegment(
                    listing_id=listing_id,
                    check_in=check_in,
                    check_out=check_out,
                ),
            ),
        )

    async def _diagnose(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> dict[str, Any]:
        """Load an entry, refresh it and download diagnostics.

        Args:
            hass: Home Assistant instance.
            sample_listing: The listing the coordinator tracks.

        Returns:
            The diagnostics mapping of the loaded entry.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        with stub_guesty_network(listings=[sample_listing]):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        self._reservation(
                            "res-ok",
                            "listing-001",
                            days_out=1,
                            guest_id="guest-1",
                        ),
                        self._reservation(
                            "res-unknown",
                            "listing-999",
                            days_out=1,
                        ),
                        self._reservation(
                            "res-far",
                            "listing-001",
                            days_out=400,
                        ),
                    ],
                    pages=2,
                    status_counts=MappingProxyType({"confirmed": 3}),
                    segment_histogram=MappingProxyType({1: 3}),
                ),
            ),
            patch(
                "custom_components.guesty.GuestyApiClient.resolve_guests",
                new_callable=AsyncMock,
                return_value=GuestLookupResult(hits=1, lookups=2, failures=1),
            ),
        ):
            await coordinator.async_refresh()
        return await async_get_config_entry_diagnostics(hass, entry)

    async def test_every_counter_is_reported(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """The block answers why a reservation did not publish."""
        result = await self._diagnose(hass, sample_listing)

        reservations = result["reservations"]
        assert reservations["path"] == RESERVATIONS_API_V3
        assert isinstance(reservations["bootstrap_pages"], int)
        refresh = reservations["refresh"]
        assert refresh["pages"] == 2
        assert refresh["received"] == 3
        assert refresh["attributed"] == 1
        assert refresh["trimmed"] == 1
        assert refresh["skipped"] == 1
        assert refresh["status_counts"] == {"confirmed": 3}
        assert refresh["segment_histogram"] == {1: 3}
        assert refresh["unknown_listings"] == 1
        assert refresh["guest_hits"] == 1
        assert refresh["guest_lookups"] == 2
        assert refresh["guest_failures"] == 1
        assert refresh["identity_ambiguous"] is False

    async def test_an_aborted_refresh_is_what_gets_reported(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """An ambiguous refresh is exactly what needs explaining."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CLIENT_ID: FAKE_CLIENT_ID,
                CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
            },
            options={CONF_RESERVATIONS_API: RESERVATIONS_API_V3},
            unique_id=FAKE_CLIENT_ID,
        )
        entry.add_to_hass(hass)
        with stub_guesty_network(listings=[sample_listing]):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        with (
            patch(
                _PATCH_RESERVATIONS,
                new_callable=AsyncMock,
                return_value=make_fetch_result(
                    [
                        self._reservation("res-a", "listing-001", days_out=1),
                        self._reservation("res-b", "listing-001", days_out=2),
                    ],
                    pages=3,
                ),
            ),
            patch.object(
                coordinator.identity,
                "compare",
                return_value=IdentityComparison(
                    conflicts=(
                        IdentityConflict(
                            known_id="res-old",
                            observed_id="res-new",
                            source="codes",
                        ),
                    ),
                ),
            ),
        ):
            await coordinator.async_refresh()

        refresh = (await async_get_config_entry_diagnostics(hass, entry))[
            "reservations"
        ]["refresh"]
        assert refresh["identity_ambiguous"] is True
        assert refresh["pages"] == 3
        assert refresh["received"] == 2
        assert refresh["attributed"] == 0

    async def test_the_counters_carry_no_personal_data(
        self,
        hass: HomeAssistant,
        sample_listing: GuestyListing,
    ) -> None:
        """No guest detail and no confirmation code appears."""
        rendered = repr(await self._diagnose(hass, sample_listing))

        for forbidden in ("CONF-", "@", "phone", "email", "full_name"):
            assert forbidden not in rendered
