# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Client for sending messages via Guesty conversations."""

from __future__ import annotations

import asyncio
import json

from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    CONVERSATIONS_PATH,
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
    SEND_MESSAGE_PATH,
)
from custom_components.guesty.api.exceptions import (
    GuestyMessageError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    Conversation,
    MessageDeliveryResult,
    MessageRequest,
)


class GuestyMessagingClient:
    """Client for sending messages via Guesty conversations.

    Orchestrates conversation resolution, input validation,
    template rendering, and message delivery. This class has
    zero Home Assistant imports.

    Attributes:
        _api_client: Guesty API client for HTTP communication.
    """

    def __init__(self, api_client: GuestyApiClient) -> None:
        """Initialize GuestyMessagingClient.

        Args:
            api_client: Guesty API client for HTTP requests.
        """
        self._api_client = api_client

    async def resolve_conversation(
        self,
        reservation_id: str,
    ) -> Conversation:
        """Resolve a reservation ID to its Guesty conversation.

        Args:
            reservation_id: The reservation identifier.

        Returns:
            The Conversation associated with the reservation.

        Raises:
            ValueError: If reservation_id is empty.
            GuestyMessageError: If no conversation is found.
            GuestyResponseError: If the response is malformed.
            GuestyApiError: If the API request fails.
        """
        if not reservation_id:
            msg = "reservation_id must be non-empty"
            raise ValueError(msg)

        filters = json.dumps(
            [
                {
                    "field": "reservation._id",
                    "operator": "$eq",
                    "value": reservation_id,
                }
            ]
        )

        response = await self._api_client._request(
            "GET",
            CONVERSATIONS_PATH,
            params={"filters": filters},
        )

        if not response.is_success:
            raise GuestyMessageError(
                f"Conversation lookup failed for "
                f"reservation '{reservation_id}': "
                f"HTTP {response.status_code}",
                reservation_id=reservation_id,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Conversation response is not valid JSON",
            ) from exc

        results = data.get("results", [])

        if not results:
            raise GuestyMessageError(
                f"No conversation found for reservation '{reservation_id}'",
                reservation_id=reservation_id,
            )

        try:
            conv = results[0]
            available_modules = conv.get(
                "availableModules",
                [],
            )
            channels = tuple(m["type"] for m in available_modules if "type" in m)

            return Conversation(
                id=conv["_id"],
                reservation_id=reservation_id,
                available_channels=channels,
            )
        except (KeyError, ValueError) as exc:
            raise GuestyResponseError(
                f"Malformed conversation response: {exc}",
            ) from exc

    async def send_message(
        self,
        reservation_id: str,
        body: str,
        channel: str | None = None,
        template_variables: dict[str, str] | None = None,
    ) -> MessageDeliveryResult:
        """Send a message to a guest via Guesty.

        Pipeline: validate, render, resolve, send, result.

        Args:
            reservation_id: Target reservation identifier.
            body: Message body (may contain template variables).
            channel: Optional delivery channel override.
            template_variables: Optional variable substitutions.

        Returns:
            MessageDeliveryResult with delivery outcome.

        Raises:
            ValueError: If inputs fail validation.
            GuestyMessageError: If messaging fails.
            KeyError: If template has missing variables.
        """
        self._validate_inputs(reservation_id, body, channel)

        if template_variables is not None:
            body = self.render_template(body, template_variables)

        conversation = await self.resolve_conversation(
            reservation_id,
        )

        request = MessageRequest(
            conversation_id=conversation.id,
            body=body,
            channel=channel,
        )

        path = SEND_MESSAGE_PATH.format(
            conversation_id=request.conversation_id,
        )
        json_body: dict[str, object] = {"body": request.body}
        if request.channel is not None:
            json_body["module"] = {"type": request.channel}

        try:
            response = await self._api_client._request(
                "POST",
                path,
                json_data=json_body,
            )
        except GuestyMessageError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise GuestyMessageError(
                f"Failed to send message for reservation '{reservation_id}': {exc}",
                reservation_id=reservation_id,
            ) from exc

        if not response.is_success:
            raise GuestyMessageError(
                f"Message send failed for reservation "
                f"'{reservation_id}': "
                f"HTTP {response.status_code}",
                reservation_id=reservation_id,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Send-message response is not valid JSON",
            ) from exc

        return MessageDeliveryResult(
            success=True,
            message_id=data.get("_id"),
            reservation_id=reservation_id,
        )

    def render_template(
        self,
        template: str,
        variables: dict[str, str],
    ) -> str:
        """Render a message template with variable substitution.

        Uses str.format_map with strict missing-key detection.

        Args:
            template: Message with {variable} placeholders.
            variables: Variable name to value mapping.

        Returns:
            The rendered message with variables substituted.

        Raises:
            KeyError: If template references undefined variables.
        """
        return template.format_map(variables)

    @staticmethod
    def _validate_inputs(
        reservation_id: str,
        body: str,
        channel: str | None,
    ) -> None:
        """Validate send_message inputs before API calls.

        Args:
            reservation_id: Reservation identifier to validate.
            body: Message body to validate.
            channel: Optional channel to validate.

        Raises:
            ValueError: If any input is invalid.
        """
        if not reservation_id:
            msg = "reservation_id must be non-empty"
            raise ValueError(msg)
        if not body:
            msg = "body must be non-empty"
            raise ValueError(msg)
        if len(body) > MAX_MESSAGE_LENGTH:
            msg = f"body exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
            raise ValueError(msg)
        if channel is not None and channel not in KNOWN_CHANNEL_TYPES:
            msg = (
                f"unknown channel '{channel}'; "
                f"known types: {sorted(KNOWN_CHANNEL_TYPES)}"
            )
            raise ValueError(msg)
