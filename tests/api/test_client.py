# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyApiClient including test_connection and listings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
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


# ── get_listings() Tests (T006) ─────────────────────────────────────


def _make_listing_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API listing dictionary with defaults.

    Args:
        **overrides: Fields to override on the default listing.

    Returns:
        Dictionary matching the Guesty listings API format.
    """
    defaults: dict[str, Any] = {
        "_id": "507f1f77bcf86cd799439011",
        "title": "Beach House",
        "nickname": "Beach Alt",
        "listed": True,
        "active": True,
        "address": {
            "full": "123 Beach Rd, Miami, FL 33139, USA",
        },
        "propertyType": "apartment",
        "roomType": "Entire home/apartment",
        "numberOfBedrooms": 2,
        "numberOfBathrooms": 1.5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckoutTime": "11:00",
        "tags": ["pet-friendly"],
        "customFields": {"region": "southeast"},
    }
    defaults.update(overrides)
    return defaults


def _make_page_response(
    listings: list[dict[str, Any]],
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty listings page response.

    Args:
        listings: Array of listing dictionaries.
        count: Total count metadata.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the listings endpoint format.
    """
    return {
        "results": listings,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


class TestGetListings:
    """Tests for GuestyApiClient.get_listings()."""

    @respx.mock
    async def test_single_page_fetch(self) -> None:
        """Single page with fewer results than limit stops."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings = [_make_listing_dict(_id=f"id-{i}") for i in range(5)]
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response(
                    listings,
                    count=5,
                    limit=100,
                    skip=0,
                ),
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_listings()
        assert len(result) == 5
        assert result[0].id == "id-0"

    @respx.mock
    async def test_multi_page_pagination(self) -> None:
        """Paginate through multiple pages to partial page."""
        from custom_components.guesty.api.const import (
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        page1 = [_make_listing_dict(_id=f"p1-{i}") for i in range(LISTINGS_PAGE_SIZE)]
        page2 = [_make_listing_dict(_id=f"p2-{i}") for i in range(LISTINGS_PAGE_SIZE)]
        page3 = [_make_listing_dict(_id=f"p3-{i}") for i in range(30)]

        route = respx.get(f"{BASE_URL}/listings")
        route.side_effect = [
            Response(
                200,
                json=_make_page_response(
                    page1,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=0,
                ),
            ),
            Response(
                200,
                json=_make_page_response(
                    page2,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=LISTINGS_PAGE_SIZE,
                ),
            ),
            Response(
                200,
                json=_make_page_response(
                    page3,
                    count=230,
                    limit=LISTINGS_PAGE_SIZE,
                    skip=LISTINGS_PAGE_SIZE * 2,
                ),
            ),
        ]

        client, _, _ = _make_client()
        result = await client.get_listings()
        assert len(result) == 230

    @respx.mock
    async def test_empty_account(self) -> None:
        """Empty account returns empty list."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response([], count=0),
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_listings()
        assert result == []

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """GuestyAuthError propagates from get_listings."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.get_listings()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """GuestyConnectionError propagates from get_listings."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.get_listings()

    @respx.mock
    async def test_response_error_on_malformed(self) -> None:
        """GuestyResponseError on malformed API response."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json={"unexpected": "format"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyResponseError):
            await client.get_listings()

    @respx.mock
    async def test_non_success_status_raises(self) -> None:
        """Non-success HTTP status raises GuestyResponseError."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(500, text="Server Error"),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Listings fetch failed",
        ):
            await client.get_listings()

    @respx.mock
    async def test_invalid_json_raises(self) -> None:
        """Non-JSON response body raises GuestyResponseError."""
        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                content=b"not json",
                headers={"content-type": "text/plain"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="not valid JSON",
        ):
            await client.get_listings()
