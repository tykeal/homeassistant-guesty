# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyMessagingClient (T003-T005, T022-T024)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    CONVERSATIONS_PATH,
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
    SEND_MESSAGE_PATH,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyConnectionError,
    GuestyMessageError,
    GuestyResponseError,
)
from custom_components.guesty.api.messaging import (
    GuestyMessagingClient,
)
from custom_components.guesty.api.models import (
    Conversation,
    MessageDeliveryResult,
)
from tests.conftest import (
    FakeTokenStorage,
    make_token_response,
)


def _make_messaging_client() -> GuestyMessagingClient:
    """Create a GuestyMessagingClient with test defaults.

    Returns:
        A GuestyMessagingClient backed by test fakes.
    """
    storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    api_client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
    )
    return GuestyMessagingClient(api_client)


def _conversation_response(
    conv_id: str = "conv-abc123",
    reservation_id: str = "res-xyz789",
    channels: list[str] | None = None,
) -> dict[str, object]:
    """Create a mock conversation list response.

    Args:
        conv_id: Conversation identifier.
        reservation_id: Reservation identifier.
        channels: Available channel types.

    Returns:
        Dictionary matching the Guesty conversations endpoint.
    """
    if channels is None:
        channels = ["email", "sms", "platform"]
    return {
        "results": [
            {
                "_id": conv_id,
                "reservation": {"_id": reservation_id},
                "availableModules": [{"type": ch} for ch in channels],
            }
        ],
        "count": 1,
    }


def _send_message_response(
    message_id: str = "msg-def456",
    conv_id: str = "conv-abc123",
) -> dict[str, object]:
    """Create a mock send-message success response.

    Args:
        message_id: Message identifier.
        conv_id: Conversation identifier.

    Returns:
        Dictionary matching the Guesty send-message endpoint.
    """
    return {
        "_id": message_id,
        "conversationId": conv_id,
        "body": "Hello, guest!",
        "module": {"type": "email"},
        "createdAt": "2025-07-24T14:30:00.000Z",
    }


class TestResolveConversation:
    """Tests for resolve_conversation (T003)."""

    @respx.mock
    async def test_successful_resolution(self) -> None:
        """Resolve returns Conversation with correct fields."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )

        client = _make_messaging_client()
        conv = await client.resolve_conversation("res-xyz789")

        assert isinstance(conv, Conversation)
        assert conv.id == "conv-abc123"
        assert conv.reservation_id == "res-xyz789"
        assert conv.available_channels == (
            "email",
            "sms",
            "platform",
        )

    @respx.mock
    async def test_empty_results_raises(self) -> None:
        """Empty results raises GuestyMessageError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json={"results": [], "count": 0},
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyMessageError,
            match="No conversation",
        ) as exc_info:
            await client.resolve_conversation("res-missing")

        assert exc_info.value.reservation_id == "res-missing"

    @respx.mock
    async def test_api_error_propagation(self) -> None:
        """API connection error propagates from GuestyApiClient."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client = _make_messaging_client()
        with pytest.raises(GuestyConnectionError):
            await client.resolve_conversation("res-xyz789")

    async def test_empty_reservation_id_raises(self) -> None:
        """Empty reservation_id raises ValueError."""
        client = _make_messaging_client()
        with pytest.raises(ValueError, match="reservation_id"):
            await client.resolve_conversation("")

    @respx.mock
    async def test_non_success_status_raises(self) -> None:
        """Non-2xx response raises GuestyMessageError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                500,
                text="Internal Server Error",
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyMessageError,
            match="HTTP 500",
        ):
            await client.resolve_conversation("res-xyz789")

    @respx.mock
    async def test_invalid_json_raises(self) -> None:
        """Non-JSON response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                text="not json",
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.resolve_conversation("res-xyz789")

    @respx.mock
    async def test_malformed_response_raises(self) -> None:
        """Missing _id in conversation raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json={
                    "results": [{"no_id_field": True}],
                    "count": 1,
                },
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyResponseError,
            match="Malformed",
        ):
            await client.resolve_conversation("res-xyz789")


class TestSendMessage:
    """Tests for send_message (T004)."""

    @respx.mock
    async def test_full_success_path(self) -> None:
        """Send returns successful MessageDeliveryResult."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            return_value=Response(
                200,
                json=_send_message_response(),
            ),
        )

        client = _make_messaging_client()
        result = await client.send_message(
            "res-xyz789",
            "Hello, guest!",
        )

        assert isinstance(result, MessageDeliveryResult)
        assert result.success is True
        assert result.message_id == "msg-def456"
        assert result.reservation_id == "res-xyz789"

    @respx.mock
    async def test_conversation_resolution_failure(self) -> None:
        """Conversation failure propagates GuestyMessageError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json={"results": [], "count": 0},
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyMessageError,
            match="No conversation",
        ):
            await client.send_message("res-missing", "Hello")

    @respx.mock
    async def test_send_api_failure(self) -> None:
        """Send API failure raises GuestyMessageError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            side_effect=httpx.ConnectError("network error"),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyMessageError,
            match="Failed to send",
        ):
            await client.send_message("res-xyz789", "Hello")

    @respx.mock
    async def test_channel_passed_to_api(self) -> None:
        """Channel is in API request body module field."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        send_route = respx.post(
            f"{BASE_URL}{send_path}",
        ).mock(
            return_value=Response(
                200,
                json=_send_message_response(),
            ),
        )

        client = _make_messaging_client()
        await client.send_message(
            "res-xyz789",
            "Hello",
            channel="email",
        )

        sent_body = send_route.calls[0].request.content
        sent_json = json.loads(sent_body)
        assert sent_json["module"] == {"type": "email"}

    @respx.mock
    async def test_template_variables_rendered(self) -> None:
        """Template variables are substituted in the body."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        send_route = respx.post(
            f"{BASE_URL}{send_path}",
        ).mock(
            return_value=Response(
                200,
                json=_send_message_response(),
            ),
        )

        client = _make_messaging_client()
        await client.send_message(
            "res-xyz789",
            "Hi {guest_name}, code: {code}",
            template_variables={
                "guest_name": "Alice",
                "code": "1234",
            },
        )

        sent_body = send_route.calls[0].request.content
        sent_json = json.loads(sent_body)
        assert sent_json["body"] == "Hi Alice, code: 1234"

    @respx.mock
    async def test_message_error_not_wrapped(self) -> None:
        """GuestyMessageError from send is not double-wrapped."""
        from unittest.mock import patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )

        client = _make_messaging_client()
        original = client._api_client._request
        call_count = 0

        async def _side_effect(
            method: str,
            path: str,
            **kwargs: object,
        ) -> Response:
            """Return real response first, raise second."""
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise GuestyMessageError(
                    "direct error",
                    reservation_id="res-xyz789",
                )
            return await original(
                method,
                path,
                **kwargs,  # type: ignore[arg-type]
            )

        with (
            patch.object(
                client._api_client,
                "_request",
                side_effect=_side_effect,
            ),
            pytest.raises(
                GuestyMessageError,
                match="direct error",
            ),
        ):
            await client.send_message(
                "res-xyz789",
                "Hello",
            )

    @respx.mock
    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError during send is not wrapped."""
        import asyncio
        from unittest.mock import patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )

        client = _make_messaging_client()
        original = client._api_client._request
        call_count = 0

        async def _side_effect(
            method: str,
            path: str,
            **kwargs: object,
        ) -> Response:
            """Return real response first, cancel second."""
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            return await original(
                method,
                path,
                **kwargs,  # type: ignore[arg-type]
            )

        with (
            patch.object(
                client._api_client,
                "_request",
                side_effect=_side_effect,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await client.send_message(
                "res-xyz789",
                "Hello",
            )

    @respx.mock
    async def test_send_non_success_status_raises(self) -> None:
        """Non-2xx send response raises GuestyMessageError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            return_value=Response(
                400,
                json={"error": "Bad Request"},
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyMessageError,
            match="HTTP 400",
        ):
            await client.send_message(
                "res-xyz789",
                "Hello",
            )

    @respx.mock
    async def test_send_invalid_json_raises(self) -> None:
        """Non-JSON send response raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            return_value=Response(
                200,
                text="not json",
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.send_message(
                "res-xyz789",
                "Hello",
            )

    @respx.mock
    async def test_empty_dict_template_triggers_render(
        self,
    ) -> None:
        """Empty dict template_variables still triggers render."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        send_route = respx.post(
            f"{BASE_URL}{send_path}",
        ).mock(
            return_value=Response(
                200,
                json=_send_message_response(),
            ),
        )

        client = _make_messaging_client()
        # Body has no placeholders, empty dict should work
        result = await client.send_message(
            "res-xyz789",
            "Plain message",
            template_variables={},
        )
        assert result.success is True

        sent_body = send_route.calls[0].request.content
        sent_json = json.loads(sent_body)
        assert sent_json["body"] == "Plain message"


class TestRenderTemplate:
    """Tests for render_template (T005)."""

    def test_successful_substitution(self) -> None:
        """Variables are substituted correctly."""
        client = GuestyMessagingClient(MagicMock())
        result = client.render_template(
            "Hello {guest_name}, code is {access_code}",
            {"guest_name": "Alice", "access_code": "1234"},
        )
        assert result == "Hello Alice, code is 1234"

    def test_missing_variable_raises_key_error(self) -> None:
        """Missing variable raises KeyError."""
        client = GuestyMessagingClient(MagicMock())
        with pytest.raises(KeyError, match="guest_name"):
            client.render_template(
                "Hello {guest_name}",
                {},
            )

    def test_template_without_placeholders(self) -> None:
        """Template without placeholders returns unchanged."""
        client = GuestyMessagingClient(MagicMock())
        result = client.render_template(
            "Plain text message",
            {},
        )
        assert result == "Plain text message"

    def test_empty_variables_no_placeholders(self) -> None:
        """Empty variables with no placeholders succeeds."""
        client = GuestyMessagingClient(MagicMock())
        result = client.render_template(
            "No variables here",
            {},
        )
        assert result == "No variables here"


class TestInputValidation:
    """Tests for input validation in send_message (T005)."""

    async def test_empty_reservation_id_raises(self) -> None:
        """Empty reservation_id raises ValueError."""
        client = _make_messaging_client()
        with pytest.raises(ValueError, match="reservation_id"):
            await client.send_message("", "Hello")

    async def test_empty_body_raises(self) -> None:
        """Empty body raises ValueError."""
        client = _make_messaging_client()
        with pytest.raises(
            ValueError,
            match="body must be non-empty",
        ):
            await client.send_message("res-123", "")

    async def test_body_exceeding_max_length_raises(self) -> None:
        """Oversized body raises ValueError."""
        client = _make_messaging_client()
        long_body = "x" * (MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(ValueError, match="maximum length"):
            await client.send_message("res-123", long_body)

    async def test_unknown_channel_raises(self) -> None:
        """Unknown channel string raises ValueError."""
        client = _make_messaging_client()
        with pytest.raises(ValueError, match="unknown channel"):
            await client.send_message(
                "res-123",
                "Hello",
                channel="carrier_pigeon",
            )

    @respx.mock
    async def test_valid_channels_accepted(self) -> None:
        """Valid known channels are accepted."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            return_value=Response(
                200,
                json=_send_message_response(),
            ),
        )

        client = _make_messaging_client()
        for channel in sorted(KNOWN_CHANNEL_TYPES):
            result = await client.send_message(
                "res-xyz789",
                "Hello",
                channel=channel,
            )
            assert result.success is True


# ── Phase 4: Rate Limit Retry Integration Tests (T022) ──────────────


class TestRateLimitRetryMessaging:
    """Rate limit retry through messaging client (T022)."""

    @respx.mock
    async def test_429_then_success_delivers_message(self) -> None:
        """429 on send-message retried and delivered."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        send_route = respx.post(f"{BASE_URL}{send_path}")
        send_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0"},
            ),
            Response(
                200,
                json=_send_message_response(),
            ),
        ]

        client = _make_messaging_client()
        with _patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await client.send_message(
                "res-xyz789",
                "After 429",
            )

        assert result.success is True
        assert result.message_id == "msg-def456"

    @respx.mock
    async def test_429_on_conversation_retried(self) -> None:
        """429 on conversation lookup retried transparently."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        conv_route = respx.get(
            f"{BASE_URL}{CONVERSATIONS_PATH}",
        )
        conv_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "0"},
            ),
            Response(
                200,
                json=_conversation_response(),
            ),
        ]

        client = _make_messaging_client()
        with _patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
        ):
            conv = await client.resolve_conversation(
                "res-xyz789",
            )

        assert conv.id == "conv-abc123"

    @respx.mock
    async def test_429_backoff_uses_retry_after(self) -> None:
        """429 retry uses Retry-After header for delay."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        conv_route = respx.get(
            f"{BASE_URL}{CONVERSATIONS_PATH}",
        )
        conv_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "2"},
            ),
            Response(
                200,
                json=_conversation_response(),
            ),
        ]

        mock_sleep = AsyncMock()
        client = _make_messaging_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.resolve_conversation("res-xyz789")

        mock_sleep.assert_called_once()
        assert mock_sleep.call_args[0][0] == 2.0


# ── Phase 4: Transient Failure Retry Integration (T023) ─────────────


class TestTransientRetryMessaging:
    """Transient failure retry through messaging client (T023)."""

    @respx.mock
    async def test_connect_error_retried_then_delivers(
        self,
    ) -> None:
        """Transient connect error on send retried and delivered."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )

        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        call_count = 0

        async def _send_side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("refused")
            return Response(
                200,
                json=_send_message_response(),
            )

        respx.post(f"{BASE_URL}{send_path}").mock(
            side_effect=_send_side_effect,
        )

        client = _make_messaging_client()
        with _patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await client.send_message(
                "res-xyz789",
                "After transient fix",
            )

        assert result.success is True

    @respx.mock
    async def test_persistent_failure_raises_after_retries(
        self,
    ) -> None:
        """Persistent failure raises GuestyConnectionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client = _make_messaging_client()
        with (
            _patch(
                "asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(GuestyConnectionError, match="retries"),
        ):
            await client.send_message(
                "res-xyz789",
                "Unreachable",
            )

    @respx.mock
    async def test_5xx_retried_and_delivers(self) -> None:
        """5xx on send retried then delivers."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        send_route = respx.post(f"{BASE_URL}{send_path}")
        send_route.side_effect = [
            Response(502, text="Bad Gateway"),
            Response(
                200,
                json=_send_message_response(),
            ),
        ]

        client = _make_messaging_client()
        with _patch(
            "asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await client.send_message(
                "res-xyz789",
                "After 502",
            )

        assert result.success is True


# ── Phase 4: Error Detail Quality (T024) ────────────────────────────


class TestErrorDetailMessaging:
    """Error detail quality at messaging layer (T024)."""

    @respx.mock
    async def test_not_found_includes_reservation_id(self) -> None:
        """No-conversation error includes reservation ID."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json={"results": [], "count": 0},
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(GuestyMessageError) as exc_info:
            await client.send_message(
                "res-not-found",
                "Hello",
            )

        assert exc_info.value.reservation_id == "res-not-found"
        assert "res-not-found" in str(exc_info.value)

    @respx.mock
    async def test_send_failure_includes_reservation_id(
        self,
    ) -> None:
        """Send failure includes reservation ID in error."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            return_value=Response(
                200,
                json=_conversation_response(),
            ),
        )
        send_path = SEND_MESSAGE_PATH.format(
            conversation_id="conv-abc123",
        )
        respx.post(f"{BASE_URL}{send_path}").mock(
            return_value=Response(
                400,
                json={"error": "Bad Request"},
            ),
        )

        client = _make_messaging_client()
        with pytest.raises(GuestyMessageError) as exc_info:
            await client.send_message(
                "res-fail-detail",
                "Hello",
            )

        assert "res-fail-detail" in str(exc_info.value)

    @respx.mock
    async def test_connection_error_after_retries_readable(
        self,
    ) -> None:
        """Connection error after retries is human-readable."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}{CONVERSATIONS_PATH}").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )

        client = _make_messaging_client()
        with (
            _patch(
                "asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(GuestyConnectionError) as exc_info,
        ):
            await client.resolve_conversation("res-xyz789")

        # The error message mentions retries
        assert "retries" in str(exc_info.value).lower()
