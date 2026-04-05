# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Token manager for Guesty OAuth 2.0 Client Credentials flow."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from custom_components.guesty.api.const import (
    DEFAULT_REFRESH_BUFFER,
    GRANT_TYPE,
    SCOPE,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import CachedToken, TokenStorage

_LOGGER = logging.getLogger(__name__)


class GuestyTokenManager:
    """Manage OAuth 2.0 token lifecycle for the Guesty API.

    Handles token acquisition via Client Credentials grant,
    in-memory and persistent caching via TokenStorage protocol,
    proactive refresh with configurable buffer, concurrent access
    serialization via asyncio.Lock double-checked locking, and
    5-per-24h token request rate limit tracking.

    Attributes:
        _client_id: Guesty API client ID.
        _client_secret: Client secret.
        _http: Injected async HTTP client.
        _storage: Persistence backend.
        _cached_token: In-memory token cache.
        _lock: Concurrent access guard.
        _refresh_buffer: Seconds before expiry to refresh.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient,
        storage: TokenStorage,
        *,
        refresh_buffer: int = DEFAULT_REFRESH_BUFFER,
        token_url: str = TOKEN_URL,
    ) -> None:
        """Initialize GuestyTokenManager.

        Args:
            client_id: Guesty API client ID.
            client_secret: Client secret.
            http_client: Async HTTP client for token requests.
            storage: Persistence backend for token and counters.
            refresh_buffer: Seconds before expiry to refresh.
            token_url: OAuth token endpoint URL.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._storage = storage
        self._refresh_buffer = refresh_buffer
        self._token_url = token_url
        self._cached_token: CachedToken | None = None
        self._lock = asyncio.Lock()
        self._request_count: int = 0
        self._window_start: datetime | None = None

    def seed_token(self, token: CachedToken) -> None:
        """Seed the in-memory cache with a persisted token.

        Called during startup to avoid a token request if a valid
        persisted token exists.

        Args:
            token: A previously persisted CachedToken.
        """
        self._cached_token = token

    def invalidate(self) -> None:
        """Clear the cached token, forcing re-acquisition.

        Used when a 401 response indicates the token is invalid.
        """
        self._cached_token = None

    async def get_token(self) -> str:
        """Get a valid access token, acquiring or refreshing as needed.

        Uses double-checked locking: first check without lock avoids
        contention in the common case; lock serializes only actual
        token acquisition; second check inside lock prevents redundant
        requests from concurrent callers.

        Returns:
            A valid Bearer access token string.

        Raises:
            GuestyAuthError: If credentials are invalid.
            GuestyConnectionError: If the token endpoint is unreachable.
            GuestyResponseError: If the response format is unexpected.
            GuestyRateLimitError: If the 5-per-24h limit is exceeded.
        """
        if self._is_cache_valid():
            return self._cached_token.access_token  # type: ignore[union-attr]

        async with self._lock:
            if self._is_cache_valid():
                return self._cached_token.access_token  # type: ignore[union-attr]

            await self._check_rate_limit()
            token = await self._request_token()
            self._cached_token = token
            await self._storage.save_token(token)
            return token.access_token

    def _is_cache_valid(self) -> bool:
        """Check if the in-memory cached token is still valid.

        Returns:
            True if cached token exists and is not expired
            (accounting for refresh buffer).
        """
        if self._cached_token is None:
            return False
        return not self._cached_token.is_expired(
            buffer_seconds=self._refresh_buffer,
        )

    async def _check_rate_limit(self) -> None:
        """Check and enforce the 5-per-24h token request rate limit.

        Loads persisted counters on first call, resets window if
        expired, warns at threshold counts, and raises
        GuestyRateLimitError if limit would be exceeded.

        Raises:
            GuestyRateLimitError: If requesting would exceed the limit.
        """
        # Lazy-load from storage only once (both zero = not yet loaded)
        from custom_components.guesty.api.const import (
            MAX_TOKEN_REQUESTS_PER_WINDOW,
            TOKEN_WINDOW_SECONDS,
        )

        if self._request_count == 0 and self._window_start is None:
            count, window = await self._storage.load_request_count()
            self._request_count = count
            self._window_start = window

        now = datetime.now(UTC)

        # Reset window if expired
        if self._window_start is not None:
            elapsed = (now - self._window_start).total_seconds()
            if elapsed >= TOKEN_WINDOW_SECONDS:
                self._request_count = 0
                self._window_start = None

        # Check if we'd exceed the limit
        if self._request_count >= MAX_TOKEN_REQUESTS_PER_WINDOW:
            from custom_components.guesty.api.exceptions import (
                GuestyRateLimitError,
            )

            reset_at = None
            if self._window_start is not None:
                from datetime import timedelta

                reset_at = self._window_start + timedelta(
                    seconds=TOKEN_WINDOW_SECONDS,
                )

            raise GuestyRateLimitError(
                "Token request rate limit exceeded "
                f"({MAX_TOKEN_REQUESTS_PER_WINDOW} per "
                f"{TOKEN_WINDOW_SECONDS}s)",
                reset_at=reset_at,
            )

        # Warn at threshold
        if self._request_count >= MAX_TOKEN_REQUESTS_PER_WINDOW - 2:
            _LOGGER.warning(
                "Token request %d of %d in current 24h window",
                self._request_count + 1,
                MAX_TOKEN_REQUESTS_PER_WINDOW,
            )

    async def _request_token(self) -> CachedToken:
        """Request a new token from the Guesty token endpoint.

        Updates the request counter and window tracking after
        successful acquisition.

        Returns:
            A new CachedToken from the token endpoint response.

        Raises:
            GuestyAuthError: On 401 Unauthorized.
            GuestyConnectionError: On network failure.
            GuestyResponseError: On unexpected response format.
        """
        from custom_components.guesty.api.const import (
            TOKEN_WINDOW_SECONDS,
        )

        now = datetime.now(UTC)

        try:
            response = await self._http.post(
                self._token_url,
                data={
                    "grant_type": GRANT_TYPE,
                    "scope": SCOPE,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise GuestyConnectionError(
                f"Failed to connect to token endpoint: {exc}",
            ) from exc

        if response.status_code == 401:
            raise GuestyAuthError("Invalid client credentials")

        if response.status_code != 200:
            raise GuestyResponseError(
                f"Unexpected token response status: {response.status_code}",
            )

        try:
            data = response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Token response is not valid JSON",
            ) from exc

        try:
            token = CachedToken(
                access_token=data["access_token"],
                token_type=data.get("token_type", "Bearer"),
                expires_in=data["expires_in"],
                scope=data.get("scope", SCOPE),
                issued_at=now,
            )
        except (KeyError, ValueError) as exc:
            raise GuestyResponseError(
                f"Malformed token response: {exc}",
            ) from exc

        # Update rate limit tracking
        if self._window_start is None:
            self._window_start = now

        # Check if window needs reset (loaded stale data)
        if self._window_start is not None:
            elapsed = (now - self._window_start).total_seconds()
            if elapsed >= TOKEN_WINDOW_SECONDS:
                self._request_count = 0
                self._window_start = now

        self._request_count += 1
        assert self._window_start is not None
        await self._storage.save_request_count(
            self._request_count,
            self._window_start,
        )

        return token
