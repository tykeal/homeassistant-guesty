# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty notify platform for sending messages to guests.

Implements the modern NotifyEntity pattern, enabling property managers
to send messages to guests by reservation ID through Home Assistant
service calls, automations, and scripts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.notify import NotifyEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.guesty.api.exceptions import GuestyApiError, GuestyMessageError
from custom_components.guesty.api.messaging import GuestyMessagingClient
from custom_components.guesty.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Guesty notify entities from a config entry.

    Creates a single GuestyNotifyEntity backed by the messaging
    client stored in runtime data during integration setup.

    Args:
        hass: Home Assistant instance.
        entry: The config entry for this integration instance.
        async_add_entities: Callback to register new entities.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    messaging_client: GuestyMessagingClient = data["messaging_client"]
    async_add_entities(
        [GuestyNotifyEntity(messaging_client, entry)],
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "send_guest_message",
        {
            vol.Required("message"): str,
            vol.Required("reservation_id"): str,
            vol.Optional("channel"): str,
            vol.Optional("template_variables"): vol.Schema(
                {str: str},
            ),
        },
        "async_send_guest_message",
    )


class GuestyNotifyEntity(NotifyEntity):
    """Notify entity for sending messages to Guesty guests.

    Delegates message delivery to GuestyMessagingClient. The
    ``title`` parameter is used as the ``reservation_id`` for
    routing messages to the correct guest conversation.

    Attributes:
        _messaging_client: Injected messaging client.
        _attr_has_entity_name: Entity uses device-relative naming.
        _attr_name: Display name for this entity.
    """

    _attr_has_entity_name: bool = True
    _attr_name: str = "Messaging"

    def __init__(
        self,
        messaging_client: GuestyMessagingClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialize GuestyNotifyEntity.

        Args:
            messaging_client: Client for Guesty message delivery.
            entry: The config entry for this integration instance.
        """
        self._messaging_client = messaging_client
        self._attr_unique_id = f"{entry.entry_id}_messaging"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Guesty",
            manufacturer="Guesty",
        )

    async def async_send_message(
        self,
        message: str,
        title: str | None = None,
    ) -> None:
        """Send a message to a guest via Guesty.

        The ``title`` parameter carries the ``reservation_id`` that
        identifies which guest conversation receives the message.

        Args:
            message: The message body to send.
            title: The reservation ID (required).

        Raises:
            HomeAssistantError: If reservation_id is missing, message
                is empty, or the messaging client reports an error.
        """
        if not title:
            raise HomeAssistantError(
                "reservation_id is required: pass the Guesty "
                "reservation ID as the 'title' parameter"
            )

        await self._deliver_message(
            reservation_id=title,
            body=message,
        )

    async def async_send_guest_message(
        self,
        message: str,
        reservation_id: str,
        channel: str | None = None,
        template_variables: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> None:
        """Send a message with channel and template support.

        Entity service method accepting ``reservation_id``,
        ``channel``, and ``template_variables`` directly instead
        of encoding them in the ``title`` parameter.

        Args:
            message: The message body (may contain placeholders).
            reservation_id: Guesty reservation identifier.
            channel: Optional delivery channel override.
            template_variables: Optional placeholder substitutions.
            **_kwargs: Ignored additional service call data.

        Raises:
            HomeAssistantError: If inputs are invalid or delivery
                fails.
        """
        if not reservation_id:
            raise HomeAssistantError("reservation_id is required and must not be empty")

        await self._deliver_message(
            reservation_id=reservation_id,
            body=message,
            channel=channel,
            template_variables=template_variables,
        )

    async def _deliver_message(
        self,
        reservation_id: str,
        body: str,
        channel: str | None = None,
        template_variables: dict[str, str] | None = None,
    ) -> None:
        """Validate, send, and translate errors for a message.

        Shared implementation for both ``async_send_message`` and
        ``async_send_guest_message``.

        Args:
            reservation_id: Target reservation identifier.
            body: Message body text (may contain placeholders).
            channel: Optional delivery channel override.
            template_variables: Optional placeholder substitutions.

        Raises:
            HomeAssistantError: On validation failure, delivery
                error, or missing template variable.
        """
        if not body:
            raise HomeAssistantError("message body is required and must not be empty")

        try:
            await self._messaging_client.send_message(
                reservation_id=reservation_id,
                body=body,
                channel=channel,
                template_variables=template_variables,
            )
        except GuestyMessageError as exc:
            _LOGGER.error(
                "Message delivery failed for reservation '%s': %s",
                reservation_id,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except GuestyApiError as exc:
            _LOGGER.error(
                "API error for reservation '%s': %s",
                reservation_id,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except KeyError as exc:
            missing = exc.args[0] if exc.args else str(exc)
            raise HomeAssistantError(f"Missing template variable: {missing}") from exc
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc
