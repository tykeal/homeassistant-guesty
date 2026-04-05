# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Guesty API requests."""

from __future__ import annotations

import logging
import random

import httpx

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.const import (
    BACKOFF_MULTIPLIER,
    BASE_URL,
    INITIAL_BACKOFF,
    MAX_BACKOFF,
    MAX_RETRIES,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)

_LOGGER = logging.getLogger(__name__)


class GuestyApiClient:
    """HTTP client for authenticated Guesty API requests.

    Wraps httpx.AsyncClient (injected) with automatic token
    management, exponential backoff on 429 responses, and
    reactive token refresh on 401 responses.

    Attributes:
        _token_manager: Token provider for authentication.
        _http: Injected async HTTP client.
        _base_url: Guesty API base URL.
    """

    def __init__(
        self,
        token_manager: GuestyTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize GuestyApiClient.

        Args:
            token_manager: Token manager for authentication.
            http_client: Async HTTP client for API requests.
            base_url: Guesty API base URL.
        """
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url

    async def test_connection(self) -> bool:
        """Validate credentials and API access.

        Acquires a token and makes a lightweight API call to verify
        the credentials work end-to-end.

        Returns:
            True if connection test succeeds.

        Raises:
            GuestyAuthError: If credentials are invalid.
            GuestyConnectionError: If the API is unreachable.
            GuestyRateLimitError: If rate limited.
            GuestyResponseError: If the API returns unexpected data.
        """
        await self._request("GET", "/listings", params={"limit": 1, "fields": "_id"})
        return True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_data: dict[str, object] | None = None,
        _retried_auth: bool = False,
    ) -> httpx.Response:
        """Make an authenticated API request with retry logic.

        Adds Authorization header, retries on 429 with exponential
        backoff, and reactively refreshes token on 401.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path relative to base URL.
            params: Optional query parameters.
            json_data: Optional JSON body.
            _retried_auth: Internal flag to prevent infinite 401 retry.

        Returns:
            The httpx.Response from the API.

        Raises:
            GuestyAuthError: On persistent 401 after token refresh.
            GuestyConnectionError: On network failures.
            GuestyRateLimitError: On 429 after max retries.
            GuestyResponseError: On unexpected response formats.
        """
        import asyncio

        url = f"{self._base_url}{path}"
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES + 1):
            token = await self._token_manager.get_token()

            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json_data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise GuestyConnectionError(
                    f"Failed to connect to Guesty API: {exc}",
                ) from exc

            if response.status_code == 401:
                if _retried_auth:
                    raise GuestyAuthError(
                        "Authentication failed after token refresh",
                    )
                self._token_manager.invalidate()
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_data=json_data,
                    _retried_auth=True,
                )

            if response.status_code == 429:
                if attempt >= MAX_RETRIES:
                    retry_after = _parse_retry_after(response)
                    raise GuestyRateLimitError(
                        "Rate limit exceeded after max retries",
                        retry_after=retry_after,
                    )

                delay = _calculate_backoff(
                    attempt,
                    backoff,
                    response,
                )
                _LOGGER.debug(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)
                continue

            if response.status_code == 403:
                raise GuestyAuthError(
                    "Insufficient permissions for this API endpoint",
                )

            return response

        # Should not reach here, but just in case
        raise GuestyResponseError(  # pragma: no cover
            "Request loop exited without returning",
        )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After header from a 429 response.

    Args:
        response: The HTTP response to parse.

    Returns:
        The retry-after value in seconds, or None if not present.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _calculate_backoff(
    attempt: int,
    base_backoff: float,
    response: httpx.Response,
) -> float:
    """Calculate backoff delay with jitter and Retry-After support.

    Args:
        attempt: Current retry attempt number (0-indexed).
        base_backoff: Base backoff delay in seconds.
        response: The HTTP response (may contain Retry-After).

    Returns:
        Delay in seconds before next retry.
    """
    retry_after = _parse_retry_after(response)
    if retry_after is not None:
        return min(retry_after, MAX_BACKOFF)

    # Exponential backoff with ±25% jitter
    delay = base_backoff * (BACKOFF_MULTIPLIER**attempt)
    delay = min(delay, MAX_BACKOFF)
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0.1, delay + jitter)
