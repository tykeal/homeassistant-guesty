# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for GuestyApiClient including test_connection and listings.

Also includes Phase 4 (T025) transient failure retry tests.
"""

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
    GuestyResponseError,
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

    @respx.mock
    async def test_429_negative_retry_after_clamped(self) -> None:
        """Negative Retry-After is clamped to minimum delay."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "-1"},
            ),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            result = await client.test_connection()

        assert result is True
        mock_sleep.assert_called_once()
        delay = mock_sleep.call_args[0][0]
        assert delay >= 0.1


class TestConnectionTestFailure:
    """Tests for test_connection non-success responses."""

    @respx.mock
    async def test_non_success_raises_response_error(self) -> None:
        """test_connection raises on non-success status."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyResponseError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(422, text="Unprocessable Entity"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyResponseError,
                match="Connection test failed: status 422",
            ),
        ):
            await client.test_connection()


class TestRequestNetworkError:
    """Tests for network errors during API requests."""

    @respx.mock
    async def test_connect_error_in_request(self) -> None:
        """ConnectError during API call raises GuestyConnectionError."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.ConnectError("refused"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
            await client.test_connection()

    @respx.mock
    async def test_timeout_in_request(self) -> None:
        """TimeoutException during API call raises error."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(200, json=make_token_response()),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=httpx.TimeoutException("timed out"),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
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
        "type": "SINGLE",
        "bedrooms": 2,
        "bathrooms": 1.5,
        "accommodates": 5,
        "timezone": "America/New_York",
        "defaultCheckInTime": "15:00",
        "defaultCheckOutTime": "11:00",
        "tags": ["pet-friendly"],
        "customFields": [
            {"fieldId": "cf_region", "value": "southeast"},
        ],
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
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings = [_make_listing_dict(_id=f"id-{i}") for i in range(5)]
        route = respx.get(f"{BASE_URL}/listings").mock(
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

        assert len(route.calls) == 1
        params = route.calls[0].request.url.params
        assert params["limit"] == str(LISTINGS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["fields"] == " ".join(LISTINGS_FIELDS)

    @respx.mock
    async def test_multi_page_pagination(self) -> None:
        """Paginate through multiple pages to partial page."""
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
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

        expected_fields = " ".join(LISTINGS_FIELDS)
        assert len(route.calls) == 3
        for idx, call in enumerate(route.calls):
            params = call.request.url.params
            assert params["limit"] == str(LISTINGS_PAGE_SIZE)
            assert params["skip"] == str(LISTINGS_PAGE_SIZE * idx)
            assert params["fields"] == expected_fields

    @respx.mock
    async def test_empty_account(self) -> None:
        """Empty account returns empty list."""
        from custom_components.guesty.api.const import (
            LISTINGS_FIELDS,
            LISTINGS_PAGE_SIZE,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        route = respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response([], count=0),
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_listings()
        assert result == []

        assert len(route.calls) == 1
        params = route.calls[0].request.url.params
        assert params["limit"] == str(LISTINGS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["fields"] == " ".join(LISTINGS_FIELDS)

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
        from unittest.mock import patch as _patch

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
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(GuestyConnectionError),
        ):
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
        from unittest.mock import patch as _patch

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
            return_value=Response(422, text="Unprocessable Entity"),
        )
        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyResponseError,
                match="Listings fetch failed: status 422",
            ),
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

    @respx.mock
    async def test_non_dict_json_raises(self) -> None:
        """Non-dict JSON body raises GuestyResponseError."""
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
            return_value=Response(200, json=[1, 2, 3]),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.get_listings()

    @respx.mock
    async def test_non_list_results_raises(self) -> None:
        """Non-list results field raises GuestyResponseError."""
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
                json={"results": "not-a-list", "count": 0},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a list",
        ):
            await client.get_listings()

    @respx.mock
    async def test_debug_logs_sample_keys(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debug log emits sorted keys of first listing."""
        import logging

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings = [_make_listing_dict()]
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                200,
                json=_make_page_response(
                    listings,
                    count=1,
                ),
            ),
        )
        client, _, _ = _make_client()
        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.api.client",
        ):
            result = await client.get_listings()
        assert len(result) == 1
        expected_keys = sorted(listings[0].keys())
        assert f"Sample listing API keys: {expected_keys}" in caplog.text


# ── get_reservations() Tests (T004) ─────────────────────────────────


def _make_reservation_dict(**overrides: Any) -> dict[str, Any]:
    """Create a Guesty API reservation dictionary with defaults.

    Args:
        **overrides: Fields to override on the default reservation.

    Returns:
        Dictionary matching the Guesty API reservation format.
    """
    defaults: dict[str, Any] = {
        "_id": "res-001",
        "listingId": "listing-001",
        "status": "confirmed",
        "checkIn": "2025-08-17T15:00:00.000Z",
        "checkOut": "2025-08-22T11:00:00.000Z",
        "confirmationCode": "GY-h5SdcsBL",
        "checkInDateLocalized": "2025-08-17",
        "checkOutDateLocalized": "2025-08-22",
        "plannedArrival": "16:00",
        "plannedDeparture": "10:00",
        "nightsCount": 5,
        "guestsCount": 3,
        "guest": {
            "fullName": "Jane Smith",
            "phone": "+1-555-0123",
            "email": "jane@example.com",
        },
        "money": {
            "totalPaid": 1250.00,
            "balanceDue": 0.00,
            "currency": "USD",
        },
        "source": "airbnb",
        "note": "Late check-in",
    }
    defaults.update(overrides)
    return defaults


def _make_reservations_page(
    reservations: list[dict[str, Any]],
    *,
    count: int = 1,
    limit: int = 100,
    skip: int = 0,
) -> dict[str, Any]:
    """Create a Guesty reservations page response.

    Args:
        reservations: Array of reservation dictionaries.
        count: Total count metadata.
        limit: Page size.
        skip: Offset.

    Returns:
        Dictionary matching the reservations endpoint format.
    """
    return {
        "results": reservations,
        "count": count,
        "limit": limit,
        "skip": skip,
    }


def _mock_token_endpoint() -> None:
    """Mock the token endpoint for all reservation tests."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


class TestGetReservations:
    """Tests for GuestyApiClient.get_reservations()."""

    @respx.mock
    async def test_single_page_fetch(self) -> None:
        """Single page with fewer results than limit stops."""
        from custom_components.guesty.api.const import (
            RESERVATIONS_FIELDS,
            RESERVATIONS_PAGE_SIZE,
        )

        _mock_token_endpoint()
        reservations = [_make_reservation_dict(_id=f"res-{i}") for i in range(5)]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            # Primary request
            Response(
                200,
                json=_make_reservations_page(
                    reservations,
                    count=5,
                ),
            ),
            # Secondary checked_in request
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert len(result) == 5
        assert result[0].id == "res-0"

        # Verify primary request params
        primary_req = route.calls[0].request
        params = primary_req.url.params
        assert params["limit"] == str(RESERVATIONS_PAGE_SIZE)
        assert params["skip"] == "0"
        assert params["sort"] == "_id"
        assert params["fields"] == " ".join(RESERVATIONS_FIELDS)
        # Verify primary request has query params
        assert "checkIn" in params
        assert "checkOut" in params
        assert "status" in params
        assert "filters" not in params

    @respx.mock
    async def test_multi_page_pagination(self) -> None:
        """Paginate through multiple pages."""
        from custom_components.guesty.api.const import (
            RESERVATIONS_PAGE_SIZE,
        )

        _mock_token_endpoint()
        page1 = [
            _make_reservation_dict(_id=f"p1-{i}") for i in range(RESERVATIONS_PAGE_SIZE)
        ]
        page2 = [_make_reservation_dict(_id=f"p2-{i}") for i in range(30)]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            # Primary: page 1 (full)
            Response(
                200,
                json=_make_reservations_page(
                    page1,
                    count=130,
                    limit=RESERVATIONS_PAGE_SIZE,
                    skip=0,
                ),
            ),
            # Primary: page 2 (partial)
            Response(
                200,
                json=_make_reservations_page(
                    page2,
                    count=130,
                    limit=RESERVATIONS_PAGE_SIZE,
                    skip=RESERVATIONS_PAGE_SIZE,
                ),
            ),
            # Secondary: empty
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert len(result) == 130

    @respx.mock
    async def test_empty_account_returns_empty_list(self) -> None:
        """Empty account returns empty list."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert result == []

    @respx.mock
    async def test_dual_request_merge(self) -> None:
        """Primary and secondary results are merged."""
        _mock_token_endpoint()
        primary = [_make_reservation_dict(_id="primary-1")]
        secondary = [
            _make_reservation_dict(
                _id="secondary-1",
                status="checked_in",
            ),
        ]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(primary, count=1),
            ),
            Response(
                200,
                json=_make_reservations_page(
                    secondary,
                    count=1,
                ),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert len(result) == 2
        ids = {r.id for r in result}
        assert ids == {"primary-1", "secondary-1"}

    @respx.mock
    async def test_deduplication_by_reservation_id(self) -> None:
        """Duplicate IDs across requests are de-duplicated."""
        _mock_token_endpoint()
        shared = _make_reservation_dict(
            _id="shared-1",
            status="checked_in",
        )
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [shared],
                    count=1,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page(
                    [shared],
                    count=1,
                ),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert len(result) == 1
        assert result[0].id == "shared-1"

    @respx.mock
    async def test_secondary_checked_in_filter(self) -> None:
        """Secondary request uses checked_in status filter."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        await client.get_reservations()
        # Second call is the secondary request
        secondary_req = route.calls[1].request
        assert secondary_req.url.params["status"] == "checked_in"
        assert "checkIn" not in secondary_req.url.params
        assert "checkOut" not in secondary_req.url.params
        assert "filters" not in secondary_req.url.params

    @respx.mock
    async def test_date_range_boundaries(self) -> None:
        """Date boundaries computed from past/future days."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.client import (
            datetime as client_datetime,
        )

        _mock_token_endpoint()
        fixed_now = datetime(2025, 8, 1, 12, 0, 0, tzinfo=UTC)
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        with _patch(
            "custom_components.guesty.api.client.datetime",
            wraps=client_datetime,
        ) as mock_dt:
            mock_dt.now.return_value = fixed_now
            await client.get_reservations(
                past_days=7,
                future_days=14,
            )

        primary_req = route.calls[0].request
        params = primary_req.url.params
        assert params["checkIn"] == "2025-07-25"
        assert params["checkOut"] == "2025-08-15"

    @respx.mock
    async def test_custom_statuses(self) -> None:
        """Custom statuses are passed through in filter."""
        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        custom = frozenset({"confirmed", "checked_in"})
        await client.get_reservations(statuses=custom)
        primary_req = route.calls[0].request
        status_param = primary_req.url.params["status"]
        assert sorted(status_param.split(",")) == [
            "checked_in",
            "confirmed",
        ]

    @respx.mock
    async def test_auth_error_propagation(self) -> None:
        """GuestyAuthError propagates from get_reservations."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                401,
                json={"error": "invalid_client"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyAuthError):
            await client.get_reservations()

    @respx.mock
    async def test_connection_error_propagation(self) -> None:
        """GuestyConnectionError propagates."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            side_effect=httpx.ConnectError("refused"),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyConnectionError):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_malformed_json(self) -> None:
        """GuestyResponseError on malformed API response."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
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
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_dict(self) -> None:
        """GuestyResponseError on non-dict JSON response."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(200, json=[1, 2, 3]),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a JSON object",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_missing_results(self) -> None:
        """GuestyResponseError when results key is absent."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(
                200,
                json={"unexpected": "format"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(GuestyResponseError):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_list_results(self) -> None:
        """GuestyResponseError when results is not a list."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(
                200,
                json={"results": "not-a-list", "count": 0},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="must be a list",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_response_error_on_non_success(self) -> None:
        """Non-success HTTP status raises GuestyResponseError."""
        _mock_token_endpoint()
        respx.get(f"{BASE_URL}/reservations").mock(
            return_value=Response(422, text="Unprocessable Entity"),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Reservations fetch failed",
        ):
            await client.get_reservations()

    @respx.mock
    async def test_invalid_reservations_skipped(self) -> None:
        """Reservations with missing fields are skipped."""
        _mock_token_endpoint()
        valid = _make_reservation_dict(_id="valid-1")
        invalid = {"status": "confirmed"}  # missing _id
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    [valid, invalid],
                    count=2,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        result = await client.get_reservations()
        assert len(result) == 1
        assert result[0].id == "valid-1"

    @respx.mock
    async def test_default_actionable_statuses(self) -> None:
        """Default statuses filter uses ACTIONABLE_STATUSES."""
        from custom_components.guesty.api.const import (
            ACTIONABLE_STATUSES,
        )

        _mock_token_endpoint()
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        await client.get_reservations()
        primary_req = route.calls[0].request
        status_param = primary_req.url.params["status"]
        assert set(status_param.split(",")) == ACTIONABLE_STATUSES

    @respx.mock
    async def test_debug_logs_reservation_sample_keys(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Debug log emits sorted keys of first reservation and guest."""
        import logging

        _mock_token_endpoint()
        reservations = [_make_reservation_dict()]
        route = respx.get(f"{BASE_URL}/reservations")
        route.side_effect = [
            Response(
                200,
                json=_make_reservations_page(
                    reservations,
                    count=1,
                ),
            ),
            Response(
                200,
                json=_make_reservations_page([], count=0),
            ),
        ]
        client, _, _ = _make_client()
        with caplog.at_level(
            logging.DEBUG,
            logger="custom_components.guesty.api.client",
        ):
            result = await client.get_reservations()
        assert len(result) == 1
        expected_keys = sorted(reservations[0].keys())
        expected_guest_keys = sorted(
            reservations[0]["guest"].keys(),
        )
        assert f"Sample reservation keys: {expected_keys}" in caplog.text
        assert f"guest keys: {expected_guest_keys}" in caplog.text


# ── Phase 4: Transient Failure Retry Tests (T025) ───────────────────


class TestTransientRetry:
    """Tests for transient failure retry with backoff (T025)."""

    @respx.mock
    async def test_connect_error_retry_then_success(self) -> None:
        """ConnectError retried then succeeds on next attempt."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("connection refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True
        assert call_count == 2

    @respx.mock
    async def test_timeout_retry_then_success(self) -> None:
        """TimeoutException retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Timeout first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.TimeoutException("timed out")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_persistent_connect_error_exhausts_retries(
        self,
    ) -> None:
        """Persistent ConnectError raises after retry exhaustion."""
        from unittest.mock import patch as _patch

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
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyConnectionError,
                match="after 3 retries",
            ),
        ):
            await client.test_connection()

    @respx.mock
    async def test_connect_error_uses_exponential_backoff(
        self,
    ) -> None:
        """Connect error retries use exponential backoff."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail twice, succeed third."""
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ConnectError("refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        mock_sleep = AsyncMock()
        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        # Two retries means two sleep calls
        assert mock_sleep.call_count == 2
        delays = [c[0][0] for c in mock_sleep.call_args_list]
        # Second delay should be larger (exponential backoff)
        assert delays[1] > delays[0]

    @respx.mock
    async def test_connect_error_logged_at_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Transient connect error retries logged at warning."""
        import logging
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        call_count = 0

        async def _side_effect(
            request: httpx.Request,
        ) -> Response:
            """Fail first, succeed second."""
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("connection refused")
            return Response(200, json={"results": []})

        respx.get(f"{BASE_URL}/listings").mock(
            side_effect=_side_effect,
        )

        client, _, _ = _make_client()
        with (
            caplog.at_level(logging.WARNING),
            _patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client.test_connection()

        assert any(
            "Transient error" in record.getMessage()
            and record.levelno == logging.WARNING
            for record in caplog.records
        )

    @respx.mock
    async def test_5xx_retry_then_success(self) -> None:
        """5xx response retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(502, text="Bad Gateway"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_503_retry_then_success(self) -> None:
        """503 Service Unavailable retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(503, text="Service Unavailable"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_504_retry_then_success(self) -> None:
        """504 Gateway Timeout retried then succeeds."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(504, text="Gateway Timeout"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client.test_connection()

        assert result is True

    @respx.mock
    async def test_500_exhausts_retries_raises_error(
        self,
    ) -> None:
        """Persistent 500 raises GuestyConnectionError after retries."""
        from unittest.mock import patch as _patch

        from custom_components.guesty.api.exceptions import (
            GuestyConnectionError,
        )

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                500,
                text="Internal Server Error",
            ),
        )

        client, _, _ = _make_client()
        with (
            _patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(
                GuestyConnectionError,
                match="after 3 retries",
            ),
        ):
            await client.test_connection()

    @respx.mock
    async def test_5xx_logged_at_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """5xx retry logged at warning with status code."""
        import logging
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(503, text="Service Unavailable"),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with (
            caplog.at_level(logging.WARNING),
            _patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await client.test_connection()

        assert any(
            "Server error 503" in record.getMessage()
            and record.levelno == logging.WARNING
            for record in caplog.records
        )

    @respx.mock
    async def test_429_retry_preserves_retry_after(self) -> None:
        """429 handling preserves Retry-After header."""
        from unittest.mock import patch as _patch

        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        mock_sleep = AsyncMock()
        listings_route = respx.get(f"{BASE_URL}/listings")
        listings_route.side_effect = [
            Response(
                429,
                headers={"Retry-After": "3"},
            ),
            Response(200, json={"results": []}),
        ]

        client, _, _ = _make_client()
        with _patch("asyncio.sleep", mock_sleep):
            await client.test_connection()

        mock_sleep.assert_called_once()
        assert mock_sleep.call_args[0][0] == 3.0

    @respx.mock
    async def test_non_retryable_status_not_retried(self) -> None:
        """Non-transient status (e.g. 422) is not retried."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/listings").mock(
            return_value=Response(
                422,
                json={"error": "Unprocessable"},
            ),
        )

        client, _, _ = _make_client()
        # 422 is not retried — returned directly
        response = await client._request("GET", "/listings")
        assert response.status_code == 422


class TestGetAccountId:
    """Tests for GuestyApiClient.get_account_id()."""

    @respx.mock
    async def test_successful_account_id(self) -> None:
        """get_account_id() returns account ID on success."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "acc-123-abc"},
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_account_id()
        assert result == "acc-123-abc"

    @respx.mock
    async def test_non_success_raises_error(self) -> None:
        """get_account_id() raises on non-2xx status."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                404,
                json={"error": "not found"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="Account lookup failed",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_invalid_json_raises_error(self) -> None:
        """get_account_id() raises on non-JSON response."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
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
            await client.get_account_id()

    @respx.mock
    async def test_missing_id_field_raises_error(self) -> None:
        """get_account_id() raises when _id is missing."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"name": "My Account"},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_empty_id_field_raises_error(self) -> None:
        """get_account_id() raises when _id is empty."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": ""},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_whitespace_id_raises_error(self) -> None:
        """get_account_id() raises when _id is whitespace-only."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "   "},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_strips_whitespace_from_id(self) -> None:
        """get_account_id() strips whitespace from _id."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": "  acc-trimmed  "},
            ),
        )
        client, _, _ = _make_client()
        result = await client.get_account_id()
        assert result == "acc-trimmed"

    @respx.mock
    async def test_non_dict_response_raises_error(self) -> None:
        """get_account_id() raises when response is not a dict."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json=["not", "a", "dict"],
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="JSON object",
        ):
            await client.get_account_id()

    @respx.mock
    async def test_non_string_id_raises_error(self) -> None:
        """get_account_id() raises when _id is not a string."""
        respx.post(TOKEN_URL).mock(
            return_value=Response(
                200,
                json=make_token_response(),
            ),
        )
        respx.get(f"{BASE_URL}/accounts/me").mock(
            return_value=Response(
                200,
                json={"_id": 12345},
            ),
        )
        client, _, _ = _make_client()
        with pytest.raises(
            GuestyResponseError,
            match="missing _id",
        ):
            await client.get_account_id()
