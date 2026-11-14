# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Legacy not-found fallback for the reservations v3 path.

Guesty Support told an external contributor, in writing on issue
#223, that their migration backfill into reservations v3 is still
running and that a reservation answering *not found* on v3 may
simply not have been backfilled yet. They advised retrieving such a
reservation from the legacy resource by identifier. Without that,
a reservation would silently vanish from a calendar and a door-lock
access code would silently never be written.

This module is permitted legacy call 2 of FR-001 and nothing more.
It issues ``GET /v1/reservations/{reservationId}`` for one
previously known reservation at a time. There is no legacy search
and no speculative sweep here; anything wider violates FR-001.

Four properties keep it honest and affordable:

* A legacy **not found** is confirmed deletion. The candidate is
  retired and never looked up again, so the fallback cannot mask a
  genuine cancellation (FR-052).
* Lookups are capped per refresh and rate-limited per candidate,
  with the interval measured from the last **attempt** rather than
  the last success, so a permanently missing reservation cannot
  hot-loop through Guesty's small daily token allowance (FR-053).
* Candidate ordering is fair — never-attempted first — so a backlog
  drains instead of starving behind re-checks (FR-053).
* An answer consistent with retirement self-disables the fallback
  for the session, and an answer that cannot be classified is read
  as retirement rather than as absence, because self-disabling
  degrades to today's behaviour while a wrong absence erases a
  reservation (FR-056).

Lookups are issued **sequentially**. They are a per-identifier
burst, which is exactly the shape that re-exposes the concurrent
HTTP 401 handling fixed in Phase 5, and a retirement answer has to
stop the batch at once rather than after the requests already in
flight. One at a time is the bounded concurrency this costs the
least to reason about.

Nothing here is persisted: a persisted absence could permanently
hide a reservation that comes back. Candidacy itself survives a
restart through the identity baseline's durable hints, which the
caller supplies (data-model.md §9).

Zero Home Assistant imports (NFR-007).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from custom_components.guesty.api.const import (
    LEGACY_FALLBACK_RECHECK_SECONDS,
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH,
    RESERVATION_BY_ID_LEGACY_PATH,
)
from custom_components.guesty.api.exceptions import GuestyApiError
from custom_components.guesty.api.models import (
    FallbackCounters,
    GuestyReservation,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.models import FallbackCandidate

_LOGGER = logging.getLogger(__name__)

# How one legacy by-id answer was read.
_OK: str = "ok"
_ABSENT: str = "absent"
_RETIRED: str = "retired"
_FAILED: str = "failed"

# Substrings that make a 404 body a statement about one specific
# reservation rather than about the resource as a whole.
_ABSENCE_MARKERS: tuple[str, ...] = (
    "not found",
    "does not exist",
    "doesn't exist",
    "no such reservation",
)

# Substrings that make an answer a statement about the resource.
_RETIREMENT_MARKERS: tuple[str, ...] = (
    "deprecat",
    "retir",
    "sunset",
    "no longer available",
    "no longer supported",
    "has been removed",
)

# Substrings that make a 404 an answer about the HTTP route rather
# than about a reservation. A framework's own "Cannot GET /v1/..."
# names both a reservation path and "not found" while establishing
# nothing whatsoever about the booking, so it is read as
# retirement.
_ROUTE_MARKERS: tuple[str, ...] = (
    "cannot get",
    "cannot post",
    "cannot put",
    "cannot patch",
    "cannot delete",
    "endpoint",
    "route",
    "://",
    "/v1/",
    "/v2/",
    "/v3/",
)

# Body fields Guesty puts a human-readable error message in.
_MESSAGE_FIELDS: tuple[str, ...] = (
    "message",
    "error",
    "detail",
    "title",
    "description",
)


def is_absent(verdict: str) -> bool:
    """Report whether a verdict is confirmed absence (FR-052).

    Args:
        verdict: One of the module's verdict constants.

    Returns:
        True when legacy stated this reservation does not exist.
    """
    return verdict == _ABSENT


def is_retired(verdict: str) -> bool:
    """Report whether a verdict is retirement-shaped (FR-056).

    Args:
        verdict: One of the module's verdict constants.

    Returns:
        True when the answer was about the resource, not the
        reservation.
    """
    return verdict == _RETIRED


@dataclass(frozen=True)
class ServedRecord:
    """One reservation a legacy by-id lookup actually returned.

    Its existence is the write-routing predicate: only a record
    created by a success carries an identifier a legacy resource
    has validated (FR-058).

    Attributes:
        reservation: The reservation the lookup returned.
        fetched_at: When that lookup succeeded.
    """

    reservation: GuestyReservation
    fetched_at: datetime


@dataclass(frozen=True)
class FallbackOutcome:
    """What one fallback resolution produced.

    Attributes:
        reservations: Reservations the fallback publishes.
        counters: This refresh's counters (FR-057).
        served: Identifiers backed by a ``ServedRecord``, which is
            the set a write routes on (FR-058).
        sourced: Identifiers published by the fallback, including
            those republished from retained values (FR-054).
        absent: Identifiers legacy confirmed are gone (FR-052).
    """

    reservations: tuple[GuestyReservation, ...] = ()
    counters: FallbackCounters = field(default_factory=FallbackCounters)
    served: tuple[str, ...] = ()
    sourced: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()


class ReservationFallbackSource:
    """Retrieves previously known reservations v3 has lost.

    Attributes:
        _client: API client issuing the authenticated requests.
        _served: Identifier to the record a lookup returned.
        _retained: Identifier to the last values published for it,
            whatever their source, so a failed lookup degrades the
            reservation rather than erasing it (FR-054).
        _last_attempt: Identifier to the last lookup attempt,
            successful or not, which the re-check interval is
            measured from (FR-053).
        _confirmed_absent: Identifiers legacy says are gone.
        _disabled: Whether retirement self-disabled the fallback.
        _retirement_logged: Whether the one warning has been made.
    """

    def __init__(self, client: GuestyApiClient) -> None:
        """Initialize the fallback for one config entry.

        Args:
            client: API client used to issue HTTP requests.
        """
        self._client = client
        self._served: dict[str, ServedRecord] = {}
        self._retained: dict[str, GuestyReservation] = {}
        self._last_attempt: dict[str, datetime] = {}
        self._confirmed_absent: set[str] = set()
        self._disabled = False
        self._retirement_logged = False

    @property
    def served_ids(self) -> frozenset[str]:
        """Return the identifiers a legacy lookup has validated.

        Returns:
            The identifiers currently served from the fallback.
        """
        return frozenset(self._served)

    @property
    def disabled(self) -> bool:
        """Report whether retirement self-disabled the fallback.

        Returns:
            True once a retirement-shaped answer was seen.
        """
        return self._disabled

    @property
    def confirmed_absent(self) -> frozenset[str]:
        """Return the identifiers legacy says no longer exist.

        Returns:
            The retired candidates of this session.
        """
        return frozenset(self._confirmed_absent)

    def note_retirement(self) -> None:
        """Self-disable after a retirement-shaped write answer.

        A legacy *write* can meet the retirement before any lookup
        does, and the same flag governs both (FR-056, FR-061).
        """
        if self._disabled:
            return
        self._disable()
        self._served.clear()
        self._retained.clear()

    def retire_candidate(self, reservation_id: str) -> None:
        """Retire a candidate a legacy write reported is gone.

        Args:
            reservation_id: The reservation legacy denied.
        """
        self._retire(reservation_id)

    async def resolve(
        self,
        *,
        retrieved: tuple[GuestyReservation, ...],
        candidates: Mapping[str, FallbackCandidate],
        past_days: int,
        future_days: int,
        statuses: frozenset[str],
    ) -> FallbackOutcome:
        """Recover the previously known reservations v3 lost.

        Nothing raised here reaches the caller: a fallback failure
        must never cost a refresh (FR-054, NFR-005).

        Args:
            retrieved: Reservations the v3 result carried.
            candidates: Durable stay dates of every reservation the
                integration already knows, keyed by identifier.
            past_days: Configured days of history to publish.
            future_days: Configured days ahead to publish.
            statuses: The configured status set (FR-050a).

        Returns:
            The reservations to publish and this refresh's counters.
        """
        now = datetime.now(UTC)
        self._absorb(retrieved)

        if self._disabled:
            self._served.clear()
            self._retained.clear()
            return FallbackOutcome()

        present = {reservation.id for reservation in retrieved}
        eligible = [
            reservation_id
            for reservation_id, hint in candidates.items()
            if reservation_id not in present
            and reservation_id not in self._confirmed_absent
            and _overlaps(hint, now, past_days, future_days)
        ]

        due = [
            reservation_id
            for reservation_id in eligible
            if self._is_due(reservation_id, now)
        ]
        due.sort(key=lambda res_id: self._priority(res_id, candidates))
        batch = due[:MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH]
        deferred = len(due) - len(batch)

        absent: list[str] = []
        succeeded = 0
        failed = 0
        for reservation_id in batch:
            verdict = await self._lookup(reservation_id, now, statuses)
            if verdict == _RETIRED:
                self._disable()
                self._served.clear()
                self._retained.clear()
                return FallbackOutcome(
                    counters=FallbackCounters(
                        attempted=len(absent) + succeeded + failed + 1,
                        deferred=deferred,
                        confirmed_absent=len(absent),
                        succeeded=succeeded,
                        failed=failed,
                    ),
                    absent=tuple(absent),
                )
            if verdict == _ABSENT:
                absent.append(reservation_id)
            elif verdict == _OK:
                succeeded += 1
            else:
                failed += 1

        published = self._publish(eligible, now, past_days, future_days, statuses)
        self._prune(present, set(candidates))

        attempted = set(batch)
        return FallbackOutcome(
            reservations=published,
            counters=FallbackCounters(
                attempted=len(batch),
                served=sum(
                    1 for reservation in published if reservation.id not in attempted
                ),
                succeeded=succeeded,
                confirmed_absent=len(absent),
                failed=failed,
                deferred=deferred,
            ),
            served=tuple(sorted(self._served)),
            sourced=tuple(reservation.id for reservation in published),
            absent=tuple(absent),
        )

    def _absorb(
        self,
        retrieved: tuple[GuestyReservation, ...],
    ) -> None:
        """Take over the values this v3 result reported.

        A reservation v3 returned is no longer the fallback's to
        serve: its record is dropped at once so a write against it
        routes to v3 (FR-058). Its values are retained all the
        same, because they are what FR-054 republishes should the
        reservation go missing and the first lookup for it fail.

        Args:
            retrieved: Reservations the v3 result carried.
        """
        for reservation in retrieved:
            self._retained[reservation.id] = reservation
            self._served.pop(reservation.id, None)
            self._last_attempt.pop(reservation.id, None)
            self._confirmed_absent.discard(reservation.id)

    def _is_due(self, reservation_id: str, now: datetime) -> bool:
        """Report whether a candidate may be looked up again.

        The interval runs from the last attempt rather than the
        last success. Measuring from a success would make a
        candidate whose first lookup failed, or whose record is
        already stale, eligible on every single poll (FR-053).

        Args:
            reservation_id: The candidate to test.
            now: The refresh instant.

        Returns:
            True when no lookup has been attempted recently.
        """
        attempted = self._last_attempt.get(reservation_id)
        if attempted is None:
            return True
        return now - attempted >= timedelta(
            seconds=LEGACY_FALLBACK_RECHECK_SECONDS,
        )

    def _priority(
        self,
        reservation_id: str,
        candidates: Mapping[str, FallbackCandidate],
    ) -> tuple[int, datetime, datetime]:
        """Order one candidate for the per-refresh cap.

        Never-attempted candidates come first, then the least
        recently attempted, then the earliest check-out. Ordering
        by check-out alone would let a re-check falling due
        displace a candidate that has never been looked up at all,
        and a backlog larger than the cap would never drain
        (FR-053).

        Args:
            reservation_id: The candidate to order.
            candidates: The durable hints of this refresh.

        Returns:
            The sort key for that candidate.
        """
        epoch = datetime.min.replace(tzinfo=UTC)
        attempted = self._last_attempt.get(reservation_id)
        hint = candidates.get(reservation_id)
        return (
            1 if attempted is not None else 0,
            attempted if attempted is not None else epoch,
            hint.check_out if hint is not None else epoch,
        )

    async def _lookup(
        self,
        reservation_id: str,
        now: datetime,
        statuses: frozenset[str],
    ) -> str:
        """Issue one legacy by-id lookup and read its answer.

        The attempt is recorded before the request so that a
        failure still resets the re-check clock (FR-053).

        Args:
            reservation_id: The reservation to retrieve.
            now: The refresh instant.
            statuses: The configured status set (FR-050a).

        Returns:
            One of the module's verdict constants.
        """
        self._last_attempt[reservation_id] = now
        try:
            response = await self._client._request(
                "GET",
                RESERVATION_BY_ID_LEGACY_PATH.format(
                    reservation_id=reservation_id,
                ),
            )
        except (GuestyApiError, httpx.HTTPError) as exc:
            _LOGGER.warning(
                "Legacy fallback lookup for reservation %s failed "
                "(%s); keeping its last known values",
                reservation_id,
                type(exc).__name__,
            )
            return _FAILED

        verdict = classify_legacy_response(response, reservation_id)
        if verdict == _ABSENT:
            self._retire(reservation_id)
            return _ABSENT
        if verdict == _RETIRED:
            return _RETIRED
        if verdict == _FAILED:
            _LOGGER.warning(
                "Legacy fallback lookup for reservation %s "
                "answered with status %d; keeping its last known "
                "values",
                reservation_id,
                response.status_code,
            )
            return _FAILED

        reservation = _parse(response, reservation_id)
        if reservation is None:
            _LOGGER.warning(
                "Legacy fallback lookup for reservation %s "
                "returned an unusable document; keeping its last "
                "known values",
                reservation_id,
            )
            return _FAILED

        if reservation.status not in statuses:
            # The reservation left the configured status set, which
            # is why v3 stopped returning it. Reintroducing it would
            # contradict the user's own filter (FR-050a).
            self._served.pop(reservation_id, None)
            self._retained.pop(reservation_id, None)
            _LOGGER.debug(
                "Legacy fallback will not republish reservation %s "
                "at status %s, which is outside the configured set",
                reservation_id,
                reservation.status,
            )
            return _OK

        self._served[reservation_id] = ServedRecord(
            reservation=reservation,
            fetched_at=now,
        )
        self._retained[reservation_id] = reservation
        return _OK

    def _retire(self, reservation_id: str) -> None:
        """Retire a candidate both APIs agree no longer exists.

        Args:
            reservation_id: The reservation legacy denied.
        """
        self._confirmed_absent.add(reservation_id)
        self._served.pop(reservation_id, None)
        self._retained.pop(reservation_id, None)
        _LOGGER.debug(
            "Legacy confirms reservation %s is gone; it will not be looked up again",
            reservation_id,
        )

    def _disable(self) -> None:
        """Self-disable the fallback, warning exactly once."""
        self._disabled = True
        if self._retirement_logged:
            return
        self._retirement_logged = True
        _LOGGER.warning(
            "The Guesty legacy reservation resource answered as "
            "retired, so the not-found fallback is disabled for "
            "this session and reservations are served from v3 "
            "alone. The legacy resources retire on %s",
            LEGACY_RESERVATIONS_RETIREMENT_DATE,
        )

    def _publish(
        self,
        eligible: list[str],
        now: datetime,
        past_days: int,
        future_days: int,
        statuses: frozenset[str],
    ) -> tuple[GuestyReservation, ...]:
        """Choose what the fallback publishes this refresh.

        A served record wins; otherwise the reservation's last
        known values are republished, which is what keeps a
        reservation visible through a failed first lookup and
        between re-checks (FR-053, FR-054).

        Args:
            eligible: Candidate identifiers still in the window.
            now: The refresh instant.
            past_days: Configured days of history to publish.
            future_days: Configured days ahead to publish.
            statuses: The configured status set (FR-050a).

        Returns:
            The reservations to publish, in candidate order.
        """
        published: list[GuestyReservation] = []
        for reservation_id in eligible:
            if reservation_id in self._confirmed_absent:
                continue
            record = self._served.get(reservation_id)
            reservation = (
                record.reservation
                if record is not None
                else self._retained.get(reservation_id)
            )
            if reservation is None:
                continue
            if reservation.status not in statuses:
                continue
            if not _in_window(
                reservation.check_in,
                reservation.check_out,
                now,
                past_days,
                future_days,
            ):
                continue
            published.append(reservation)
        return tuple(published)

    def _prune(self, present: set[str], known: set[str]) -> None:
        """Drop state for reservations no longer worth holding.

        Args:
            present: Identifiers this v3 result carried.
            known: Identifiers the durable candidate hints cover.
        """
        keep = present | known
        for mapping in (self._retained, self._served, self._last_attempt):
            for reservation_id in [res_id for res_id in mapping if res_id not in keep]:
                del mapping[reservation_id]
        self._confirmed_absent &= known


def _overlaps(
    hint: FallbackCandidate,
    now: datetime,
    past_days: int,
    future_days: int,
) -> bool:
    """Test a durable hint against the configured window.

    Args:
        hint: The candidate's last known stay endpoints.
        now: The refresh instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.

    Returns:
        True when the stay still overlaps the window.
    """
    return _in_window(
        hint.check_in,
        hint.check_out,
        now,
        past_days,
        future_days,
    )


def _in_window(
    check_in: datetime,
    check_out: datetime,
    now: datetime,
    past_days: int,
    future_days: int,
) -> bool:
    """Test one stay against the configured instant window.

    The predicate matches the coordinator's trim, so the fallback
    never spends a request on a reservation the trim would drop
    (FR-051).

    Args:
        check_in: UTC check-in instant.
        check_out: UTC check-out instant.
        now: The refresh instant.
        past_days: Configured days of history to publish.
        future_days: Configured days ahead to publish.

    Returns:
        True when the stay overlaps the configured window.
    """
    return check_out > now - timedelta(days=past_days) and check_in < now + timedelta(
        days=future_days,
    )


def classify_legacy_response(
    response: httpx.Response,
    reservation_id: str,
) -> str:
    """Read one legacy answer as absence, retirement or failure.

    Telling "this reservation does not exist" from "this resource
    is retired" is UNVERIFIED against a live account, so the rule
    is deliberately asymmetric: an answer that cannot be classified
    is read as **retirement**. Self-disabling degrades to today's
    v3-only behaviour, whereas a wrong absence retires a
    reservation permanently and erases it from the sensors
    (FR-052, FR-056).

    Args:
        response: The HTTP response to the by-id lookup.
        reservation_id: The reservation that was requested.

    Returns:
        One of the module's verdict constants.
    """
    if response.status_code == 410:
        return _RETIRED
    if response.is_success:
        return _OK
    if response.status_code != 404:
        return _FAILED

    parts = _message_parts(response)
    if not parts:
        return _RETIRED
    joined = " ".join(parts).lower()
    if any(marker in joined for marker in _RETIREMENT_MARKERS):
        return _RETIRED
    if any(marker in joined for marker in _ROUTE_MARKERS):
        return _RETIRED
    lowered_id = reservation_id.lower()
    for part in parts:
        lowered = part.lower()
        names_reservation = "reservation" in lowered or lowered_id in lowered
        if names_reservation and any(marker in lowered for marker in _ABSENCE_MARKERS):
            return _ABSENT
    return _RETIRED


def _message_parts(response: httpx.Response) -> tuple[str, ...]:
    """Extract the human-readable messages of an error body.

    The fields are kept apart rather than joined: a generic
    ``"error": "Not Found"`` beside an unrelated ``message`` would
    otherwise lend its absence wording to text that never made a
    statement about the reservation at all.

    Args:
        response: The HTTP response to read.

    Returns:
        The message strings the body carried, in field order.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return ()
    if not isinstance(body, dict):
        return ()
    return tuple(
        value
        for key in _MESSAGE_FIELDS
        if isinstance(value := body.get(key), str) and value
    )


def _parse(
    response: httpx.Response,
    reservation_id: str,
) -> GuestyReservation | None:
    """Map one legacy by-id document to a reservation.

    The document's own identifier is checked against the one that
    was requested: a legacy resource answering about a different
    reservation is not evidence about this one, and acting on it
    would be exactly the wrong-identity write the identity guard
    exists to prevent (FR-031e).

    Args:
        response: The HTTP response to the by-id lookup.
        reservation_id: The reservation that was requested.

    Returns:
        The mapped reservation, or None when unusable.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    if body.get("_id") != reservation_id:
        _LOGGER.warning(
            "Legacy fallback asked for reservation %s and was "
            "answered about a different one; ignoring it",
            reservation_id,
        )
        return None
    for required in ("status", "listingId"):
        if not isinstance(body.get(required), str):
            # ``from_api_dict`` only tests these for truthiness, so a
            # list or a dict would reach status filtering and listing
            # attribution and raise there, aborting the whole refresh
            # over one malformed document (FR-054).
            _LOGGER.warning(
                "Legacy fallback answer for reservation %s carries "
                "a non-textual %s; ignoring it",
                reservation_id,
                required,
            )
            return None
    return GuestyReservation.from_api_dict(body)
