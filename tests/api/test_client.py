# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyApiClient including test_connection."""

from __future__ import annotations

from datetime import UTC, datetime

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
