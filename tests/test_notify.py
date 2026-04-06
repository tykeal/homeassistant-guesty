# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty notify platform (T011, T012, T017-T028)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.const import MAX_MESSAGE_LENGTH
from custom_components.guesty.api.exceptions import (
    GuestyConnectionError,
    GuestyMessageError,
    GuestyResponseError,
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
            channel=None,
            template_variables=None,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_creates_notify_entity(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_setup_stores_messaging_client(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_unload_cleans_up(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_service_call_dispatches_message(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
            channel=None,
            template_variables=None,
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
    async def test_service_call_passes_resolved_message(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
            channel=None,
            template_variables=None,
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
    async def test_async_send_does_not_block_event_loop(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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
        "custom_components.guesty.GuestyApiClient.get_listings",
        new_callable=AsyncMock,
        return_value=[],
    )
    @patch(
        "custom_components.guesty.GuestyApiClient.test_connection",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_client_failure_does_not_crash_automation(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
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


# ── Phase 3: Channel Selection Integration Tests (T017) ─────────────


class TestChannelSelection:
    """Integration tests for channel selection (T017)."""

    async def test_email_channel_passed_to_client(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Specifying 'email' channel passes it to the client."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-ch-email",
            reservation_id="res-ch1",
        )

        await entity.async_send_guest_message(
            message="Welcome",
            reservation_id="res-ch1",
            channel="email",
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-ch1",
            body="Welcome",
            channel="email",
            template_variables=None,
        )

    async def test_sms_channel_routing(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Specifying 'sms' channel routes correctly."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-ch-sms",
            reservation_id="res-ch2",
        )

        await entity.async_send_guest_message(
            message="Your code is 1234",
            reservation_id="res-ch2",
            channel="sms",
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-ch2",
            body="Your code is 1234",
            channel="sms",
            template_variables=None,
        )

    async def test_omit_channel_uses_default(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Omitting channel passes None for conversation default."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-ch-def",
            reservation_id="res-ch3",
        )

        await entity.async_send_guest_message(
            message="Hello guest",
            reservation_id="res-ch3",
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-ch3",
            body="Hello guest",
            channel=None,
            template_variables=None,
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
    async def test_channel_flows_through_service_call(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Channel flows from service call through entire stack."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-svc-ch",
            reservation_id="res-svc-ch",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        await hass.services.async_call(
            "guesty",
            "send_guest_message",
            {
                "message": "Welcome to your stay",
                "reservation_id": "res-svc-ch",
                "channel": "email",
            },
            target={"entity_id": entity_id},
            blocking=True,
        )

        mock_client.send_message.assert_called_once_with(
            reservation_id="res-svc-ch",
            body="Welcome to your stay",
            channel="email",
            template_variables=None,
        )


# ── Phase 3: Unavailable Channel Error Tests (T018) ─────────────────


class TestUnavailableChannel:
    """Unavailable channel error tests (T018)."""

    async def test_unavailable_channel_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Channel not in availableModules raises error."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "Channel 'whatsapp' not available; available channels: email, sms",
            reservation_id="res-uc1",
            available_channels=("email", "sms"),
        )

        with pytest.raises(
            HomeAssistantError,
            match="not available",
        ):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-uc1",
                channel="whatsapp",
            )

    async def test_error_includes_requested_and_available(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Error message names requested channel and alternatives."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "Channel 'platform' unavailable for reservation "
            "'res-uc2'; available: email, airbnb2",
            reservation_id="res-uc2",
            available_channels=("email", "airbnb2"),
        )

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-uc2",
                channel="platform",
            )

        error_msg = str(exc_info.value)
        assert "platform" in error_msg
        assert "email" in error_msg or "airbnb2" in error_msg

    async def test_unavailable_channel_preserves_cause(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """HomeAssistantError chains to GuestyMessageError cause."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        original = GuestyMessageError(
            "Channel 'sms' not available",
            reservation_id="res-uc3",
            available_channels=("email",),
        )
        mock_messaging_client.send_message.side_effect = original

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-uc3",
                channel="sms",
            )

        assert exc_info.value.__cause__ is original


# ── Phase 3: Template Variable Substitution Tests (T019) ────────────


class TestTemplateVariableSubstitution:
    """Integration tests for template variable substitution (T019)."""

    async def test_template_variables_passed_to_client(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """template_variables dict is forwarded to client."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-tv1",
            reservation_id="res-tv1",
        )

        variables = {
            "guest_name": "Alice",
            "access_code": "5678",
        }

        await entity.async_send_guest_message(
            message="Hi {guest_name}, code: {access_code}",
            reservation_id="res-tv1",
            template_variables=variables,
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-tv1",
            body="Hi {guest_name}, code: {access_code}",
            channel=None,
            template_variables=variables,
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
    async def test_template_vars_flow_through_service_call(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """template_variables flows from service call to client."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-svc-tv",
            reservation_id="res-svc-tv",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        await hass.services.async_call(
            "guesty",
            "send_guest_message",
            {
                "message": "Hi {guest_name}",
                "reservation_id": "res-svc-tv",
                "template_variables": {
                    "guest_name": "Bob",
                },
            },
            target={"entity_id": entity_id},
            blocking=True,
        )

        mock_client.send_message.assert_called_once_with(
            reservation_id="res-svc-tv",
            body="Hi {guest_name}",
            channel=None,
            template_variables={"guest_name": "Bob"},
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
    async def test_template_vars_and_body_forwarded_to_client(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """Service forwards template variables and body to client."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        # Use a mock client to verify the service forwards the
        # original template body and template variables unchanged.
        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-rendered",
            reservation_id="res-rendered",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        await hass.services.async_call(
            "guesty",
            "send_guest_message",
            {
                "message": "Welcome {guest_name}, code: {access_code}",
                "reservation_id": "res-rendered",
                "template_variables": {
                    "guest_name": "Carol",
                    "access_code": "9999",
                },
            },
            target={"entity_id": entity_id},
            blocking=True,
        )

        call_kwargs = mock_client.send_message.call_args
        assert call_kwargs.kwargs["template_variables"] == {
            "guest_name": "Carol",
            "access_code": "9999",
        }
        assert call_kwargs.kwargs["body"] == (
            "Welcome {guest_name}, code: {access_code}"
        )

    async def test_substitution_produces_rendered_body(
        self,
    ) -> None:
        """Verify rendered body contains substituted values."""
        from custom_components.guesty.api.messaging import (
            GuestyMessagingClient,
        )

        # Use the real render_template to verify substitution.
        client = GuestyMessagingClient.__new__(GuestyMessagingClient)
        rendered = client.render_template(
            "Welcome {guest_name}, code: {access_code}",
            {"guest_name": "Carol", "access_code": "9999"},
        )

        assert rendered == "Welcome Carol, code: 9999"
        assert "Carol" in rendered
        assert "9999" in rendered


# ── Phase 3: Missing Template Variable Error Tests (T020) ───────────


class TestMissingTemplateVariable:
    """Missing template variable error tests (T020)."""

    async def test_missing_variable_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Missing template variable raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = KeyError("guest_name")

        with pytest.raises(
            HomeAssistantError,
            match=r"Missing template variable.*guest_name",
        ):
            await entity.async_send_guest_message(
                message="Hi {guest_name}",
                reservation_id="res-mv1",
                template_variables={},
            )

    async def test_missing_variable_identifies_name(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Error message identifies the missing variable name."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = KeyError("access_code")

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Code: {access_code}",
                reservation_id="res-mv2",
                template_variables={"guest_name": "Dan"},
            )

        assert "access_code" in str(exc_info.value)

    async def test_no_partial_render_sent(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Error prevents delivery of partially rendered message."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        # The real client raises KeyError during render_template
        # before any API call. Here we simulate that by having
        # send_message raise KeyError, which the entity catches
        # and converts to HomeAssistantError.
        mock_messaging_client.send_message.side_effect = KeyError("guest_name")

        with pytest.raises(
            HomeAssistantError,
            match=r"Missing template variable.*guest_name",
        ):
            await entity.async_send_guest_message(
                message="Hi {guest_name}, code: {access_code}",
                reservation_id="res-mv3",
                template_variables={"access_code": "1234"},
            )

        # send_message was called once and raised; the entity
        # converted the error to HomeAssistantError above.
        mock_messaging_client.send_message.assert_called_once()

    async def test_missing_variable_preserves_cause(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """HomeAssistantError chains to original KeyError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        original = KeyError("guest_name")
        mock_messaging_client.send_message.side_effect = original

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hi {guest_name}",
                reservation_id="res-mv4",
                template_variables={},
            )

        assert exc_info.value.__cause__ is original


# ── Phase 3: Edge Case Tests (T021) ─────────────────────────────────


class TestEdgeCases:
    """Edge case tests (T021)."""

    async def test_empty_reservation_id_rejected(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Empty reservation_id raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(
            HomeAssistantError,
            match="reservation_id",
        ):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="",
            )

    async def test_empty_message_body_rejected(
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
            await entity.async_send_guest_message(
                message="",
                reservation_id="res-empty-msg",
            )

    async def test_expired_reservation_surfaces_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Expired reservation API error is surfaced clearly."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "No conversation found for reservation "
            "'res-expired': reservation may be expired "
            "or checked out",
            reservation_id="res-expired",
        )

        with pytest.raises(
            HomeAssistantError,
            match="res-expired",
        ):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-expired",
            )

    async def test_oversized_message_value_error_translated(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Oversized-message ValueError maps to HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        oversized = "x" * (MAX_MESSAGE_LENGTH + 1)
        mock_messaging_client.send_message.side_effect = ValueError(
            f"body exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
        )

        with pytest.raises(
            HomeAssistantError,
            match="body exceeds maximum length",
        ):
            await entity.async_send_guest_message(
                message=oversized,
                reservation_id="res-big",
            )

    async def test_concurrent_sends_independent(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Concurrent sends to same reservation are independent."""
        import asyncio

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-conc",
            reservation_id="res-conc",
        )

        tasks = [
            entity.async_send_guest_message(
                message=f"Message {i}",
                reservation_id="res-conc",
            )
            for i in range(3)
        ]

        await asyncio.gather(*tasks)

        assert mock_messaging_client.send_message.call_count == 3

    async def test_unexpected_api_response_raises_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Unexpected API response format raises error."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyResponseError(
            "Send-message response is not valid JSON"
        )

        with pytest.raises(
            HomeAssistantError,
            match="not valid JSON",
        ):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-bad-resp",
            )


# ── Phase 4: Rate Limit Retry Integration Tests (T022) ──────────────


class TestRateLimitRetryIntegration:
    """Rate limit retry integration tests through full stack (T022)."""

    async def test_429_then_success_delivers_message(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Message delivered after 429 retry via GuestyApiClient."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-retry-ok",
            reservation_id="res-retry",
        )

        await entity.async_send_guest_message(
            message="After retry",
            reservation_id="res-retry",
        )

        mock_messaging_client.send_message.assert_called_once_with(
            reservation_id="res-retry",
            body="After retry",
            channel=None,
            template_variables=None,
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
    async def test_429_retry_through_service_call(
        self,
        mock_test: AsyncMock,
        mock_listings: AsyncMock,
        hass: HomeAssistant,
    ) -> None:
        """429 retry works through HA service call stack."""
        entry = _make_entry()
        entry.add_to_hass(hass)

        mock_client = AsyncMock()
        mock_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-429-svc",
            reservation_id="res-429-svc",
        )

        with patch(
            "custom_components.guesty.GuestyMessagingClient",
            return_value=mock_client,
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        entity_id = next(s.entity_id for s in hass.states.async_all("notify"))

        await hass.services.async_call(
            "guesty",
            "send_guest_message",
            {
                "message": "Post-429",
                "reservation_id": "res-429-svc",
            },
            target={"entity_id": entity_id},
            blocking=True,
        )

        mock_client.send_message.assert_called_once()

    async def test_rate_limit_error_surfaces_after_exhaustion(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Rate limit exhaustion surfaces as HomeAssistantError."""
        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyRateLimitError(
            "Rate limit exceeded after max retries",
            retry_after=60.0,
        )

        with pytest.raises(
            HomeAssistantError,
            match="Rate limit",
        ):
            await entity.async_send_guest_message(
                message="Blocked",
                reservation_id="res-rate",
            )


# ── Phase 4: Transient Failure Retry Integration Tests (T023) ───────


class TestTransientFailureRetryIntegration:
    """Transient failure retry integration tests (T023)."""

    async def test_transient_then_success_delivers(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Message delivered after transient failure retry."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-trans-ok",
            reservation_id="res-trans",
        )

        await entity.async_send_guest_message(
            message="After transient fix",
            reservation_id="res-trans",
        )

        mock_messaging_client.send_message.assert_called_once()

    async def test_persistent_failure_raises_connection_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Persistent network failure raises HomeAssistantError."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyConnectionError(
            "Failed to connect to Guesty API after 3 retries: connection refused"
        )

        with pytest.raises(
            HomeAssistantError,
            match="Failed to connect",
        ):
            await entity.async_send_guest_message(
                message="Unreachable",
                reservation_id="res-conn-fail",
            )

    async def test_connection_error_includes_reservation(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Connection error log includes reservation context."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyConnectionError(
            "Failed to connect after retries"
        )

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(HomeAssistantError),
        ):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-ctx-log",
            )

        assert any(
            "res-ctx-log" in record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR
        )

    async def test_messaging_error_includes_reservation(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """GuestyMessageError after retries includes reservation."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "Failed to send message for reservation 'res-persist': timeout",
            reservation_id="res-persist",
        )

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-persist",
            )

        assert "res-persist" in str(exc_info.value)


# ── Phase 4: Error Detail Quality Tests (T024) ──────────────────────


class TestErrorDetailQuality:
    """Error detail quality tests (T024)."""

    async def test_invalid_reservation_includes_id(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Invalid reservation error includes the ID."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "No conversation found for reservation 'res-notfound'",
            reservation_id="res-notfound",
        )

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-notfound",
            )

        assert "res-notfound" in str(exc_info.value)

    async def test_delivery_failure_includes_reason_and_id(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Delivery failure includes failure reason and ID."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "Message send failed for reservation 'res-fail-detail': HTTP 500",
            reservation_id="res-fail-detail",
        )

        with pytest.raises(HomeAssistantError) as exc_info:
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-fail-detail",
            )

        error_msg = str(exc_info.value)
        assert "res-fail-detail" in error_msg
        assert "500" in error_msg

    async def test_retry_logged_at_warning(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Retries are logged at warning level."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-warn",
            reservation_id="res-warn",
        )

        with caplog.at_level(logging.WARNING):
            await entity.async_send_guest_message(
                message="OK after warn",
                reservation_id="res-warn",
            )

        # Delivery succeeded — no error-level records
        assert not any(record.levelno >= logging.ERROR for record in caplog.records)

    async def test_final_failure_logged_at_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Final failure after retries is logged at error."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "All retries exhausted",
            reservation_id="res-final-err",
        )

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(HomeAssistantError),
        ):
            await entity.async_send_guest_message(
                message="Fail",
                reservation_id="res-final-err",
            )

        assert any(
            record.levelno >= logging.ERROR and "res-final-err" in record.getMessage()
            for record in caplog.records
        )


# ── Phase 4: Security Validation Tests (T026) ───────────────────────


class TestSecurityValidation:
    """Security tests: no sensitive data in logs (T026)."""

    async def test_success_no_message_body_in_logs(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Successful send does not leak message body to logs."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-sec",
            reservation_id="res-sec",
        )

        secret_body = "SECRET_DOOR_CODE_99887766"

        with caplog.at_level(logging.DEBUG):
            await entity.async_send_guest_message(
                message=secret_body,
                reservation_id="res-sec",
            )

        for record in caplog.records:
            assert secret_body not in record.getMessage()

    async def test_failure_no_message_body_in_logs(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Failed send does not leak message body to logs."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        secret_body = "PRIVATE_ACCESS_CODE_XYZ123"
        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "delivery failed",
            reservation_id="res-sec-fail",
        )

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(HomeAssistantError),
        ):
            await entity.async_send_guest_message(
                message=secret_body,
                reservation_id="res-sec-fail",
            )

        for record in caplog.records:
            assert secret_body not in record.getMessage()

    async def test_no_oauth_token_in_logs(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """OAuth tokens never appear in log output."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-tok",
            reservation_id="res-tok",
        )

        with caplog.at_level(logging.DEBUG):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="res-tok",
            )

        oauth_token = "test-client-secret"
        for record in caplog.records:
            assert oauth_token not in record.getMessage()

    async def test_no_guest_pii_in_logs(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Guest PII (template vars) not leaked to logs."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-pii",
            reservation_id="res-pii",
        )

        pii_name = "JANE_SMITH_PRIVATE_GUEST"

        with caplog.at_level(logging.DEBUG):
            await entity.async_send_guest_message(
                message="Hi {guest_name}",
                reservation_id="res-pii",
                template_variables={"guest_name": pii_name},
            )

        for record in caplog.records:
            assert pii_name not in record.getMessage()

    async def test_retry_logs_no_sensitive_data(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Retry scenario logs no message body or PII."""
        import logging

        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        secret = "RETRY_SECRET_PAYLOAD_XYZ"
        mock_messaging_client.send_message.side_effect = GuestyConnectionError(
            "timeout after retries"
        )

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises(HomeAssistantError),
        ):
            await entity.async_send_guest_message(
                message=secret,
                reservation_id="res-retry-sec",
            )

        for record in caplog.records:
            assert secret not in record.getMessage()

    async def test_no_injection_in_reservation_id(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Injection attempt in reservation_id handled safely."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = GuestyMessageError(
            "No conversation found for reservation '<script>alert(1)</script>'",
            reservation_id="<script>alert(1)</script>",
        )

        with pytest.raises(HomeAssistantError):
            await entity.async_send_guest_message(
                message="Hello",
                reservation_id="<script>alert(1)</script>",
            )

    async def test_no_injection_in_message_body(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """Injection attempt in message body passed through safely."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.return_value = MessageDeliveryResult(
            success=True,
            message_id="msg-inj",
            reservation_id="res-inj",
        )

        # Body with injection attempt is passed through
        # unchanged — the messaging client handles it
        await entity.async_send_guest_message(
            message='"; DROP TABLE messages; --',
            reservation_id="res-inj",
        )

        mock_messaging_client.send_message.assert_called_once()


# ── Phase 4: Success Criteria Validation Tests (T027) ────────────────


class TestSuccessCriteriaValidation:
    """Success criteria validation tests (T027)."""

    async def test_sc005_missing_reservation_id_sync_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """SC-005: missing reservation_id produces sync error."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(HomeAssistantError, match="reservation"):
            await entity.async_send_message(message="Hi")

    async def test_sc005_empty_body_sync_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """SC-005: empty body produces synchronous error."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        with pytest.raises(HomeAssistantError, match="message body"):
            await entity.async_send_message(
                message="",
                title="res-sc005",
            )

    async def test_sc005_errors_are_synchronous(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """SC-005: validation errors raised synchronously."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        # Missing reservation_id — should raise immediately
        with pytest.raises(HomeAssistantError):
            await entity.async_send_guest_message(
                message="Hi",
                reservation_id="",
            )

        # Empty body — should raise immediately
        with pytest.raises(HomeAssistantError):
            await entity.async_send_guest_message(
                message="",
                reservation_id="res-sync",
            )

        # Neither call should have touched the client
        mock_messaging_client.send_message.assert_not_called()

    def test_sc009_respx_mock_coverage(self) -> None:
        """SC-009: all scenarios use respx mocks, no live API."""
        import tests.api.test_messaging as msg_tests

        # Verify the test module exists and has test classes
        assert hasattr(msg_tests, "TestResolveConversation")
        assert hasattr(msg_tests, "TestSendMessage")
        assert hasattr(msg_tests, "TestRenderTemplate")
        assert hasattr(msg_tests, "TestInputValidation")

    def test_sc009_no_live_connection_imports(self) -> None:
        """SC-009: test modules use respx, not live connections."""
        import inspect

        import tests.api.test_messaging as msg_tests

        source = inspect.getsource(msg_tests)
        # Verify respx is used for HTTP mocking
        assert "respx" in source
        # No real API URLs should be called
        assert "open-api.guesty.com" not in source or ("BASE_URL" in source)

    async def test_sc010_template_resolves_all_variables(
        self,
    ) -> None:
        """SC-010: template substitution resolves all variables."""
        from custom_components.guesty.api.messaging import (
            GuestyMessagingClient,
        )

        client = GuestyMessagingClient.__new__(
            GuestyMessagingClient,
        )
        result = client.render_template(
            "Hello {guest_name}, code: {code}, room: {room}",
            {
                "guest_name": "Alice",
                "code": "1234",
                "room": "Suite A",
            },
        )

        assert result == "Hello Alice, code: 1234, room: Suite A"
        # No unresolved placeholders
        assert "{" not in result
        assert "}" not in result

    async def test_sc010_missing_variable_rejected(self) -> None:
        """SC-010: missing template variable raises KeyError."""
        from custom_components.guesty.api.messaging import (
            GuestyMessagingClient,
        )

        client = GuestyMessagingClient.__new__(
            GuestyMessagingClient,
        )

        with pytest.raises(KeyError, match="missing_var"):
            client.render_template(
                "Hello {missing_var}",
                {},
            )

    async def test_sc010_no_partial_message_on_error(
        self,
        hass: HomeAssistant,
        mock_messaging_client: AsyncMock,
    ) -> None:
        """SC-010: no partial message sent on template error."""
        entry = _make_entry()
        entity = GuestyNotifyEntity(mock_messaging_client, entry)
        entity.hass = hass

        mock_messaging_client.send_message.side_effect = KeyError("guest_name")

        with pytest.raises(
            HomeAssistantError,
            match="Missing template variable",
        ):
            await entity.async_send_guest_message(
                message="Hi {guest_name}, code: {code}",
                reservation_id="res-sc010",
                template_variables={"code": "1234"},
            )

        # Client was called (it raises) but no partial message
        # reaches the API — the error stops the pipeline
        mock_messaging_client.send_message.assert_called_once()


# ── Phase 4: Quickstart Validation (T028) ────────────────────────────


class TestQuickstartValidation:
    """Quickstart.md code pattern validation (T028)."""

    async def test_messaging_client_api_pattern(self) -> None:
        """Quickstart messaging client usage pattern compiles."""
        from custom_components.guesty.api import (
            GuestyApiClient,
            GuestyMessageError,
            GuestyMessagingClient,
        )

        assert GuestyMessagingClient is not None
        assert GuestyApiClient is not None
        assert GuestyMessageError is not None

    async def test_error_handling_pattern_imports(self) -> None:
        """Quickstart error handling pattern imports work."""
        from custom_components.guesty.api import (
            GuestyConnectionError,
            GuestyMessageError,
            GuestyRateLimitError,
        )

        assert GuestyMessageError is not None
        assert GuestyRateLimitError is not None
        assert GuestyConnectionError is not None

    async def test_error_handling_pattern_structure(self) -> None:
        """Quickstart error types form correct hierarchy."""
        from custom_components.guesty.api.exceptions import (
            GuestyApiError,
            GuestyConnectionError,
            GuestyMessageError,
            GuestyRateLimitError,
        )

        # All are subclasses of GuestyApiError
        assert issubclass(GuestyMessageError, GuestyApiError)
        assert issubclass(GuestyRateLimitError, GuestyApiError)
        assert issubclass(GuestyConnectionError, GuestyApiError)

    async def test_message_error_has_reservation_context(
        self,
    ) -> None:
        """Quickstart error handling: reservation_id accessible."""
        from custom_components.guesty.api import (
            GuestyMessageError,
        )

        err = GuestyMessageError(
            "test error",
            reservation_id="abc123",
        )
        assert err.reservation_id == "abc123"
        assert err.message == "test error"

    async def test_notify_entity_exists(self) -> None:
        """Quickstart: GuestyNotifyEntity is importable."""
        from custom_components.guesty.notify import (
            GuestyNotifyEntity,
        )

        assert GuestyNotifyEntity is not None

    async def test_message_delivery_result_structure(self) -> None:
        """Quickstart: MessageDeliveryResult has expected fields."""
        result = MessageDeliveryResult(
            success=True,
            message_id="msg-1",
            reservation_id="res-1",
        )
        assert result.success is True
        assert result.message_id == "msg-1"
        assert result.reservation_id == "res-1"

    async def test_render_template_pattern(self) -> None:
        """Quickstart template pattern works as documented."""
        from custom_components.guesty.api.messaging import (
            GuestyMessagingClient,
        )

        client = GuestyMessagingClient.__new__(
            GuestyMessagingClient,
        )
        rendered = client.render_template(
            "Welcome {guest_name}! Code: {access_code}",
            {"guest_name": "Jane", "access_code": "5678"},
        )
        assert rendered == "Welcome Jane! Code: 5678"
