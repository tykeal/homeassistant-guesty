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

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
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

SET_CUSTOM_FIELD_SCHEMA = vol.Schema(
    {
        vol.Required("target_type"): vol.In(
            ["listing", "reservation"],
        ),
        vol.Required("target_id"): str,
        vol.Required("field_id"): str,
        vol.Required("value"): vol.Any(str, int, float, bool),
    }
)


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

    Uses Home Assistant's managed HTTP client, creates the token
    manager, API client, coordinators, and stores them in
    hass.data for use by platforms. Tests the connection and
    raises ConfigEntryNotReady on failure.

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
    http_client = get_async_client(hass)

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
        raise ConfigEntryNotReady(
            f"Failed to connect to Guesty API: {exc.message}",
        ) from exc

    coordinator = ListingsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
    )
    await coordinator.async_config_entry_first_refresh()

    reservations_coordinator = ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=coordinator,
    )
    await reservations_coordinator.async_config_entry_first_refresh()

    messaging_client = GuestyMessagingClient(api_client)
    actions_client = GuestyActionsClient(api_client)
    cf_client = GuestyCustomFieldsClient(api_client)

    cf_coordinator = CustomFieldsDefinitionCoordinator(
        hass=hass,
        entry=entry,
        cf_client=cf_client,
    )
    # Custom field definitions are optional — don't block setup
    try:
        await cf_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as exc:
        _LOGGER.warning(
            "Custom field definitions unavailable; "
            "set_custom_field service will not validate "
            "field IDs until definitions load on next "
            "refresh: %s",
            exc,
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "token_manager": token_manager,
        "api_client": api_client,
        "coordinator": coordinator,
        "reservations_coordinator": reservations_coordinator,
        "messaging_client": messaging_client,
        "actions_client": actions_client,
        "cf_client": cf_client,
        "cf_coordinator": cf_coordinator,
    }

    async def _async_handle_set_custom_field(
        call: ServiceCall,
    ) -> ServiceResponse:
        """Handle the guesty.set_custom_field service call.

        Args:
            call: The service call containing target and field data.

        Returns:
            Structured response dict when return_response is True.

        Raises:
            HomeAssistantError: On validation or API failure.
        """
        target_type: str = call.data["target_type"]
        target_id: str = call.data["target_id"]
        field_id: str = call.data["field_id"]
        value: str | int | float | bool = call.data["value"]

        domain_data = hass.data.get(DOMAIN, {})
        if not domain_data:
            raise HomeAssistantError(
                "Guesty integration not loaded",
            )

        if len(domain_data) > 1:
            raise HomeAssistantError(
                "guesty.set_custom_field is ambiguous with multiple config entries",
            )

        entry_data = next(iter(domain_data.values()))

        local_cf_coordinator: CustomFieldsDefinitionCoordinator = entry_data[
            "cf_coordinator"
        ]
        local_cf_client: GuestyCustomFieldsClient = entry_data["cf_client"]

        field_def = local_cf_coordinator.get_field(field_id)
        if field_def is None:
            available = local_cf_coordinator.get_fields_for_target(
                target_type,
            )
            field_names = [f"{f.field_id} ({f.name})" for f in available]
            raise HomeAssistantError(
                f"Field '{field_id}' not found in definitions "
                f"for {target_type}. Available fields: "
                f"{', '.join(field_names) if field_names else 'none'}",
            )

        if target_type not in field_def.applicable_to:
            available = local_cf_coordinator.get_fields_for_target(
                target_type,
            )
            field_names = [f"{f.field_id} ({f.name})" for f in available]
            raise HomeAssistantError(
                f"Field '{field_id}' is not applicable to "
                f"{target_type}. Available fields: "
                f"{', '.join(field_names) if field_names else 'none'}",
            )

        try:
            local_cf_client.validate_value(
                value,
                field_def.field_type,
            )
        except GuestyCustomFieldError as err:
            raise HomeAssistantError(str(err)) from err

        try:
            result = await local_cf_client.set_field(
                target_type=target_type,
                target_id=target_id,
                field_id=field_id,
                value=value,
            )
        except GuestyCustomFieldError as err:
            _LOGGER.error(
                "Custom field update failed for %s",
                target_type,
            )
            raise HomeAssistantError(
                f"Custom field update failed for {err.target_type}/{err.field_id}",
            ) from None
        except GuestyApiError:
            _LOGGER.error(
                "API error during custom field update for %s",
                target_type,
            )
            raise HomeAssistantError(
                f"API error during custom field update for {target_type}",
            ) from None

        if call.return_response:
            return {
                "target_type": result.target_type,
                "target_id": result.target_id,
                "field_id": result.field_id,
                "result": "success",
            }
        return None

    if not hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CUSTOM_FIELD,
            _async_handle_set_custom_field,
            schema=SET_CUSTOM_FIELD_SCHEMA,
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

    Shuts down coordinators and removes hass.data entry.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being unloaded.

    Returns:
        True if unload succeeds.
    """
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

        # Unregister service when no entries remain
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_SET_CUSTOM_FIELD)

        from custom_components.guesty.actions import (
            async_unload_actions,
        )

        await async_unload_actions(hass, entry)

    return unload_ok
