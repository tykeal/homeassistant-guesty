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
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.guesty.actions import guard_reservation_write
from custom_components.guesty.api.actions import GuestyActionsClient
from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
)
from custom_components.guesty.api.custom_fields import GuestyCustomFieldsClient
from custom_components.guesty.api.exceptions import (
    GuestyActionError,
    GuestyApiError,
    GuestyCustomFieldError,
    GuestyMessageError,
)
from custom_components.guesty.api.messaging import GuestyMessagingClient
from custom_components.guesty.api.models import CachedToken, TokenStorage
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_RESERVATIONS_API,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_RESERVATIONS_API,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    PLATFORMS,
    RESERVATIONS_API_LEGACY,
    SERVICE_GET_CUSTOM_FIELD_VALUES,
    SERVICE_GET_CUSTOM_FIELDS,
    SERVICE_SEND_GUEST_MESSAGE,
    SERVICE_SET_CUSTOM_FIELD,
)
from custom_components.guesty.coordinator import (
    CustomFieldsDefinitionCoordinator,
    ListingsCoordinator,
    ReservationsCoordinator,
)
from custom_components.guesty.identity import (
    CONTINUITY_UNCONFIRMED,
    ReservationIdentityGuard,
    async_bootstrap_identity,
)

_LOGGER = logging.getLogger(__name__)

# Custom field target type that addresses a reservation.
CF_TARGET_RESERVATION = "reservation"

# Repairs issue and translation key for FR-031c's write refusal.
ISSUE_IDENTITY_UNCONFIRMED = "identity_continuity_unconfirmed"

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

GET_CUSTOM_FIELDS_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): str,
    }
)

GET_CUSTOM_FIELD_VALUES_SCHEMA = vol.Schema(
    {
        vol.Required("target_type"): vol.In(
            ["listing", "reservation"],
        ),
        vol.Required("target_id"): str,
        vol.Optional("config_entry_id"): str,
    }
)

SEND_GUEST_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("reservation_id"): str,
        vol.Required("message"): str,
        vol.Optional("channel"): str,
        vol.Optional("template_variables"): vol.Schema(
            {str: vol.Coerce(str)},
        ),
        vol.Optional("config_entry_id"): str,
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


def async_notify_identity_continuity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    guard: ReservationIdentityGuard,
) -> None:
    """Surface, or withdraw, the FR-031c continuity notice (T075).

    When the one-time bootstrap sweep is unavailable or its
    comparison is uninformative, reservations still publish but every
    reservation write is refused. That is invisible until someone
    tries to write — typically an automation setting a door code —
    so it is reported through both surfaces decided in T003: a
    non-fixable repairs issue, which is the only in-product surface a
    dashboard-only user sees, and one warning log line.

    The notice is withdrawn as soon as a later refresh produces the
    overlap the bootstrap could not, so a resolved question does not
    leave a stale repair behind.

    The warning is logged only on a transition into the unconfirmed
    state. The repairs issue is the standing surface; repeating the
    log on every refresh would bury it.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        guard: The entry's identity guard, already loaded.
    """
    registry = ir.async_get(hass)
    issue_id = f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}"
    baseline = guard.baseline
    on_v3 = (
        entry.options.get(
            CONF_RESERVATIONS_API,
            DEFAULT_RESERVATIONS_API,
        )
        != RESERVATIONS_API_LEGACY
    )
    unconfirmed = (
        on_v3 and baseline is not None and baseline.continuity == CONTINUITY_UNCONFIRMED
    )
    if not unconfirmed:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    reported = registry.async_get_issue(DOMAIN, issue_id) is not None
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_IDENTITY_UNCONFIRMED,
        translation_placeholders={"title": entry.title},
    )
    if reported:
        return

    _LOGGER.warning(
        "Guesty reservation identity continuity is unconfirmed for "
        "'%s'. Reservations are published normally, but every "
        "reservation write (notes, status and custom fields) is "
        "refused until a refresh shares at least one confirmation "
        "code with the legacy identity evidence. Leave the "
        "integration running so a shared reservation can appear, or "
        "set the 'Reservations API' option to 'legacy' and reload "
        "the entry to roll back",
        entry.title,
    )


def async_track_identity_continuity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ReservationsCoordinator,
) -> None:
    """Keep the FR-031c notice current after setup (T075).

    Continuity is not settled once at setup: an entry left running
    reaches ``verified`` the first time a refresh shares a code with
    the bootstrap evidence, and a later refresh can equally demote a
    settled baseline. Evaluating only at setup would leave a repair
    saying writes are refused long after they were permitted again,
    and would never raise one for a demotion.

    The subscription is released with the config entry, so a reload
    does not accumulate listeners.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being set up.
        coordinator: The entry's reservations coordinator.
    """
    guard = coordinator.identity

    @callback
    def _update() -> None:
        """Re-evaluate the notice after a refresh."""
        async_notify_identity_continuity(hass, entry, guard)

    entry.async_on_unload(coordinator.async_add_listener(_update))


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

    reservations_api = entry.options.get(
        CONF_RESERVATIONS_API,
        DEFAULT_RESERVATIONS_API,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http_client,
        reservations_api=reservations_api,
    )

    if api_client.reservations_api == RESERVATIONS_API_LEGACY:
        _LOGGER.warning(
            "The Guesty legacy reservation API is in use; Guesty "
            "retires it on %s. Set the 'Reservations API' option "
            "to 'v3' before then",
            LEGACY_RESERVATIONS_RETIREMENT_DATE,
        )

    try:
        await api_client.test_connection()
    except GuestyApiError as exc:
        raise ConfigEntryNotReady(
            f"Failed to connect to Guesty API: {exc.message}",
        ) from exc

    account_id: str | None = None
    try:
        account_id = await api_client.get_account_id()
    except GuestyApiError as exc:
        _LOGGER.warning(
            "Failed to fetch Guesty account ID: %s; "
            "custom field definitions will remain unavailable "
            "until the config entry is reloaded",
            exc.message,
        )

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
    # Permitted legacy call 1 of FR-001: one-time, pre-publication,
    # and only for an entry upgrading from a legacy-only release.
    await async_bootstrap_identity(
        hass,
        entry,
        guard=reservations_coordinator.identity,
        api_client=api_client,
        past_days=entry.options.get(CONF_PAST_DAYS, DEFAULT_PAST_DAYS),
        future_days=entry.options.get(
            CONF_FUTURE_DAYS,
            DEFAULT_FUTURE_DAYS,
        ),
    )
    await reservations_coordinator.async_config_entry_first_refresh()
    async_notify_identity_continuity(
        hass,
        entry,
        reservations_coordinator.identity,
    )
    async_track_identity_continuity(hass, entry, reservations_coordinator)

    messaging_client = GuestyMessagingClient(api_client)
    actions_client = GuestyActionsClient(api_client)
    cf_client = GuestyCustomFieldsClient(
        api_client,
        account_id=account_id,
    )

    cf_coordinator = CustomFieldsDefinitionCoordinator(
        hass=hass,
        entry=entry,
        cf_client=cf_client,
    )
    # Custom field definitions are optional — don't block setup
    try:
        await cf_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as exc:
        _LOGGER.debug(
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

        if target_type == CF_TARGET_RESERVATION:
            try:
                guard_reservation_write(
                    entry_data,
                    target_id,
                    SERVICE_SET_CUSTOM_FIELD,
                )
            except GuestyActionError as err:
                raise HomeAssistantError(str(err)) from err

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
                "Custom field update failed for %s/%s: %s",
                err.target_type,
                err.target_id,
                err.message,
                exc_info=err,
            )
            raise HomeAssistantError(
                f"Custom field update failed for {err.target_type}/{err.target_id}",
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

    def _resolve_entry_data(
        call_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve domain entry data from a service call.

        Args:
            call_data: The service call data dict.

        Returns:
            The entry data dict for the resolved config entry.

        Raises:
            HomeAssistantError: When resolution fails.
        """
        domain_data = hass.data.get(DOMAIN, {})
        if not domain_data:
            raise HomeAssistantError(
                "Guesty integration not loaded",
            )

        config_entry_id = call_data.get("config_entry_id")
        if config_entry_id:
            entry_data = domain_data.get(config_entry_id)
            if entry_data is None:
                raise HomeAssistantError(
                    f"Config entry '{config_entry_id}' not found",
                )
            return entry_data  # type: ignore[no-any-return]

        if len(domain_data) > 1:
            raise HomeAssistantError(
                "Multiple Guesty config entries loaded; specify config_entry_id",
            )
        return next(iter(domain_data.values()))  # type: ignore[no-any-return]

    async def _async_handle_get_custom_fields(
        call: ServiceCall,
    ) -> ServiceResponse:
        """Handle the guesty.get_custom_fields service call.

        Returns all cached custom field definitions from the
        coordinator.

        Args:
            call: The service call with optional config_entry_id.

        Returns:
            Dict with a ``fields`` list of definition dicts.

        Raises:
            HomeAssistantError: When entry resolution fails.
        """
        entry_data = _resolve_entry_data(call.data)

        local_cf_coordinator: CustomFieldsDefinitionCoordinator = entry_data[
            "cf_coordinator"
        ]

        definitions = local_cf_coordinator.data
        if definitions is None:
            definitions = []

        fields_list: list[dict[str, Any]] = [
            {
                "field_id": f.field_id,
                "name": f.name,
                "display_name": f.display_name,
                "type": f.field_type,
                "target_types": sorted(f.applicable_to),
                "is_public": f.is_public,
                "is_required": f.is_required,
                "options": list(f.options),
            }
            for f in definitions
        ]

        return {"fields": fields_list}  # type: ignore[dict-item]

    async def _async_handle_get_custom_field_values(
        call: ServiceCall,
    ) -> ServiceResponse:
        """Handle the guesty.get_custom_field_values service call.

        Fetches actual custom field values for a listing or
        reservation, resolving field IDs to human-readable names
        when definitions are available.

        Args:
            call: The service call with target_type, target_id,
                and optional config_entry_id.

        Returns:
            Dict with target info and a ``fields`` list of value
            dicts.

        Raises:
            HomeAssistantError: When entry resolution or API
                call fails.
        """
        entry_data = _resolve_entry_data(call.data)

        target_type: str = call.data["target_type"]
        target_id: str = call.data["target_id"]

        local_cf_client: GuestyCustomFieldsClient = entry_data["cf_client"]
        local_cf_coordinator: CustomFieldsDefinitionCoordinator = entry_data[
            "cf_coordinator"
        ]

        try:
            raw_fields = await local_cf_client.get_field_values(
                target_type=target_type,
                target_id=target_id,
            )
        except GuestyCustomFieldError as err:
            _LOGGER.error(
                "Custom field value fetch failed for %s/%s: %s",
                target_type,
                target_id,
                err.message,
                exc_info=err,
            )
            raise HomeAssistantError(
                f"Failed to fetch custom field values for {target_type}/{target_id}",
            ) from None
        except GuestyApiError:
            _LOGGER.error(
                "API error fetching custom field values for %s",
                target_type,
            )
            raise HomeAssistantError(
                f"API error fetching custom field values for {target_type}",
            ) from None

        # Resolve field IDs to human-readable names
        fields_list: list[dict[str, Any]] = []
        for raw in raw_fields:
            field_id = str(raw["fieldId"])
            value = raw["value"]
            name: str | None = None
            defn = local_cf_coordinator.get_field(field_id)
            if defn is not None:
                name = defn.name
            fields_list.append(
                {
                    "field_id": field_id,
                    "name": name or field_id,
                    "value": value,
                }
            )

        return {
            "target_type": target_type,
            "target_id": target_id,
            "fields": fields_list,  # type: ignore[dict-item]
        }

    async def _async_handle_send_guest_message(
        call: ServiceCall,
    ) -> None:
        """Handle the guesty.send_guest_message service call.

        Sends a message to a guest via the messaging client
        resolved from the appropriate config entry.

        Args:
            call: The service call with message data.

        Raises:
            HomeAssistantError: On validation or delivery failure.
        """
        entry_data = _resolve_entry_data(call.data)

        local_messaging_client: GuestyMessagingClient = entry_data["messaging_client"]

        reservation_id: str = call.data["reservation_id"]
        body: str = call.data["message"]
        channel: str | None = call.data.get("channel")
        template_variables: dict[str, str] | None = call.data.get(
            "template_variables",
        )

        if not reservation_id:
            raise HomeAssistantError(
                "reservation_id is required and must not be empty",
            )
        if not body:
            raise HomeAssistantError(
                "message body is required and must not be empty",
            )

        try:
            await local_messaging_client.send_message(
                reservation_id=reservation_id,
                body=body,
                channel=channel,
                template_variables=template_variables,
            )
        except GuestyMessageError as exc:
            _LOGGER.error(
                "Message delivery failed for reservation '%s': %s",
                reservation_id,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except GuestyApiError as exc:
            _LOGGER.error(
                "API error for reservation '%s': %s",
                reservation_id,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except KeyError as exc:
            missing = exc.args[0] if exc.args else str(exc)
            raise HomeAssistantError(
                f"Missing template variable: {missing}",
            ) from exc
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc

    if not hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CUSTOM_FIELD,
            _async_handle_set_custom_field,
            schema=SET_CUSTOM_FIELD_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_CUSTOM_FIELDS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
            _async_handle_get_custom_fields,
            schema=GET_CUSTOM_FIELDS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GET_CUSTOM_FIELD_VALUES):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELD_VALUES,
            _async_handle_get_custom_field_values,
            schema=GET_CUSTOM_FIELD_VALUES_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_GUEST_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_GUEST_MESSAGE,
            _async_handle_send_guest_message,
            schema=SEND_GUEST_MESSAGE_SCHEMA,
        )

    _prev_selected = entry.options.get(CONF_SELECTED_LISTINGS)
    _prev_reservations_api = entry.options.get(
        CONF_RESERVATIONS_API,
        DEFAULT_RESERVATIONS_API,
    )
    _prev_intervals: dict[str, int] = {
        CONF_SCAN_INTERVAL: entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        ),
        CONF_RESERVATION_SCAN_INTERVAL: entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        ),
        CONF_CF_SCAN_INTERVAL: entry.options.get(
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        ),
    }

    async def _async_options_updated(
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Handle options update by reloading or reconfiguring.

        When the selected-listings filter changes, reloads the
        config entry so that all entities and devices are cleanly
        torn down and recreated with the new filter.  When the
        reservation API selection changes, reloads so the client
        rebuilds on the newly selected path.  When only polling
        intervals change, updates coordinators in place and
        triggers an immediate refresh.  When nothing changes, no
        action is taken.

        Args:
            hass: Home Assistant instance.
            entry: The config entry that was updated.
        """
        nonlocal _prev_selected, _prev_intervals
        nonlocal _prev_reservations_api

        new_selected = entry.options.get(CONF_SELECTED_LISTINGS)
        if new_selected != _prev_selected:
            _prev_selected = new_selected

            # Remove devices for deselected listings before reload
            if new_selected is not None:
                selected_set = set(new_selected)
                dev_registry = dr.async_get(hass)
                devices = dr.async_entries_for_config_entry(
                    dev_registry,
                    entry.entry_id,
                )
                for device in devices:
                    for identifier in device.identifiers:
                        if (
                            identifier[0] == DOMAIN
                            and identifier[1] != entry.entry_id
                            and identifier[1] not in selected_set
                        ):
                            dev_registry.async_update_device(
                                device.id,
                                remove_config_entry_id=entry.entry_id,
                            )
                            break

            await hass.config_entries.async_reload(entry.entry_id)
            return

        new_reservations_api = entry.options.get(
            CONF_RESERVATIONS_API,
            DEFAULT_RESERVATIONS_API,
        )
        if new_reservations_api != _prev_reservations_api:
            _prev_reservations_api = new_reservations_api
            await hass.config_entries.async_reload(entry.entry_id)
            return

        from datetime import timedelta

        new_intervals = {
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL,
                DEFAULT_SCAN_INTERVAL,
            ),
            CONF_RESERVATION_SCAN_INTERVAL: entry.options.get(
                CONF_RESERVATION_SCAN_INTERVAL,
                DEFAULT_RESERVATION_SCAN_INTERVAL,
            ),
            CONF_CF_SCAN_INTERVAL: entry.options.get(
                CONF_CF_SCAN_INTERVAL,
                DEFAULT_CF_SCAN_INTERVAL,
            ),
        }

        if new_intervals == _prev_intervals:
            return

        _prev_intervals = new_intervals

        coordinator.update_interval = timedelta(
            minutes=new_intervals[CONF_SCAN_INTERVAL],
        )
        reservations_coordinator.update_interval = timedelta(
            minutes=new_intervals[CONF_RESERVATION_SCAN_INTERVAL],
        )
        cf_coordinator.update_interval = timedelta(
            minutes=new_intervals[CONF_CF_SCAN_INTERVAL],
        )

        await coordinator.async_request_refresh()
        await reservations_coordinator.async_request_refresh()
        await cf_coordinator.async_request_refresh()

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
            hass.services.async_remove(DOMAIN, SERVICE_GET_CUSTOM_FIELDS)
            hass.services.async_remove(DOMAIN, SERVICE_GET_CUSTOM_FIELD_VALUES)
            hass.services.async_remove(DOMAIN, SERVICE_SEND_GUEST_MESSAGE)

        from custom_components.guesty.actions import (
            async_unload_actions,
        )

        await async_unload_actions(hass, entry)

    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Delete entry-scoped storage when the entry is removed.

    Home Assistant calls this on permanent removal only, never on
    an unload or a reload, so the identity baseline survives a
    restart but does not outlive the entry it describes
    (data-model.md section 4). Constructing the guard directly keeps
    the cleanup working even when setup never completed.

    The entry's continuity notice goes with it. The repair names a
    single entry and is cleared by that entry's coordinator, so one
    left behind by a removed entry could never be withdrawn while
    another Guesty entry kept the domain alive.

    Args:
        hass: Home Assistant instance.
        entry: The config entry being removed.
    """
    ir.async_delete_issue(
        hass,
        DOMAIN,
        f"{ISSUE_IDENTITY_UNCONFIRMED}_{entry.entry_id}",
    )
    await ReservationIdentityGuard(hass, entry.entry_id).async_remove()
