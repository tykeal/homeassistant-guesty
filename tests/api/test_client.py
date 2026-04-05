# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyApiClient including test_connection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import BASE_URL, TOKEN_URL
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
)
from custom_components.guesty.api.models import CachedToken
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_TOKEN,
    FakeTokenStorage,
    make_token_response,
)


def _make_client(
    storage: FakeTokenStorage | None = None,
) -> tuple[GuestyApiClient, GuestyTokenManager, FakeTokenStorage]:
    """Create a GuestyApiClient with test defaults.

    Args:
        storage: Optional FakeTokenStorage to use.

    Returns:
        Tuple of (api_client, token_manager, storage).
    """
    if storage is None:
        storage = FakeTokenStorage()
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=http,
        storage=storage,
        refresh_buffer=0,
    )
    client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
    )
    return client, token_manager, storage


class TestTestConnection:
    """Tests for GuestyApiClient.test_connection()."""

    @respx.mock
    async def test_successful_connection(self) -> None:
        """test_connection() succeeds with valid credentials."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )
        client, _, _ = _make_client()
        result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """test_connection() propagates GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.test_connection()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """test_connection() propagates GuestyConnectionError."""
        respx.post(TOKEN_URL).mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.test_connection()

    @respx.mock
    async def test_bearer_header_included(self) -> None:
        """Requests include Authorization: Bearer header."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        route = respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(200, json={"results": []}),
        )
        client, _, _ = _make_client()
        await client.test_connection()
        request = route.calls[0].request
        assert request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"


class TestReactiveRefresh:
    """Tests for reactive 401 refresh in _request()."""

    @respx.mock
    async def test_401_triggers_refresh_and_retry(self) -> None:
        """A 401 response triggers token invalidation and retry."""
        # First token request succeeds
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        # First API call returns 401, retry succeeds
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(401, json={"error": "Unauthorized"}),
            Response(200, json={"results": []}),
        ]

        storage = FakeTokenStorage()
        client, manager, _ = _make_client(storage)
        # Pre-seed a token so the first call uses it
        manager.seed_token(
            CachedToken(
                access_token="old-token",
                token_type="Bearer",
                expires_in=86400,
                scope="open-api",
                issued_at=datetime.now(UTC),
            ),
        )

        result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_double_401_raises_auth_error(self) -> None:
        """Two consecutive 401s raise GuestyAuthError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                401,
                json={"error": "Unauthorized"},
            ),
        )

        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError, match="after token refresh"):
            await client.test_connection()


class TestRateLimitBackoff:
    """Tests for HTTP 429 exponential backoff."""

    @respx.mock
    async def test_429_triggers_retry(self) -> None:
        """429 response triggers retry with backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "0"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()
        assert result is True

    @respx.mock
    async def test_429_retry_after_header_respected(self) -> None:
        """Retry-After header is used for backoff delay."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "5"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay == 5.0

    @respx.mock
    async def test_429_max_retries_raises(self) -> None:
        """Exhausted retries raise GuestyRateLimitError."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                429,
                headers={"Retry-After": "0"},
            ),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyRateLimitError, match="max retries"),
        ):
            await client.test_connection()

    @respx.mock
    async def test_429_without_retry_after_uses_jitter(self) -> None:
        """429 without Retry-After header uses jitter backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay >= 0.1

    @respx.mock
    async def test_429_max_retries_no_retry_after(self) -> None:
        """Max retries with no Retry-After raises with None."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(429),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyRateLimitError) as exc_info,
        ):
            await client.test_connection()
        assert exc_info.value.retry_after is None

    @respx.mock
    async def test_429_invalid_retry_after_header(self) -> None:
        """Invalid Retry-After header falls back to jitter."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(429, headers={"Retry-After": "not-a-number"}),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()


class TestConnectionTestFailure:
    """Tests for test_connection non-success responses."""

    @respx.mock
    async def test_non_success_raises_response_error(self) -> None:
        """test_connection raises on non-success status."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(500, text="Internal Server Error"),
        )

        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Connection test failed",
        ):
            await client.test_connection()


class TestRequestNetworkError:
    """Tests for network errors during API requests."""

    @respx.mock
    async def test_connect_error_in_request(self) -> None:
        """ConnectError during API call raises GuestyConnectionError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.test_connection()

    @respx.mock
    async def test_timeout_in_request(self) -> None:
        """TimeoutException during API call raises error."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.TimeoutException("timed out"),
        )

        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.test_connection()


class TestForbiddenResponse:
    """Tests for HTTP 403 Forbidden responses."""

    @respx.mock
    async def test_403_raises_auth_error(self) -> None:
        """403 response raises GuestyAuthError."""
        from custom_components.guesty.api.exceptions import (
            GuestyAuthError as AuthError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                403,
                json={"error": "Forbidden"},
            ),
        )

        client, _, _ = _make_client()
        with pytest.raises(AuthError, match="Insufficient permissions"):
            await client.test_connection()
