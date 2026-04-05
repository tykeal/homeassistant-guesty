# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty integration setup and teardown."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)


def _make_entry(**overrides: object) -> MockConfigEntry:
    """Create a MockConfigEntry with test defaults.

    Args:
        **overrides: Fields to override.

    Returns:
        A MockConfigEntry for the Guesty integration.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: "test-client-id",
            CONF_CLIENT_SECRET: "test-client-secret",
        },
        unique_id="test-client-id",
        **overrides,  # type: ignore[arg-type]
    )


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_runtime_data(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates http_client, token_manager, api_client."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        data = hass.data[DOMAIN][entry.entry_id]
        assert "http_client" in data
        assert "token_manager" in data
        assert "api_client" in data

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("cannot connect"),
    )
    async def test_setup_failure_raises_not_ready(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup raises ConfigEntryNotReady on connection failure."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        side_effect=GuestyAuthError("bad creds"),
    )
    async def test_auth_error_raises_not_ready(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup raises ConfigEntryNotReady on auth failure."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_cleans_data(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload closes HTTP client and removes hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.NOT_LOADED
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
