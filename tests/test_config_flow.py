# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import respx
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
)
from custom_components.guesty.const import (
    CONF_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from tests.conftest import make_token_response

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
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_successful_entry_creation(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
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
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_duplicate_detection(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
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

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    )
    async def test_unknown_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unexpected exception shows unknown error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


class TestStepReauth:
    """Tests for the reauth config flow step."""

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_form_displayed(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
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
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_reauth_success(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
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

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyAuthError("bad creds"),
    )
    async def test_reauth_auth_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth auth error shows invalid_auth."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

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
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network"),
    )
    async def test_reauth_connection_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth connection error shows cannot_connect."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

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
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=GuestyRateLimitError("rate limited"),
    )
    async def test_reauth_rate_limited_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth rate limit shows rate_limited."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

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
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "rate_limited"}

    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    )
    async def test_reauth_unknown_error(
        self,
        mock_validate: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reauth unexpected error shows unknown."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data=VALID_INPUT,
            unique_id="test-client-id",
        )
        entry.add_to_hass(hass)

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
                "client_secret": "bad",
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "unknown"}


class TestValidateCredentialsDirect:
    """Tests for _validate_credentials without mocking it."""

    @respx.mock
    async def test_validate_credentials_success(self) -> None:
        """_validate_credentials succeeds with valid responses."""
        from custom_components.guesty.config_flow import (
            _validate_credentials,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json={"results": []},
            ),
        )

        await _validate_credentials("test-id", "test-secret")


class TestNullStorage:
    """Tests for _NullStorage used during config flow validation."""

    async def test_load_token_returns_none(self) -> None:
        """_NullStorage.load_token returns None."""
        from custom_components.guesty.config_flow import _NullStorage

        storage = _NullStorage()
        result = await storage.load_token()
        assert result is None


class TestOptionsFlow:
    """Tests for GuestyOptionsFlowHandler."""

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_presents_scan_interval(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options flow shows scan_interval with default."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )

        entry = hass.config_entries.async_entries(DOMAIN)[0]
        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_valid_interval_saves(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Valid scan_interval saves to entry.options."""
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_SCAN_INTERVAL: 10},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_SCAN_INTERVAL] == 10

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_options_flow_below_minimum_rejects(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Interval below MIN_SCAN_INTERVAL raises error."""
        from homeassistant.data_entry_flow import InvalidData

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        result = await hass.config_entries.options.async_init(
            entry.entry_id,
        )
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={CONF_SCAN_INTERVAL: MIN_SCAN_INTERVAL - 1},
            )

    @patch(
        "custom_components.guesty.async_setup_entry",
        return_value=True,
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    async def test_config_flow_has_options_flow(
        self,
        mock_validate: AsyncMock,
        mock_setup: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """GuestyConfigFlow has async_get_options_flow."""
        from custom_components.guesty.config_flow import GuestyConfigFlow

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=VALID_INPUT,
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]

        handler = GuestyConfigFlow.async_get_options_flow(entry)
        assert handler is not None
