# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyTokenManager token acquisition."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.const import TOKEN_URL
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyResponseError,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_TOKEN,
    FakeTokenStorage,
    make_token_response,
)


def _make_manager(
    storage: FakeTokenStorage | None = None,
    **kwargs: object,
) -> tuple[GuestyTokenManager, FakeTokenStorage]:
    """Create a GuestyTokenManager with test defaults.

    Args:
        storage: Optional FakeTokenStorage to use.
        **kwargs: Additional keyword arguments for the manager.

    Returns:
        Tuple of (token_manager, storage).
    """
    if storage is None:
        storage = FakeTokenStorage()
    manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=httpx.AsyncClient(),
        storage=storage,
        refresh_buffer=0,
        **kwargs,  # type: ignore[arg-type]
    )
    return manager, storage


class TestTokenAcquisition:
    """Tests for basic token acquisition via get_token()."""

    @respx.mock
    async def test_successful_acquisition(self) -> None:
        """get_token() acquires a token from the endpoint."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        manager, _ = _make_manager()
        token = await manager.get_token()
        assert token == FAKE_TOKEN

    @respx.mock
    async def test_cached_token_reuse(self) -> None:
        """get_token() reuses cached token on second call."""
        route = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        manager, _ = _make_manager()
        token1 = await manager.get_token()
        token2 = await manager.get_token()
        assert token1 == token2
        assert route.call_count == 1

    @respx.mock
    async def test_auth_error_on_401(self) -> None:
        """get_token() raises GuestyAuthError on 401."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyAuthError, match="Invalid client"):
            await manager.get_token()

    @respx.mock
    async def test_connection_error_on_network_failure(self) -> None:
        """get_token() raises GuestyConnectionError on timeout."""
        respx.post(TOKEN_URL).mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyConnectionError, match="connect"):
            await manager.get_token()

    @respx.mock
    async def test_response_error_on_malformed_json(self) -> None:
        """get_token() raises GuestyResponseError on bad JSON."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, text="not json"),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyResponseError, match="not valid JSON"):
            await manager.get_token()

    @respx.mock
    async def test_response_error_on_missing_fields(self) -> None:
        """get_token() raises GuestyResponseError on missing fields."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json={"token_type": "Bearer"}),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyResponseError, match="Malformed"):
            await manager.get_token()

    @respx.mock
    async def test_token_saved_to_storage(self) -> None:
        """get_token() persists acquired token via storage."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        manager, storage = _make_manager()
        await manager.get_token()
        saved = await storage.load_token()
        assert saved is not None
        assert saved.access_token == FAKE_TOKEN

    @respx.mock
    async def test_seed_token_avoids_request(self) -> None:
        """seed_token() prevents a network request."""
        from custom_components.guesty.api.models import CachedToken

        route = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        manager, _ = _make_manager()
        manager.seed_token(
            CachedToken(
                access_token="seeded-token",
                token_type="Bearer",
                expires_in=86400,
                scope="open-api",
                issued_at=datetime.now(UTC),
            ),
        )
        token = await manager.get_token()
        assert token == "seeded-token"
        assert route.call_count == 0
