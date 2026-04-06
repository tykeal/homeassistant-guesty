# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects and storage protocol for the Guesty API client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from custom_components.guesty.api.const import (
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
)

_LOGGER = logging.getLogger(__name__)


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


# ── Messaging Data Models ───────────────────────────────────────────


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


# ── Listing Data Models ─────────────────────────────────────────────


@dataclass(frozen=True)
class GuestyAddress:
    """Structured address for a Guesty listing.

    All fields are optional since the Guesty API may return partial
    addresses.

    Attributes:
        full: Pre-formatted full address from API.
        street: Street address.
        city: City name.
        state: State or province.
        zipcode: Postal code.
        country: Country name.
    """

    full: str | None
    street: str | None
    city: str | None
    state: str | None
    zipcode: str | None
    country: str | None

    def formatted(self) -> str | None:
        """Return a human-readable address string.

        Returns ``full`` if present; otherwise joins non-empty
        components with ``", "``. Returns ``None`` if all
        components are empty.

        Returns:
            Formatted address string, or None.
        """
        if self.full:
            return self.full
        parts = [
            p
            for p in (
                self.street,
                self.city,
                self.state,
                self.zipcode,
                self.country,
            )
            if p
        ]
        return ", ".join(parts) if parts else None

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> GuestyAddress | None:
        """Create a GuestyAddress from a Guesty API address dict.

        Args:
            data: Address dictionary from the API, or None.

        Returns:
            A GuestyAddress instance, or None if input is None
            or empty.
        """
        if not data:
            return None
        return cls(
            full=data.get("full"),
            street=data.get("street"),
            city=data.get("city"),
            state=data.get("state"),
            zipcode=data.get("zipcode"),
            country=data.get("country"),
        )


@dataclass(frozen=True)
class GuestyListing:
    """A single Guesty property listing.

    HA-independent frozen dataclass representing a listing from
    the Guesty Open API.

    Attributes:
        id: Guesty listing ``_id`` (MongoDB ObjectID).
        title: Primary listing name.
        nickname: Alternative display name.
        status: Derived status: active, inactive, or archived.
        address: Structured address, or None.
        property_type: E.g., apartment, house, villa.
        room_type: E.g., entire home, private room.
        bedrooms: Number of bedrooms, or None.
        bathrooms: Bathroom count (half-baths), or None.
        timezone: IANA timezone string.
        check_in_time: Default check-in time (HH:MM), or None.
        check_out_time: Default check-out time (HH:MM), or None.
        tags: Immutable tuple of listing tags.
        custom_fields: Immutable mapping of custom name-value pairs.
    """

    id: str
    title: str
    nickname: str | None
    status: str
    address: GuestyAddress | None
    property_type: str | None
    room_type: str | None
    bedrooms: int | None
    bathrooms: float | None
    timezone: str
    check_in_time: str | None
    check_out_time: str | None
    tags: tuple[str, ...]
    custom_fields: MappingProxyType[str, str]

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyListing | None:
        """Create a GuestyListing from a Guesty API listing dict.

        Returns None if ``_id`` is missing or empty. Handles all
        optional field defaults per the data model specification.

        Args:
            data: Listing dictionary from the Guesty API.

        Returns:
            A GuestyListing instance, or None if invalid.
        """
        listing_id = data.get("_id", "")
        if not listing_id:
            _LOGGER.warning("Skipping listing with missing _id")
            return None

        status = _derive_listing_status(data)

        nickname = data.get("nickname")
        title = data.get("title") or nickname or "Unknown"

        raw_tags = data.get("tags", [])
        tags = (
            tuple(tag for tag in raw_tags if isinstance(tag, str))
            if isinstance(raw_tags, (list, tuple))
            else ()
        )

        raw_cf = data.get("customFields", {})
        custom_fields = MappingProxyType(
            {k: str(v) for k, v in raw_cf.items()} if isinstance(raw_cf, dict) else {},
        )

        return cls(
            id=listing_id,
            title=title,
            nickname=nickname,
            status=status,
            address=GuestyAddress.from_api_dict(
                data.get("address"),
            ),
            property_type=data.get("propertyType"),
            room_type=data.get("roomType"),
            bedrooms=data.get("numberOfBedrooms"),
            bathrooms=data.get("numberOfBathrooms"),
            timezone=data.get("timezone", "UTC"),
            check_in_time=data.get("defaultCheckInTime"),
            check_out_time=data.get("defaultCheckoutTime"),
            tags=tags,
            custom_fields=custom_fields,
        )


def _derive_listing_status(data: dict[str, Any]) -> str:
    """Derive listing status from API fields.

    Checks for Guesty's explicit archive indicator
    (``pms.active`` is False) first, then derives from
    ``listed`` and ``active`` booleans.

    Args:
        data: Listing dictionary from the Guesty API.

    Returns:
        One of ``"active"``, ``"inactive"``, or ``"archived"``.
    """
    pms = data.get("pms")
    if isinstance(pms, dict) and pms.get("active") is False:
        return "archived"

    listed = data.get("listed", True)
    active = data.get("active", True)

    if listed and active:
        return "active"
    return "inactive"


@dataclass(frozen=True)
class GuestyListingsResponse:
    """Pagination response wrapper for the listings endpoint.

    Attributes:
        results: Immutable tuple of parsed valid listings.
        count: Total count from API metadata.
        limit: Page size used.
        skip: Offset used.
    """

    results: tuple[GuestyListing, ...]
    count: int
    limit: int
    skip: int

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyListingsResponse:
        """Create from an API response dictionary.

        Parses ``results`` via ``GuestyListing.from_api_dict()``,
        filtering out None entries and non-dict items.

        Args:
            data: Response dictionary from the listings endpoint.

        Returns:
            A GuestyListingsResponse instance.
        """
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        listings = tuple(
            listing
            for item in raw_results
            if isinstance(item, dict)
            and (listing := GuestyListing.from_api_dict(item)) is not None
        )
        return cls(
            results=listings,
            count=data.get("count", 0),
            limit=data.get("limit", 100),
            skip=data.get("skip", 0),
        )
