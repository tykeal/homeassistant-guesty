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

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.models import FallbackCounters
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)
from custom_components.guesty.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    make_fetch_result,
    make_identity_reservations,
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

        for forbidden in ("CONF-", "@", "guest", "phone", "email"):
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
