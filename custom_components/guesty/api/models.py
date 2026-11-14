# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Data transfer objects and storage protocol for the Guesty API client."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from custom_components.guesty.api.const import (
    KNOWN_CHANNEL_TYPES,
    MAX_MESSAGE_LENGTH,
    V3_STATUS_FILTER_MAP,
    V3_STATUS_MAP,
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
class GuestyStaySegment:
    """One listing interval of a Guesty v3 reservation stay.

    A v3 reservation carries its listing attribution in ``stay[]``
    rather than at the top level, and a stay may move between
    listings. Each segment is one listing for one interval.

    Attributes:
        listing_id: Listing the interval belongs to.
        parent_listing_id: Parent listing for a multi-unit sub-unit.
        check_in: UTC check-in datetime of the interval.
        check_out: UTC check-out datetime of the interval.
        check_in_local: Localized check-in date string.
        check_out_local: Localized check-out date string.
        guests_count: Party size for the interval.
    """

    listing_id: str
    check_in: datetime
    check_out: datetime
    parent_listing_id: str | None = None
    check_in_local: str | None = None
    check_out_local: str | None = None
    guests_count: int | None = None

    @classmethod
    def from_api_dict(
        cls,
        data: Any,
    ) -> GuestyStaySegment | None:
        """Create a GuestyStaySegment from a v3 ``stay[]`` entry.

        A segment missing ``listingId``, ``checkIn`` or ``checkOut``
        is dropped with a debug log.

        Args:
            data: Stay segment dictionary from the v3 API.

        Returns:
            A GuestyStaySegment instance, or None if unusable.
        """
        if not isinstance(data, dict):
            _LOGGER.debug("Dropping non-dict stay segment")
            return None

        listing_id = data.get("listingId") or ""
        if not listing_id:
            _LOGGER.debug("Dropping stay segment with missing listingId")
            return None

        check_in = _parse_iso_datetime(data.get("checkIn"))
        if check_in is None:
            _LOGGER.debug(
                "Dropping stay segment for listing %s with unparsable checkIn",
                listing_id,
            )
            return None

        check_out = _parse_iso_datetime(data.get("checkOut"))
        if check_out is None:
            _LOGGER.debug(
                "Dropping stay segment for listing %s with unparsable checkOut",
                listing_id,
            )
            return None

        return cls(
            listing_id=listing_id,
            check_in=check_in,
            check_out=check_out,
            parent_listing_id=data.get("parentListingId"),
            check_in_local=data.get("checkInDateLocalized"),
            check_out_local=data.get("checkOutDateLocalized"),
            guests_count=data.get("guestsCount"),
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
        stay: Per-listing intervals; populated on the v3 path only.
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
    stay: tuple[GuestyStaySegment, ...] = ()

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

    @classmethod
    def from_v3_api_dict(
        cls,
        data: dict[str, Any],
    ) -> GuestyReservation | None:
        """Create a GuestyReservation from a v3 search result.

        Returns None when ``reservationId`` or ``status`` is missing,
        or when no usable ``stay`` segment remains. ``money`` is
        always None on the v3 path, and ``nights_count`` is derived
        from the localized dates.

        Args:
            data: Reservation dictionary from the v3 search response.

        Returns:
            A GuestyReservation instance, or None if invalid.
        """
        reservation_id = data.get("reservationId") or ""
        if not reservation_id:
            _LOGGER.warning(
                "Skipping v3 reservation with missing reservationId",
            )
            return None

        raw_status = data.get("status") or ""
        if not raw_status:
            _LOGGER.warning(
                "Skipping v3 reservation %s with missing status",
                reservation_id,
            )
            return None

        raw_stay = data.get("stay")
        if not isinstance(raw_stay, list):
            raw_stay = []
        stay = tuple(
            segment
            for item in raw_stay
            if (segment := GuestyStaySegment.from_api_dict(item)) is not None
        )
        if not stay:
            _LOGGER.warning(
                "Skipping v3 reservation %s with no usable stay segment",
                reservation_id,
            )
            return None

        check_in = _parse_iso_datetime(data.get("checkIn")) or stay[0].check_in
        check_out = _parse_iso_datetime(data.get("checkOut")) or stay[-1].check_out

        check_in_local = data.get("checkInDateLocalized") or stay[0].check_in_local
        check_out_local = data.get("checkOutDateLocalized") or stay[-1].check_out_local

        nights_count = data.get("nightsCount")
        if not isinstance(nights_count, int):
            nights_count = derive_nights_count(
                check_in_local,
                check_out_local,
            )

        notes = data.get("notes")
        note = notes.get("other") if isinstance(notes, dict) else None

        guest_id = data.get("guestId")
        guest = GuestyGuest(guest_id=guest_id) if guest_id else None

        custom_fields = MappingProxyType(
            _parse_custom_fields_array(data.get("customFields", [])),
        )

        return cls(
            id=reservation_id,
            listing_id=stay[0].listing_id,
            status=map_v3_status(raw_status, reservation_id),
            check_in=check_in,
            check_out=check_out,
            confirmation_code=data.get("confirmationCode"),
            check_in_local=check_in_local,
            check_out_local=check_out_local,
            planned_arrival=data.get("plannedArrival"),
            planned_departure=data.get("plannedDeparture"),
            nights_count=nights_count,
            guests_count=stay[0].guests_count,
            source=data.get("source"),
            note=note,
            guest=guest,
            money=None,
            custom_fields=custom_fields,
            stay=stay,
        )


def map_v3_status(raw_status: str, reservation_id: str = "") -> str:
    """Map a v3 status value to the model status vocabulary.

    Comparison is case-insensitive. Unrecognized values pass through
    verbatim with an informational log (FR-026).

    Args:
        raw_status: The status value from the v3 payload.
        reservation_id: Reservation id used for logging context.

    Returns:
        The mapped model status, or the raw value when unrecognized.
    """
    mapped = V3_STATUS_MAP.get(raw_status.strip().lower())
    if mapped is None:
        _LOGGER.info(
            "Unrecognized v3 reservation status %s on reservation %s",
            raw_status,
            reservation_id,
        )
        return raw_status
    return mapped


def translate_v3_status_filter(statuses: Iterable[str]) -> str:
    """Translate configured statuses into a v3 status filter value.

    Each configured status is requested together with its v3
    equivalents, so ``checked_out`` additionally requests
    ``completed`` (FR-003a). The configured set is never modified.

    Args:
        statuses: The configured status values.

    Returns:
        The sorted, comma-joined status filter value.
    """
    requested: set[str] = set()
    for status in statuses:
        requested.update(V3_STATUS_FILTER_MAP.get(status, (status,)))
    return ",".join(sorted(requested))


def derive_nights_count(
    check_in_local: str | None,
    check_out_local: str | None,
) -> int | None:
    """Derive a night count from two localized date strings.

    Args:
        check_in_local: Localized check-in date (``YYYY-MM-DD``).
        check_out_local: Localized check-out date (``YYYY-MM-DD``).

    Returns:
        Whole nights between the dates, or None if unparsable.
    """
    if not isinstance(check_in_local, str) or not isinstance(check_out_local, str):
        return None
    try:
        start = date.fromisoformat(check_in_local)
        end = date.fromisoformat(check_out_local)
    except ValueError:
        return None
    return (end - start).days


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


# ── Reservation Fetch Seam (Feature 008, Phase 1) ───────────────────


@dataclass(frozen=True)
class FallbackCandidate:
    """Durable stay dates of one legacy-fallback candidate.

    Carries identifiers and dates only, never guest data (FR-049a).
    Both endpoints are kept because the window predicate needs the
    check-in as well as the check-out: a check-out alone cannot
    tell an overlapping stay from a future one once ``future_days``
    is narrowed (data-model.md §4.1).

    It lives here rather than in ``reservations_fallback`` so that
    the ``ReservationSource`` protocol can name it without that
    module importing this one back.

    Attributes:
        check_in: UTC check-in instant of the last known stay.
        check_out: UTC check-out instant of the last known stay.
    """

    check_in: datetime
    check_out: datetime


@dataclass(frozen=True)
class FallbackCounters:
    """Per-refresh legacy not-found fallback counters (FR-057).

    Counts only; no identifier and no guest data (FR-021,
    NFR-009). Defined here because ``ReservationFetchResult``
    carries it and the fallback module imports this one.

    Attributes:
        attempted: Legacy by-id lookups issued this refresh.
        served: Reservations published from an existing record or
            from retained values, costing no request this refresh.
        succeeded: Lookups that returned a usable document.
        confirmed_absent: Lookups legacy answered as not found,
            retiring the candidate for good (FR-052).
        failed: Lookups that failed for any other reason (FR-054).
        deferred: Due candidates left over the per-refresh cap.
    """

    attempted: int = 0
    served: int = 0
    succeeded: int = 0
    confirmed_absent: int = 0
    failed: int = 0
    deferred: int = 0


@dataclass(frozen=True)
class ReservationFetchResult:
    """Result of one reservation retrieval, whatever the source.

    Returned by every ``ReservationSource`` so consumers stay
    source-agnostic. The diagnostics counters are best-effort: a
    source populates only the counters it can derive from its own
    payloads.

    Attributes:
        reservations: Validated, mapped reservations.
        pages: Number of API pages fetched.
        status_counts: Histogram of raw, pre-mapping statuses.
        segment_histogram: Histogram of stay-segment counts per
            reservation. Empty for sources without stay segments.
        fallback: Legacy not-found fallback counters (FR-057).
        fallback_served: Identifiers the fallback is currently
            serving from a legacy document it retrieved in this
            session. This is the set a write routes on (FR-058).
        fallback_sourced: Identifiers published by the fallback
            this refresh, including those republished from
            retained values. Diagnostics only (FR-057).
        fallback_absent: Identifiers legacy confirmed are gone, so
            the caller can retire their durable candidacy (FR-052).
    """

    reservations: tuple[GuestyReservation, ...] = ()
    pages: int = 0
    status_counts: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    segment_histogram: Mapping[int, int] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    fallback: FallbackCounters = field(
        default_factory=FallbackCounters,
    )
    fallback_served: tuple[str, ...] = ()
    fallback_sourced: tuple[str, ...] = ()
    fallback_absent: tuple[str, ...] = ()


@runtime_checkable
class ReservationSource(Protocol):
    """Protocol for a reservation retrieval strategy.

    Implemented once per reservation API generation so the client
    can select a path without the callers knowing which is active.
    """

    async def fetch(
        self,
        *,
        past_days: int,
        future_days: int,
        statuses: frozenset[str] | None = None,
        candidates: Mapping[str, FallbackCandidate] | None = None,
    ) -> ReservationFetchResult:
        """Fetch reservations for the configured window.

        Args:
            past_days: Days in the past to include.
            future_days: Days in the future to include.
            statuses: Statuses to request, or None for the
                source's own default set.
            candidates: Durable stay dates of the reservations the
                integration already knows, keyed by identifier. Only
                the v3 source uses them, to decide which absentees
                are worth a legacy by-id lookup (FR-050).

        Returns:
            The reservations and the diagnostics counters.
        """
        ...


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
