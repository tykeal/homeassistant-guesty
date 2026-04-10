# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects and storage protocol for the Guesty API client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from custom_components.guesty.api.const import (
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
)

_LOGGER = logging.getLogger(__name__)


def _parse_custom_fields_array(
    raw: Any,
) -> dict[str, str]:
    """Parse Guesty customFields array into a fieldId→value map.

    Args:
        raw: The customFields value from the API (expected list).

    Returns:
        Dict mapping fieldId to string value.
    """
    if not isinstance(raw, list):
        return {}
    result: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        field_id = item.get("fieldId")
        value = item.get("value")
        if isinstance(field_id, str) and field_id:
            result[field_id] = str(value) if value is not None else ""
    return result


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
            A GuestyAddress instance, or None if input is None,
            empty, or not a dict.
        """
        if not data or not isinstance(data, dict):
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
        listing_type: Single vs multi-unit (SINGLE, MULTI).
        bedrooms: Number of bedrooms, or None.
        bathrooms: Bathroom count (half-baths), or None.
        accommodates: Maximum guest capacity, or None.
        timezone: IANA timezone string.
        check_in_time: Default check-in time (HH:MM), or None.
        check_out_time: Default check-out time (HH:MM), or None.
        tags: Immutable tuple of listing tags.
        custom_fields: Immutable mapping of field IDs to values.
    """

    id: str
    title: str
    nickname: str | None
    status: str
    address: GuestyAddress | None
    property_type: str | None
    room_type: str | None
    listing_type: str | None
    bedrooms: int | None
    bathrooms: float | None
    accommodates: int | None
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

        raw_cf = data.get("customFields", [])
        custom_fields = MappingProxyType(
            _parse_custom_fields_array(raw_cf),
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
            listing_type=data.get("type"),
            bedrooms=data.get("bedrooms"),
            bathrooms=data.get("bathrooms"),
            accommodates=data.get("accommodates"),
            timezone=data.get("timezone") or "UTC",
            check_in_time=data.get("defaultCheckInTime"),
            check_out_time=data.get("defaultCheckOutTime"),
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


# ── Reservation Data Models ─────────────────────────────────────────


@dataclass(frozen=True)
class GuestyGuest:
    """Guest contact information from a Guesty reservation.

    Attributes:
        full_name: Guest display name.
        phone: Contact phone number.
        email: Contact email address.
        guest_id: Guesty guest identifier.
    """

    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    guest_id: str | None = None

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> GuestyGuest | None:
        """Create a GuestyGuest from a Guesty API guest dict.

        Args:
            data: Guest dictionary from the API, or None.

        Returns:
            A GuestyGuest instance, or None if input is None
            or empty dict.
        """
        if not data or not isinstance(data, dict):
            return None
        return cls(
            full_name=data.get("fullName"),
            phone=data.get("phone"),
            email=data.get("email"),
            guest_id=data.get("_id"),
        )


@dataclass(frozen=True)
class GuestyMoney:
    """Financial summary for a Guesty reservation.

    Attributes:
        total_paid: Total paid amount.
        balance_due: Outstanding balance.
        currency: ISO currency code.
    """

    total_paid: float | None = None
    balance_due: float | None = None
    currency: str | None = None

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> GuestyMoney | None:
        """Create a GuestyMoney from a Guesty API money dict.

        Args:
            data: Money dictionary from the API, or None.

        Returns:
            A GuestyMoney instance, or None if input is None
            or empty dict.
        """
        if not data or not isinstance(data, dict):
            return None
        return cls(
            total_paid=data.get("totalPaid"),
            balance_due=data.get("balanceDue"),
            currency=data.get("currency"),
        )


@dataclass(frozen=True)
class GuestyReservation:
    """A Guesty reservation/booking record.

    Frozen dataclass representing a single reservation from the
    Guesty Open API. Required fields are ``id``, ``listing_id``,
    ``status``, ``check_in``, and ``check_out``; all others are
    optional.

    Attributes:
        id: Guesty reservation identifier.
        listing_id: Parent listing reference.
        status: Reservation lifecycle state.
        check_in: UTC check-in datetime.
        check_out: UTC check-out datetime.
        confirmation_code: Booking confirmation code.
        check_in_local: Localized check-in date string.
        check_out_local: Localized check-out date string.
        planned_arrival: Override arrival time.
        planned_departure: Override departure time.
        nights_count: Stay duration in nights.
        guests_count: Party size.
        source: Booking channel.
        note: Reservation notes.
        guest: Guest contact information.
        money: Financial summary.
        custom_fields: Immutable mapping of field IDs to values.
    """

    id: str
    listing_id: str
    status: str
    check_in: datetime
    check_out: datetime
    confirmation_code: str | None = None
    check_in_local: str | None = None
    check_out_local: str | None = None
    planned_arrival: str | None = None
    planned_departure: str | None = None
    nights_count: int | None = None
    guests_count: int | None = None
    source: str | None = None
    note: str | None = None
    guest: GuestyGuest | None = None
    money: GuestyMoney | None = None
    custom_fields: MappingProxyType[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
    )

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyReservation | None:
        """Create a GuestyReservation from a Guesty API dict.

        Returns None if required fields (``_id``, ``listingId``,
        ``status``, ``checkIn``, ``checkOut``) are missing or
        invalid. Logs a warning for each skipped reservation.

        Args:
            data: Reservation dictionary from the Guesty API.

        Returns:
            A GuestyReservation instance, or None if invalid.
        """
        reservation_id = data.get("_id", "")
        if not reservation_id:
            _LOGGER.warning("Skipping reservation with missing _id")
            return None

        listing_id = data.get("listingId", "")
        if not listing_id:
            _LOGGER.warning(
                "Skipping reservation %s with missing listingId",
                reservation_id,
            )
            return None

        status = data.get("status", "")
        if not status:
            _LOGGER.warning(
                "Skipping reservation %s with missing status",
                reservation_id,
            )
            return None

        check_in = _parse_iso_datetime(data.get("checkIn"))
        if check_in is None:
            _LOGGER.warning(
                "Skipping reservation %s with unparsable checkIn",
                reservation_id,
            )
            return None

        check_out = _parse_iso_datetime(data.get("checkOut"))
        if check_out is None:
            _LOGGER.warning(
                "Skipping reservation %s with unparsable checkOut",
                reservation_id,
            )
            return None

        raw_cf = data.get("customFields", [])
        custom_fields = MappingProxyType(
            _parse_custom_fields_array(raw_cf),
        )

        return cls(
            id=reservation_id,
            listing_id=listing_id,
            status=status,
            check_in=check_in,
            check_out=check_out,
            confirmation_code=data.get("confirmationCode"),
            check_in_local=data.get("checkInDateLocalized"),
            check_out_local=data.get("checkOutDateLocalized"),
            planned_arrival=data.get("plannedArrival"),
            planned_departure=data.get("plannedDeparture"),
            nights_count=data.get("nightsCount"),
            guests_count=data.get("guestsCount"),
            source=data.get("source"),
            note=data.get("note"),
            guest=GuestyGuest.from_api_dict(data.get("guest")),
            money=GuestyMoney.from_api_dict(data.get("money")),
            custom_fields=custom_fields,
        )


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO 8601 datetime string to a timezone-aware datetime.

    Args:
        value: The value to parse (expected string).

    Returns:
        A timezone-aware datetime, or None if parsing fails.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass(frozen=True)
class GuestyReservationsResponse:
    """Paginated response from the reservations endpoint.

    Attributes:
        results: Immutable tuple of parsed valid reservations.
        count: Total count from API metadata.
        limit: Page size used.
        skip: Offset used.
    """

    results: tuple[GuestyReservation, ...]
    count: int
    limit: int
    skip: int

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyReservationsResponse:
        """Create from an API response dictionary.

        Parses ``results`` via ``GuestyReservation.from_api_dict()``,
        filtering out None entries and non-dict items.

        Args:
            data: Response dictionary from the reservations endpoint.

        Returns:
            A GuestyReservationsResponse instance.
        """
        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        reservations = tuple(
            reservation
            for item in raw_results
            if isinstance(item, dict)
            and (reservation := GuestyReservation.from_api_dict(item)) is not None
        )
        return cls(
            results=reservations,
            count=data.get("count", 0),
            limit=data.get("limit", 100),
            skip=data.get("skip", 0),
        )


# --- Custom Field Models (Feature 004, Phase 1) ---

_GUESTY_TYPE_MAP: dict[str, str] = {"string": "text"}


@dataclass(frozen=True)
class GuestyCustomFieldDefinition:
    """Immutable custom field definition from the Guesty API.

    Attributes:
        field_id: Unique identifier for the custom field.
        name: Human-readable display name (from API ``key``).
        field_type: Normalised value type for known Guesty types
            (e.g. "string" becomes "text"), or the raw API type
            string preserved as-is for unrecognised types.
        applicable_to: Set of entity types this field applies to.
        display_name: Slugified variable name (from API
            ``displayName``).
        is_public: Whether the field is publicly visible.
        is_required: Whether the field is required.
        options: Available options for the field.
    """

    field_id: str
    name: str
    field_type: str
    applicable_to: frozenset[str]
    display_name: str = ""
    is_public: bool = False
    is_required: bool = False
    options: tuple[str, ...] = ()

    @classmethod
    def from_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyCustomFieldDefinition | None:
        """Create a definition from a Guesty API response dict.

        Returns ``None`` when required fields (fieldId, key, type)
        are missing so callers can filter incomplete records.

        Args:
            data: Single definition dict from
                GET /accounts/{id}/custom-fields.

        Returns:
            A populated instance, or None if required data is
            missing.
        """
        field_id = data.get("fieldId")
        name = data.get("key")
        raw_type = data.get("type")

        if field_id is None or name is None or raw_type is None:
            return None

        if not isinstance(raw_type, str):
            return None

        field_type = _GUESTY_TYPE_MAP.get(raw_type, raw_type)

        object_type = data.get("object")
        if object_type == "both":
            applicable_to = frozenset({"listing", "reservation"})
        elif isinstance(object_type, str):
            applicable_to = frozenset({object_type})
        else:
            applicable_to = frozenset()

        raw_display_name = data.get("displayName")
        display_name = raw_display_name if isinstance(raw_display_name, str) else ""
        raw_is_public = data.get("isPublic", False)
        is_public = raw_is_public if isinstance(raw_is_public, bool) else False
        raw_is_required = data.get("isRequired", False)
        is_required = raw_is_required if isinstance(raw_is_required, bool) else False

        raw_options = data.get("options", ())
        if isinstance(raw_options, list):
            options = tuple(str(o) for o in raw_options if isinstance(o, str))
        else:
            options = ()

        return cls(
            field_id=str(field_id),
            name=str(name),
            field_type=str(field_type),
            applicable_to=applicable_to,
            display_name=display_name,
            is_public=is_public,
            is_required=is_required,
            options=options,
        )


@dataclass(frozen=True)
class GuestyCustomFieldUpdate:
    """Single custom field value update payload.

    Attributes:
        field_id: The custom field identifier.
        value: The new value (string, int, float, or bool).
    """

    field_id: str
    value: str | int | float | bool

    def __post_init__(self) -> None:
        """Validate required fields after initialization.

        Raises:
            ValueError: If field_id is empty.
        """
        if not self.field_id:
            raise ValueError("field_id must not be empty")

    def to_api_dict(self) -> dict[str, str | int | float | bool]:
        """Serialise to the Guesty API request body format.

        Returns:
            Dict with fieldId and value keys.
        """
        return {"fieldId": self.field_id, "value": self.value}


@dataclass(frozen=True)
class GuestyCustomFieldResult:
    """Result of a custom field write operation.

    Attributes:
        success: Whether the write succeeded.
        target_type: Entity type that was updated.
        target_id: Entity identifier that was updated.
        field_id: Custom field that was updated.
        error_details: Error description when success is False.
    """

    success: bool
    target_type: str
    target_id: str
    field_id: str
    error_details: str | None = None


# ── Action Data Models ──────────────────────────────────────────────


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a Guesty action operation.

    Attributes:
        success: Whether the action was accepted by Guesty.
        target_id: The resource identifier that was acted upon.
        error: Error description if the action failed.
    """

    success: bool
    target_id: str
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate action result fields after initialization.

        Raises:
            ValueError: If target_id is empty, or if success is
                False and error is missing or empty.
        """
        if not self.target_id:
            msg = "target_id must be non-empty"
            raise ValueError(msg)
        if not self.success and (self.error is None or not self.error.strip()):
            msg = "error must be provided when success is False"
            raise ValueError(msg)
