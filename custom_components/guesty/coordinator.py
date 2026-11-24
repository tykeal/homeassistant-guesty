# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Guesty listings and reservations."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Collection, Coroutine, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    REQUEST_REFRESH_DEFAULT_IMMEDIATE,
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.guesty.api.client import resolve_reservations_api
from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    HOLD_STATUSES,
    RESERVATIONS_API_V3,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyFilterRejectedError,
    GuestyRateLimitError,
)
from custom_components.guesty.api.guest_cache import GuestLookupResult
from custom_components.guesty.api.models import (
    FallbackCandidate,
    FallbackCounters,
    GuestyCustomFieldDefinition,
    GuestyGuest,
    GuestyListing,
    GuestyReservation,
    GuestyStaySegment,
    LegacyWriteCounters,
    ReservationFetchResult,
    derive_nights_count,
)
from custom_components.guesty.api.timing import (
    is_valid_local_wall_time,
    resolve_local_datetime,
)
from custom_components.guesty.const import (
    CONF_CF_SCAN_INTERVAL,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATION_SCAN_INTERVAL,
    CONF_RESERVATIONS_API,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_LISTINGS,
    DEFAULT_CF_SCAN_INTERVAL,
    DEFAULT_RESERVATION_SCAN_INTERVAL,
    DEFAULT_RESERVATIONS_API,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.guesty.identity import (
    CandidateHint,
    IdentityComparison,
    ReservationIdentityGuard,
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

# Identifier pairs named in the single FR-032 abort log line.
_MAX_LOGGED_CONFLICTS: int = 5

RESERVATION_SEARCH_REJECTED = (
    f"Guesty rejected the reservation search ({RESERVATIONS_V3_SEARCH_ENDPOINT}, "
    "HTTP 400). Check search settings or contact support."
)


class ReservationSearchRejected(UpdateFailed):
    """Report a rejected reservation search without response contents."""


class _ReservationRefreshDebouncer(Debouncer[Coroutine[Any, Any, None]]):
    """Keep debounced requests pending while a recovered write holds the lock."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Use Home Assistant's normal refresh cooldown and scheduling policy."""
        super().__init__(
            hass,
            _LOGGER,
            cooldown=REQUEST_REFRESH_DEFAULT_COOLDOWN,
            immediate=REQUEST_REFRESH_DEFAULT_IMMEDIATE,
        )
        self._write_dispatching = False
        self._write_finished = asyncio.Event()
        self._write_finished.set()
        self._cancel_generation = 0

    @callback
    def async_cancel(self) -> None:
        """Cancel timers and invalidate any already-deferred timer handler."""
        self._cancel_generation += 1
        super().async_cancel()

    @asynccontextmanager
    async def async_write_lock(self) -> AsyncIterator[None]:
        """Hold refresh serialization while identifying a non-refresh holder."""
        async with self.async_lock():
            self._write_dispatching = True
            self._write_finished.clear()
            try:
                yield
            finally:
                self._write_dispatching = False
                self._write_finished.set()

    async def _handle_timer_finish(self) -> None:
        """Defer HA's timer handler until a recovered write releases the lock."""
        generation = self._cancel_generation
        while self._write_dispatching:
            await self._write_finished.wait()
        if (
            generation == self._cancel_generation
            and not self._shutdown_requested
            and self._timer_task is None
        ):
            await super()._handle_timer_finish()


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
        timing_listings: All fetched listings, including unselected exact rooms.
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
        self.timing_listings: dict[str, GuestyListing] = {}
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
        self.timing_listings = new_data
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
        identity: The reservation identity guard for this entry.
        reservations_path: Reservation path this entry is using.
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
        self._zone_cache: dict[str, ZoneInfo | None] = {}
        self.identity = ReservationIdentityGuard(hass, entry.entry_id)
        self.reservations_path = resolve_reservations_api(
            entry.options.get(
                CONF_RESERVATIONS_API,
                DEFAULT_RESERVATIONS_API,
            ),
        )
        self._identity_logged_path: str | None = None
        self._identity_divergence_logged: str | None = None
        self.fallback_served_ids: frozenset[str] = frozenset()
        self.fallback_sourced_ids: frozenset[str] = frozenset()
        self.last_fallback_counters = FallbackCounters()
        self.legacy_write_counters = LegacyWriteCounters()
        # FR-042 requires the same counters on both surfaces, so the
        # last refresh's numbers are retained for the diagnostics
        # download rather than discarded with the refresh.
        self.last_refresh_diagnostics: dict[str, Any] = {}
        self.bootstrap_pages: int = 0
        self._write_miss_generation = 0
        self._write_miss_attempt_generation = 0
        self._write_miss_outcomes: deque[
            tuple[
                int,
                int,
                bool,
                bool,
                Mapping[str, Any],
                tuple[Any, ...],
                ConfigEntry,
                GuestyApiClient,
            ]
        ] = deque(maxlen=256)
        interval_minutes = entry.options.get(
            CONF_RESERVATION_SCAN_INTERVAL,
            DEFAULT_RESERVATION_SCAN_INTERVAL,
        )
        self._reservation_debouncer = _ReservationRefreshDebouncer(hass)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_reservations",
            update_interval=timedelta(minutes=interval_minutes),
            request_refresh_debouncer=self._reservation_debouncer,
        )

    @property
    def write_miss_generation(self) -> int:
        """Return the number of completed coordinator refresh attempts."""
        return self._write_miss_generation

    @property
    def write_miss_attempt_generation(self) -> int:
        """Return the generation of the latest started coordinator attempt."""
        return self._write_miss_attempt_generation

    @property
    def write_miss_available(self) -> bool:
        """Report whether shutdown still permits recovery and dispatch."""
        return not self._shutdown_requested and not self.hass.is_stopping

    @asynccontextmanager
    async def async_write_miss_dispatch_lock(self) -> AsyncIterator[None]:
        """Prevent coordinator refreshes throughout a recovered dispatch."""
        async with self._reservation_debouncer.async_write_lock():
            yield

    def _write_miss_settings(self) -> tuple[Any, ...]:
        """Capture effective source and selection values without credentials."""
        options = self.config_entry.options
        selected = options.get(CONF_SELECTED_LISTINGS)
        return (
            resolve_reservations_api(
                options.get(CONF_RESERVATIONS_API, DEFAULT_RESERVATIONS_API)
            ),
            options.get(CONF_PAST_DAYS, DEFAULT_PAST_DAYS),
            options.get(CONF_FUTURE_DAYS, DEFAULT_FUTURE_DAYS),
            None if selected is None else frozenset(selected),
        )

    async def _async_refresh(
        self,
        log_failures: bool = True,
        raise_on_auth_failed: bool = False,
        scheduled: bool = False,
        raise_on_entry_error: bool = False,
    ) -> None:
        """Record completed refresh outcomes after Home Assistant processes them."""
        if self._shutdown_requested or (scheduled and self.hass.is_stopping):
            await super()._async_refresh(
                log_failures=log_failures,
                raise_on_auth_failed=raise_on_auth_failed,
                scheduled=scheduled,
                raise_on_entry_error=raise_on_entry_error,
            )
            return
        entry = self.config_entry
        client = self.api_client
        options = entry.options
        settings = self._write_miss_settings()
        self._write_miss_attempt_generation += 1
        attempt = self._write_miss_attempt_generation
        success = False
        completed = False
        try:
            await super()._async_refresh(
                log_failures=log_failures,
                raise_on_auth_failed=raise_on_auth_failed,
                scheduled=scheduled,
                raise_on_entry_error=raise_on_entry_error,
            )
            completed = True
            success = (
                self.last_update_success
                and self.config_entry is entry
                and self.api_client is client
                and self.config_entry.options is options
                and self._write_miss_settings() == settings
            )
        finally:
            self._write_miss_generation += 1
            self._write_miss_outcomes.append(
                (
                    self._write_miss_generation,
                    attempt,
                    success,
                    completed
                    and not self.last_update_success
                    and isinstance(self.last_exception, ReservationSearchRejected),
                    options,
                    settings,
                    entry,
                    client,
                )
            )

    async def async_refresh_for_write_miss(self, generation: int) -> bool:
        """Join or initiate one completed refresh after the captured generation."""
        entry = self.config_entry
        client = self.api_client
        options = entry.options
        settings = self._write_miss_settings()

        async def refresh() -> bool:
            """Share the coordinator lock and consume the first eligible outcome."""
            async with self._debounced_refresh.async_lock():
                if not self.write_miss_available:
                    return False
                if self._write_miss_outcomes:
                    first = self._write_miss_outcomes[0][0]
                    if generation < first - 1:
                        return False
                    for (
                        completed,
                        _attempt,
                        success,
                        rejected,
                        refresh_options,
                        refresh_settings,
                        refresh_entry,
                        refresh_client,
                    ) in self._write_miss_outcomes:
                        if completed > generation:
                            if (
                                rejected
                                and refresh_options is options
                                and refresh_settings == settings
                                and refresh_entry is entry
                                and refresh_client is client
                                and self.config_entry is entry
                                and self.api_client is client
                                and self.config_entry.options is options
                                and self._write_miss_settings() == settings
                            ):
                                raise ReservationSearchRejected(
                                    RESERVATION_SEARCH_REJECTED
                                )
                            return (
                                success
                                and self.write_miss_available
                                and self.last_update_success
                                and refresh_options is options
                                and refresh_settings == settings
                                and refresh_entry is entry
                                and refresh_client is client
                                and self.config_entry is entry
                                and self.api_client is client
                                and self.config_entry.options is options
                                and self._write_miss_settings() == settings
                            )
                if (
                    self.config_entry is not entry
                    or self.api_client is not client
                    or self.config_entry.options is not options
                    or self._write_miss_settings() != settings
                ):
                    return False
                await self._async_refresh(log_failures=False)
                if (
                    self._write_miss_generation > generation
                    and self._write_miss_outcomes
                    and self._write_miss_outcomes[-1][3]
                    and self.config_entry is entry
                    and self.api_client is client
                    and self.config_entry.options is options
                    and self._write_miss_settings() == settings
                ):
                    raise ReservationSearchRejected(RESERVATION_SEARCH_REJECTED)
                return (
                    self._write_miss_generation > generation
                    and self.write_miss_available
                    and bool(self._write_miss_outcomes)
                    and self._write_miss_outcomes[-1][2]
                    and self.config_entry is entry
                    and self.api_client is client
                    and self.config_entry.options is options
                    and self._write_miss_settings() == settings
                )

        task = entry.async_create_background_task(
            self.hass, refresh(), "guesty write-miss refresh", eager_start=True
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            waiter = asyncio.current_task()
            if task.cancelled() and waiter is not None and not waiter.cancelling():
                return False
            raise

    async def _async_update_data(
        self,
    ) -> dict[str, list[GuestyReservation]]:
        """Contain unexpected refresh errors without exposing exception contents."""
        try:
            return await self._async_fetch_reservations()
        except ConfigEntryAuthFailed, ConfigEntryError, UpdateFailed:
            raise
        except Exception:
            raise UpdateFailed("Reservation refresh failed") from None

    async def _async_fetch_reservations(
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
                candidates=self._fallback_candidates(),
            )
        except GuestyFilterRejectedError:
            raise ReservationSearchRejected(RESERVATION_SEARCH_REJECTED) from None
        except GuestyAuthError:
            raise UpdateFailed(
                "Guesty authentication failed. Check the integration credentials."
            ) from None
        except GuestyRateLimitError:
            raise UpdateFailed("Guesty rate limited reservation requests.") from None
        except GuestyApiError:
            raise UpdateFailed("Error fetching reservations from Guesty.") from None

        reservations = await self._resolve_v3_reservations(
            fetch_result.reservations,
        )
        known_fallback_ids = self.fallback_served_ids | self.fallback_sourced_ids
        retired = (
            bool(known_fallback_ids)
            and not self.api_client.legacy_write_routing_available
        )
        if not retired:
            self.fallback_served_ids = frozenset(fetch_result.fallback_served)
            self.fallback_sourced_ids = frozenset(fetch_result.fallback_sourced)
        else:
            # A retired legacy generation stops reporting anything,
            # so keeping the last known sets is what makes a write
            # against one of them refuse with the retirement
            # message instead of silently going to v3 (FR-061). A
            # reservation that has reappeared in v3 leaves them.
            returned = {reservation.id for reservation in fetch_result.reservations}
            self.fallback_served_ids -= returned
            self.fallback_sourced_ids -= returned
        self.last_fallback_counters = fetch_result.fallback
        counters = _RefreshCounters(received=len(reservations))

        try:
            comparison = await self._check_identity(
                reservations,
                self.fallback_sourced_ids,
            )
        except UpdateFailed:
            # An aborted refresh is precisely the one the download
            # has to explain, so its counters are published before
            # the abort propagates. Home Assistant retains the last
            # known good entity data regardless (FR-042, FR-032).
            self._publish_refresh_diagnostics(
                fetch_result,
                counters,
                GuestLookupResult(),
            )
            raise
        counters.identity_matched = comparison.matched

        # Feature 007 decides tracking on the listing side (FR-014).
        known_ids: set[str] = set()
        if self.listings_coordinator.data:
            known_ids = set(self.listings_coordinator.data.keys())

        if not known_ids and reservations:
            _LOGGER.debug(
                "No known listings yet; skipping %d reservation(s)",
                len(reservations),
            )
            await self._record_identity(
                reservations,
                {},
                comparison,
                past_days,
                fetch_result,
            )
            self._publish_refresh_diagnostics(
                fetch_result,
                counters,
                GuestLookupResult(),
            )
            return {}

        now = datetime.now(UTC)
        timing_listings = self._timing_listings()
        grouped: dict[str, list[GuestyReservation]] = defaultdict(list)
        guest_ids: list[str] = []
        published: dict[str, CandidateHint] = {}

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
                timing_listings=timing_listings,
                zones=self._zone_cache,
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
            counters.unknown_listings |= unknown_ids
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
        await self._record_identity(
            reservations,
            published,
            comparison,
            past_days,
            fetch_result,
        )
        self._publish_refresh_diagnostics(fetch_result, counters, lookup)

        return {
            listing_id: sorted(
                (_with_guest(projection, lookup.guests) for projection in projections),
                key=_reservation_sort_key,
            )
            for listing_id, projections in grouped.items()
        }

    async def _resolve_v3_reservations(
        self,
        reservations: tuple[GuestyReservation, ...],
    ) -> tuple[GuestyReservation, ...]:
        """Resolve only V3 segments from already-fetched exact-room settings.

        Args:
            reservations: The source records returned by this refresh.

        Returns:
            Records with current V3 endpoint values resolved independently.
            Legacy and fallback-sourced records remain unchanged.
        """
        fetched = getattr(
            self.listings_coordinator,
            "timing_listings",
            None,
        )
        if not isinstance(fetched, Mapping):
            fetched = self.listings_coordinator.data
        listings = fetched if isinstance(fetched, Mapping) else {}
        resolved: list[GuestyReservation] = []
        for reservation in reservations:
            if not reservation.is_v3 or not reservation.stay:
                resolved.append(reservation)
                continue

            segments: list[GuestyStaySegment] = []
            for segment in reservation.stay:
                listing = listings.get(segment.listing_id)
                if (
                    not isinstance(listing, GuestyListing)
                    or not listing.timezone_explicit
                ):
                    segments.append(replace(segment, check_in=None, check_out=None))
                    continue
                zone = await self._async_timezone(listing.timezone)
                segments.append(
                    replace(
                        segment,
                        check_in=resolve_local_datetime(
                            segment.check_in_local,
                            listing.check_in_time,
                            zone,
                        ),
                        check_out=resolve_local_datetime(
                            segment.check_out_local,
                            listing.check_out_time,
                            zone,
                        ),
                    ),
                )

            resolved_stay = tuple(segments)
            resolved_stay = _apply_same_date_handoffs(
                resolved_stay,
                listings,
                self._zone_cache,
            )
            first = resolved_stay[0]
            last = resolved_stay[-1]
            resolved.append(
                replace(
                    reservation,
                    check_in=first.check_in,
                    check_out=last.check_out,
                    check_in_local=first.check_in_local,
                    check_out_local=last.check_out_local,
                    guests_count=first.guests_count,
                    stay=resolved_stay,
                ),
            )
        return tuple(resolved)

    def _timing_listings(self) -> Mapping[str, GuestyListing]:
        """Return the full fetched listing map without changing selection.

        Returns:
            Exact-room metadata retained by the listings coordinator.
        """
        fetched = getattr(
            self.listings_coordinator,
            "timing_listings",
            None,
        )
        if not isinstance(fetched, Mapping):
            fetched = self.listings_coordinator.data
        if not isinstance(fetched, Mapping):
            return {}
        return {
            listing_id: listing
            for listing_id, listing in fetched.items()
            if isinstance(listing, GuestyListing)
        }

    async def _async_timezone(self, timezone_name: str) -> ZoneInfo | None:
        """Load and cache one IANA zone outside Home Assistant's event loop.

        Args:
            timezone_name: Explicit timezone name from a listing DTO.

        Returns:
            The prepared timezone, or None for invalid input.
        """
        if not isinstance(timezone_name, str) or not timezone_name.strip():
            return None
        if timezone_name in self._zone_cache:
            return self._zone_cache[timezone_name]
        try:
            zone = await self.hass.async_add_executor_job(ZoneInfo, timezone_name)
        except ZoneInfoNotFoundError, ValueError, TypeError, OSError:
            zone = None
        self._zone_cache[timezone_name] = zone
        return zone

    def record_legacy_write(self, operation: str, outcome: str) -> None:
        """Count one legacy-routed write outcome (FR-062).

        Args:
            operation: One of ``LEGACY_WRITE_OPERATIONS``.
            outcome: One of ``LEGACY_WRITE_OUTCOMES``.
        """
        self.legacy_write_counters = self.legacy_write_counters.recorded(
            operation,
            outcome,
        )
        _LOGGER.debug(
            "Legacy-routed reservation writes: %s",
            self.legacy_write_counters.as_dict(),
        )

    async def _check_identity(
        self,
        reservations: tuple[GuestyReservation, ...],
        fallback_ids: Collection[str] = (),
    ) -> IdentityComparison:
        """Compare a fetched refresh against the identity baseline.

        Runs before publication and costs no request: it reads only
        the data this refresh already fetched (FR-034). A changed
        confirmation code to identifier mapping — including a
        wholesale swap between two identifiers that are both still
        present — aborts the refresh so the coordinator retains its
        last-known-good data, publishes nothing and blocks writes
        (FR-032).

        Args:
            reservations: Reservations the refresh retrieved.
            fallback_ids: Identifiers this refresh published from
                the legacy not-found fallback, which are compared
                for conflicts but may not confirm continuity
                (FR-055).

        Returns:
            The comparison outcome of a non-ambiguous refresh.

        Raises:
            UpdateFailed: When the identity is ambiguous.
        """
        if not self.identity.loaded:
            await self.identity.async_load()

        comparison = self.identity.compare(
            reservations,
            self.reservations_path,
            fallback_ids,
        )
        if comparison.is_ambiguous:
            self.identity.mark_ambiguous()
            _LOGGER.error(
                "Reservation identity is ambiguous: %d confirmation "
                "code(s) now identify a different reservation (%s). "
                "The refresh was aborted and the last known "
                "reservations were retained; no reservation write "
                "will be performed. Set the 'Reservations API' "
                "option to 'legacy' and reload the entry to roll "
                "back",
                len(comparison.conflicts),
                ", ".join(
                    f"{conflict.known_id} -> {conflict.observed_id}"
                    for conflict in comparison.conflicts[:_MAX_LOGGED_CONFLICTS]
                ),
            )
            raise UpdateFailed(
                "Reservation identity is ambiguous; refusing to "
                "publish or write until it is resolved",
            )

        self._log_identity_match(comparison)
        self._log_identity_divergence(comparison)
        return comparison

    def _log_identity_match(
        self,
        comparison: IdentityComparison,
    ) -> None:
        """Announce the FR-035 match count once per path activation.

        Args:
            comparison: The comparison this refresh ran.
        """
        path = self.reservations_path
        if self._identity_logged_path == path:
            return
        self._identity_logged_path = path
        _LOGGER.info(
            "Reservation identity: %d previously known reservation "
            "identifier(s) matched on the %s path",
            comparison.matched,
            path,
        )

    def _log_identity_divergence(
        self,
        comparison: IdentityComparison,
    ) -> None:
        """Explain a cross-path identifier divergence once.

        Divergence does not abort the refresh — the paths simply
        number the same booking differently — but it does refuse
        every reservation write, so it has to be visible.

        Args:
            comparison: The comparison this refresh ran.
        """
        if not comparison.cross_path_conflicts:
            return
        if self._identity_divergence_logged == self.reservations_path:
            return
        self._identity_divergence_logged = self.reservations_path
        _LOGGER.warning(
            "Reservation identity differs between the legacy and v3 "
            "reservation paths for %d confirmation code(s) (%s). "
            "Reservations are still published, but no reservation "
            "write will be performed while the two paths disagree",
            len(comparison.cross_path_conflicts),
            ", ".join(
                f"{conflict.known_id} -> {conflict.observed_id}"
                for conflict in comparison.cross_path_conflicts[:_MAX_LOGGED_CONFLICTS]
            ),
        )

    async def _record_identity(
        self,
        reservations: tuple[GuestyReservation, ...],
        published: Mapping[str, CandidateHint],
        comparison: IdentityComparison,
        past_days: int,
        fetch_result: ReservationFetchResult,
    ) -> None:
        """Persist the baseline this successful refresh produced.

        Storage failures must never cost a refresh: the published
        data is already correct, and the next refresh retries the
        write. The guard reports the failure itself and keeps
        refusing writes until the baseline is stored.

        Args:
            reservations: Reservations the refresh retrieved.
            published: Stay hints of the reservations published.
            comparison: The comparison this refresh ran.
            past_days: The configured past-day window, which dates
                the safe retirement of bootstrap evidence (T074).
            fetch_result: The retrieval result, which reports what
                the legacy not-found fallback published and what it
                confirmed is gone.
        """
        stored = await self.identity.async_record_refresh(
            path=self.reservations_path,
            reservations=reservations,
            published=published,
            comparison=comparison,
            updated=datetime.now(UTC).isoformat(),
            past_days=past_days,
            fallback_ids=fetch_result.fallback_sourced,
            absent_ids=fetch_result.fallback_absent,
            observed_ids=fetch_result.observed_ids,
            observed_reservations=fetch_result.observed_reservations,
        )
        if stored and fetch_result.fallback_absent:
            # Only a persisted retirement may clear the fallback's
            # queue; otherwise the candidate would survive a reload
            # and be looked up again (FR-052).
            self.api_client.acknowledge_absent_reservations(
                fetch_result.fallback_absent,
            )

    def _fallback_candidates(self) -> dict[str, FallbackCandidate]:
        """Build this refresh's legacy fallback candidate hints.

        Every reservation the integration already knows is offered;
        the source itself decides which of them v3 failed to return
        and which are worth a request (FR-050). Hints that cannot be
        parsed are dropped rather than failing the refresh, because
        a corrupt stored date must not stop reservations publishing.

        Returns:
            Last known stay endpoints, keyed by identifier.
        """
        baseline = self.identity.baseline
        if baseline is None:
            return {}
        candidates: dict[str, FallbackCandidate] = {}
        for reservation_id, hint in baseline.candidates.items():
            if (
                hint.timing_status != "known"
                or hint.check_in is None
                or hint.check_out is None
            ):
                continue
            try:
                check_in = datetime.fromisoformat(hint.check_in)
                check_out = datetime.fromisoformat(hint.check_out)
            except TypeError, ValueError:
                continue
            candidates[reservation_id] = FallbackCandidate(
                check_in=_as_utc(check_in),
                check_out=_as_utc(check_out),
            )
        return candidates

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

    def _publish_refresh_diagnostics(
        self,
        fetch_result: ReservationFetchResult,
        counters: _RefreshCounters,
        lookup: GuestLookupResult,
    ) -> None:
        """Retain and log this refresh's counters (FR-042).

        Both diagnostics surfaces carry the same numbers, so they
        are produced once here: the DEBUG line for a log reader and
        the retained mapping for the diagnostics download.

        Args:
            fetch_result: The retrieval result and its counters.
            counters: The attribution counters of this refresh.
            lookup: The guest cache counters of this refresh.
        """
        ambiguous = self.identity.last_refresh_ambiguous
        self.last_refresh_diagnostics = {
            "pages": fetch_result.pages,
            "received": counters.received,
            "attributed": counters.attributed,
            "partially_attributed": counters.partially_attributed,
            "trimmed": counters.trim_drops,
            "skipped": counters.skipped,
            "alteration_drops": counters.alteration_drops,
            "status_counts": dict(fetch_result.status_counts),
            "segment_histogram": dict(fetch_result.segment_histogram),
            "unknown_listings": len(counters.unknown_listings),
            "guest_hits": lookup.hits,
            "guest_lookups": lookup.lookups,
            "guest_failures": lookup.failures,
            "guest_deferrals": lookup.deferrals,
            "identity_matched": counters.identity_matched,
            "identity_ambiguous": ambiguous,
        }
        _log_refresh_diagnostics(
            fetch_result,
            counters,
            lookup,
            identity_ambiguous=ambiguous,
        )


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
        identity_matched: Previously known identifiers matched.
        received: Reservations the source returned this refresh.
        unknown_listings: Distinct listing ids a stay segment named
            that no tracked listing claims.
    """

    attributed: int = 0
    partially_attributed: int = 0
    skipped: int = 0
    alteration_drops: int = 0
    trim_drops: int = 0
    identity_matched: int = 0
    received: int = 0
    unknown_listings: set[str] = field(default_factory=set)


def _log_refresh_diagnostics(
    fetch_result: ReservationFetchResult,
    counters: _RefreshCounters,
    lookup: GuestLookupResult,
    *,
    identity_ambiguous: bool = False,
) -> None:
    """Emit the per-refresh DEBUG diagnostics line (FR-042).

    The line carries counts only: no reservation identifier, no
    confirmation code and no guest detail ever appears in it
    (FR-021, NFR-009).

    Args:
        fetch_result: The retrieval result and its source counters.
        counters: The attribution counters of this refresh.
        lookup: The guest cache counters of this refresh.
        identity_ambiguous: Whether an ambiguous identity aborted
            the last refresh.
    """
    _LOGGER.debug(
        "Reservation refresh: pages=%d statuses=%s segments=%s "
        "attributed=%d partially_attributed=%d skipped=%d "
        "alteration_drops=%d trim_drops=%d unknown_listings=%d "
        "guest_hits=%d "
        "guest_lookups=%d guest_failures=%d guest_deferrals=%d "
        "identity_matched=%d identity_ambiguous=%s "
        "fallback_attempted=%d fallback_served=%d "
        "fallback_succeeded=%d fallback_absent=%d "
        "fallback_failed=%d fallback_deferred=%d",
        fetch_result.pages,
        dict(fetch_result.status_counts),
        dict(fetch_result.segment_histogram),
        counters.attributed,
        counters.partially_attributed,
        counters.skipped,
        counters.alteration_drops,
        counters.trim_drops,
        len(counters.unknown_listings),
        lookup.hits,
        lookup.lookups,
        lookup.failures,
        lookup.deferrals,
        counters.identity_matched,
        identity_ambiguous,
        fetch_result.fallback.attempted,
        fetch_result.fallback.served,
        fetch_result.fallback.succeeded,
        fetch_result.fallback.confirmed_absent,
        fetch_result.fallback.failed,
        fetch_result.fallback.deferred,
    )


def _as_utc(moment: datetime) -> datetime:
    """Normalize a stored instant to an aware UTC instant.

    A naive stored date is read as UTC, which is how the hints are
    written; comparing it against an aware instant would otherwise
    raise and lose the candidate entirely.

    Args:
        moment: The parsed instant.

    Returns:
        The same instant, in UTC and aware.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


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
    timing_listings: Mapping[str, GuestyListing] | None = None,
    zones: Mapping[str, ZoneInfo | None] | None = None,
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
    if reservation.check_in is not None and reservation.check_out is not None:
        return reservation.check_out > now - timedelta(
            days=past_days,
        ) and reservation.check_in < now + timedelta(days=future_days)

    if not reservation.is_v3 or not reservation.stay:
        return reservation.is_v3

    listings = timing_listings or {}
    prepared_zones = zones or {}
    for segment in reservation.stay:
        listing = listings.get(segment.listing_id)
        if (
            listing is None
            or not listing.timezone_explicit
            or listing.timezone not in prepared_zones
        ):
            return True
        zone = prepared_zones[listing.timezone]
        if zone is None:
            return True
        check_in_date = _parse_local_date(segment.check_in_local)
        check_out_date = _parse_local_date(segment.check_out_local)
        if check_in_date is None or check_out_date is None:
            return True
        local_today = now.astimezone(zone).date()
        if check_out_date >= local_today - timedelta(
            days=past_days
        ) and check_in_date <= local_today + timedelta(days=future_days):
            return True
    return False


def _parse_local_date(value: str | None) -> date | None:
    """Parse a canonical localized calendar date.

    Args:
        value: Candidate ISO calendar-date value.

    Returns:
        The date, or None when missing or invalid.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _apply_same_date_handoffs(
    segments: tuple[GuestyStaySegment, ...],
    listings: Mapping[str, GuestyListing],
    zones: Mapping[str, ZoneInfo | None],
) -> tuple[GuestyStaySegment, ...]:
    """Apply the narrowly allowed checkout-to-checkin handoff.

    Args:
        segments: Current reservation segments in parsed raw order.
        listings: Exact-room metadata from the existing listing response.
        zones: Prepared timezone cache.

    Returns:
        Segments with only qualifying destination starts adjusted.
    """
    adjusted = list(segments)
    for index in range(1, len(adjusted)):
        previous = adjusted[index - 1]
        destination = adjusted[index]
        if destination.raw_position != previous.raw_position + 1:
            continue
        handoff_date = _parse_local_date(previous.check_out_local)
        if (
            handoff_date is None
            or handoff_date != _parse_local_date(destination.check_in_local)
            or previous.check_out is None
            or destination.check_out is None
        ):
            continue

        prior_listing = listings.get(previous.listing_id)
        destination_listing = listings.get(destination.listing_id)
        if (
            prior_listing is None
            or destination_listing is None
            or not prior_listing.timezone_explicit
            or not destination_listing.timezone_explicit
            or prior_listing.timezone != destination_listing.timezone
            or prior_listing.timezone not in zones
            or zones[prior_listing.timezone] is None
            or not is_valid_local_wall_time(prior_listing.check_in_time)
            or not is_valid_local_wall_time(prior_listing.check_out_time)
            or not is_valid_local_wall_time(destination_listing.check_in_time)
            or not is_valid_local_wall_time(destination_listing.check_out_time)
        ):
            continue
        if previous.check_out < destination.check_out:
            adjusted[index] = replace(
                destination,
                check_in=previous.check_out,
            )
    return tuple(adjusted)


def _reservation_sort_key(
    reservation: GuestyReservation,
) -> tuple[int, datetime, str, str]:
    """Order fully timed records before deterministic unknown-date records.

    Args:
        reservation: The per-listing reservation projection.

    Returns:
        A sortable key with no comparison between a date and an instant.
    """
    if (
        reservation.check_in is not None
        and reservation.check_out is not None
        and reservation.check_in < reservation.check_out
    ):
        return (0, reservation.check_in, "", reservation.id)
    local_date = _parse_local_date(reservation.check_in_local)
    date_key = local_date.isoformat() if local_date is not None else "~"
    return (1, datetime.max.replace(tzinfo=UTC), date_key, reservation.id)


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
        projected = replace(reservation, listing_id=listing_id, stay=())
        if (
            reservation.is_v3
            and reservation.status in _TEMPORAL_STATUSES
            and reservation.timing_status == "unknown"
        ):
            projected = replace(
                projected,
                status="unknown",
                reservation_status=reservation.status,
            )
        return projected

    segment = _select_segment(segments, now)
    status = _segment_status(reservation.status, segment, now)
    return replace(
        reservation,
        listing_id=listing_id,
        status=status,
        check_in=segment.check_in,
        check_out=segment.check_out,
        check_in_local=segment.check_in_local,
        check_out_local=segment.check_out_local,
        guests_count=segment.guests_count,
        nights_count=_segment_nights(segment),
        reservation_status=(reservation.status if status == "unknown" else None),
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
    timed = [
        segment
        for segment in segments
        if segment.check_in is not None
        and segment.check_out is not None
        and segment.check_in < segment.check_out
    ]
    ordered = sorted(
        timed,
        key=lambda segment: (
            segment.check_in
            if segment.check_in is not None
            else datetime.max.replace(tzinfo=UTC)
        ),
    )
    for segment in ordered:
        assert segment.check_in is not None
        assert segment.check_out is not None
        if segment.check_in <= now < segment.check_out:
            return segment
    for segment in ordered:
        assert segment.check_in is not None
        if segment.check_in > now:
            return segment
    unknown = [
        segment
        for segment in segments
        if segment.check_in is None
        or segment.check_out is None
        or segment.check_in >= segment.check_out
    ]
    if unknown:
        return min(
            unknown,
            key=lambda segment: (
                _parse_local_date(segment.check_in_local) or date.max,
                segment.raw_position,
            ),
        )
    return max(
        ordered,
        key=lambda segment: (
            segment.check_out
            if segment.check_out is not None
            else datetime.min.replace(tzinfo=UTC)
        ),
    )


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
    if (
        segment.check_in is None
        or segment.check_out is None
        or segment.check_in >= segment.check_out
    ):
        return "unknown"
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
    if segment.check_in is None or segment.check_out is None:
        return None
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
        for definition in self.data:
            if definition.field_id == field_id:
                return definition
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
