# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config flow for the Guesty integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import DEFAULT_TIMEOUT
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)
from custom_components.guesty.api.models import CachedToken
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
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
    client_id: str,
    client_secret: str,
) -> None:
    """Validate Guesty credentials by acquiring a token and testing.

    Args:
        client_id: Guesty API client ID.
        client_secret: Guesty API client secret.

    Raises:
        GuestyAuthError: If credentials are invalid.
        GuestyConnectionError: If the API is unreachable.
        GuestyRateLimitError: If rate limited.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as http_client:
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

    Supports initial user setup and re-authentication flows.
    """

    VERSION = 1

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
                await _validate_credentials(client_id, client_secret)
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
                await _validate_credentials(client_id, client_secret)
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
