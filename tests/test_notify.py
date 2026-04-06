# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty notify platform (T011, T012, T017-T021)."""

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
