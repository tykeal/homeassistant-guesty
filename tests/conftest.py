# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared test fixtures for the Guesty integration test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from custom_components.guesty.api.models import CachedToken, TokenStorage

# Common test constants
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret"
FAKE_TOKEN = "test-access-token-jwt"
FAKE_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
FAKE_BASE_URL = "https://open-api.guesty.com/v1"


def make_token_response(**overrides: Any) -> dict[str, Any]:
    """Create a mock Guesty token endpoint response.

    Args:
        **overrides: Fields to override on the default response.

    Returns:
        Dictionary matching the Guesty token endpoint response format.
    """
    defaults: dict[str, Any] = {
        "token_type": "Bearer",
        "access_token": FAKE_TOKEN,
        "expires_in": 86400,
        "scope": "open-api",
    }
    defaults.update(overrides)
    return defaults


class FakeTokenStorage:
    """In-memory TokenStorage implementation for tests.

    Implements the TokenStorage protocol without any HA dependencies,
    suitable for unit testing the API layer in isolation.
    """

    def __init__(self) -> None:
        """Initialize with empty state."""
        self._token: CachedToken | None = None
        self._count: int = 0
        self._window: datetime | None = None

    async def load_token(self) -> CachedToken | None:
        """Load the stored token.

        Returns:
            The stored CachedToken, or None if not set.
        """
        return self._token

    async def save_token(self, token: CachedToken) -> None:
        """Store a token.

        Args:
            token: The CachedToken to store.
        """
        self._token = token

    async def load_request_count(self) -> tuple[int, datetime | None]:
        """Load the token request counter.

        Returns:
            Tuple of (count, window_start).
        """
        return (self._count, self._window)

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Store the token request counter.

        Args:
            count: Number of token requests in current window.
            window_start: Start time of the current window.
        """
        self._count = count
        self._window = window_start


# Verify FakeTokenStorage satisfies the protocol at module level
_storage_check: TokenStorage = FakeTokenStorage()


@pytest.fixture
def fake_storage() -> FakeTokenStorage:
    """Provide a clean FakeTokenStorage instance.

    Returns:
        A new FakeTokenStorage for the test.
    """
    return FakeTokenStorage()


@pytest.fixture
def mock_token() -> CachedToken:
    """Provide a valid CachedToken for tests.

    Returns:
        A CachedToken with default test values.
    """
    return CachedToken(
        access_token=FAKE_TOKEN,
        token_type="Bearer",
        expires_in=86400,
        scope="open-api",
        issued_at=datetime.now(UTC),
    )
