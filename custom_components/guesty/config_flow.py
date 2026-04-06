# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config flow for the Guesty integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)
from custom_components.guesty.api.models import CachedToken, GuestyListing
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    CONF_TAG_FILTER,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_RESERVATION_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): str,
        vol.Required(CONF_CLIENT_SECRET): str,
    }
)


class _NullStorage:
    """Minimal TokenStorage for config flow validation.

    During config flow, we only need to validate credentials by
    acquiring a token. No persistence is needed.
    """

    async def load_token(self) -> CachedToken | None:
        """Return no stored token.

        Returns:
            Always None.
        """
        return None

    async def save_token(self, token: CachedToken) -> None:
        """Discard the token.

        Args:
            token: The token to discard.
        """

    async def load_request_count(self) -> tuple[int, datetime | None]:
        """Return zero request count.

        Returns:
            Tuple of (0, None).
        """
        return (0, None)

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Discard the request count.

        Args:
            count: The count to discard.
            window_start: The window start to discard.
        """


async def _validate_credentials(
    hass: HomeAssistant,
    client_id: str,
    client_secret: str,
) -> None:
    """Validate Guesty credentials by acquiring a token and testing.

    Args:
        hass: Home Assistant instance.
        client_id: Guesty API client ID.
        client_secret: Guesty API client secret.

    Raises:
        GuestyAuthError: If credentials are invalid.
        GuestyConnectionError: If the API is unreachable.
        GuestyRateLimitError: If rate limited.
    """
    http_client = get_async_client(hass)
    storage = _NullStorage()
    token_manager = GuestyTokenManager(
        client_id=client_id,
        client_secret=client_secret,
        http_client=http_client,
        storage=storage,
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http_client,
    )
    await api_client.test_connection()


class GuestyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Guesty integration.

    Supports initial user setup, re-authentication, and options flows.
    """

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> GuestyOptionsFlowHandler:
        """Return the options flow handler.

        Args:
            config_entry: The config entry.

        Returns:
            The options flow handler instance.
        """
        return GuestyOptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        """Initialize config flow state."""
        super().__init__()
        self._reauth_client_id: str | None = None

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial user setup step.

        Prompts for client_id and client_secret, validates by
        acquiring a token and testing the connection, creates the
        config entry on success.

        Args:
            user_input: User-provided form data, or None for initial
                form display.

        Returns:
            Config flow result (form, error, or entry creation).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]

            await self.async_set_unique_id(client_id)
            self._abort_if_unique_id_configured()

            try:
                await _validate_credentials(self.hass, client_id, client_secret)
            except GuestyAuthError:
                errors["base"] = "invalid_auth"
            except GuestyConnectionError:
                errors["base"] = "cannot_connect"
            except GuestyRateLimitError:
                errors["base"] = "rate_limited"
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"Guesty ({client_id[:8]}...)",
                    data={
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> ConfigFlowResult:
        """Handle reauth initiation.

        Stores the existing client_id and shows the reauth form.

        Args:
            entry_data: Existing config entry data.

        Returns:
            Config flow result showing the reauth confirmation form.
        """
        self._reauth_client_id = entry_data.get(CONF_CLIENT_ID)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reauth confirmation step.

        Pre-fills client_id, accepts new client_secret, validates
        credentials, and updates the config entry on success.

        Args:
            user_input: User-provided form data, or None for initial
                form display.

        Returns:
            Config flow result (form, error, or abort on success).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            client_id = self._reauth_client_id or user_input[CONF_CLIENT_ID]
            client_secret = user_input[CONF_CLIENT_SECRET]

            try:
                await _validate_credentials(self.hass, client_id, client_secret)
            except GuestyAuthError:
                errors["base"] = "invalid_auth"
            except GuestyConnectionError:
                errors["base"] = "cannot_connect"
            except GuestyRateLimitError:
                errors["base"] = "rate_limited"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"],
                )
                if entry is not None:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_CLIENT_ID: client_id,
                            CONF_CLIENT_SECRET: client_secret,
                            "cached_token": None,
                            "token_request_count": 0,
                            "token_window_start": None,
                        },
                    )
                    await self.hass.config_entries.async_reload(
                        entry.entry_id,
                    )
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLIENT_ID,
                    default=self._reauth_client_id or "",
                ): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )


class GuestyOptionsFlowHandler(OptionsFlow):
    """Handle options flow for the Guesty integration.

    Three-step wizard: tag filter → listing selection → intervals.

    Attributes:
        _config_entry: The config entry being configured.
        _tag_filter: Tag filter values from step 1.
        _selected_listings: Selected listing IDs from step 2.
        _available_listings: Listings fetched from the API.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow.

        Args:
            config_entry: The config entry being configured.
        """
        self._config_entry = config_entry
        self._tag_filter: list[str] = []
        self._selected_listings: list[str] = []
        self._available_listings: list[GuestyListing] = []

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the tag filter step and fetch listings.

        Shows an optional tag filter field. On submit, fetches all
        listings from the API and transitions to select_listings.

        Args:
            user_input: User-provided form data, or None for
                initial display.

        Returns:
            Config flow result (form or next step).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            self._tag_filter = user_input.get(CONF_TAG_FILTER, [])
            try:
                api_client: GuestyApiClient = self.hass.data[DOMAIN][
                    self._config_entry.entry_id
                ]["api_client"]
                self._available_listings = await api_client.get_listings()
            except GuestyAuthError:
                errors["base"] = "invalid_auth"
            except GuestyRateLimitError:
                errors["base"] = "rate_limited"
            except GuestyApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_select_listings()

        current_tags = self._config_entry.options.get(
            CONF_TAG_FILTER,
            [],
        )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_TAG_FILTER,
                    default=current_tags,
                ): TextSelector(
                    TextSelectorConfig(multiple=True),
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_select_listings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the listing selection step.

        Builds a multi-select list of available listings with
        labels in "{title} — {address}" format. Validates that
        at least one listing is selected.

        Args:
            user_input: User-provided form data, or None for
                initial display.

        Returns:
            Config flow result (form or next step).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = user_input.get(CONF_SELECTED_LISTINGS, [])
            if not selected:
                errors["base"] = "no_listings_selected"
            else:
                self._selected_listings = selected
                return await self.async_step_intervals()

        options: list[SelectOptionDict] = []
        for listing in self._available_listings:
            addr = listing.address.formatted() if listing.address else None
            label = f"{listing.title} \u2014 {addr or 'No address'}"
            options.append(
                SelectOptionDict(value=listing.id, label=label),
            )

        current = self._config_entry.options.get(
            CONF_SELECTED_LISTINGS,
        )
        if current is None:
            default = [listing.id for listing in self._available_listings]
        else:
            available_ids = {listing.id for listing in self._available_listings}
            default = [lid for lid in current if lid in available_ids]

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SELECTED_LISTINGS,
                    default=default,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="select_listings",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_intervals(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the polling intervals step.

        Presents scan interval, reservation scan interval, past
        days, and future days fields. On submit, merges data from
        all three steps and creates the entry.

        Args:
            user_input: User-provided form data, or None for
                initial display.

        Returns:
            Config flow result (form or entry creation).
        """
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_TAG_FILTER: self._tag_filter,
                    CONF_SELECTED_LISTINGS: self._selected_listings,
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_RESERVATION_SCAN_INTERVAL: user_input[
                        CONF_RESERVATION_SCAN_INTERVAL
                    ],
                    CONF_PAST_DAYS: user_input[CONF_PAST_DAYS],
                    CONF_FUTURE_DAYS: user_input[CONF_FUTURE_DAYS],
                },
            )

        current_scan = self._config_entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        current_res_scan = self._config_entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        from custom_components.guesty.api.const import (
            DEFAULT_FUTURE_DAYS,
            DEFAULT_PAST_DAYS,
        )

        current_past_days = self._config_entry.options.get(
            CONF_PAST_DAYS,
            DEFAULT_PAST_DAYS,
        )
        current_future_days = self._config_entry.options.get(
            CONF_FUTURE_DAYS,
            DEFAULT_FUTURE_DAYS,
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=current_scan,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_RESERVATION_SCAN_INTERVAL,
                    default=current_res_scan,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_RESERVATION_SCAN_INTERVAL),
                ),
                vol.Required(
                    CONF_PAST_DAYS,
                    default=current_past_days,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=1),
                ),
                vol.Required(
                    CONF_FUTURE_DAYS,
                    default=current_future_days,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=1),
                ),
            }
        )

        return self.async_show_form(
            step_id="intervals",
            data_schema=schema,
        )
