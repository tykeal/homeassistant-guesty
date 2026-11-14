# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Service handlers for Guesty automation actions.

Bridges the HA-independent GuestyActionsClient to Home Assistant's
service infrastructure. Registers five domain-level services via
hass.services.async_register() and translates API exceptions into
HomeAssistantError for clear automation feedback.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.guesty.api.const import (
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    LEGACY_WRITE_FAILED,
    LEGACY_WRITE_NOTE,
    LEGACY_WRITE_REFUSED,
    LEGACY_WRITE_STATUS,
    LEGACY_WRITE_SUCCEEDED,
    VALID_CALENDAR_OPS,
    VALID_LISTING_STATUSES,
    VALID_RESERVATION_STATUSES,
    WRITE_ROUTE_LEGACY,
    WRITE_ROUTE_V3,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
)
from custom_components.guesty.api.legacy_writes import (
    REFUSAL_LEGACY_RETIRED,
)
from custom_components.guesty.api.models import ActionResult
from custom_components.guesty.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.guesty.api.actions import (
        GuestyActionsClient,
    )
    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.coordinator import (
        ReservationsCoordinator,
    )

_LOGGER = logging.getLogger(__name__)

# ── Service names ───────────────────────────────────────────────────

SERVICE_ADD_NOTE = "add_reservation_note"
SERVICE_SET_STATUS = "set_listing_status"
SERVICE_CREATE_TASK = "create_task"
SERVICE_SET_CALENDAR = "set_calendar_availability"
SERVICE_SET_RESERVATION_STATUS = "set_reservation_status"
_ALL_SERVICES = (
    SERVICE_ADD_NOTE,
    SERVICE_SET_STATUS,
    SERVICE_CREATE_TASK,
    SERVICE_SET_CALENDAR,
    SERVICE_SET_RESERVATION_STATUS,
)

# ── Write routing (FR-058) ──────────────────────────────────────────

# The reservation resource generation a write is sent to. These
# are the api/ constants, re-exported so the service layer and the
# client cannot drift apart on the value they exchange.
ROUTE_V3: Final[str] = WRITE_ROUTE_V3
ROUTE_LEGACY: Final[str] = WRITE_ROUTE_LEGACY

REFUSAL_NOT_REACHABLE: Final[str] = (
    "Reservation {reservation_id} is currently being published from "
    "its last known values because it is missing from the "
    "reservations v3 results and the legacy lookup for it has not "
    "succeeded in this session. Its identifier has not been "
    "validated against either resource, so no write is sent. Retry "
    "after the next successful refresh."
)


# ── Voluptuous schemas ──────────────────────────────────────────────

_SCHEMA_ADD_NOTE = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("note_text"): str,
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_STATUS = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("status"): vol.In(
            sorted(VALID_LISTING_STATUSES),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_CREATE_TASK = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("task_title"): str,
        vol.Optional("description"): str,
        vol.Optional("assignee"): str,
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_CALENDAR = vol.Schema(
    {
        vol.Required("listing_id"): str,
        vol.Required("start_date"): str,
        vol.Required("end_date"): str,
        vol.Required("operation"): vol.In(
            sorted(VALID_CALENDAR_OPS),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

_SCHEMA_SET_RESERVATION_STATUS = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("status"): vol.In(
            sorted(VALID_RESERVATION_STATUSES),
        ),
        vol.Optional("config_entry_id"): str,
    },
)

# ── Helpers ─────────────────────────────────────────────────────────


def _get_entry_data(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Resolve the hass.data entry a service call addresses.

    Uses an explicit config_entry_id when provided, otherwise
    resolves the sole loaded entry automatically.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The resolved entry's hass.data mapping.

    Raises:
        HomeAssistantError: If resolution fails.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})

    config_entry_id = call.data.get("config_entry_id")
    if config_entry_id:
        entry_data: dict[str, Any] | None = domain_data.get(config_entry_id)
        if entry_data is None:
            msg = f"Config entry '{config_entry_id}' not found"
            raise HomeAssistantError(msg)
        return entry_data

    entries = list(domain_data.keys())
    if len(entries) == 0:
        msg = "No Guesty config entries loaded"
        raise HomeAssistantError(msg)
    if len(entries) > 1:
        msg = "Multiple Guesty config entries found; specify config_entry_id"
        raise HomeAssistantError(msg)

    resolved: dict[str, Any] = domain_data[entries[0]]
    return resolved


def _get_actions_client(
    hass: HomeAssistant,
    call: ServiceCall,
) -> GuestyActionsClient:
    """Resolve the GuestyActionsClient for a service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The GuestyActionsClient for the resolved entry.

    Raises:
        HomeAssistantError: If resolution fails.
    """
    client: GuestyActionsClient = _get_entry_data(hass, call)["actions_client"]
    return client


def guard_reservation_write(
    entry_data: Mapping[str, Any],
    reservation_id: str,
    action_type: str,
) -> None:
    """Fail a reservation write closed on an unsure identity.

    The guard lives in the Home Assistant service layer so that
    ``api/`` keeps zero Home Assistant imports (NFR-007). It is
    evaluated before the request is built, so a refused write sends
    nothing at all (FR-031c, FR-033).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write would target.
        action_type: The action being refused, for the error.

    Raises:
        GuestyActionError: When the identity cannot be confirmed.
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    refusal = coordinator.identity.write_refusal(reservation_id)
    if refusal is not None:
        raise GuestyActionError(
            refusal,
            target_id=reservation_id,
            action_type=action_type,
        )


def select_reservation_write_route(
    entry_data: Mapping[str, Any],
    reservation_id: str,
    action_type: str,
) -> str:
    """Choose the resource generation a reservation write uses.

    Evaluated **after** ``guard_reservation_write``: identity comes
    first, and routing is only meaningful once the identifier is
    trusted (FR-060).

    The decision is read from the fallback's live served set at the
    moment of the write, never from a value captured at refresh
    time and never from anything the caller supplied. A reservation
    the fallback is serving exists only on the legacy resource
    right now, so a v3 write against it would fail; one that has
    reappeared in v3 since the last refresh is already out of that
    set and routes to v3.

    A target the fallback is publishing from retained values but
    has never successfully looked up in this session selects
    neither route. Its identifier has not been validated by either
    resource this session, and guessing a generation would mean
    writing to whichever one happens to accept the identifier
    (FR-055, FR-058).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write would target.
        action_type: The action being routed, for the error.

    Returns:
        ``ROUTE_LEGACY`` or ``ROUTE_V3``.

    Raises:
        GuestyActionError: When the target is reachable on neither
            resource, or when the legacy generation it would need
            has retired (FR-061).
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    if reservation_id in coordinator.fallback_served_ids:
        api_client: GuestyApiClient = entry_data["api_client"]
        if not api_client.legacy_write_routing_available:
            raise GuestyActionError(
                REFUSAL_LEGACY_RETIRED.format(
                    reservation_id=reservation_id,
                ),
                target_id=reservation_id,
                action_type=action_type,
            )
        return ROUTE_LEGACY
    if reservation_id in coordinator.fallback_sourced_ids:
        raise GuestyActionError(
            REFUSAL_NOT_REACHABLE.format(reservation_id=reservation_id),
            target_id=reservation_id,
            action_type=action_type,
        )
    return ROUTE_V3


def _retired_target(
    entry_data: Mapping[str, Any],
    reservation_id: str,
) -> bool:
    """Report whether only a retired legacy resource held this.

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation a write would target.

    Returns:
        True when the fallback was publishing this reservation and
        the legacy generation has since self-disabled.
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    known = coordinator.fallback_served_ids | coordinator.fallback_sourced_ids
    if reservation_id not in known:
        return False
    api_client: GuestyApiClient = entry_data["api_client"]
    return not api_client.legacy_write_routing_available


async def execute_reservation_write[WriteResultT](
    entry_data: Mapping[str, Any],
    reservation_id: str,
    *,
    operation: str,
    action_type: str,
    write: Callable[[str], Awaitable[WriteResultT]],
) -> WriteResultT:
    """Guard, route, announce and count one reservation write.

    Every reservation write goes through here so the three FR-001
    exceptions stay in one place: the identity guard runs first,
    then the route is chosen, and only a legacy-routed write is
    announced and counted (FR-060, FR-062).

    Args:
        entry_data: The addressed entry's hass.data mapping.
        reservation_id: The reservation the write targets.
        operation: One of ``LEGACY_WRITE_OPERATIONS``, for counting.
        action_type: The service name, for error reporting.
        write: Coroutine function taking the chosen route.

    Returns:
        Whatever the write returned.

    Raises:
        GuestyActionError: When the write is refused before it is
            sent.
    """
    coordinator: ReservationsCoordinator = entry_data["reservations_coordinator"]
    try:
        guard_reservation_write(entry_data, reservation_id, action_type)
    except GuestyActionError as exc:
        # The guard still decides, and still refuses. A refresh
        # after the self-disable can drop the identity evidence for
        # a reservation only the retired legacy resource held, and
        # the bare identity message would not explain why it can
        # never succeed again (FR-061, FR-062).
        if _retired_target(entry_data, reservation_id):
            coordinator.record_legacy_write(operation, LEGACY_WRITE_REFUSED)
            raise GuestyActionError(
                f"{exc.message} "
                + REFUSAL_LEGACY_RETIRED.format(
                    reservation_id=reservation_id,
                ),
                target_id=reservation_id,
                action_type=action_type,
            ) from exc
        raise
    try:
        route = select_reservation_write_route(
            entry_data,
            reservation_id,
            action_type,
        )
    except GuestyActionError:
        coordinator.record_legacy_write(operation, LEGACY_WRITE_REFUSED)
        raise

    if route != ROUTE_LEGACY:
        return await write(route)

    _LOGGER.warning(
        "Sending %s for reservation %s to the legacy Guesty "
        "reservation resource: it is missing from the reservations "
        "v3 results and is currently being served by the "
        "not-found fallback. The legacy resources retire on %s.",
        action_type,
        reservation_id,
        LEGACY_RESERVATIONS_RETIREMENT_DATE,
    )
    try:
        result = await write(route)
    except Exception:
        coordinator.record_legacy_write(operation, LEGACY_WRITE_FAILED)
        raise
    coordinator.record_legacy_write(operation, LEGACY_WRITE_SUCCEEDED)
    return result


def _result_to_dict(result: ActionResult) -> dict[str, Any]:
    """Convert an ActionResult to a service response dictionary.

    Args:
        result: The ActionResult from the actions client.

    Returns:
        Dictionary suitable for service response data.
    """
    response: dict[str, Any] = {
        "success": result.success,
        "target_id": result.target_id,
    }
    if result.error is not None:
        response["error"] = result.error
    return response


# ── Service handlers ────────────────────────────────────────────────


async def _handle_add_note(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.add_reservation_note service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    entry_data = _get_entry_data(hass, call)
    client: GuestyActionsClient = entry_data["actions_client"]
    reservation_id: str = call.data["reservation_id"]
    note_text: str = call.data["note_text"]
    try:
        result = await execute_reservation_write(
            entry_data,
            reservation_id,
            operation=LEGACY_WRITE_NOTE,
            action_type=SERVICE_ADD_NOTE,
            write=lambda route: client.add_reservation_note(
                reservation_id=reservation_id,
                note_text=note_text,
                route=route,
            ),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_status(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_listing_status service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.set_listing_status(
            listing_id=call.data["listing_id"],
            status=call.data["status"],
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_create_task(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.create_task service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.create_task(
            listing_id=call.data["listing_id"],
            task_title=call.data["task_title"],
            description=call.data.get("description"),
            assignee=call.data.get("assignee"),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_calendar(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_calendar_availability service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    client = _get_actions_client(hass, call)
    try:
        result = await client.set_calendar_availability(
            listing_id=call.data["listing_id"],
            start_date=call.data["start_date"],
            end_date=call.data["end_date"],
            operation=call.data["operation"],
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


async def _handle_set_reservation_status(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.set_reservation_status service call.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        ActionResult as a dictionary.

    Raises:
        HomeAssistantError: On API or validation failure.
    """
    entry_data = _get_entry_data(hass, call)
    client: GuestyActionsClient = entry_data["actions_client"]
    reservation_id: str = call.data["reservation_id"]
    status: str = call.data["status"]
    try:
        result = await execute_reservation_write(
            entry_data,
            reservation_id,
            operation=LEGACY_WRITE_STATUS,
            action_type=SERVICE_SET_RESERVATION_STATUS,
            write=lambda route: client.set_reservation_status(
                reservation_id=reservation_id,
                status=status,
                route=route,
            ),
        )
    except (GuestyActionError, GuestyApiError, ValueError) as exc:
        raise HomeAssistantError(str(exc)) from exc
    return _result_to_dict(result)


# ── Lifecycle ───────────────────────────────────────────────────────

_HandlerFn = Callable[
    [HomeAssistant, ServiceCall],
    Coroutine[Any, Any, dict[str, Any]],
]

_SERVICE_DEFS: tuple[
    tuple[str, vol.Schema, _HandlerFn],
    ...,
] = (
    (SERVICE_ADD_NOTE, _SCHEMA_ADD_NOTE, _handle_add_note),
    (SERVICE_SET_STATUS, _SCHEMA_SET_STATUS, _handle_set_status),
    (
        SERVICE_CREATE_TASK,
        _SCHEMA_CREATE_TASK,
        _handle_create_task,
    ),
    (
        SERVICE_SET_CALENDAR,
        _SCHEMA_SET_CALENDAR,
        _handle_set_calendar,
    ),
    (
        SERVICE_SET_RESERVATION_STATUS,
        _SCHEMA_SET_RESERVATION_STATUS,
        _handle_set_reservation_status,
    ),
)


async def async_setup_actions(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Register Guesty action services on first entry load.

    Skips registration if services are already present (multi-entry).

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
    """
    if hass.services.has_service(DOMAIN, SERVICE_ADD_NOTE):
        return

    for name, schema, handler in _SERVICE_DEFS:

        async def _make_handler(
            call: ServiceCall,
            _fn: _HandlerFn = handler,
        ) -> dict[str, Any]:
            """Wrap handler with hass binding.

            Args:
                call: The incoming service call.
                _fn: The handler function (captured via default).

            Returns:
                Service response dictionary.
            """
            return await _fn(hass, call)

        hass.services.async_register(
            DOMAIN,
            name,
            _make_handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )

    _LOGGER.debug("Registered %d action services", len(_SERVICE_DEFS))


async def async_unload_actions(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove Guesty action services on last entry unload.

    Called after the entry has been removed from hass.data[DOMAIN],
    so remaining entries are simply len(domain_data).

    Args:
        hass: Home Assistant instance.
        entry: The config entry being unloaded.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    if len(domain_data) > 0:
        return

    for name in _ALL_SERVICES:
        hass.services.async_remove(DOMAIN, name)

    _LOGGER.debug("Removed %d action services", len(_ALL_SERVICES))
