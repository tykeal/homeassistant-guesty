# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DataUpdateCoordinators for Guesty listings and reservations."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import (
    AsyncIterator,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Mapping,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
    REQUEST_REFRESH_DEFAULT_IMMEDIATE,
    DataUpdateCoordinator,
    UpdateFailed,
)

from custom_components.guesty.api.client import (
    private_http_logging,
    resolve_reservations_api,
)
from custom_components.guesty.api.const import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    HOLD_STATUSES,
    RESERVATIONS_API_V3,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    V3_STATUS_MAP,
)
from custom_components.guesty.api.exceptions import (
    GuestyApiError,
    GuestyAuthError,
    GuestyConnectionError,
    GuestyFilterRejectedError,
    GuestyPermissionError,
    GuestyRateLimitError,
    GuestyResponseError,
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
    RejectedCode,
    ReservationFetchResult,
    ValidationSummary,
    derive_nights_count,
)
from custom_components.guesty.api.timing import (
    is_valid_local_wall_time,
    resolve_local_datetime,
)
from custom_components.guesty.api.validation import ValidationReason
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
    EVIDENCE_COMPROMISED,
    EVIDENCE_SATURATED,
    MAX_CONFLICT_ONLY_EVIDENCE,
    CandidateHint,
    IdentityComparison,
    ReservationIdentityGuard,
    observe_codes,
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

RESERVATION_SEARCH_REJECTED_MESSAGE = (
    f"Guesty rejected {RESERVATIONS_V3_SEARCH_ENDPOINT} with HTTP 400. "
    "Check your reservation search settings and contact support."
)

# Bound on the exclusion keys remembered between refreshes (R14).
MAX_TRACKED_EXCLUSIONS: int = 256

# Rejected identifiers shown in the diagnostics download (R15).
MAX_DIAGNOSTIC_REJECTED_IDS: int = 100

# Status histogram keys a diagnostics surface may show (FR-010).
_REPORTABLE_STATUSES: frozenset[str] = frozenset(
    {*V3_STATUS_MAP.values(), "unrecognized"},
)

# Repair issue identifier prefixes; each is also the translation key.
ISSUE_LISTING_TIMEZONE_UNVERIFIED = "listing_timezone_unverified"
ISSUE_CONFLICT_EVIDENCE_NEAR_CAPACITY = "conflict_evidence_near_capacity"
ISSUE_CONFLICT_EVIDENCE_UNAVAILABLE = "conflict_evidence_unavailable"


def listing_timezone_issue_prefix(entry_id: str) -> str:
    """Return the issue-ID prefix of an entry's timezone issues.

    Args:
        entry_id: The config entry ID.

    Returns:
        The prefix every listing-timezone issue of the entry carries.
    """
    return f"{ISSUE_LISTING_TIMEZONE_UNVERIFIED}_{entry_id}_"


def conflict_evidence_issue_ids(entry_id: str) -> tuple[str, str]:
    """Return an entry's two conflict-evidence issue IDs.

    Args:
        entry_id: The config entry ID.

    Returns:
        The near-capacity and the unavailable issue IDs.
    """
    return (
        f"{ISSUE_CONFLICT_EVIDENCE_NEAR_CAPACITY}_{entry_id}",
        f"{ISSUE_CONFLICT_EVIDENCE_UNAVAILABLE}_{entry_id}",
    )


def sanitized_status_counts(counts: Mapping[str, int]) -> dict[str, int]:
    """Fold any unexpected status key into the ``unrecognized`` bucket.

    The sources already map every raw status through the allowlist;
    this keeps a raw value off both diagnostics surfaces regardless.

    Args:
        counts: The status histogram of one refresh.

    Returns:
        The histogram with only reportable keys.
    """
    sanitized: dict[str, int] = {}
    for status, count in counts.items():
        key = status if status in _REPORTABLE_STATUSES else "unrecognized"
        sanitized[key] = sanitized.get(key, 0) + count
    return sanitized


class ExclusionTracker:
    """Remember reported exclusions so each warns once (R14, SC-004).

    Keys are ``(reason, scope_id)`` with a fixed reason and a
    reservation or listing ID, nothing else. A new key logs one
    WARNING, a known key logs at DEBUG and a key an accepted refresh
    no longer reports is forgotten, so it can warn again. Beyond
    ``MAX_TRACKED_EXCLUSIONS`` new keys log at DEBUG under one
    aggregate WARNING per overflow episode, which ends once the
    tracked set falls below the bound. Memory-only.
    """

    def __init__(self) -> None:
        """Start with nothing tracked."""
        self._active: dict[tuple[ValidationReason, str], None] = {}
        self._overflowed = False

    def report(self, keys: Iterable[tuple[ValidationReason, str]]) -> None:
        """Report the exclusion set of one accepted refresh.

        Args:
            keys: The refresh's ``(reason, scope_id)`` keys.
        """
        current = sorted(set(keys))
        present = set(current)
        for key in [key for key in self._active if key not in present]:
            del self._active[key]
        untracked = 0
        for key in current:
            reason, scope_id = key
            if key in self._active:
                _LOGGER.debug(
                    "Reservation data still excluded: %s for %s",
                    str(reason.value),
                    scope_id,
                )
            elif len(self._active) < MAX_TRACKED_EXCLUSIONS:
                self._active[key] = None
                _LOGGER.warning(
                    "Reservation data excluded: %s for %s",
                    str(reason.value),
                    scope_id,
                )
            else:
                untracked += 1
                _LOGGER.debug(
                    "Reservation data excluded (untracked): %s for %s",
                    str(reason.value),
                    scope_id,
                )
        if untracked and not self._overflowed:
            self._overflowed = True
            _LOGGER.warning(
                "%d further reservation data exclusions are not tracked; "
                "they are logged at DEBUG",
                untracked,
            )
        if len(self._active) < MAX_TRACKED_EXCLUSIONS:
            self._overflowed = False


@dataclass(frozen=True)
class SnapshotRecord:
    """Identity facts of one record an accepted refresh carried.

    No confirmation code is held, only its kind.

    Attributes:
        code_kind: ``none``, ``empty``, ``string`` or ``invalid``.
        hold: Whether the status is an alteration hold.
        listing_ids: Verified ``(listing_id, parent_listing_id)``
            pairs, one per stay segment or the top-level listing for
            a record without segments, or None when any listing
            reference is not a string (unverifiable).
        v3: Whether the raw v3 search returned this identifier.
    """

    code_kind: Literal["none", "empty", "string", "invalid"]
    hold: bool
    listing_ids: frozenset[tuple[str, str | None]] | None
    v3: bool


@dataclass(frozen=True)
class AcceptedRefreshSnapshot:
    """In-memory evidence of the last accepted reservation refresh.

    It is never persisted, logged or added to diagnostics, and its
    code-bearing fields are excluded from ``repr`` (feature 014,
    data-model "Accepted-refresh snapshot").

    Attributes:
        attempt: The refresh attempt generation it was built in.
        path: The reservation source path of that refresh.
        returned_ids: Raw v3 identifiers the search returned.
        rejected_ids: Raw v3 identifiers the source did not accept.
        records: Identity facts per accepted record identifier.
        observed_codes: The full, unbounded code to identifier map
            over the accepted records.
        fallback_observed_codes: The code to identifier map over
            every record a legacy by-ID lookup returned, whatever
            its status.
        fallback_served: Identifiers the fallback served.
        fallback_sourced: Identifiers the fallback published.
        fallback_absent: Identifiers legacy confirmed are gone.
        fallback_observed: Identifiers a legacy lookup returned.
    """

    attempt: int
    path: str
    returned_ids: frozenset[str]
    rejected_ids: frozenset[str]
    records: Mapping[str, SnapshotRecord]
    observed_codes: Mapping[str, str] = field(repr=False)
    fallback_observed_codes: Mapping[str, str] = field(repr=False)
    fallback_served: frozenset[str] = frozenset()
    fallback_sourced: frozenset[str] = frozenset()
    fallback_absent: frozenset[str] = frozenset()
    fallback_observed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class VerifiedSnapshot:
    """The records of the last accepted refresh, kept for display.

    It is memory-only and separate from ``AcceptedRefreshSnapshot``:
    it only lets a failed refresh re-project the last verified data,
    and is never write evidence (FR-012, R10).

    Attributes:
        reservations: Every accepted record before publication
            filtering, including partially valid ones, with guest
            records attached.
        stale_ids: Records that refresh could only serve stale.
        accepted_at: When that refresh was accepted.
    """

    reservations: tuple[GuestyReservation, ...] = field(repr=False)
    stale_ids: frozenset[str]
    accepted_at: datetime


def _code_kind(
    code: object,
) -> Literal["none", "empty", "string", "invalid"]:
    """Classify a record's confirmation code without keeping it.

    Args:
        code: The parsed confirmation code value.

    Returns:
        The code kind.
    """
    if code is None:
        return "none"
    if not isinstance(code, str):
        return "invalid"
    return "string" if code else "empty"


def _snapshot_listing_ids(
    reservation: GuestyReservation,
) -> frozenset[tuple[str, str | None]] | None:
    """Build a record's verified listing association pairs.

    Building never raises: any listing reference that is not a
    string (or a parent that is neither a string nor None) makes the
    record unverifiable instead.

    Args:
        reservation: The accepted record.

    Returns:
        The association pairs, or None when unverifiable.
    """
    pairs: set[tuple[str, str | None]] = set()
    if not reservation.stay:
        listing_id: object = reservation.listing_id
        if not isinstance(listing_id, str):
            return None
        return frozenset({(listing_id, None)})
    for segment in reservation.stay:
        unit: object = segment.listing_id
        parent: object = segment.parent_listing_id
        if not isinstance(unit, str) or (
            parent is not None and not isinstance(parent, str)
        ):
            return None
        pairs.add((unit, parent))
    return frozenset(pairs)


def _build_snapshot(
    attempt: int,
    path: str,
    reservations: tuple[GuestyReservation, ...],
    fetch_result: ReservationFetchResult,
) -> AcceptedRefreshSnapshot:
    """Build the accepted-refresh snapshot of one refresh.

    Args:
        attempt: The in-progress refresh attempt generation.
        path: The reservation source path.
        reservations: Reservations the refresh accepted.
        fetch_result: The retrieval result.

    Returns:
        The snapshot.
    """
    returned = frozenset(fetch_result.returned_ids)
    records = {
        reservation.id: SnapshotRecord(
            code_kind=_code_kind(reservation.confirmation_code),
            hold=reservation.status in HOLD_STATUSES,
            listing_ids=_snapshot_listing_ids(reservation),
            v3=reservation.id in returned,
        )
        for reservation in reservations
    }
    return AcceptedRefreshSnapshot(
        attempt=attempt,
        path=path,
        returned_ids=returned,
        rejected_ids=frozenset(fetch_result.rejected_ids),
        records=MappingProxyType(records),
        observed_codes=MappingProxyType(observe_codes(reservations).mapping),
        fallback_observed_codes=MappingProxyType(
            observe_codes(fetch_result.observed_reservations).mapping,
        ),
        fallback_served=frozenset(fetch_result.fallback_served),
        fallback_sourced=frozenset(fetch_result.fallback_sourced),
        fallback_absent=frozenset(fetch_result.fallback_absent),
        fallback_observed=frozenset(fetch_result.fallback_observed),
    )


class ReservationSearchRejected(UpdateFailed):
    """Report a rejected reservation search without response content."""


class _ReservationDebouncer(Debouncer[Coroutine[Any, Any, None]]):
    """Keep debounced requests pending while a recovered write owns the lock."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create a debouncer with Home Assistant's normal refresh settings."""
        super().__init__(
            hass,
            _LOGGER,
            cooldown=REQUEST_REFRESH_DEFAULT_COOLDOWN,
            immediate=REQUEST_REFRESH_DEFAULT_IMMEDIATE,
        )
        self._recovery_dispatch_active = False
        self._cancel_generation = 0

    @asynccontextmanager
    async def async_recovery_dispatch_lock(self) -> AsyncIterator[None]:
        """Serialize dispatch without letting a scheduled request disappear."""
        async with self.async_lock():
            self._recovery_dispatch_active = True
            try:
                yield
            finally:
                self._recovery_dispatch_active = False

    def async_cancel(self) -> None:
        """Invalidate a timer already waiting behind a recovered write."""
        self._cancel_generation += 1
        super().async_cancel()

    async def _handle_timer_finish(self) -> None:
        """Defer a pending refresh instead of treating a write as a refresh."""
        if self._recovery_dispatch_active:
            generation = self._cancel_generation
            async with self.async_lock():
                if (
                    generation != self._cancel_generation
                    or self._shutdown_requested
                    or self._timer_task is not None
                ):
                    return
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
        self.identity = ReservationIdentityGuard(
            hass,
            entry.entry_id,
            past_days=lambda: int(
                entry.options.get(CONF_PAST_DAYS, DEFAULT_PAST_DAYS),
            ),
        )
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
        self._candidate_routing: tuple[frozenset[str], frozenset[str]] = (
            frozenset(),
            frozenset(),
        )
        self.legacy_write_counters = LegacyWriteCounters()
        # FR-042 requires the same counters on both surfaces, so the
        # last refresh's numbers are retained for the diagnostics
        # download rather than discarded with the refresh.
        self.last_refresh_diagnostics: dict[str, Any] = {}
        self.bootstrap_pages: int = 0
        self._write_miss_generation = 0
        self._write_miss_attempt_generation = 0
        self._write_miss_tasks: set[asyncio.Task[bool]] = set()
        self._write_miss_refresh_task: asyncio.Task[Any] | None = None
        self._write_miss_outcomes: deque[
            tuple[
                int,
                int,
                bool,
                Mapping[str, Any],
                tuple[Any, ...],
                ConfigEntry,
                GuestyApiClient,
            ]
        ] = deque(maxlen=256)
        self._accepted_snapshot: AcceptedRefreshSnapshot | None = None
        self._verified_snapshot: VerifiedSnapshot | None = None
        self.snapshot_stale = False
        self.stale_reservation_ids: frozenset[str] = frozenset()
        self._exclusions = ExclusionTracker()
        self.last_validation = ValidationSummary()
        self.last_rejected_ids: tuple[str, ...] = ()
        self.last_fallback_invalid_ids: tuple[str, ...] = ()
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
            request_refresh_debouncer=_ReservationDebouncer(hass),
        )
        self._remove_evidence_listener = self.identity.conflict_evidence.add_listener(
            self._reconcile_evidence_issues,
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
    def stale_display_available(self) -> bool:
        """Return whether failed-refresh entities show verified data.

        True only while the last refresh failed and ``data`` holds the
        re-projection of the verified snapshot (FR-012, R13).
        """
        return (
            not self.last_update_success
            and self.snapshot_stale
            and self.data is not None
            and self._verified_snapshot is not None
        )

    @property
    def last_verified_at(self) -> datetime | None:
        """Return when the displayed stale snapshot was accepted.

        None whenever the display is not a stale snapshot.
        """
        snapshot = self._verified_snapshot
        if not self.snapshot_stale or snapshot is None:
            return None
        return snapshot.accepted_at

    @asynccontextmanager
    async def async_write_miss_dispatch_lock(self) -> AsyncIterator[None]:
        """Serialize a recovered write with coordinator refreshes."""
        assert isinstance(self._debounced_refresh, _ReservationDebouncer)
        async with self._debounced_refresh.async_recovery_dispatch_lock():
            yield

    def accepted_refresh_snapshot(self) -> AcceptedRefreshSnapshot | None:
        """Return the last accepted refresh's snapshot while still valid.

        It is valid only when its attempt is the latest completed
        attempt, that attempt succeeded, and its options, settings,
        entry and client are still the current ones. Anything else
        is unavailable evidence.

        Returns:
            The snapshot, or None when missing or stale.
        """
        snapshot = self._accepted_snapshot
        if snapshot is None or not self._write_miss_outcomes:
            return None
        (
            _completed,
            attempt,
            success,
            options,
            settings,
            entry,
            client,
        ) = self._write_miss_outcomes[-1]
        if (
            attempt != snapshot.attempt
            or not success
            or not self.last_update_success
            or entry is not self.config_entry
            or client is not self.api_client
            or options is not self.config_entry.options
            or settings != self._write_miss_settings()
        ):
            return None
        return snapshot

    async def async_shutdown(self) -> None:
        """Stop shared recovery fetches before this entry can be unloaded."""
        self._accepted_snapshot = None
        self._remove_evidence_listener()
        await super().async_shutdown()
        current = asyncio.current_task()
        tasks = [task for task in self._write_miss_tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        previous_success = self.last_update_success
        try:
            await super()._async_refresh(
                log_failures=log_failures,
                raise_on_auth_failed=raise_on_auth_failed,
                scheduled=scheduled,
                raise_on_entry_error=raise_on_entry_error,
            )
            if not previous_success and not self.last_update_success:
                # Home Assistant only notifies on the first failure; a
                # later one still changes the re-projected display.
                self.async_update_listeners()
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
                if self._shutdown_requested or self.hass.is_stopping:
                    return False
                if self._write_miss_outcomes:
                    first = self._write_miss_outcomes[0][0]
                    if generation < first - 1:
                        return False
                    for (
                        completed,
                        _attempt,
                        success,
                        refresh_options,
                        refresh_settings,
                        refresh_entry,
                        refresh_client,
                    ) in self._write_miss_outcomes:
                        if completed > generation:
                            return (
                                success
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
                # Task identity is not inherited by HA's future poll tasks.
                self._write_miss_refresh_task = asyncio.current_task()
                try:
                    await self._async_refresh(log_failures=False)
                finally:
                    self._write_miss_refresh_task = None
                return (
                    self._write_miss_generation > generation
                    and bool(self._write_miss_outcomes)
                    and self._write_miss_outcomes[-1][2]
                    and not self._shutdown_requested
                    and not self.hass.is_stopping
                    and self.config_entry is entry
                    and self.api_client is client
                    and self.config_entry.options is options
                    and self._write_miss_settings() == settings
                )

        task = self.hass.async_create_background_task(
            refresh(), "guesty write-miss refresh", eager_start=True
        )
        self._write_miss_tasks.add(task)
        task.add_done_callback(self._write_miss_tasks.discard)
        try:
            result = await asyncio.shield(task)
            return (
                result
                and not self._shutdown_requested
                and not self.hass.is_stopping
                and self.config_entry is entry
                and self.api_client is client
                and self.config_entry.options is options
                and self._write_miss_settings() == settings
            )
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is None or current.cancelling():
                raise
            return False

    async def _async_update_data(
        self,
    ) -> dict[str, list[GuestyReservation]]:
        """Contain unexpected recovery errors without exposing exception contents.

        Every failure, including a ``CancelledError`` Home Assistant
        counts as one, first re-projects the verified snapshot, so the
        listener update Home Assistant sends for it already sees the
        stale display (R10).
        """
        try:
            try:
                with private_http_logging(True):
                    return await self._async_fetch_reservations()
            except ConfigEntryAuthFailed, ConfigEntryError, UpdateFailed:
                raise
            except Exception:
                if (
                    self._write_miss_refresh_task is not None
                    and asyncio.current_task() is self._write_miss_refresh_task
                ):
                    raise UpdateFailed("Reservation refresh failed") from None
                raise
        except Exception, asyncio.CancelledError:
            self._publish_stale_snapshot()
            raise

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
            raise ReservationSearchRejected(
                RESERVATION_SEARCH_REJECTED_MESSAGE
            ) from None
        except GuestyApiError as exc:
            if isinstance(exc, GuestyPermissionError):
                category = "permission"
            elif isinstance(exc, GuestyAuthError):
                category = "authentication"
            elif isinstance(exc, GuestyRateLimitError):
                category = "rate limit (rate limited)"
            elif isinstance(exc, GuestyConnectionError):
                category = "connection"
            elif isinstance(exc, GuestyResponseError):
                category = "response"
            else:
                category = "API"
            raise UpdateFailed(
                f"Guesty reservation {category} error; check the integration "
                "and retry.",
            ) from None
        except Exception:
            raise UpdateFailed(
                "Unexpected error fetching reservations; check the integration "
                "and retry.",
            ) from None

        try:
            return await self._async_accept_fetch(
                fetch_result,
                past_days,
                future_days,
            )
        except BaseException:
            self._discard_disproven(fetch_result)
            raise

    def _discard_disproven(self, fetch_result: ReservationFetchResult) -> None:
        """Drop what a fetched but failed batch proved invalid (R10).

        A refresh that fetched and validated its batch but then
        failed, on an identity abort or any later error, does not
        replace the verified snapshot. Records that batch rejected,
        kept only partially or found invalid through the fallback
        are nevertheless removed from it, so the stale display never
        re-projects data proven invalid. The validation diagnostics
        describe the same batch, so the download names what the
        stale display dropped. Nothing else changes: publication,
        routing and identity state are untouched.

        Args:
            fetch_result: The fetched batch of the failed refresh.
        """
        self.last_validation = fetch_result.validation
        self.last_rejected_ids = tuple(fetch_result.rejected_ids)
        self.last_fallback_invalid_ids = tuple(fetch_result.fallback_invalid_ids)
        snapshot = self._verified_snapshot
        if snapshot is None:
            return
        disproven = (
            frozenset(fetch_result.rejected_ids)
            | frozenset(fetch_result.fallback_invalid_ids)
            | frozenset(r.id for r in fetch_result.reservations if r.partial)
        )
        if not disproven:
            return
        snapshot = replace(
            snapshot,
            reservations=tuple(
                r for r in snapshot.reservations if r.id not in disproven
            ),
            stale_ids=snapshot.stale_ids - disproven,
        )
        self._verified_snapshot = snapshot
        self.stale_reservation_ids = snapshot.stale_ids

    async def _async_accept_fetch(
        self,
        fetch_result: ReservationFetchResult,
        past_days: int,
        future_days: int,
    ) -> dict[str, list[GuestyReservation]]:
        """Check, publish and record one fetched batch.

        Args:
            fetch_result: The fetched batch.
            past_days: Configured days of history to publish.
            future_days: Configured days ahead to publish.

        Returns:
            Dictionary mapping listing ID to sorted projections.

        Raises:
            UpdateFailed: When the identity is ambiguous.
        """
        timing_listings = self._timing_listings()
        reservations = await self._resolve_v3_reservations(
            fetch_result.reservations,
            timing_listings,
        )
        known_fallback_ids = self.fallback_served_ids | self.fallback_sourced_ids
        retired = (
            bool(known_fallback_ids)
            and not self.api_client.legacy_write_routing_available
        )
        if not retired:
            served = frozenset(fetch_result.fallback_served)
            sourced = frozenset(fetch_result.fallback_sourced)
        else:
            # A retired legacy generation stops reporting anything,
            # so keeping the last known sets is what makes a write
            # against one of them refuse with the retirement
            # message instead of silently going to v3 (FR-061). A
            # reservation that has reappeared in v3 leaves them.
            returned = {reservation.id for reservation in fetch_result.reservations}
            served = self.fallback_served_ids - returned
            sourced = self.fallback_sourced_ids - returned
        # The routing sets are candidates until the baseline naming
        # them is stored (R7 decision 8); the counters stay live.
        self._candidate_routing = (served, sourced)
        self.last_fallback_counters = fetch_result.fallback
        counters = _RefreshCounters(received=len(reservations))
        # Identity reads only fully verified records; rejected and
        # partial records take part as conflict-only pairs (FR-008b).
        rejected_ids = frozenset(fetch_result.rejected_ids)
        verified = tuple(
            reservation
            for reservation in reservations
            if not reservation.partial and reservation.id not in rejected_ids
        )

        try:
            comparison = await self._check_identity(
                verified,
                sourced | frozenset(fetch_result.fallback_invalid_ids),
                conflict_only=fetch_result.rejected_codes,
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
                verified,
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
            self._store_verified_snapshot(fetch_result, {})
            self._report_validation(
                fetch_result, reservations, known_ids, timing_listings
            )
            return {}

        publication = _publish_reservations(
            reservations,
            now=datetime.now(UTC),
            past_days=past_days,
            future_days=future_days,
            known_ids=known_ids,
            timing_listings=timing_listings,
            zones=self._zone_cache,
            counters=counters,
        )

        lookup = await self._resolve_guests(publication.guest_ids)
        await self._record_identity(
            verified,
            {},
            comparison,
            past_days,
            fetch_result,
        )
        self._publish_refresh_diagnostics(fetch_result, counters, lookup)
        self._store_verified_snapshot(fetch_result, lookup.guests)
        self._report_validation(fetch_result, reservations, known_ids, timing_listings)

        return _sorted_publication(publication.grouped, lookup.guests)

    def _report_validation(
        self,
        fetch_result: ReservationFetchResult,
        reservations: Iterable[GuestyReservation],
        known_ids: Collection[str],
        timing_listings: Mapping[str, GuestyListing],
    ) -> None:
        """Report an accepted refresh's exclusions and timezone repairs.

        Runs after accepted refreshes only, so a failed or aborted
        refresh neither reports nor resolves anything (R14).

        A segment is timed by its own listing's zone, including an
        untracked unit published on its tracked parent, so the key and
        repair name the listing whose zone is unverified.

        Args:
            fetch_result: The accepted retrieval result.
            reservations: The accepted records.
            known_ids: The tracked listing IDs.
            timing_listings: This refresh's listings by ID.
        """
        unverified: set[str] = set()
        for reservation in reservations:
            for segment in reservation.stay:
                listing_id = segment.listing_id
                parent_id = segment.parent_listing_id
                if (
                    (
                        listing_id in known_ids
                        or (isinstance(parent_id, str) and parent_id in known_ids)
                    )
                    and listing_id not in unverified
                    and _verified_timezone(
                        timing_listings.get(listing_id),
                        self._zone_cache,
                    )
                    is None
                ):
                    unverified.add(listing_id)
        self._exclusions.report(
            [
                *fetch_result.exclusions,
                *(
                    (ValidationReason.LISTING_TIMEZONE_UNVERIFIED, listing_id)
                    for listing_id in unverified
                ),
            ]
        )
        self._reconcile_timezone_issues(unverified, timing_listings)

    def _reconcile_timezone_issues(
        self,
        unverified: Collection[str],
        timing_listings: Mapping[str, GuestyListing],
    ) -> None:
        """Match the listing-timezone repairs to one accepted refresh.

        Args:
            unverified: Listings with published reservations and no
                verified timezone, tracked or shown on a tracked
                parent.
            timing_listings: This refresh's listings by ID, which name
                an untracked unit.
        """
        prefix = listing_timezone_issue_prefix(self.config_entry.entry_id)
        listings = self.listings_coordinator.data or {}
        wanted: set[str] = set()
        for listing_id in unverified:
            issue_id = f"{prefix}{listing_id}"
            wanted.add(issue_id)
            listing = listings.get(listing_id) or timing_listings.get(listing_id)
            name = (
                (listing.title or listing.nickname)
                if isinstance(listing, GuestyListing)
                else None
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_LISTING_TIMEZONE_UNVERIFIED,
                translation_placeholders={
                    "listing_name": name or listing_id,
                    "listing_id": listing_id,
                },
            )
        registry = ir.async_get(self.hass)
        stale = [
            issue_id
            for domain, issue_id in registry.issues
            if domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id not in wanted
        ]
        for issue_id in stale:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _reconcile_evidence_issues(self) -> None:
        """Match the conflict-evidence repairs to the evidence state.

        Called after the evidence loads and on every state, count or
        capacity change, whatever the refresh outcome, so a store
        whose pairs abort every refresh is still reported (R7).
        """
        evidence = self.identity.conflict_evidence
        near_id, unavailable_id = conflict_evidence_issue_ids(
            self.config_entry.entry_id,
        )
        if (
            not evidence.near_capacity
            and ir.async_get(self.hass).async_get_issue(DOMAIN, near_id) is not None
        ):
            # A persisted issue means the hysteresis was entered
            # before a restart; it holds until below 70% (R7 d6).
            evidence.resume_near_capacity()
        if evidence.near_capacity:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                near_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CONFLICT_EVIDENCE_NEAR_CAPACITY,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, near_id)
        if evidence.state in (EVIDENCE_SATURATED, EVIDENCE_COMPROMISED):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                unavailable_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CONFLICT_EVIDENCE_UNAVAILABLE,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, unavailable_id)

    def validation_diagnostics(self) -> dict[str, Any]:
        """Return the ``reservations.validation`` diagnostics block (R15).

        Counts and reservation IDs only: no code, status, guest datum
        or payload value, and never which IDs hold stored pairs. The
        rejected IDs include fallback-invalid records, which the
        counts include too.

        Returns:
            A JSON-serializable mapping.
        """
        summary = self.last_validation
        rejected = sorted(
            set(self.last_rejected_ids) | set(self.last_fallback_invalid_ids)
        )
        evidence = self.identity.conflict_evidence
        verified_at = self.last_verified_at
        return {
            "rejected_records": summary.rejected_records,
            "partial_records": summary.partial_records,
            "rejected_segments": summary.rejected_segments,
            "reasons": {
                str(reason.value): count
                for reason, count in sorted(summary.reasons.items())
            },
            "rejected_ids": rejected[:MAX_DIAGNOSTIC_REJECTED_IDS],
            "rejected_ids_truncated": len(rejected) > MAX_DIAGNOSTIC_REJECTED_IDS,
            "conflict_evidence": {
                "state": evidence.state,
                "available": evidence.available(),
                "count": evidence.count,
                "dated": evidence.dated,
                "undated": evidence.undated,
                "bound": MAX_CONFLICT_ONLY_EVIDENCE,
                "near_capacity": evidence.near_capacity,
            },
            "stale": {
                "snapshot_stale": self.snapshot_stale,
                "stale_count": len(self.stale_reservation_ids),
                "last_verified_at": (
                    verified_at.isoformat() if verified_at is not None else None
                ),
            },
        }

    def _store_verified_snapshot(
        self,
        fetch_result: ReservationFetchResult,
        guests: Mapping[str, GuestyGuest],
    ) -> None:
        """Replace the display snapshot after an accepted refresh.

        The snapshot holds every record the refresh accepted, before
        publication filtering, so a later failed refresh can re-project
        it at a new instant. It replaces the previous one wholesale
        whether or not the identity saves succeeded, so a record this
        refresh no longer returned or rejected can never come back
        from an older snapshot (FR-012b). It is memory-only and never
        evidence for a write (R10).

        Args:
            fetch_result: The accepted retrieval result.
            guests: Guest records resolved for this refresh.
        """
        snapshot = VerifiedSnapshot(
            reservations=tuple(
                _with_guest(reservation, guests)
                for reservation in fetch_result.reservations
            ),
            stale_ids=frozenset(fetch_result.stale_ids),
            accepted_at=datetime.now(UTC),
        )
        self._verified_snapshot = snapshot
        self.snapshot_stale = False
        self.stale_reservation_ids = snapshot.stale_ids

    def _publish_stale_snapshot(self) -> None:
        """Re-project the verified snapshot after a failed refresh (R10).

        The snapshot is published through the same pure function as
        an accepted refresh, at the current instant and options, so a
        stay that has since ended shows as history and one outside a
        narrowed window disappears (FR-012). A zone not already
        loaded counts as unverified. Nothing is published without a
        snapshot, and a failing re-projection publishes nothing. An
        unchanged projection keeps the already-published object.
        ``last_update_success`` is left to Home Assistant, so no write
        guard is ever satisfied by this data.
        """
        snapshot = self._verified_snapshot
        if snapshot is None:
            return
        try:
            options = self.config_entry.options
            known = self.listings_coordinator.data
            publication = _publish_reservations(
                snapshot.reservations,
                now=datetime.now(UTC),
                past_days=options.get(CONF_PAST_DAYS, DEFAULT_PAST_DAYS),
                future_days=options.get(CONF_FUTURE_DAYS, DEFAULT_FUTURE_DAYS),
                known_ids=set(known) if isinstance(known, Mapping) else set(),
                timing_listings=self._timing_listings(),
                zones=self._zone_cache,
                log=False,
            )
            data = _sorted_publication(publication.grouped, {})
        except Exception:
            _LOGGER.debug(
                "Could not re-project the last verified reservations",
                exc_info=True,
            )
            self.data = None  # type: ignore[assignment]
            self.snapshot_stale = False
            return
        if data != self.data:
            self.data = data
        self.snapshot_stale = True

    async def _resolve_v3_reservations(
        self,
        reservations: tuple[GuestyReservation, ...],
        listings: Mapping[str, GuestyListing] | None = None,
    ) -> tuple[GuestyReservation, ...]:
        """Resolve only V3 segments from already-fetched exact-room settings.

        The explicit zones the records need are loaded first, outside
        the event loop, so the resolution itself stays pure and can be
        repeated by the publication function (R3).

        Args:
            reservations: The source records returned by this refresh.
            listings: This refresh's timing listings, or None to read
                them from the listings coordinator.

        Returns:
            Records with current V3 endpoint values resolved independently.
            Legacy and fallback-sourced records remain unchanged.
        """
        if listings is None:
            listings = self._timing_listings()
        for reservation in reservations:
            if not reservation.is_v3:
                continue
            for segment in reservation.stay:
                listing = listings.get(segment.listing_id)
                if listing is not None and listing.timezone_explicit:
                    await self._async_timezone(listing.timezone)
        return _resolve_v3_timing(reservations, listings, self._zone_cache)

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
        *,
        conflict_only: Iterable[RejectedCode] = (),
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
            conflict_only: The refresh's rejected ``(reservation_id,
                code)`` pairs, compared for conflicts only.

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
            conflict_only=conflict_only,
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

        The fallback routing sets and the accepted-refresh snapshot
        are adopted only once the baseline is stored, from the
        guard's ``on_baseline_stored`` callback, so routing never
        runs ahead of the identity evidence that justifies it.

        Args:
            reservations: Fully verified reservations the refresh
                retrieved.
            published: Stay hints of the reservations published.
            comparison: The comparison this refresh ran.
            past_days: The configured past-day window, which dates
                the safe retirement of bootstrap evidence (T074).
            fetch_result: The retrieval result, which reports what
                the legacy not-found fallback published and what it
                confirmed is gone.
        """
        served, sourced = self._candidate_routing

        def _adopt() -> None:
            """Adopt the routing and snapshot of the stored baseline."""
            self.fallback_served_ids = served
            self.fallback_sourced_ids = sourced
            self._accepted_snapshot = _build_snapshot(
                self._write_miss_attempt_generation,
                self.reservations_path,
                reservations,
                fetch_result,
            )

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
            rejected_ids=fetch_result.rejected_ids,
            fallback_invalid_ids=fetch_result.fallback_invalid_ids,
            rejected_codes=fetch_result.rejected_codes,
            rejected_check_out_bounds=fetch_result.rejected_check_out_bounds,
            on_baseline_stored=_adopt,
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
        self.last_validation = fetch_result.validation
        self.last_rejected_ids = tuple(fetch_result.rejected_ids)
        self.last_fallback_invalid_ids = tuple(fetch_result.fallback_invalid_ids)
        self.last_refresh_diagnostics = {
            "pages": fetch_result.pages,
            "received": counters.received,
            "attributed": counters.attributed,
            "partially_attributed": counters.partially_attributed,
            "trimmed": counters.trim_drops,
            "skipped": counters.skipped,
            "alteration_drops": counters.alteration_drops,
            "status_counts": sanitized_status_counts(fetch_result.status_counts),
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
        sanitized_status_counts(fetch_result.status_counts),
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


def _resolve_v3_timing(
    reservations: Iterable[GuestyReservation],
    listings: Mapping[str, GuestyListing],
    zones: Mapping[str, ZoneInfo | None],
) -> tuple[GuestyReservation, ...]:
    """Resolve V3 segment instants from listing-local dates and times.

    Each segment is resolved from its own listing's verified zone and
    default times, or left date-only. Resolution starts from the
    local dates every time, so it is idempotent. A partial record
    keeps no reservation-level span, guest count or dates, because
    those could bridge a rejected segment (FR-005b).

    Args:
        reservations: Source records; non-V3 records pass unchanged.
        listings: Exact-room metadata by listing id.
        zones: Prepared timezone cache, keyed by zone name.

    Returns:
        The records with their V3 timing resolved.
    """
    resolved: list[GuestyReservation] = []
    for reservation in reservations:
        if not reservation.is_v3 or not reservation.stay:
            resolved.append(reservation)
            continue

        segments: list[GuestyStaySegment] = []
        for segment in reservation.stay:
            listing = listings.get(segment.listing_id)
            zone = _verified_timezone(listing, zones)
            if listing is None or zone is None:
                segments.append(replace(segment, check_in=None, check_out=None))
                continue
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

        resolved_stay = _apply_same_date_handoffs(tuple(segments), listings, zones)
        if reservation.partial:
            resolved.append(
                replace(
                    reservation,
                    check_in=None,
                    check_out=None,
                    check_in_local=None,
                    check_out_local=None,
                    guests_count=None,
                    stay=resolved_stay,
                ),
            )
            continue
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


@dataclass
class _Publication:
    """The per-listing output of one publication pass.

    Attributes:
        grouped: Projections by tracked listing id, unsorted and
            without resolved guest records.
        guest_ids: Guest ids of the published reservations.
        counters: The attribution counters of the pass.
    """

    grouped: dict[str, list[GuestyReservation]]
    guest_ids: list[str]
    counters: _RefreshCounters


def _publish_reservations(
    reservations: Iterable[GuestyReservation],
    *,
    now: datetime,
    past_days: int,
    future_days: int,
    known_ids: Collection[str],
    timing_listings: Mapping[str, GuestyListing],
    zones: Mapping[str, ZoneInfo | None],
    counters: _RefreshCounters | None = None,
    log: bool = True,
) -> _Publication:
    """Publish verified records per tracked listing (FR-003, R10).

    This one pure function serves both an accepted refresh and the
    re-projection of the verified snapshot after a failed one, so
    expiry, uncertainty, the window and attribution can never drift
    between the two (FR-012). It resolves V3 timing, drops holds
    (FR-024), enforces the window (FR-002a), attributes segments to
    tracked listings (FR-009, FR-010) and projects each attribution
    (FR-011).

    Args:
        reservations: The verified records to publish.
        now: The publication instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.
        known_ids: Listing ids the listings coordinator tracks.
        timing_listings: Exact-room metadata by listing id.
        zones: Prepared timezone cache; an unloaded zone is
            unverified rather than loaded here.
        counters: Counters to fill, or None for fresh ones.
        log: Whether to log per-record decisions.

    Returns:
        The projections, guest ids and counters of the pass.
    """
    counters = counters if counters is not None else _RefreshCounters()
    known = set(known_ids)
    grouped: dict[str, list[GuestyReservation]] = defaultdict(list)
    guest_ids: list[str] = []
    for reservation in _resolve_v3_timing(reservations, timing_listings, zones):
        if reservation.status in HOLD_STATUSES:
            counters.alteration_drops += 1
            if log:
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
            zones=zones,
        ):
            counters.trim_drops += 1
            if log:
                _LOGGER.debug(
                    "Trimming reservation %s outside the configured window",
                    reservation.id,
                )
            continue

        attributed, unknown_ids = _attribute_segments(reservation, known)
        counters.unknown_listings |= unknown_ids
        if not attributed:
            counters.skipped += 1
            if log:
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
            if log:
                _LOGGER.debug(
                    "Ignoring stay segments of reservation %s for "
                    "untracked listing(s) %s",
                    reservation.id,
                    ", ".join(sorted(unknown_ids)),
                )

        for listing_id, segments in attributed.items():
            grouped[listing_id].append(
                _project(
                    reservation,
                    listing_id,
                    segments,
                    now,
                    timing_listings=timing_listings,
                    zones=zones,
                ),
            )
    return _Publication(dict(grouped), guest_ids, counters)


def _sorted_publication(
    grouped: Mapping[str, list[GuestyReservation]],
    guests: Mapping[str, GuestyGuest],
) -> dict[str, list[GuestyReservation]]:
    """Attach guest records and order each listing's projections.

    Args:
        grouped: Projections by tracked listing id.
        guests: Guest records resolved for this refresh.

    Returns:
        The published coordinator data.
    """
    return {
        listing_id: sorted(
            (_with_guest(projection, guests) for projection in projections),
            key=_reservation_sort_key,
        )
        for listing_id, projections in grouped.items()
    }


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

    A v3 stay without both exact endpoints is a member only when one
    of its verified segments conclusively overlaps the window: in the
    listing's local calendar when its timezone is verified, else with
    one day of margin on each side of the UTC date, because any real
    zone is within a day of UTC (R4 decision 8). A segment whose
    local dates are missing or unparsable is a member only when its
    one known date lies inside that window, or through its verified
    exact instants; otherwise it is inconclusive and is not
    published (it stays writable), even with a verified zone. A partial record is
    always judged by its verified segments, never by a span that may
    bridge a rejected one (FR-005b).

    Args:
        reservation: The reservation to test.
        now: The refresh instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.
        timing_listings: Exact-room metadata by listing id.
        zones: Prepared timezone cache, keyed by zone name.

    Returns:
        True when the reservation overlaps the configured window.
    """
    if (
        not reservation.partial
        and reservation.check_in is not None
        and reservation.check_out is not None
    ):
        return reservation.check_out > now - timedelta(
            days=past_days,
        ) and reservation.check_in < now + timedelta(days=future_days)

    if not reservation.is_v3 or not reservation.stay:
        return False

    listings = timing_listings or {}
    prepared_zones = zones or {}
    utc_today = now.astimezone(UTC).date()
    for segment in reservation.stay:
        zone = _verified_timezone(listings.get(segment.listing_id), prepared_zones)
        check_in_date = _parse_local_date(segment.check_in_local)
        check_out_date = _parse_local_date(segment.check_out_local)
        if zone is not None:
            local_today = now.astimezone(zone).date()
            earliest = local_today - timedelta(days=past_days)
            latest = local_today + timedelta(days=future_days)
        else:
            earliest = utc_today + timedelta(days=1 - past_days)
            latest = utc_today + timedelta(days=future_days - 1)
        if check_in_date is not None and check_out_date is not None:
            if check_out_date >= earliest and check_in_date <= latest:
                return True
            continue
        # Missing or unparsable dates are never assumed, even with a
        # verified zone. A single known endpoint date inside the
        # window is still conclusive, because a stay contains both of
        # its endpoints; otherwise only verified exact segment
        # instants can establish membership.
        known = [d for d in (check_in_date, check_out_date) if d is not None]
        if any(earliest <= d <= latest for d in known):
            return True
        if _segment_instants_overlap(
            segment,
            now=now,
            past_days=past_days,
            future_days=future_days,
        ):
            return True
    return False


def _segment_instants_overlap(
    segment: GuestyStaySegment,
    *,
    now: datetime,
    past_days: int,
    future_days: int,
) -> bool:
    """Test a segment's verified exact instants against the window.

    Args:
        segment: The stay segment to test.
        now: The refresh instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.

    Returns:
        True only when both instants are known, offset-aware and
        overlap the configured window.
    """
    check_in = segment.check_in
    check_out = segment.check_out
    if (
        check_in is None
        or check_out is None
        or check_in.utcoffset() is None
        or check_out.utcoffset() is None
    ):
        return False
    return check_out > now - timedelta(days=past_days) and check_in < now + timedelta(
        days=future_days
    )


def _verified_timezone(
    listing: GuestyListing | None,
    zones: Mapping[str, ZoneInfo | None],
) -> ZoneInfo | None:
    """Return a listing's zone only when it is verified (R4 decision 1).

    A zone is verified only when the listing named it explicitly and
    it has been loaded; the model's UTC default, an invalid name and
    a zone not yet loaded are all unverified.

    Args:
        listing: The listing a segment belongs to, if known.
        zones: Prepared timezone cache, keyed by zone name.

    Returns:
        The verified zone, or None.
    """
    if not isinstance(listing, GuestyListing) or not listing.timezone_explicit:
        return None
    return zones.get(listing.timezone)


def _checkout_expired(
    check_out: datetime | None,
    check_out_local: str | None,
    zone: ZoneInfo | None,
    now: datetime,
) -> bool:
    """Test whether a stay has conclusively ended (FR-004, R4).

    An exact checkout instant decides on its own. A date-only
    checkout has ended once its date is before the listing's local
    date in a verified zone; on that date itself the exact time is
    unknown, so the stay is not yet history (FR-004a). Without a
    verified zone the date must be more than a day before the UTC
    date, because some real zone may still be on it (R4 decision 5).

    Args:
        check_out: The exact checkout instant, if known.
        check_out_local: The listing-local checkout date, if known.
        zone: The verified listing timezone, or None.
        now: The refresh instant.

    Returns:
        True only when the stay has conclusively ended.
    """
    if check_out is not None:
        return now >= check_out
    check_out_date = _parse_local_date(check_out_local)
    if check_out_date is None:
        return False
    if zone is not None:
        return check_out_date < now.astimezone(zone).date()
    return check_out_date < now.astimezone(UTC).date() - timedelta(days=1)


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
    *,
    timing_listings: Mapping[str, GuestyListing] | None = None,
    zones: Mapping[str, ZoneInfo | None] | None = None,
) -> GuestyReservation:
    """Project a reservation onto one tracked listing (FR-011).

    A fully verified reservation with at most one segment projects to
    itself apart from the tracked listing it is published under. With
    more than one segment, or when a raw segment was rejected, the
    listing's own verified interval supplies the dates, guest count
    and night count, and the status becomes segment-aware (FR-005b).
    Either way a temporal status whose checkout has conclusively
    passed is published as ``checked_out`` (FR-004), and one with
    unknown exact timing as ``unknown`` with its source lifecycle
    kept apart, so no occupancy is claimed (FR-004a).

    Args:
        reservation: The reservation to project.
        listing_id: The tracked listing to project onto.
        segments: The reservation's segments for that listing.
        now: The refresh instant.
        timing_listings: Exact-room metadata by listing id.
        zones: Prepared timezone cache, keyed by zone name.

    Returns:
        The projection published for that listing.
    """
    listings = timing_listings or {}
    prepared_zones = zones or {}

    def _zone_of(segment_listing_id: str) -> ZoneInfo | None:
        """Return the verified zone of one segment's listing."""
        return _verified_timezone(listings.get(segment_listing_id), prepared_zones)

    if (
        not reservation.partial
        and len(reservation.stay) <= 1
        and (reservation.raw_stay_count or 0) <= 1
    ):
        if not reservation.stay and reservation.listing_id == listing_id:
            projected = reservation
        else:
            projected = replace(reservation, listing_id=listing_id, stay=())
        if reservation.status not in _TEMPORAL_STATUSES:
            return projected
        if reservation.is_v3:
            zone = (
                _zone_of(reservation.stay[0].listing_id) if reservation.stay else None
            )
            expired = _checkout_expired(
                reservation.check_out,
                reservation.check_out_local,
                zone,
                now,
            )
        else:
            # Legacy instants are exact, so only they can end a stay.
            expired = _checkout_expired(reservation.check_out, None, None, now)
        if expired:
            return replace(
                projected,
                status="checked_out",
                reservation_status=None,
            )
        if reservation.is_v3 and reservation.timing_status == "unknown":
            projected = replace(
                projected,
                status="unknown",
                reservation_status=reservation.status,
            )
        return projected

    segment = _select_segment(segments, now, _zone_of)
    status = _segment_status(
        reservation.status,
        segment,
        now,
        _zone_of(segment.listing_id),
    )
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
    zone_of: Callable[[str], ZoneInfo | None] | None = None,
) -> GuestyStaySegment:
    """Select the interval a listing's projection represents.

    Segments in the same listing stay disjoint rather than being
    collapsed into one span, because an A -> B -> A stay would then
    cover the guest's time in B. The timed interval containing the
    refresh instant wins, else the earliest future timed one, else
    the earliest date-only one that has not conclusively ended, else
    the latest past one (data-model.md 3.1, FR-004).

    Args:
        segments: The listing's segments, in payload order.
        now: The refresh instant.
        zone_of: Returns the verified zone of a segment's listing.

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
    unknown = [segment for segment in segments if segment not in timed]
    pending = [
        segment
        for segment in unknown
        if not _checkout_expired(
            segment.check_out,
            segment.check_out_local,
            zone_of(segment.listing_id) if zone_of is not None else None,
            now,
        )
    ]
    if pending:
        return min(
            pending,
            key=lambda segment: (
                _parse_local_date(segment.check_in_local) or date.max,
                segment.raw_position,
            ),
        )
    return max(
        ordered + unknown,
        key=lambda segment: (
            _parse_local_date(segment.check_out_local) or date.min,
            (
                segment.check_out
                if segment.check_out is not None
                else datetime.min.replace(tzinfo=UTC)
            ),
            segment.raw_position,
        ),
    )


def _segment_status(
    status: str,
    segment: GuestyStaySegment,
    now: datetime,
    zone: ZoneInfo | None = None,
) -> str:
    """Derive a listing's status from its segment (FR-011a).

    Only ``confirmed`` and ``checked_in`` are temporal: every other
    status, including ``canceled``, is carried unchanged to every
    listing the reservation touches, so a canceled stay can never
    surface as upcoming on a later segment's listing. A temporal
    segment that has conclusively ended is ``checked_out`` even
    without exact timing (FR-004); one that has not, without exact
    timing, is ``unknown`` (FR-004a).

    Args:
        status: The reservation-level status.
        segment: The segment this listing is published from.
        now: The refresh instant.
        zone: The verified timezone of the segment's listing.

    Returns:
        The status published for that listing.
    """
    if status not in _TEMPORAL_STATUSES:
        return status
    if _checkout_expired(segment.check_out, segment.check_out_local, zone, now):
        return "checked_out"
    if (
        segment.check_in is None
        or segment.check_out is None
        or segment.check_in >= segment.check_out
    ):
        return "unknown"
    if now < segment.check_in:
        return "confirmed"
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
