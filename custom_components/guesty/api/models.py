# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects and storage protocol for the Guesty API client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from custom_components.guesty.api.const import (
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
)


@dataclass(frozen=True)
class CachedToken:
    """Immutable representation of a cached OAuth 2.0 access token.

    Attributes:
        access_token: The Bearer token value.
        token_type: Token type (always "Bearer").
        expires_in: Token lifetime in seconds.
        scope: OAuth scope granted.
        issued_at: UTC timestamp when the token was acquired.
    """

    access_token: str
    token_type: str
    expires_in: int
    scope: str
    issued_at: datetime

    def __post_init__(self) -> None:
        """Validate token fields after initialization.

        Raises:
            ValueError: If access_token is empty, expires_in is not
                positive, or issued_at is naive (no timezone).
        """
        if not self.access_token:
            msg = "access_token must be non-empty"
            raise ValueError(msg)
        if self.expires_in <= 0:
            msg = "expires_in must be positive"
            raise ValueError(msg)
        if self.issued_at.tzinfo is None:
            msg = "issued_at must be timezone-aware"
            raise ValueError(msg)

    @property
    def expires_at(self) -> datetime:
        """Compute the expiration timestamp.

        Returns:
            UTC datetime when this token expires.
        """
        return self.issued_at + timedelta(seconds=self.expires_in)

    def is_expired(self, buffer_seconds: int = 0) -> bool:
        """Check whether the token is expired or within the buffer.

        Args:
            buffer_seconds: Safety margin in seconds before actual
                expiry to consider the token expired.

        Returns:
            True if the token is expired or within the buffer window.
        """
        return datetime.now(UTC) >= self.expires_at - timedelta(
            seconds=buffer_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            Dictionary representation suitable for config_entry
            persistence.
        """
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedToken:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with token fields, as produced by
                to_dict().

        Returns:
            A new CachedToken instance.
        """
        return cls(
            access_token=data["access_token"],
            token_type=data["token_type"],
            expires_in=data["expires_in"],
            scope=data["scope"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
        )


class TokenStorage(Protocol):
    """Protocol for persisting token and rate limit data.

    The api/ package defines this interface; consumers (e.g., the HA
    integration) provide concrete implementations.
    """

    async def load_token(self) -> CachedToken | None:
        """Load a previously persisted token.

        Returns:
            The stored CachedToken, or None if no token is stored or
            stored data is corrupted/invalid.
        """
        ...

    async def save_token(self, token: CachedToken) -> None:
        """Persist a token for later retrieval.

        Args:
            token: The CachedToken to persist.
        """
        ...

    async def load_request_count(self) -> tuple[int, datetime | None]:
        """Load the token request counter and window start time.

        Returns:
            Tuple of (count, window_start). If no counter is stored,
            returns (0, None).
        """
        ...

    async def save_request_count(
        self,
        count: int,
        window_start: datetime,
    ) -> None:
        """Persist the token request counter and window start.

        Args:
            count: Number of token requests in the current window.
            window_start: Start time of the current rate limit window.
        """
        ...


@dataclass(frozen=True)
class Conversation:
    """Guesty conversation associated with a reservation.

    Attributes:
        id: Guesty conversation identifier.
        reservation_id: Associated reservation identifier.
        available_channels: Communication channels available.
    """

    id: str
    reservation_id: str
    available_channels: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate conversation fields after initialization.

        Raises:
            ValueError: If any required field is empty.
        """
        if not self.id:
            msg = "id must be non-empty"
            raise ValueError(msg)
        if not self.reservation_id:
            msg = "reservation_id must be non-empty"
            raise ValueError(msg)
        if not self.available_channels:
            msg = "available_channels must be non-empty"
            raise ValueError(msg)


@dataclass(frozen=True)
class MessageRequest:
    """Validated request to send a message via Guesty.

    Attributes:
        conversation_id: Target conversation identifier.
        body: Rendered message text.
        channel: Optional delivery channel override.
    """

    conversation_id: str
    body: str
    channel: str | None = None

    def __post_init__(self) -> None:
        """Validate message request fields after initialization.

        Raises:
            ValueError: If conversation_id or body is empty, body
                exceeds MAX_MESSAGE_LENGTH, or channel unknown.
        """
        if not self.conversation_id:
            msg = "conversation_id must be non-empty"
            raise ValueError(msg)
        if not self.body:
            msg = "body must be non-empty"
            raise ValueError(msg)
        if len(self.body) > MAX_MESSAGE_LENGTH:
            msg = f"body exceeds maximum length of {MAX_MESSAGE_LENGTH} characters"
            raise ValueError(msg)
        if self.channel is not None and self.channel not in KNOWN_CHANNEL_TYPES:
            msg = (
                f"unknown channel '{self.channel}'; "
                f"known types: {sorted(KNOWN_CHANNEL_TYPES)}"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class MessageDeliveryResult:
    """Outcome of a Guesty message delivery attempt.

    Attributes:
        success: Whether delivery was accepted by Guesty.
        message_id: Guesty message identifier if successful.
        error_details: Error description if delivery failed.
        reservation_id: The targeted reservation for context.
    """

    success: bool
    message_id: str | None = None
    error_details: str | None = None
    reservation_id: str | None = None
