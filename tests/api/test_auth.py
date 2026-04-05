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


class TestProactiveRefresh:
    """Tests for proactive token refresh with buffer."""

    @respx.mock
    async def test_refresh_within_buffer(self) -> None:
        """get_token() refreshes when within buffer of expiry."""
        from datetime import timedelta

        from custom_components.guesty.api.models import CachedToken

        route = respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(access_token="new-token"),
            ),
        )
        manager, _ = _make_manager()
        # Seed a token that is about to expire (within 300s buffer)
        manager._refresh_buffer = 300
        manager.seed_token(
            CachedToken(
                access_token="old-token",
                token_type="Bearer",
                expires_in=86400,
                scope="open-api",
                issued_at=datetime.now(UTC) - timedelta(seconds=86200),
            ),
        )
        token = await manager.get_token()
        assert token == "new-token"
        assert route.call_count == 1

    @respx.mock
    async def test_invalidate_forces_reacquisition(self) -> None:
        """invalidate() clears cache; next get_token() re-acquires."""
        from custom_components.guesty.api.models import CachedToken

        route = respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(access_token="fresh"),
            ),
        )
        manager, _ = _make_manager()
        manager.seed_token(
            CachedToken(
                access_token="cached",
                token_type="Bearer",
                expires_in=86400,
                scope="open-api",
                issued_at=datetime.now(UTC),
            ),
        )
        manager.invalidate()
        token = await manager.get_token()
        assert token == "fresh"
        assert route.call_count == 1


class TestConcurrentAccess:
    """Tests for concurrent token access with locking."""

    @respx.mock
    async def test_concurrent_callers_single_request(self) -> None:
        """Multiple concurrent get_token() calls make one request."""
        import asyncio

        route = respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        manager, _ = _make_manager()
        tokens = await asyncio.gather(
            *[manager.get_token() for _ in range(5)],
        )
        assert all(t == FAKE_TOKEN for t in tokens)
        # Double-checked locking means at most 1 request
        assert route.call_count == 1


class TestTokenRateLimit:
    """Tests for 5-per-24h token request rate limit."""

    @respx.mock
    async def test_requests_1_through_5_succeed(self) -> None:
        """First 5 token requests succeed."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        storage = FakeTokenStorage()
        for _i in range(5):
            manager, _ = _make_manager(storage=storage)
            manager.invalidate()
            token = await manager.get_token()
            assert token == FAKE_TOKEN

    @respx.mock
    async def test_request_6_raises_rate_limit(self) -> None:
        """6th token request raises GuestyRateLimitError."""
        from custom_components.guesty.api.exceptions import (
            GuestyRateLimitError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        storage = FakeTokenStorage()
        # Make 5 requests
        for _i in range(5):
            manager, _ = _make_manager(storage=storage)
            manager.invalidate()
            await manager.get_token()

        # 6th should fail
        manager, _ = _make_manager(storage=storage)
        manager.invalidate()
        with pytest.raises(GuestyRateLimitError, match="rate limit"):
            await manager.get_token()

    @respx.mock
    async def test_counter_persisted_via_storage(self) -> None:
        """Request counter is saved via TokenStorage."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        storage = FakeTokenStorage()
        manager, _ = _make_manager(storage=storage)
        await manager.get_token()

        count, window = await storage.load_request_count()
        assert count == 1
        assert window is not None


class TestDoubleCheckedLocking:
    """Tests for double-checked locking inside the async lock."""

    @respx.mock
    async def test_second_caller_finds_cache_inside_lock(
        self,
    ) -> None:
        """Second caller hits cache check inside the lock."""
        import asyncio

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        manager, _ = _make_manager()

        acquired = asyncio.Event()
        release = asyncio.Event()
        original_check = manager._check_rate_limit
        call_count = 0

        async def slow_check() -> None:
            """Delay inside the lock on the first call."""
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                acquired.set()
                await release.wait()
            await original_check()

        manager._check_rate_limit = slow_check  # type: ignore[assignment]

        task_a = asyncio.create_task(manager.get_token())
        await acquired.wait()

        task_b = asyncio.create_task(manager.get_token())
        await asyncio.sleep(0)

        release.set()

        token_a = await task_a
        token_b = await task_b

        assert token_a == FAKE_TOKEN
        assert token_b == FAKE_TOKEN


class TestStaleWindowReset:
    """Tests for stale window reset in _request_token."""

    @respx.mock
    async def test_stale_window_resets_counter(self) -> None:
        """Stale window in _request_token resets count."""
        from datetime import timedelta

        from custom_components.guesty.api.const import (
            TOKEN_WINDOW_SECONDS,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        manager, _storage = _make_manager()

        old_window = datetime.now(UTC) - timedelta(
            seconds=TOKEN_WINDOW_SECONDS + 1,
        )
        manager._window_start = old_window
        manager._request_count = 2

        token = await manager._request_token()
        assert token.access_token == FAKE_TOKEN
        assert manager._request_count == 1
        assert manager._window_start != old_window


class TestEdgeCases:
    """Edge case tests for token manager."""

    @respx.mock
    async def test_unexpected_status_code(self) -> None:
        """Unexpected status code raises GuestyResponseError."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(500, text="Internal Server Error"),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyResponseError, match="Unexpected"):
            await manager.get_token()

    @respx.mock
    async def test_timeout_raises_connection_error(self) -> None:
        """Timeout raises GuestyConnectionError."""
        respx.post(TOKEN_URL).mock(
            side_effect=httpx.TimeoutException("timed out"),
        )
        manager, _ = _make_manager()
        with pytest.raises(GuestyConnectionError, match="connect"):
            await manager.get_token()
