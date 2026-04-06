# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty notify platform for sending messages to guests.

Implements the modern NotifyEntity pattern, enabling property managers
to send messages to guests by reservation ID through Home Assistant
service calls, automations, and scripts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.notify import NotifyEntity
from homeassistant.exceptions import HomeAssistantError
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

        if not message:
            raise HomeAssistantError("message body is required and must not be empty")

        try:
            await self._messaging_client.send_message(
                reservation_id=title,
                body=message,
            )
        except GuestyMessageError as exc:
            _LOGGER.error(
                "Message delivery failed for reservation '%s': %s",
                title,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except GuestyApiError as exc:
            _LOGGER.error(
                "API error for reservation '%s': %s",
                title,
                exc,
                exc_info=True,
            )
            raise HomeAssistantError(str(exc)) from exc
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc
