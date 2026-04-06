# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for Guesty integration setup and teardown."""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
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
    CONF_CF_SCAN_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    CONF_TAG_FILTER,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_SET_CUSTOM_FIELD,
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates token_manager, api_client, coordinator."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        data = hass.data[DOMAIN][entry.entry_id]
        assert "token_manager" in data
        assert "api_client" in data
        assert "actions_client" in data

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

    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network down"),
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_first_refresh_failure_retries(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """First refresh failure triggers setup retry."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("cf network down"),
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_cf_refresh_failure_logs_debug_but_loads(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_cf_defs: AsyncMock,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """CF coordinator failure logs debug but loads."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        with caplog.at_level(logging.DEBUG):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert "Custom field definitions unavailable" in caplog.text


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for unload tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload shuts down coordinators and removes hass.data."""
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for token tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for counter tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for log tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for e2e tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for coord tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
        mock_reservations: AsyncMock,
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


class TestReservationsCoordinatorSetup:
    """Tests for ReservationsCoordinator in async_setup_entry."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for res tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_setup_creates_reservations_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates reservations coordinator in hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        assert "reservations_coordinator" in data

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_reservations_first_refresh_called(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup calls first refresh on reservations coordinator."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        mock_reservations.assert_awaited_once()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_unload_removes_reservations_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload removes reservations coordinator from hass.data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_options_update_reconfigures_reservation_interval(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options update reconfigures reservation coordinator."""
        from datetime import timedelta

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        res_coordinator = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        assert res_coordinator.update_interval == timedelta(
            minutes=DEFAULT_RESERVATION_SCAN_INTERVAL,
        )

        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_SCAN_INTERVAL: 15,
                CONF_RESERVATION_SCAN_INTERVAL: 10,
            },
        )
        await hass.async_block_till_done()

        assert res_coordinator.update_interval == timedelta(
            minutes=10,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError("network down"),
    )
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
    async def test_reservation_refresh_failure_retries(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Reservation first refresh failure retries setup."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestAsyncSetupEntryCustomFields:
    """Tests for custom field integration in async_setup_entry (T018)."""

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_setup_creates_cf_client_and_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup creates custom fields client and coordinator."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        data = hass.data[DOMAIN][entry.entry_id]
        assert "cf_client" in data
        assert "cf_coordinator" in data

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_setup_registers_service(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup registers guesty.set_custom_field service."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD)

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_unload_removes_service_and_coordinator(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload cleans up coordinator and service."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.services.has_service(DOMAIN, SERVICE_SET_CUSTOM_FIELD)

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.NOT_LOADED
        assert not hass.services.has_service(
            DOMAIN,
            SERVICE_SET_CUSTOM_FIELD,
        )

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        side_effect=GuestyConnectionError(
            "custom fields API unreachable",
        ),
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_cf_refresh_failure_still_loads(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Custom fields coordinator failure still loads entry."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

    @patch(
        "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_options_update_reconfigures_cf_interval(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        mock_get_defs: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Options update reconfigures cf_coordinator interval."""
        from datetime import timedelta

        from custom_components.guesty.const import (
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]
        assert cf_coord.update_interval == timedelta(
            minutes=DEFAULT_CF_SCAN_INTERVAL,
        )

        hass.config_entries.async_update_entry(
            entry,
            options={CONF_CF_SCAN_INTERVAL: 30},
        )
        await hass.async_block_till_done()

        assert cf_coord.update_interval == timedelta(minutes=30)


class TestEntityCleanup:
    """Tests for entity and coordinator cleanup on unload (T031)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for cleanup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_unload_removes_all_reservation_entities(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
        sample_reservation: object,
    ) -> None:
        """Unload removes all reservation sensor entities."""
        mock_listings.return_value = [sample_listing]
        mock_reservations.return_value = [sample_reservation]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.beach_house_reservation_status"
        assert hass.states.get(entity_id) is not None

        # Verify coordinator resources exist
        assert entry.entry_id in hass.data.get(DOMAIN, {})
        data = hass.data[DOMAIN][entry.entry_id]
        assert "reservations_coordinator" in data
        assert "coordinator" in data

        # Unload
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.NOT_LOADED
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
        # HA marks unloaded entities as unavailable (not removed)
        post = hass.states.get(entity_id)
        assert post is not None
        assert post.state == "unavailable"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_no_orphaned_entities_for_unknown_listings(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Reservations for non-existent listings create no entities."""
        from datetime import UTC, datetime

        from custom_components.guesty.api.models import (
            GuestyReservation,
        )

        mock_listings.return_value = [sample_listing]
        # Reservation for an unknown listing ID
        orphan_res = GuestyReservation(
            id="res-orphan",
            listing_id="listing-unknown",
            status="confirmed",
            check_in=datetime(2025, 8, 1, 15, 0, tzinfo=UTC),
            check_out=datetime(2025, 8, 5, 11, 0, tzinfo=UTC),
        )
        mock_reservations.return_value = [orphan_res]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Known listing has sensor
        known_entity = "sensor.beach_house_reservation_status"
        assert hass.states.get(known_entity) is not None

        # Unknown listing does NOT create an additional reservation sensor
        all_states = hass.states.async_all("sensor")
        reservation_status_entities = [
            state.entity_id
            for state in all_states
            if state.entity_id.endswith("_reservation_status")
        ]
        assert reservation_status_entities == [known_entity]


class TestSetupEntryNoFilterOptions:
    """Test async_setup_entry with no filter options (T018)."""

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_setup_entry_no_filter_options(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T018: Setup succeeds with no filter options set."""
        entry = _make_entry()
        assert CONF_SELECTED_LISTINGS not in entry.options
        assert CONF_TAG_FILTER not in entry.options

        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]


class TestDeselectedDeviceRemoval:
    """Tests for listing filter changes triggering reload (T030-T033).

    Also verifies that deselected listing devices are removed from
    the device registry before the config entry is reloaded.
    """

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for removal tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_updated_reloads_when_deselected(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """T030: Deselecting a listing triggers config entry reload."""
        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [
                    listing_a.id,
                    "listing-002",
                ],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_awaited_once_with(entry.entry_id)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_updated_reloads_when_multiple_deselected(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """T031: Deselecting multiple listings triggers reload."""
        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [
                    listing_a.id,
                    "listing-002",
                    "listing-003",
                ],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_awaited_once_with(entry.entry_id)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_updated_no_reload_when_same_selection(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """T032: Same selection does not trigger reload."""
        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [listing_a.id],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_not_awaited()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_options_updated_reloads_none_to_explicit(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """T033: None to explicit selection triggers reload."""
        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        # Start with no filter (None = all tracked)
        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_awaited_once_with(entry.entry_id)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_deselected_devices_removed_on_filter_change(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Deselected listing devices removed from registry."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [
                    listing_a.id,
                    "listing-002",
                ],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-002")},
            name="Second Listing",
        )

        devices_before = dr.async_entries_for_config_entry(
            dev_reg,
            entry.entry_id,
        )
        listing_ids_before = {
            ident[1]
            for d in devices_before
            for ident in d.identifiers
            if ident[0] == DOMAIN
        }
        assert "listing-002" in listing_ids_before

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()
            mock_reload.assert_awaited_once_with(entry.entry_id)

        devices_after = dr.async_entries_for_config_entry(
            dev_reg,
            entry.entry_id,
        )
        listing_ids_after = {
            ident[1]
            for d in devices_after
            for ident in d.identifiers
            if ident[0] == DOMAIN
        }
        assert "listing-002" not in listing_ids_after
        assert listing_a.id in listing_ids_after
        # Verify device fully removed from registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-002")},
            )
            is None
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_none_to_explicit_removes_deselected_devices(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """None→explicit selection removes deselected devices."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry()
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-002")},
            name="Second Listing",
        )
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-003")},
            name="Third Listing",
        )

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()
            mock_reload.assert_awaited_once_with(entry.entry_id)

        devices_after = dr.async_entries_for_config_entry(
            dev_reg,
            entry.entry_id,
        )
        listing_ids_after = {
            ident[1]
            for d in devices_after
            for ident in d.identifiers
            if ident[0] == DOMAIN
        }
        assert "listing-002" not in listing_ids_after
        assert "listing-003" not in listing_ids_after
        assert listing_a.id in listing_ids_after
        # Verify devices fully removed from registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-002")},
            )
            is None
        )
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-003")},
            )
            is None
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_notify_device_not_removed_on_filter(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Notify device (entry_id identifier) not removed."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [
                    listing_a.id,
                    "listing-002",
                ],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name="Guesty Notify",
        )
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-002")},
            name="Second Listing",
        )

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [listing_a.id],
                },
            )
            await hass.async_block_till_done()

        devices_after = dr.async_entries_for_config_entry(
            dev_reg,
            entry.entry_id,
        )
        ids_after = {
            ident[1]
            for d in devices_after
            for ident in d.identifiers
            if ident[0] == DOMAIN
        }
        assert entry.entry_id in ids_after
        assert "listing-002" not in ids_after
        # Verify listing-002 fully removed from registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-002")},
            )
            is None
        )
        # Verify notify device still exists in registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, entry.entry_id)},
            )
            is not None
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_selected_devices_not_removed(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
        sample_listing: object,
    ) -> None:
        """Devices for selected listings are preserved."""
        from homeassistant.helpers import device_registry as dr

        from custom_components.guesty.api.models import (
            GuestyListing,
        )

        listing_a = sample_listing
        assert isinstance(listing_a, GuestyListing)

        mock_listings.return_value = [listing_a]

        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: [
                    listing_a.id,
                    "listing-002",
                    "listing-003",
                ],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-002")},
            name="Second Listing",
        )
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "listing-003")},
            name="Third Listing",
        )

        with patch.object(
            hass.config_entries,
            "async_reload",
            new_callable=AsyncMock,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: [
                        listing_a.id,
                        "listing-002",
                    ],
                },
            )
            await hass.async_block_till_done()

        devices_after = dr.async_entries_for_config_entry(
            dev_reg,
            entry.entry_id,
        )
        ids_after = {
            ident[1]
            for d in devices_after
            for ident in d.identifiers
            if ident[0] == DOMAIN
        }
        assert listing_a.id in ids_after
        assert "listing-002" in ids_after
        assert "listing-003" not in ids_after
        # Verify listing-003 fully removed from registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-003")},
            )
            is None
        )
        # Verify selected devices still exist in registry
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, listing_a.id)},
            )
            is not None
        )
        assert (
            dev_reg.async_get_device(
                identifiers={(DOMAIN, "listing-002")},
            )
            is not None
        )


class TestIntervalRefreshOnOptionsUpdate:
    """Tests for immediate refresh on interval changes (T037-T039)."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_interval_change_triggers_refresh(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T037: Interval change calls async_request_refresh."""
        from datetime import timedelta

        entry = _make_entry(
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]

        with (
            patch.object(
                coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_list_refresh,
            patch.object(
                res_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_res_refresh,
            patch.object(
                cf_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_cf_refresh,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={CONF_SCAN_INTERVAL: 10},
            )
            await hass.async_block_till_done()

            mock_list_refresh.assert_awaited_once()
            mock_res_refresh.assert_awaited_once()
            mock_cf_refresh.assert_awaited_once()

        assert coord.update_interval == timedelta(minutes=10)

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_no_refresh_when_nothing_changes(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T038: No refresh when options saved with identical values."""
        entry = _make_entry(
            options={
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_RESERVATION_SCAN_INTERVAL: DEFAULT_RESERVATION_SCAN_INTERVAL,
                CONF_CF_SCAN_INTERVAL: DEFAULT_CF_SCAN_INTERVAL,
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]
        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                new_callable=AsyncMock,
            ) as mock_reload,
            patch.object(
                coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_list_refresh,
            patch.object(
                res_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_res_refresh,
            patch.object(
                cf_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_cf_refresh,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    CONF_RESERVATION_SCAN_INTERVAL: DEFAULT_RESERVATION_SCAN_INTERVAL,
                    CONF_CF_SCAN_INTERVAL: DEFAULT_CF_SCAN_INTERVAL,
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_not_awaited()
            mock_list_refresh.assert_not_awaited()
            mock_res_refresh.assert_not_awaited()
            mock_cf_refresh.assert_not_awaited()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_reload_when_listings_change_not_refresh(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T039: Listings change triggers reload, not refresh."""
        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: ["listing-001"],
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                new_callable=AsyncMock,
            ) as mock_reload,
            patch.object(
                coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_list_refresh,
            patch.object(
                res_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_res_refresh,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: ["listing-002"],
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_awaited_once_with(entry.entry_id)
            mock_list_refresh.assert_not_awaited()
            mock_res_refresh.assert_not_awaited()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_only_interval_change_refreshes_not_reloads(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T039: Only interval change refreshes, does not reload."""
        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: ["listing-001"],
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                new_callable=AsyncMock,
            ) as mock_reload,
            patch.object(
                coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_list_refresh,
            patch.object(
                res_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_res_refresh,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: ["listing-001"],
                    CONF_SCAN_INTERVAL: 10,
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_not_awaited()
            mock_list_refresh.assert_awaited_once()
            mock_res_refresh.assert_awaited_once()

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_both_change_triggers_reload_only(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """T039: Both listings and interval change triggers reload."""
        entry = _make_entry(
            options={
                CONF_SELECTED_LISTINGS: ["listing-001"],
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            },
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        res_coord = hass.data[DOMAIN][entry.entry_id]["reservations_coordinator"]

        with (
            patch.object(
                hass.config_entries,
                "async_reload",
                new_callable=AsyncMock,
            ) as mock_reload,
            patch.object(
                coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_list_refresh,
            patch.object(
                res_coord,
                "async_request_refresh",
                new_callable=AsyncMock,
            ) as mock_res_refresh,
        ):
            hass.config_entries.async_update_entry(
                entry,
                options={
                    CONF_SELECTED_LISTINGS: ["listing-002"],
                    CONF_SCAN_INTERVAL: 10,
                },
            )
            await hass.async_block_till_done()

            mock_reload.assert_awaited_once_with(entry.entry_id)
            mock_list_refresh.assert_not_awaited()
            mock_res_refresh.assert_not_awaited()


class TestGetCustomFieldsService:
    """Tests for guesty.get_custom_fields service."""

    @pytest.fixture(autouse=True)
    def _mock_cf_defs(self) -> Generator[None]:
        """Auto-mock custom field definitions for setup tests."""
        with patch(
            "custom_components.guesty.GuestyCustomFieldsClient.get_definitions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            yield

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_returns_definitions(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service returns all cached field definitions."""
        from custom_components.guesty.api.models import (
            GuestyCustomFieldDefinition,
        )
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]
        cf_coord.data = [
            GuestyCustomFieldDefinition(
                field_id="cf-001",
                name="VIP Status",
                field_type="text",
                applicable_to=frozenset({"listing", "reservation"}),
            ),
            GuestyCustomFieldDefinition(
                field_id="cf-002",
                name="Rating",
                field_type="number",
                applicable_to=frozenset({"listing"}),
            ),
        ]

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
            {},
            blocking=True,
            return_response=True,
        )

        assert result is not None
        assert "fields" in result
        fields: Any = result["fields"]
        assert len(fields) == 2

        first: Any = fields[0]
        assert first["field_id"] == "cf-001"
        assert first["name"] == "VIP Status"
        assert first["type"] == "text"
        assert first["target_types"] == ["listing", "reservation"]

        second: Any = fields[1]
        assert second["field_id"] == "cf-002"
        assert second["name"] == "Rating"
        assert second["type"] == "number"
        assert second["target_types"] == ["listing"]

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_empty_list(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service returns empty list when no definitions."""
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]
        cf_coord.data = []

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
            {},
            blocking=True,
            return_response=True,
        )

        assert result is not None
        assert result["fields"] == []

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_coordinator_no_data(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service returns empty list when coordinator has None."""
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]
        cf_coord.data = None

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
            {},
            blocking=True,
            return_response=True,
        )

        assert result is not None
        assert result["fields"] == []

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_with_config_entry_id(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service resolves correct entry via config_entry_id."""
        from custom_components.guesty.api.models import (
            GuestyCustomFieldDefinition,
        )
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        cf_coord = hass.data[DOMAIN][entry.entry_id]["cf_coordinator"]
        cf_coord.data = [
            GuestyCustomFieldDefinition(
                field_id="cf-x",
                name="Test",
                field_type="boolean",
                applicable_to=frozenset({"reservation"}),
            ),
        ]

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
            {"config_entry_id": entry.entry_id},
            blocking=True,
            return_response=True,
        )

        assert result is not None
        fields: Any = result["fields"]
        assert len(fields) == 1
        assert fields[0]["field_id"] == "cf-x"

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_invalid_config_entry_id(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service raises error for unknown config_entry_id."""
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="not found"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_GET_CUSTOM_FIELDS,
                {"config_entry_id": "nonexistent-id"},
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_no_entries_loaded(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service raises error when no entries loaded."""
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Clear domain data to simulate no entries
        hass.data[DOMAIN].clear()

        with pytest.raises(
            HomeAssistantError,
            match="not loaded",
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_GET_CUSTOM_FIELDS,
                {},
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_get_custom_fields_multiple_entries_no_id(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service raises error with multiple entries and no id."""
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Add a second fake entry to domain data
        hass.data[DOMAIN]["fake-second-entry"] = {
            "cf_coordinator": None,
        }

        with pytest.raises(
            HomeAssistantError,
            match="config_entry_id",
        ):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_GET_CUSTOM_FIELDS,
                {},
                blocking=True,
                return_response=True,
            )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_setup_registers_get_custom_fields(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Setup registers guesty.get_custom_fields service."""
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.services.has_service(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.get_reservations",
        new_callable=AsyncMock,
        return_value=[],
    )
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
    async def test_unload_removes_get_custom_fields(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        mock_reservations: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unload removes guesty.get_custom_fields service."""
        from custom_components.guesty.const import (
            SERVICE_GET_CUSTOM_FIELDS,
        )

        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.services.has_service(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
        )

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert not hass.services.has_service(
            DOMAIN,
            SERVICE_GET_CUSTOM_FIELDS,
        )
