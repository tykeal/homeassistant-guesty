# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty integration setup and teardown."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty import HATokenStorage
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
)
from custom_components.guesty.api.models import CachedToken
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_runtime_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_cleans_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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


class TestHATokenStorage:
    """Tests for HATokenStorage persistence."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_save_and_load_token(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """save_token persists to config_entry.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        storage = HATokenStorage(hass, entry)
        token = CachedToken(
            access_token="saved-token",
            token_type="Bearer",
            expires_in=86400,
            scope="open-api",
            issued_at=datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC),
        )
        await storage.save_token(token)
        assert entry.data.get("cached_token") is not None

        loaded = await storage.load_token()
        assert loaded is not None
        assert loaded.access_token == "saved-token"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_load_token_returns_none_for_missing(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """load_token returns None when no token stored."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        storage = HATokenStorage(hass, entry)
        result = await storage.load_token()
        assert result is None

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_save_and_load_request_count(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """save/load_request_count round-trips correctly."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        storage = HATokenStorage(hass, entry)
        now = datetime(2025, 7, 18, 12, 0, 0, tzinfo=UTC)
        await storage.save_request_count(3, now)

        count, window = await storage.load_request_count()
        assert count == 3
        assert window == now

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_load_token_handles_corrupted_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """load_token returns None for corrupted data."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Manually corrupt token data
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "cached_token": {"bad": "data"}},
        )

        storage = HATokenStorage(hass, entry)
        result = await storage.load_token()
        assert result is None


class TestHATokenStorageCorruptedCounters:
    """Tests for HATokenStorage handling corrupted counter data."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_invalid_request_count_resets(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid token_request_count resets to zero."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "token_request_count": "not-a-number",
            },
        )

        storage = HATokenStorage(hass, entry)
        count, _ = await storage.load_request_count()
        assert count == 0

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_invalid_window_start_resets(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Invalid token_window_start resets to None."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "token_request_count": 3,
                "token_window_start": "not-a-date",
            },
        )

        storage = HATokenStorage(hass, entry)
        count, window = await storage.load_request_count()
        assert window is None
        assert count == 0

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_naive_datetime_treated_as_utc(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Naive datetime from config entry is treated as UTC."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "token_request_count": 2,
                "token_window_start": "2025-07-18T12:00:00",
            },
        )

        storage = HATokenStorage(hass, entry)
        count, window = await storage.load_request_count()
        assert window is not None
        assert window.tzinfo is UTC
        assert count == 2

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_count_resets_when_window_none(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Non-zero count with None window_start resets to 0."""
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "token_request_count": 3,
            },
        )

        storage = HATokenStorage(hass, entry)
        count, window = await storage.load_request_count()
        assert window is None
        assert count == 0


class TestLogSanitization:
    """Tests ensuring credentials never appear in logs."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_no_secrets_in_logs(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Tokens and secrets never appear in log output."""
        import logging

        entry = _make_entry()
        entry.add_to_hass(hass)

        with caplog.at_level(logging.DEBUG):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        secret = entry.data[CONF_CLIENT_SECRET]
        for record in caplog.records:
            assert secret not in record.getMessage()


class TestEndToEnd:
    """End-to-end integration tests spanning full lifecycle."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.config_flow._validate_credentials",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_flow_to_setup_to_unload(
        self,
        mock_test: AsyncMock,
        mock_validate: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Config flow → setup entry → client operational → unload."""
        from homeassistant.config_entries import SOURCE_USER
        from homeassistant.data_entry_flow import FlowResultType

        # Step 1: Config flow creates entry
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={
                CONF_CLIENT_ID: "e2e-client",
                CONF_CLIENT_SECRET: "e2e-secret",
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        # Step 2: Entry is loaded
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert entry.state is ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]

        # Step 3: Unload
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED


class TestCoordinatorSetup:
    """Tests for coordinator integration in async_setup_entry."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates coordinator and stores it in hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        assert "coordinator" in data

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_calls_first_refresh(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup calls async_config_entry_first_refresh."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # If first refresh was called, get_listings was called
        mock_listings.assert_awaited_once()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_removes_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload removes coordinator from hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_update_reconfigures_interval(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options update listener reconfigures coordinator interval."""
        from datetime import timedelta

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coordinator.update_interval == timedelta(
            minutes=DEFAULT_SCAN_INTERVAL,
        )

        # Update options
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_SCAN_INTERVAL: 10},
        )
        await hass.async_block_till_done()

        assert coordinator.update_interval == timedelta(minutes=10)
