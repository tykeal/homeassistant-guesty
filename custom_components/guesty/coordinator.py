# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Guesty listings and reservations."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    HOLD_STATUSES,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyFilterRejectedError,
)
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
    ReservationFetchResult,
    derive_nights_count,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.custom_fields import (
        GuestyCustomFieldsClient,
    )

_LOGGER = logging.getLogger(__name__)

# Statuses whose per-listing meaning depends on the stay segment.
_TEMPORAL_STATUSES: frozenset[str] = frozenset({"confirmed", "checked_in"})


class ListingsCoordinator(
    DataUpdateCoordinator[dict[str, GuestyListing]],
):
    """Coordinator that fetches Guesty listings periodically.

    Wraps the API client's ``get_listings()`` call inside a
    ``DataUpdateCoordinator`` so sensor entities receive automatic
    updates. The poll interval is configurable via options flow.

    Attributes:
        api_client: The Guesty API client instance.
        config_entry: The integration config entry.
        disappeared_listing_ids: IDs of listings previously present
            but absent in the most recent successful fetch.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: GuestyApiClient,
    ) -> None:
        """Initialize the listings coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Guesty API client for fetching listings.
        """
        self.api_client = api_client
        self.disappeared_listing_ids: set[str] = set()
        self._previous_listing_ids: set[str] | None = None
        interval_minutes = entry.options.get(
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_listings",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[str, GuestyListing]:
        """Fetch listings and return a possibly filtered dict by ID.

        Fetches listings from the API, tracks disappeared listing IDs
        against the raw API response, then applies the
        ``CONF_SELECTED_LISTINGS`` filter when present in config entry
        options. When the option is absent (``None``), all listings
        are returned unchanged for backward compatibility.

        Returns:
            Dictionary mapping listing ID to GuestyListing.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        try:
            listings = await self.api_client.get_listings()
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching listings: {exc.message}",
            ) from exc

        new_data = {listing.id: listing for listing in listings}
        current_ids = set(new_data.keys())

        if self._previous_listing_ids is not None:
            newly_disappeared = self._previous_listing_ids - current_ids
            for lid in newly_disappeared:
                _LOGGER.warning(
                    "Listing %s disappeared from API response",
                    lid,
                )
            self.disappeared_listing_ids = (
                self.disappeared_listing_ids | newly_disappeared
            ) - current_ids

        self._previous_listing_ids = current_ids

        selected = self.config_entry.options.get(
            CONF_SELECTED_LISTINGS,
        )
        if selected is not None:
            selected_set = set(selected)
            new_data = {
                lid: listing for lid, listing in new_data.items() if lid in selected_set
            }

        return new_data


class ReservationsCoordinator(
    DataUpdateCoordinator[dict[str, list[GuestyReservation]]],
):
    """Coordinator that fetches Guesty reservations periodically.

    Groups reservations by listing ID and filters out reservations
    for listings not tracked by the ``ListingsCoordinator``. Each
    listing's reservations are sorted by check-in date.

    Attributes:
        api_client: The Guesty API client instance.
        config_entry: The integration config entry.
        listings_coordinator: Reference to the listings coordinator
            for known listing ID validation.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api_client: GuestyApiClient,
        listings_coordinator: ListingsCoordinator,
    ) -> None:
        """Initialize the reservations coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            api_client: Guesty API client for fetching reservations.
            listings_coordinator: The listings coordinator for
                known listing ID validation.
        """
        self.api_client = api_client
        self.listings_coordinator = listings_coordinator
        interval_minutes = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_reservations",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> dict[str, list[GuestyReservation]]:
        """Fetch reservations and publish them per tracked listing.

        Reservations are retrieved from the configured source, then
        proposed changes are dropped (FR-024), the configured instant
        window is enforced client-side (FR-002a), and each surviving
        reservation is attributed to every tracked listing its stay
        touches (FR-009, FR-010). Each attribution is published as a
        per-listing projection (FR-011) whose status is segment-aware
        for a multi-segment stay (FR-011a). Guest details are then
        resolved from the bounded cache for the published
        reservations only (FR-016, FR-017, NFR-001).

        Returns:
            Dictionary mapping listing ID to sorted projections.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        past_days = self.config_entry.options.get(
            CONF_PAST_DAYS,
            DEFAULT_PAST_DAYS,
        )
        future_days = self.config_entry.options.get(
            CONF_FUTURE_DAYS,
            DEFAULT_FUTURE_DAYS,
        )

        try:
            fetch_result = await self.api_client.get_reservations(
                past_days=past_days,
                future_days=future_days,
            )
        except GuestyFilterRejectedError as exc:
            raise UpdateFailed(
                f"Guesty rejected the reservation query filters: {exc.message}",
            ) from exc
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching reservations: {exc.message}",
            ) from exc

        reservations = fetch_result.reservations
        counters = _RefreshCounters()

        # Feature 007 decides tracking on the listing side (FR-014).
        known_ids: set[str] = set()
        if self.listings_coordinator.data:
            known_ids = set(self.listings_coordinator.data.keys())

        if not known_ids and reservations:
            _LOGGER.debug(
                "No known listings yet; skipping %d reservation(s)",
                len(reservations),
            )
            _log_refresh_diagnostics(
                fetch_result,
                counters,
                GuestLookupResult(),
            )
            return {}

        now = datetime.now(UTC)
        grouped: dict[str, list[GuestyReservation]] = defaultdict(list)
        guest_ids: list[str] = []

        for reservation in reservations:
            if reservation.status in HOLD_STATUSES:
                counters.alteration_drops += 1
                _LOGGER.debug(
                    "Dropping reservation %s held at status %s",
                    reservation.id,
                    reservation.status,
                )
                continue

            if not _within_window(
                reservation,
                now=now,
                past_days=past_days,
                future_days=future_days,
            ):
                counters.trim_drops += 1
                _LOGGER.debug(
                    "Trimming reservation %s outside the configured window",
                    reservation.id,
                )
                continue

            attributed, unknown_ids = _attribute_segments(
                reservation,
                known_ids,
            )
            if not attributed:
                counters.skipped += 1
                _LOGGER.warning(
                    "Skipping reservation %s: no tracked listing among %s",
                    reservation.id,
                    ", ".join(sorted(unknown_ids)),
                )
                continue

            counters.attributed += 1
            if reservation.guest is not None and reservation.guest.guest_id:
                guest_ids.append(reservation.guest.guest_id)
            if unknown_ids:
                counters.partially_attributed += 1
                _LOGGER.debug(
                    "Ignoring stay segments of reservation %s for "
                    "untracked listing(s) %s",
                    reservation.id,
                    ", ".join(sorted(unknown_ids)),
                )

            for listing_id, segments in attributed.items():
                grouped[listing_id].append(
                    _project(reservation, listing_id, segments, now),
                )

        lookup = await self._resolve_guests(guest_ids)
        _log_refresh_diagnostics(fetch_result, counters, lookup)

        return {
            listing_id: sorted(
                (_with_guest(projection, lookup.guests) for projection in projections),
                key=lambda r: r.check_in,
            )
            for listing_id, projections in grouped.items()
        }

    async def _resolve_guests(
        self,
        guest_ids: list[str],
    ) -> GuestLookupResult:
        """Resolve guest records for the published reservations.

        Lookups are issued on the v3 path only, where the payload
        carries a guest id and nothing else; the legacy payloads
        embed the guest already. Nothing raised here may reach the
        caller: a guest lookup must never cost a refresh (FR-020,
        NFR-005).

        Args:
            guest_ids: Guest ids of the attributed reservations,
                which may repeat across reservations.

        Returns:
            The known guest records and this refresh's counters.
        """
        distinct = list(dict.fromkeys(guest_ids))
        if not distinct:
            return GuestLookupResult()
        if self.api_client.reservations_api != RESERVATIONS_API_V3:
            return GuestLookupResult()
        try:
            return await self.api_client.resolve_guests(distinct)
        except Exception as exc:
            _LOGGER.warning(
                "Guest lookups failed (%s); publishing without them",
                type(exc).__name__,
            )
            return GuestLookupResult()


@dataclass
class _RefreshCounters:
    """Per-refresh attribution counters for FR-042 diagnostics.

    Attributes:
        attributed: Reservations published on at least one listing.
        partially_attributed: Published reservations that also had
            at least one untracked stay segment.
        skipped: Reservations no tracked listing could claim.
        alteration_drops: Reservations dropped as proposed changes.
        trim_drops: Reservations dropped by the window trim.
    """

    attributed: int = 0
    partially_attributed: int = 0
    skipped: int = 0
    alteration_drops: int = 0
    trim_drops: int = 0


def _log_refresh_diagnostics(
    fetch_result: ReservationFetchResult,
    counters: _RefreshCounters,
    lookup: GuestLookupResult,
) -> None:
    """Emit the per-refresh DEBUG diagnostics line (FR-042).

    The line carries counts only: no reservation identifier and no
    guest detail ever appears in it (FR-021, NFR-009).

    Args:
        fetch_result: The retrieval result and its source counters.
        counters: The attribution counters of this refresh.
        lookup: The guest cache counters of this refresh.
    """
    _LOGGER.debug(
        "Reservation refresh: pages=%d statuses=%s segments=%s "
        "attributed=%d partially_attributed=%d skipped=%d "
        "alteration_drops=%d trim_drops=%d guest_hits=%d "
        "guest_lookups=%d guest_failures=%d guest_deferrals=%d",
        fetch_result.pages,
        dict(fetch_result.status_counts),
        dict(fetch_result.segment_histogram),
        counters.attributed,
        counters.partially_attributed,
        counters.skipped,
        counters.alteration_drops,
        counters.trim_drops,
        lookup.hits,
        lookup.lookups,
        lookup.failures,
        lookup.deferrals,
    )


def _with_guest(
    reservation: GuestyReservation,
    guests: Mapping[str, GuestyGuest],
) -> GuestyReservation:
    """Attach the cached guest record to one projection.

    A projection whose guest is unknown this refresh keeps its
    id-only placeholder, so it still publishes with empty guest
    attributes rather than not publishing at all (FR-020).

    Args:
        reservation: The projection about to be published.
        guests: Guest records resolved for this refresh.

    Returns:
        The projection, with its guest record attached if known.
    """
    guest = reservation.guest
    if guest is None or not guest.guest_id:
        return reservation
    resolved = guests.get(guest.guest_id)
    if resolved is None:
        return reservation
    return replace(reservation, guest=resolved)


def _within_window(
    reservation: GuestyReservation,
    *,
    now: datetime,
    past_days: int,
    future_days: int,
) -> bool:
    """Test a reservation against the configured instant window.

    The server-side window is padded because the v3 filters compare
    listing-localized dates, so the configured window is enforced
    here instead (FR-002a). A stay in progress satisfies both terms
    by construction and can never be trimmed (FR-004).

    Args:
        reservation: The reservation to test.
        now: The refresh instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.

    Returns:
        True when the reservation overlaps the configured window.
    """
    return reservation.check_out > now - timedelta(
        days=past_days
    ) and reservation.check_in < now + timedelta(days=future_days)


def _attribute_segments(
    reservation: GuestyReservation,
    known_ids: set[str],
) -> tuple[dict[str, list[GuestyStaySegment]], set[str]]:
    """Attribute a reservation's stay segments to tracked listings.

    A segment is attributed to its own listing when that listing is
    tracked, else to its parent listing when that is (FR-012). A
    reservation without stay segments carries its listing at the top
    level, which is how the legacy path reports attribution.

    Args:
        reservation: The reservation to attribute.
        known_ids: Listing IDs the listings coordinator tracks.

    Returns:
        Tuple of the tracked listings with their segments, and the
        listing IDs that could not be resolved.
    """
    attributed: dict[str, list[GuestyStaySegment]] = {}
    unknown_ids: set[str] = set()

    if not reservation.stay:
        if reservation.listing_id in known_ids:
            attributed[reservation.listing_id] = []
        else:
            unknown_ids.add(reservation.listing_id)
        return attributed, unknown_ids

    for segment in reservation.stay:
        if segment.listing_id in known_ids:
            listing_id = segment.listing_id
        elif segment.parent_listing_id in known_ids:
            listing_id = segment.parent_listing_id or ""
        else:
            unknown_ids.add(segment.listing_id)
            continue
        attributed.setdefault(listing_id, []).append(segment)

    return attributed, unknown_ids


def _project(
    reservation: GuestyReservation,
    listing_id: str,
    segments: list[GuestyStaySegment],
    now: datetime,
) -> GuestyReservation:
    """Project a reservation onto one tracked listing (FR-011).

    A reservation with at most one segment projects to itself apart
    from the tracked listing it is published under. With more than
    one segment the listing's own interval supplies the dates, guest
    count and night count, and the status becomes segment-aware.

    Args:
        reservation: The reservation to project.
        listing_id: The tracked listing to project onto.
        segments: The reservation's segments for that listing.
        now: The refresh instant.

    Returns:
        The projection published for that listing.
    """
    if len(reservation.stay) <= 1:
        if not reservation.stay and reservation.listing_id == listing_id:
            return reservation
        return replace(reservation, listing_id=listing_id, stay=())

    segment = _select_segment(segments, now)
    return replace(
        reservation,
        listing_id=listing_id,
        status=_segment_status(reservation.status, segment, now),
        check_in=segment.check_in,
        check_out=segment.check_out,
        check_in_local=segment.check_in_local,
        check_out_local=segment.check_out_local,
        guests_count=segment.guests_count,
        nights_count=_segment_nights(segment),
        stay=(),
    )


def _select_segment(
    segments: list[GuestyStaySegment],
    now: datetime,
) -> GuestyStaySegment:
    """Select the interval a listing's projection represents.

    Segments in the same listing stay disjoint rather than being
    collapsed into one span, because an A -> B -> A stay would then
    cover the guest's time in B. The interval containing the refresh
    instant wins, else the earliest future one, else the latest past
    one (data-model.md 3.1).

    Args:
        segments: The listing's segments, in payload order.
        now: The refresh instant.

    Returns:
        The selected segment.
    """
    ordered = sorted(segments, key=lambda segment: segment.check_in)
    for segment in ordered:
        if segment.check_in <= now < segment.check_out:
            return segment
    for segment in ordered:
        if segment.check_in > now:
            return segment
    return max(ordered, key=lambda segment: segment.check_out)


def _segment_status(
    status: str,
    segment: GuestyStaySegment,
    now: datetime,
) -> str:
    """Derive a listing's status from its segment (FR-011a).

    Only ``confirmed`` and ``checked_in`` are temporal: every other
    status, including ``canceled``, is carried unchanged to every
    listing the reservation touches, so a canceled stay can never
    surface as upcoming on a later segment's listing.

    Args:
        status: The reservation-level status.
        segment: The segment this listing is published from.
        now: The refresh instant.

    Returns:
        The status published for that listing.
    """
    if status not in _TEMPORAL_STATUSES:
        return status
    if now < segment.check_in:
        return "confirmed"
    if now >= segment.check_out:
        return "checked_out"
    return status


def _segment_nights(segment: GuestyStaySegment) -> int | None:
    """Derive the night count of one stay segment.

    Args:
        segment: The segment to measure.

    Returns:
        Whole nights in the segment, or None when undeterminable.
    """
    nights = derive_nights_count(
        segment.check_in_local,
        segment.check_out_local,
    )
    if nights is not None:
        return nights
    return (segment.check_out.date() - segment.check_in.date()).days


class CustomFieldsDefinitionCoordinator(
    DataUpdateCoordinator[list[GuestyCustomFieldDefinition]],
):
    """Coordinator that fetches custom field definitions periodically.

    Wraps the custom fields client's ``get_definitions()`` call inside
    a ``DataUpdateCoordinator`` so the service handler can validate
    field IDs and types against cached definitions. The poll interval
    is configurable via options flow.

    Attributes:
        cf_client: The custom fields client instance.
        config_entry: The integration config entry.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        cf_client: GuestyCustomFieldsClient,
    ) -> None:
        """Initialize the custom fields definition coordinator.

        Args:
            hass: Home Assistant instance.
            entry: The config entry for this integration.
            cf_client: Custom fields client for fetching definitions.
        """
        self.cf_client = cf_client
        interval_minutes = entry.options.get(
            CONF_CF_SCAN_INTERVAL,
            DEFAULT_CF_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_custom_fields",
            update_interval=timedelta(minutes=interval_minutes),
        )

    async def _async_update_data(
        self,
    ) -> list[GuestyCustomFieldDefinition]:
        """Fetch all custom field definitions.

        Returns:
            List of custom field definitions from the API.

        Raises:
            UpdateFailed: On any Guesty API error.
        """
        try:
            return await self.cf_client.get_definitions()
        except GuestyApiError as exc:
            raise UpdateFailed(
                f"Error fetching custom field definitions: {exc.message}",
            ) from exc

    def get_field(
        self,
        field_id: str,
    ) -> GuestyCustomFieldDefinition | None:
        """Look up a custom field definition by ID.

        Args:
            field_id: The custom field identifier to look up.

        Returns:
            The matching definition, or None if not found.
        """
        if self.data is None:
            return None
        for field in self.data:
            if field.field_id == field_id:
                return field
        return None

    def get_fields_for_target(
        self,
        target_type: str,
    ) -> list[GuestyCustomFieldDefinition]:
        """Filter definitions applicable to a target type.

        Args:
            target_type: Entity type to filter by
                ('listing' or 'reservation').

        Returns:
            List of definitions applicable to the target type.
        """
        if self.data is None:
            return []
        return [f for f in self.data if target_type in f.applicable_to]
