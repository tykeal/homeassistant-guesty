# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Bounded, deduplicated cache of Guesty guest records.

Reservations v3 embeds no guest object, only a ``guestId``, so the
guest name, phone and email the sensors publish today have to be
re-sourced from the ``/guests`` family (FR-016). That turns one
reservation refresh into potentially one request per distinct
guest, which this module bounds.

Three rules shape everything here:

- a guest lookup **never** fails a refresh. A missing name is a
  cosmetic degradation; a failed refresh is a functional outage
  (FR-020, NFR-005);
- entries are keyed on ``guest_id`` alone, so a record can never be
  published under another guest's id (FR-019);
- nothing but the guest id and counts is ever logged, at any level
  (FR-021, NFR-009).

The cache lives on the API client instance, so it is discarded on
reload and never reaches disk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import httpx

from custom_components.guesty.api.const import (
    GUEST_CACHE_MAX_ENTRIES,
    GUEST_CACHE_MISS_TTL_SECONDS,
    GUEST_CACHE_TTL_SECONDS,
    GUEST_FIELDS,
    GUEST_LOOKUP_CONCURRENCY,
    GUESTS_CRUD_PATH,
    MAX_GUEST_LOOKUPS_PER_REFRESH,
)
from custom_components.guesty.api.exceptions import GuestyApiError
from custom_components.guesty.api.models import GuestyGuest

if TYPE_CHECKING:
    from custom_components.guesty.api.client import GuestyApiClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuestCacheEntry:
    """One cached guest record.

    Attributes:
        guest: The cached guest, or None for a confirmed not-found.
        fetched_at: Clock reading when the record was stored.
        failed_at: Clock reading of the last failed refresh of this
            record, or None if the last attempt succeeded.
    """

    guest: GuestyGuest | None
    fetched_at: float
    failed_at: float | None = None


@dataclass(frozen=True)
class GuestLookupResult:
    """Outcome of resolving the guest ids of one refresh.

    ``guests`` carries every id with a known record, including one
    whose refresh was deferred or failed, so a caller can keep
    publishing last-known values (FR-020).

    Attributes:
        guests: Known guest records by guest id.
        hits: Ids served from a fresh cache entry.
        lookups: Requests issued.
        failures: Requests that failed.
        deferrals: Ids left for a later refresh by the burst cap.
    """

    guests: Mapping[str, GuestyGuest] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    hits: int = 0
    lookups: int = 0
    failures: int = 0
    deferrals: int = 0


class GuestCache:
    """TTL and LRU bounded cache of guest records.

    Attributes:
        _client: API client issuing the authenticated requests.
        _clock: Monotonic clock the entries are aged against.
        _entries: Cached records, in least-recently-used order.
        _attempts: Sequence number of the last attempt per guest
            id, which orders the burst cap oldest-first.
        _attempt_seq: Monotonic counter stamped onto attempts, so
            ordering never depends on the clock's resolution.
    """

    def __init__(
        self,
        client: GuestyApiClient,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the guest cache.

        Args:
            client: API client used to issue HTTP requests.
            clock: Monotonic clock, injectable for tests.
        """
        self._client = client
        self._clock = clock
        self._entries: OrderedDict[str, GuestCacheEntry] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()
        self._attempt_seq = 0

    def __len__(self) -> int:
        """Return the number of cached records.

        Returns:
            How many guest records the cache holds.
        """
        return len(self._entries)

    def __contains__(self, guest_id: object) -> bool:
        """Report whether a guest id has a cached record.

        Args:
            guest_id: The guest id to test.

        Returns:
            True when a record is held for that id.
        """
        return guest_id in self._entries

    async def resolve(
        self,
        guest_ids: Iterable[str],
    ) -> GuestLookupResult:
        """Resolve guest records for one refresh.

        Each distinct id costs at most one request, and at most
        ``MAX_GUEST_LOOKUPS_PER_REFRESH`` requests are issued in
        total. No failure propagates: an id that cannot be
        refreshed keeps whatever record it already had.

        Args:
            guest_ids: Guest ids the refresh needs, with repeats.

        Returns:
            The known records and this refresh's counters.
        """
        now = self._clock()
        unique = list(dict.fromkeys(guest_ids))
        hits, needed = self._triage(unique, now)

        needed.sort(key=self._priority)
        batch = needed[:MAX_GUEST_LOOKUPS_PER_REFRESH]
        deferrals = len(needed) - len(batch)

        failures = 0
        if batch:
            semaphore = asyncio.Semaphore(GUEST_LOOKUP_CONCURRENCY)
            outcomes = await asyncio.gather(
                *(self._lookup(guest_id, semaphore) for guest_id in batch),
            )
            failures = sum(1 for succeeded in outcomes if not succeeded)

        self._trim_attempts(set(unique))

        result = GuestLookupResult(
            guests=MappingProxyType(self._known(unique)),
            hits=hits,
            lookups=len(batch),
            failures=failures,
            deferrals=deferrals,
        )
        _LOGGER.debug(
            "Guest cache: hits=%d lookups=%d failures=%d deferrals=%d entries=%d",
            result.hits,
            result.lookups,
            result.failures,
            result.deferrals,
            len(self._entries),
        )
        return result

    def _triage(
        self,
        guest_ids: list[str],
        now: float,
    ) -> tuple[int, list[str]]:
        """Split the requested ids into fresh hits and lookups.

        Args:
            guest_ids: The distinct ids this refresh needs.
            now: The refresh's clock reading.

        Returns:
            Tuple of the hit count and the ids needing a lookup.
        """
        hits = 0
        needed: list[str] = []
        for guest_id in guest_ids:
            entry = self._entries.get(guest_id)
            if entry is not None:
                self._entries.move_to_end(guest_id)
                if self._is_fresh(entry, now):
                    hits += 1
                    continue
            needed.append(guest_id)
        return hits, needed

    def _priority(self, guest_id: str) -> tuple[int, int]:
        """Order one candidate id for the burst cap.

        Scheduling is a strict round robin on the last attempt,
        which is the only ordering that stays fair when a refresh
        needs more distinct guests than the cache can hold. An id
        never attempted sorts first, so a newly seen guest is
        fetched promptly and an expired record is still guaranteed
        its turn rather than queueing behind every absent id
        forever. The attempt log keeps every id still in use, so
        bounding it cannot reset an id's place in the rotation.

        An id with no record at all breaks a tie, because it
        publishes nothing until it is fetched while an expired
        record keeps serving its last known values.

        Args:
            guest_id: The candidate id.

        Returns:
            The sort key for that id.
        """
        has_entry = 1 if guest_id in self._entries else 0
        return self._attempts.get(guest_id, -1), has_entry

    def _is_fresh(self, entry: GuestCacheEntry, now: float) -> bool:
        """Test a cached record against its freshness window.

        Args:
            entry: The cached record.
            now: The refresh's clock reading.

        Returns:
            True while the record may be served without a request.
        """
        ttl = (
            GUEST_CACHE_TTL_SECONDS
            if entry.guest is not None
            else GUEST_CACHE_MISS_TTL_SECONDS
        )
        return now - entry.fetched_at < ttl

    def _known(self, guest_ids: list[str]) -> dict[str, GuestyGuest]:
        """Collect the records held for the requested ids.

        Args:
            guest_ids: The distinct ids this refresh needs.

        Returns:
            Guest records by id, omitting ids with no record.
        """
        known: dict[str, GuestyGuest] = {}
        for guest_id in guest_ids:
            entry = self._entries.get(guest_id)
            if entry is not None and entry.guest is not None:
                known[guest_id] = entry.guest
        return known

    async def _lookup(
        self,
        guest_id: str,
        semaphore: asyncio.Semaphore,
    ) -> bool:
        """Fetch one guest record, retaining the old one on failure.

        Args:
            guest_id: The guest id to fetch.
            semaphore: Bound on concurrent lookups.

        Returns:
            True when the lookup produced a stored record.
        """
        async with semaphore:
            self._attempt_seq += 1
            self._attempts[guest_id] = self._attempt_seq
            self._attempts.move_to_end(guest_id)
            try:
                response = await self._client._request(
                    "GET",
                    GUESTS_CRUD_PATH.format(guest_id=guest_id),
                    params={"fields": " ".join(GUEST_FIELDS)},
                )
            except (GuestyApiError, httpx.HTTPError) as exc:
                self._record_failure(guest_id, type(exc).__name__)
                return False

            if response.status_code == 404:
                self._store(guest_id, None)
                return True

            if not response.is_success:
                self._record_failure(
                    guest_id,
                    f"status {response.status_code}",
                )
                return False

            guest = _parse_guest(response, guest_id)
            if guest is None:
                self._record_failure(guest_id, "unusable response")
                return False

            self._store(guest_id, guest)
            return True

    def _store(self, guest_id: str, guest: GuestyGuest | None) -> None:
        """Store a fetched record and enforce the LRU bound.

        Args:
            guest_id: The guest id fetched.
            guest: The record fetched, or None for a not-found.
        """
        self._entries[guest_id] = GuestCacheEntry(
            guest=guest,
            fetched_at=self._clock(),
        )
        self._entries.move_to_end(guest_id)
        self._trim(self._entries)

    def _record_failure(self, guest_id: str, reason: str) -> None:
        """Note a failed lookup without discarding the old record.

        A failure is deliberately not stored as a record of its
        own: a not-found is cached, an outage is retried.

        Args:
            guest_id: The guest id whose lookup failed.
            reason: Exception class or status, never a response
                body, so no guest data can reach the log.
        """
        entry = self._entries.get(guest_id)
        if entry is not None:
            self._entries[guest_id] = replace(
                entry,
                failed_at=self._clock(),
            )
        _LOGGER.warning(
            "Guest lookup for %s failed (%s); keeping last known values",
            guest_id,
            reason,
        )

    def _trim(self, store: OrderedDict[str, Any]) -> None:
        """Evict least-recently-used keys beyond the bound.

        Args:
            store: The ordered store to trim in place.
        """
        while len(store) > GUEST_CACHE_MAX_ENTRIES:
            store.popitem(last=False)

    def _trim_attempts(self, in_play: set[str]) -> None:
        """Bound the attempt log without forgetting live ids.

        Forgetting an id that is still being requested would make
        it look never attempted and send it back to the front of
        the queue, so a working set larger than the record bound
        could cycle forever without the tail ever being served.
        The log therefore keeps every id this refresh needed and
        sheds only the oldest ids that have dropped out of use.

        Args:
            in_play: Ids the current refresh asked for.
        """
        excess = len(self._attempts) - max(
            GUEST_CACHE_MAX_ENTRIES,
            len(in_play),
        )
        if excess <= 0:
            return
        stale = [guest_id for guest_id in self._attempts if guest_id not in in_play][
            :excess
        ]
        for guest_id in stale:
            del self._attempts[guest_id]


def _parse_guest(
    response: httpx.Response,
    guest_id: str,
) -> GuestyGuest | None:
    """Map one guests-crud response body onto a guest record.

    ``guest_id`` comes from the request rather than the payload, so
    a record can only ever be published under the id it was asked
    for (FR-019).

    Args:
        response: The successful guests-crud response.
        guest_id: The guest id that was requested.

    Returns:
        The mapped guest, or None when the body is unusable.
    """
    try:
        data = response.json()
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    return GuestyGuest(
        full_name=data.get("fullName"),
        phone=data.get("phone"),
        email=data.get("email"),
        guest_id=guest_id,
    )
