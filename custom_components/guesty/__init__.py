# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty integration for Home Assistant.

Provides OAuth 2.0 authenticated access to the Guesty Open API
for property management automation. Implements HATokenStorage
for token persistence across HA restarts and manages the API
client lifecycle.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import DEFAULT_TIMEOUT
from custom_components.guesty.api.custom_fields import GuestyCustomFieldsClient
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyCustomFieldError,
)
from custom_components.guesty.api.messaging import GuestyMessagingClient
from custom_components.guesty.api.models import CachedToken, TokenStorage
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_CUSTOM_FIELD,
)
from custom_components.guesty.coordinator import (
    CustomFieldsDefinitionCoordinator,
    ListingsCoordinator,
    ReservationsCoordinator,
)

_LOGGER = logging.getLogger(__name__)


class HATokenStorage:
    """Home Assistant implementation of TokenStorage.

    Persists token and rate limit data in config_entry.data via
    hass.config_entries.async_update_entry(). Satisfies the
    TokenStorage protocol from the api/ package.

    Attributes:
        _hass: Home Assistant instance.
        _entry: The integration's config entry.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize HATokenStorage.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration instance.
        """
        self._hass = hass
        self._entry = entry

    async def load_token(self) -> CachedToken | None:
        """Load a persisted token from config_entry.data.

        Returns:
            The stored CachedToken, or None if missing or corrupted.
        """
        cached = self._entry.data.get("cached_token")
        if cached is None:
            return None
        try:
            return CachedToken.from_dict(cached)
        except (KeyError, ValueError, TypeError) as exc:
            _LOGGER.warning("Failed to load cached token: %s", exc)
            return None

    async def save_token(self, token: CachedToken) -> None:
        """Persist a token to config_entry.data.

        Args:
            token: The CachedToken to persist.
        """
        new_data: dict[str, Any] = {**self._entry.data}
        new_data["cached_token"] = token.to_dict()
        self._hass.config_entries.async_update_entry(
            self._entry,
            data=new_data,
        )

    async def load_request_count(
        self,
    ) -> tuple[int, datetime | None]:
        """Load the token request counter from config_entry.data.

        Returns:
            Tuple of (count, window_start).
        """
        raw_count = self._entry.data.get("token_request_count", 0)
        try:
            count = int(raw_count)
        except (ValueError, TypeError):  # fmt: skip
            _LOGGER.warning("Invalid token_request_count, resetting")
            count = 0
        window_str = self._entry.data.get("token_window_start")
        window = None
        if window_str is not None:
            try:
                window = datetime.fromisoformat(window_str)
                if window.tzinfo is None:
                    window = window.replace(tzinfo=UTC)
            except (ValueError, TypeError):  # fmt: skip
                _LOGGER.warning("Invalid token_window_start, resetting")
                count = 0

        # Ensure count is consistent with window_start
        if window is None and count > 0:
            _LOGGER.warning("Non-zero count without window_start, resetting")
            count = 0

        return (count, window)

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Persist the token request counter to config_entry.data.

        Args:
            count: Number of token requests in current window.
            window_start: Start time of the current window.
        """
        new_data: dict[str, Any] = {**self._entry.data}
        new_data["token_request_count"] = count
        new_data["token_window_start"] = window_start.isoformat()
        self._hass.config_entries.async_update_entry(
            self._entry,
            data=new_data,
        )


# Verify protocol compliance at type-check time only
if TYPE_CHECKING:
    _check: TokenStorage = HATokenStorage.__new__(HATokenStorage)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up the Guesty integration from a config entry.

    Creates the HTTP client, token manager, API client,
    listings coordinator, and stores them in hass.data for use
    by platforms. Tests the connection and raises
    ConfigEntryNotReady on failure.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.

    Returns:
        True if setup succeeds.

    Raises:
        ConfigEntryNotReady: If the API connection cannot be
            established.
    """
    storage = HATokenStorage(hass, entry)
    http_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    token_manager = GuestyTokenManager(
        client_id=entry.data[CONF_CLIENT_ID],
        client_secret=entry.data[CONF_CLIENT_SECRET],
        http_client=http_client,
        storage=storage,
    )

    # Seed from persisted token if available
    persisted = await storage.load_token()
    if persisted is not None and not persisted.is_expired():
        token_manager.seed_token(persisted)

    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http_client,
    )

    try:
        await api_client.test_connection()
    except GuestyApiError as exc:
        await http_client.aclose()
        raise ConfigEntryNotReady(
            f"Failed to connect to Guesty API: {exc.message}",
        ) from exc

    coordinator = ListingsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await http_client.aclose()
        raise

    reservations_coordinator = ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=coordinator,
    )
    try:
        await reservations_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await http_client.aclose()
        raise

    messaging_client = GuestyMessagingClient(api_client)
    actions_client = GuestyActionsClient(api_client)

    cf_client = GuestyCustomFieldsClient(api_client)
    cf_coordinator = CustomFieldsDefinitionCoordinator(
        hass=hass,
        entry=entry,
        cf_client=cf_client,
    )
    try:
        await cf_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await coordinator.async_shutdown()
        await reservations_coordinator.async_shutdown()
        await http_client.aclose()
        raise

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "http_client": http_client,
        "token_manager": token_manager,
        "api_client": api_client,
        "coordinator": coordinator,
        "reservations_coordinator": reservations_coordinator,
        "messaging_client": messaging_client,
        "cf_client": cf_client,
        "cf_coordinator": cf_coordinator,
        "actions_client": actions_client,
    }

    async def _async_handle_set_custom_field(
        call: ServiceCall,
    ) -> dict[str, Any]:
        """Handle the set_custom_field service call.

        Args:
            call: The service call with target_type, target_id,
                field_id, and value parameters.

        Returns:
            Structured response dict with result status.

        Raises:
            HomeAssistantError: On validation or API errors.
        """
        target_type: str = call.data["target_type"]
        target_id: str = call.data["target_id"]
        field_id: str = call.data["field_id"]
        value: str | int | float | bool = call.data["value"]

        # Find a loaded entry for this service
        entry_data = None
        for _eid, edata in hass.data.get(DOMAIN, {}).items():
            if isinstance(edata, dict) and "cf_coordinator" in edata:
                entry_data = edata
                break

        if entry_data is None:
            raise HomeAssistantError(
                "Guesty integration is not configured or loaded",
            )

        local_cf_coordinator: CustomFieldsDefinitionCoordinator = entry_data[
            "cf_coordinator"
        ]
        local_cf_client: GuestyCustomFieldsClient = entry_data["cf_client"]

        defn = local_cf_coordinator.get_field(field_id)
        if defn is None:
            available = local_cf_coordinator.get_fields_for_target(
                target_type,
            )
            field_list = ", ".join(f.field_id for f in available)
            raise HomeAssistantError(
                f"Custom field '{field_id}' not found. "
                f"Available fields for {target_type}: "
                f"{field_list or 'none'}",
            )

        if target_type not in defn.applicable_to:
            raise HomeAssistantError(
                f"Custom field '{field_id}' is not applicable "
                f"to target type '{target_type}'. Applicable "
                f"types: {', '.join(sorted(defn.applicable_to))}",
            )

        try:
            local_cf_client.validate_value(value, defn.field_type)
        except GuestyCustomFieldError as exc:
            raise HomeAssistantError(str(exc)) from exc

        try:
            result = await local_cf_client.set_field(
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
                value=value,
            )
        except GuestyCustomFieldError as exc:
            ctx = (
                f" (target_type={target_type},"
                f" target_id={target_id},"
                f" field_id={field_id})"
            )
            raise HomeAssistantError(str(exc) + ctx) from exc
        except GuestyApiError as exc:
            raise HomeAssistantError(
                f"Guesty API error: {exc.message}",
            ) from exc

        return {
            "target_type": result.target_type,
            "target_id": result.target_id,
            "field_id": result.field_id,
            "result": "success" if result.success else "failure",
        }

    if not hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CUSTOM_FIELD,
            _async_handle_set_custom_field,
            schema=vol.Schema(
                {
                    vol.Required("target_type"): vol.In(
                        ["listing", "reservation"],
                    ),
                    vol.Required("target_id"): str,
                    vol.Required("field_id"): str,
                    vol.Required("value"): vol.Any(
                        str,
                        int,
                        float,
                        bool,
                    ),
                },
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _async_options_updated(
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Handle options update by reconfiguring coordinators.

        Args:
            hass: Home Assistant instance.
            entry: The config entry that was updated.
        """
        from datetime import timedelta

        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        coordinator.update_interval = timedelta(minutes=interval)

        res_interval = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        reservations_coordinator.update_interval = timedelta(
            minutes=res_interval,
        )

        from custom_components.guesty.const import (
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        )

        cf_interval = entry.options.get(
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        )
        cf_coordinator.update_interval = timedelta(
            minutes=cf_interval,
        )

    entry.async_on_unload(
        entry.add_update_listener(_async_options_updated),
    )

    from custom_components.guesty.actions import async_setup_actions

    await async_setup_actions(hass, entry)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a Guesty config entry.

    Closes the HTTP client and removes hass.data entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        True if unload succeeds.
    """
    from custom_components.guesty.actions import async_unload_actions

    await async_unload_actions(hass, entry)

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )

    if unload_ok:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if data is not None:
            for key in (
                "coordinator",
                "reservations_coordinator",
                "cf_coordinator",
            ):
                coord = data.get(key)
                if coord is not None:
                    await coord.async_shutdown()
            http_client: httpx.AsyncClient = data["http_client"]
            await http_client.aclose()

        # Remove service if no entries remain
        remaining = hass.data.get(DOMAIN, {})
        if not remaining:
            hass.services.async_remove(
                DOMAIN,
                SERVICE_SET_CUSTOM_FIELD,
            )

    return unload_ok
