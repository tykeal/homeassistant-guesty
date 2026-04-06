# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty notify platform (T011, T012)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.exceptions import (
    GuestyConnectionError,
    GuestyMessageError,
)
from custom_components.guesty.api.models import MessageDeliveryResult
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
)
from custom_components.guesty.notify import GuestyNotifyEntity


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


class TestGuestyNotifyEntityAttributes:
    """Tests for GuestyNotifyEntity attributes (T011)."""

    def test_unique_id(
        self,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Entity unique_id is derived from entry_id."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)

        assert entity.unique_id == f"{entry.entry_id}_messaging"

    def test_has_entity_name(
        self,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Entity uses device-relative naming."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)

        assert entity.has_entity_name is True

    def test_name(
        self,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Entity name is 'Messaging'."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)

        assert entity.name == "Messaging"

    def test_device_info(
        self,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Entity provides device_info with correct identifiers."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)

        info = entity.device_info
        assert info is not None
        assert info["identifiers"] == {(DOMAIN, entry.entry_id)}
        assert info["name"] == "Guesty"
        assert info["manufacturer"] == "Guesty"


class TestGuestyNotifyEntitySendMessage:
    """Tests for GuestyNotifyEntity.async_send_message (T011)."""

    async def test_delivers_message_via_client(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """async_send_message delegates to messaging client."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-123",
            reservation_id="res-456",
        )

        await entity.async_send_message(
            message="Hello guest",
            title="res-456",
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-456",
            body="Hello guest",
        )

    async def test_missing_reservation_id_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Missing reservation_id raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match="reservation_id",
        ):
            await entity.async_send_message(message="Hello")

    async def test_none_title_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Explicit None title raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match="reservation_id",
        ):
            await entity.async_send_message(
                message="Hello",
                title=None,
            )

    async def test_empty_title_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Empty string title raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match="reservation_id",
        ):
            await entity.async_send_message(
                message="Hello",
                title="",
            )

    async def test_empty_message_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Empty message body raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match="message body",
        ):
            await entity.async_send_message(
                message="",
                title="res-456",
            )

    async def test_messaging_error_maps_to_ha_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """GuestyMessageError is mapped to HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "delivery failed",
            reservation_id="res-456",
        )

        with pytest.raises(
            HomeAssistantError,
            match="delivery failed",
        ):
            await entity.async_send_message(
                message="Hello",
                title="res-456",
            )

    async def test_messaging_error_preserves_cause(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """HomeAssistantError chains to the original cause."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        original = GuestyMessageError(
            "network issue",
            reservation_id="res-789",
        )
        mock_messaging_client.send_message.side_effect = original

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_message(
                message="Hello",
                title="res-789",
            )

        assert exc_info.value.__cause__ is original

    async def test_messaging_error_is_logged(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Message delivery failure is logged at error level."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "timeout",
            reservation_id="res-log",
        )

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(HomeAssistantError),
        ):
            await entity.async_send_message(
                message="Hello",
                title="res-log",
            )

        assert any(
            "res-log" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )

    async def test_client_validation_error_maps_to_ha_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """ValueError from messaging client maps to HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = ValueError(
            "body exceeds maximum length of 10000 characters"
        )

        with pytest.raises(
            HomeAssistantError,
            match="body exceeds maximum length",
        ):
            await entity.async_send_message(
                message="x" * 20000,
                title="res-val",
            )

    async def test_api_error_maps_to_ha_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """GuestyApiError subclasses map to HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyConnectionError(
            "network timeout"
        )

        with pytest.raises(
            HomeAssistantError,
            match="network timeout",
        ) as exc_info:
            await entity.async_send_message(
                message="Hello",
                title="res-conn",
            )

        assert isinstance(
            exc_info.value.__cause__,
            GuestyConnectionError,
        )


class TestNotifyPlatformSetup:
    """Tests for async_setup_entry creating the entity (T011)."""

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_notify_entity(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """async_setup_entry registers a notify entity."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

        entity_ids = [s.entity_id for s in hass.states.async_all("notify")]
        assert len(entity_ids) == 1
        assert entity_ids[0].startswith("notify.")

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_stores_messaging_client(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """async_setup_entry stores messaging_client in runtime data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        data = hass.data[DOMAIN][entry.entry_id]
        assert "messaging_client" in data

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_cleans_up(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Unloading entry removes runtime data."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.NOT_LOADED
        assert entry.entry_id not in hass.data.get(DOMAIN, {})


class TestAutomationCompatibility:
    """Automation dispatch and non-blocking tests (T012)."""

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_service_call_dispatches_message(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """HA service call dispatches message through client."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-svc",
            reservation_id="res-svc",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        await hass.services.async_call(
            "notify",
            "send_message",
            {"message": "Code: 1234", "title": "res-svc"},
            target={"entity_id": entity_id},
            blocking=True,
        )

        mock_client.send_message.assert_called_once_with(
            reservation_id="res-svc",
            body="Code: 1234",
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_service_call_passes_resolved_message(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Pre-resolved HA template message is passed through."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-tpl",
            reservation_id="res-tpl",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        # HA resolves templates before the service call reaches
        # the entity, so the entity receives the resolved string.
        resolved_msg = "Your code is 9876"

        await hass.services.async_call(
            "notify",
            "send_message",
            {"message": resolved_msg, "title": "res-tpl"},
            target={"entity_id": entity_id},
            blocking=True,
        )

        mock_client.send_message.assert_called_once_with(
            reservation_id="res-tpl",
            body=resolved_msg,
        )

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_async_send_does_not_block_event_loop(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """async_send_message is non-blocking (coroutine)."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(AsyncMock(), entry)
        entity.hass = hass

        import asyncio
        import inspect

        assert inspect.iscoroutinefunction(
            entity.async_send_message,
        )
        coro = entity.async_send_message(
            message="test",
            title="res-async",
        )
        assert asyncio.iscoroutine(coro)
        await coro

    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_client_failure_does_not_crash_automation(
        self,
        mock_test: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Messaging failure raises error without crashing HA."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.side_effect = GuestyMessageError(
            "API unavailable",
            reservation_id="res-fail",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "notify",
                "send_message",
                {"message": "Hello", "title": "res-fail"},
                target={"entity_id": entity_id},
                blocking=True,
            )

        # HA continues running after the error
        assert entry.state is ConfigEntryState.LOADED
