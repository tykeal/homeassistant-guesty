# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)
from custom_components.guesty.const import DOMAIN

VALID_INPUT = {
    "client_id": "test-client-id",
    "client_secret": "test-client-secret",
}


class TestStepUser:
    """Tests for the user config flow step."""

    async def test_form_displayed(self, hass: HomeAssistant) -> None:
        """User step shows the credential form."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_successful_entry_creation(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid credentials create a config entry."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["client_id"] == "test-client-id"
        assert result["data"]["client_secret"] == "test-client-secret"

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyAuthError("bad creds"),
    )
    async def test_invalid_auth_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid credentials show invalid_auth error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network"),
    )
    async def test_cannot_connect_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Connection failure shows cannot_connect error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyRateLimitError("rate limited"),
    )
    async def test_rate_limited_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Rate limit shows rate_limited error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "rate_limited"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_duplicate_detection(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Duplicate client_id triggers already_configured abort."""
        # Create first entry
        first = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert first["type"] is FlowResultType.CREATE_ENTRY

        # Attempt duplicate
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert second["type"] is FlowResultType.ABORT
        assert second["reason"] == "already_configured"

    async def test_form_fields(self, hass: HomeAssistant) -> None:
        """Form includes client_id and client_secret fields."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
        )
        schema = result["data_schema"]
        assert schema is not None
        keys = [str(k) for k in schema.schema]
        assert "client_id" in keys
        assert "client_secret" in keys


class TestStepReauth:
    """Tests for the reauth config flow step."""

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_form_displayed(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth step shows the confirmation form."""
        # Create initial entry
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_success(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Successful reauth updates entry and aborts."""
        # Create initial entry
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "client_id": "test-client-id",
                "client_secret": "new-secret",
            },
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
