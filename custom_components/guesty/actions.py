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
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.guesty.api.const import (
    VALID_CALENDAR_OPS,
    VALID_LISTING_STATUSES,
)
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
)
from custom_components.guesty.api.models import ActionResult
from custom_components.guesty.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from custom_components.guesty.api.actions import (
        GuestyActionsClient,
    )

_LOGGER = logging.getLogger(__name__)

# ── Service names ───────────────────────────────────────────────────

SERVICE_ADD_NOTE = "add_reservation_note"
SERVICE_SET_STATUS = "set_listing_status"
SERVICE_CREATE_TASK = "create_task"
SERVICE_SET_CALENDAR = "set_calendar_availability"
SERVICE_UPDATE_FIELD = "update_reservation_custom_field"

_ALL_SERVICES = (
    SERVICE_ADD_NOTE,
    SERVICE_SET_STATUS,
    SERVICE_CREATE_TASK,
    SERVICE_SET_CALENDAR,
    SERVICE_UPDATE_FIELD,
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

_SCHEMA_UPDATE_FIELD = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("field_id"): str,
        vol.Required("value"): str,
        vol.Optional("config_entry_id"): str,
    },
)


# ── Helpers ─────────────────────────────────────────────────────────


def _get_actions_client(
    hass: HomeAssistant,
    call: ServiceCall,
) -> GuestyActionsClient:
    """Resolve the GuestyActionsClient for a service call.

    Uses an explicit config_entry_id when provided, otherwise
    resolves the sole loaded entry automatically.

    Args:
        hass: Home Assistant instance.
        call: The incoming service call.

    Returns:
        The GuestyActionsClient for the resolved entry.

    Raises:
        HomeAssistantError: If resolution fails.
    """
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})

    config_entry_id = call.data.get("config_entry_id")
    if config_entry_id:
        entry_data = domain_data.get(config_entry_id)
        if entry_data is None:
            msg = f"Config entry '{config_entry_id}' not found"
            raise HomeAssistantError(msg)
        client: GuestyActionsClient = entry_data["actions_client"]
        return client

    entries = list(domain_data.keys())
    if len(entries) == 0:
        msg = "No Guesty config entries loaded"
        raise HomeAssistantError(msg)
    if len(entries) > 1:
        msg = "Multiple Guesty config entries found; specify config_entry_id"
        raise HomeAssistantError(msg)

    resolved: GuestyActionsClient = domain_data[entries[0]]["actions_client"]
    return resolved


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
    client = _get_actions_client(hass, call)
    try:
        result = await client.add_reservation_note(
            reservation_id=call.data["reservation_id"],
            note_text=call.data["note_text"],
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


async def _handle_update_field(
    hass: HomeAssistant,
    call: ServiceCall,
) -> dict[str, Any]:
    """Handle guesty.update_reservation_custom_field call.

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
        result = await client.update_reservation_custom_field(
            reservation_id=call.data["reservation_id"],
            custom_field_id=call.data["field_id"],
            value=call.data["value"],
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
        SERVICE_UPDATE_FIELD,
        _SCHEMA_UPDATE_FIELD,
        _handle_update_field,
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
