# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config entry diagnostics for the Guesty integration.

Provides the download half of FR-042's two diagnostics surfaces.
The per-refresh DEBUG log is the other half and lives in the
coordinator.

A ``reservations`` block is the only thing reported here for now:
it answers the question the v3 migration actually raises, which is
why a reservation is or is not being published and which resource
generation a write against it would use. The legacy-routed write
counters of FR-062 are reported here and in the per-refresh DEBUG
log, and never as a sensor attribute. Reservation identifiers
appear in it; guest names, contact details and confirmation code
values never do (FR-021, NFR-009).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from custom_components.guesty.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.coordinator import ReservationsCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return the diagnostics download for one config entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being diagnosed.

    Returns:
        A JSON-serializable diagnostics mapping.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return {"reservations": {"loaded": False}}

    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    api_client: GuestyApiClient = entry_data["api_client"]
    baseline = coordinator.identity.baseline
    counters = coordinator.last_fallback_counters
    legacy_writes = coordinator.legacy_write_counters.as_dict()
    legacy_writes["routing_available"] = api_client.legacy_write_routing_available
    return {
        "reservations": {
            "loaded": True,
            "path": coordinator.reservations_path,
            "continuity": baseline.continuity if baseline else None,
            "fallback": {
                "attempted": counters.attempted,
                "served": counters.served,
                "succeeded": counters.succeeded,
                "confirmed_absent": counters.confirmed_absent,
                "failed": counters.failed,
                "deferred": counters.deferred,
                "served_ids": sorted(coordinator.fallback_served_ids),
                "sourced_ids": sorted(coordinator.fallback_sourced_ids),
            },
            "legacy_writes": legacy_writes,
        },
    }
