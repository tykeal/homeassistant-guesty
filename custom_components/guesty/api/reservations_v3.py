# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservations v3 search source.

Retrieves reservations from the Guesty reservations v3 search
resource, which replaces the legacy Open API reservation resources
retired on ``LEGACY_RESERVATIONS_RETIREMENT_DATE``.

The request is built from documented bracket filters only: an
unsupported filter is rejected with HTTP 400 rather than silently
ignored, and such a rejection is never retried with the filters
removed, because an unfiltered search returns the wrong
reservations without saying so (issue #204).

No listing filter is ever sent: ``filter[listingId]`` matches the
**first** stay segment only, so it would silently drop a
reservation that moved to another listing mid-stay (FR-015).
Listing attribution is derived locally from ``stay[]``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import httpx

from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    MAX_V3_RESERVATION_PAGES,
    RESERVATIONS_PAGE_SIZE,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    V3_DATE_PAD_DAYS,
)
from custom_components.guesty.api.exceptions import (
    GuestyFilterRejectedError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    GuestyReservation,
    ReservationFetchResult,
    translate_v3_status_filter,
)
from custom_components.guesty.api.reservations_fallback import (
    ReservationFallbackSource,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.models import FallbackCandidate

_LOGGER = logging.getLogger(__name__)


class V3ReservationSource:
    """Reservation source backed by the v3 search resource.

    Attributes:
        _client: API client issuing the authenticated requests.
        _fallback: Legacy not-found fallback owned by this source,
            so its session state lives exactly as long as the v3
            path it corrects (FR-050).
    """

    def __init__(self, client: GuestyApiClient) -> None:
        """Initialize the v3 reservation source.

        Args:
            client: API client used to issue HTTP requests.
        """
        self._client = client
        self._fallback = ReservationFallbackSource(client)

    @property
    def fallback(self) -> ReservationFallbackSource:
        """Return the fallback this source resolves absentees with.

        Returns:
            The fallback owned by this source.
        """
        return self._fallback

    async def fetch(
        self,
        *,
        past_days: int = DEFAULT_PAST_DAYS,
        future_days: int = DEFAULT_FUTURE_DAYS,
        statuses: frozenset[str] | None = None,
        candidates: Mapping[str, FallbackCandidate] | None = None,
    ) -> ReservationFetchResult:
        """Fetch reservations from the v3 search resource.

        Issues one paginated search for the configured window,
        using the documented overlap filters padded by one day on
        each side because the server compares listing-localized
        dates while the window is instant-based.

        Every page is validated before anything is mapped: a page
        that fails any check aborts the whole refresh so the
        coordinator keeps its last-known-good data (FR-006).

        Args:
            past_days: Days in the past to include. Default 30.
            future_days: Days in the future to include. Default 365.
            statuses: Statuses to request. Defaults to
                ACTIONABLE_STATUSES if None.
            candidates: Durable stay dates of every reservation the
                integration already knows, keyed by identifier.
                Any that this search does not return is a
                candidate for a legacy by-id lookup, because
                Guesty's backfill into v3 is still running and an
                absence is not yet evidence of deletion (FR-050).

        Returns:
            The mapped reservations and the diagnostics counters.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyFilterRejectedError: When the API rejects the
                query filters with HTTP 400.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On a malformed or inconsistent
                response.
        """
        if statuses is None:
            statuses = ACTIONABLE_STATUSES

        now = datetime.now(UTC)
        window_start = (now - timedelta(days=past_days + V3_DATE_PAD_DAYS)).strftime(
            "%Y-%m-%d"
        )
        window_end = (now + timedelta(days=future_days + V3_DATE_PAD_DAYS)).strftime(
            "%Y-%m-%d",
        )
        status_filter = translate_v3_status_filter(statuses)

        results = await self._fetch_all_pages(
            window_start=window_start,
            window_end=window_end,
            status_filter=status_filter,
        )
        result = _map_results(results)
        # Resolved even with no candidates: the fallback has to see
        # every v3 result to keep the last known values it
        # republishes when a reservation later goes missing and its
        # first lookup fails (FR-054).
        outcome = await self._fallback.resolve(
            retrieved=result.reservations,
            candidates=candidates or {},
            past_days=past_days,
            future_days=future_days,
            statuses=statuses,
        )
        return replace(
            result,
            reservations=result.reservations + outcome.reservations,
            fallback=outcome.counters,
            fallback_served=outcome.served,
            fallback_sourced=outcome.sourced,
            fallback_absent=outcome.absent,
        )

    async def _fetch_all_pages(
        self,
        *,
        window_start: str,
        window_end: str,
        status_filter: str,
    ) -> list[list[dict[str, Any]]]:
        """Page through the v3 search, validating every page.

        Args:
            window_start: Localized date the window starts on.
            window_end: Localized date the window ends on.
            status_filter: Comma-joined v3 status filter value.

        Returns:
            One list of raw reservation dicts per fetched page.

        Raises:
            GuestyFilterRejectedError: On HTTP 400.
            GuestyResponseError: On any failed page check.
        """
        pages: list[list[dict[str, Any]]] = []
        seen_ids: set[str] = set()
        previous_id: str | None = None
        skip = 0

        for _ in range(MAX_V3_RESERVATION_PAGES):
            params = _build_search_params(
                window_start=window_start,
                window_end=window_end,
                status_filter=status_filter,
                skip=skip,
            )
            response = await self._client._request(
                "GET",
                RESERVATIONS_V3_SEARCH_ENDPOINT,
                params=params,
            )

            if response.status_code == 400:
                raise GuestyFilterRejectedError(
                    "Guesty rejected the reservation search filters",
                    endpoint=RESERVATIONS_V3_SEARCH_ENDPOINT,
                    status_code=response.status_code,
                    body_excerpt=response.text,
                )

            if not response.is_success:
                raise GuestyResponseError(
                    f"Reservations v3 search failed: status {response.status_code}",
                )

            results, has_more = _validate_page(response, skip=skip)
            previous_id = _validate_identifiers(
                results,
                seen_ids=seen_ids,
                previous_id=previous_id,
            )
            pages.append(results)

            if not has_more:
                return pages

            skip += RESERVATIONS_PAGE_SIZE

        raise GuestyResponseError(
            f"Reservations v3 search still reported more results "
            f"after {MAX_V3_RESERVATION_PAGES} pages",
        )


def _build_search_params(
    *,
    window_start: str,
    window_end: str,
    status_filter: str,
    skip: int,
) -> dict[str, str | int]:
    """Build the query parameters for one v3 search page.

    Only documented parameters are sent. No ``fields`` projection
    exists on this resource (FR-007), and no listing filter is ever
    sent (FR-015).

    Args:
        window_start: Localized date the window starts on.
        window_end: Localized date the window ends on.
        status_filter: Comma-joined v3 status filter value.
        skip: Pagination offset for this page.

    Returns:
        Dict of query parameters for the v3 search resource.
    """
    return {
        "filter[checkOut][gte]": window_start,
        "filter[checkIn][lte]": window_end,
        "filter[status][in]": status_filter,
        "sort": "_id",
        "limit": RESERVATIONS_PAGE_SIZE,
        "skip": skip,
    }


def _validate_page(
    response: httpx.Response,
    *,
    skip: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Validate the envelope of one v3 search page.

    Args:
        response: The HTTP response holding the page.
        skip: The pagination offset that was requested.

    Returns:
        Tuple of the page's results and its ``hasMore`` flag.

    Raises:
        GuestyResponseError: On any failed check.
    """
    try:
        data = response.json()
    except Exception as exc:
        raise GuestyResponseError(
            "Reservations v3 response is not valid JSON",
        ) from exc

    if not isinstance(data, dict):
        raise GuestyResponseError(
            "Reservations v3 response must be a JSON object",
        )

    results = data.get("results")
    if not isinstance(results, list):
        raise GuestyResponseError(
            "Reservations v3 response missing results",
        )

    pagination = data.get("pagination")
    if not isinstance(pagination, dict):
        raise GuestyResponseError(
            "Reservations v3 response missing pagination",
        )

    has_more = pagination.get("hasMore")
    if not isinstance(has_more, bool):
        raise GuestyResponseError(
            "Reservations v3 pagination.hasMore must be a boolean",
        )

    if pagination.get("skip") != skip:
        raise GuestyResponseError(
            f"Reservations v3 pagination.skip "
            f"{pagination.get('skip')!r} does not match the "
            f"requested skip {skip}",
        )

    if len(results) > RESERVATIONS_PAGE_SIZE:
        raise GuestyResponseError(
            f"Reservations v3 page returned {len(results)} results, "
            f"more than the requested limit {RESERVATIONS_PAGE_SIZE}",
        )

    if has_more and not results:
        raise GuestyResponseError(
            "Reservations v3 reported more results after an empty page",
        )

    if has_more and len(results) < RESERVATIONS_PAGE_SIZE:
        raise GuestyResponseError(
            "Reservations v3 reported more results after a short page",
        )

    return results, has_more


def _validate_identifiers(
    results: list[dict[str, Any]],
    *,
    seen_ids: set[str],
    previous_id: str | None,
) -> str | None:
    """Validate the reservation identifiers of one page.

    Identifiers must exist, be strings, be unique across the whole
    refresh, and ascend strictly, because the paging is only
    deterministic while ``sort=_id`` holds.

    Args:
        results: The raw reservation dicts of this page.
        seen_ids: Identifiers already seen in this refresh; updated
            in place.
        previous_id: The last identifier seen, or None on the first
            page.

    Returns:
        The last identifier seen after this page.

    Raises:
        GuestyResponseError: On any failed check.
    """
    for item in results:
        reservation_id = item.get("reservationId") if isinstance(item, dict) else None
        if not isinstance(reservation_id, str) or not reservation_id:
            raise GuestyResponseError(
                "Reservations v3 result missing a string reservationId",
            )

        if reservation_id in seen_ids:
            raise GuestyResponseError(
                f"Reservations v3 returned duplicate reservation {reservation_id}",
            )

        if previous_id is not None and reservation_id <= previous_id:
            raise GuestyResponseError(
                f"Reservations v3 returned reservation "
                f"{reservation_id} out of ascending order after "
                f"{previous_id}",
            )

        seen_ids.add(reservation_id)
        previous_id = reservation_id

    return previous_id


def _map_results(
    pages: list[list[dict[str, Any]]],
) -> ReservationFetchResult:
    """Map validated pages into a reservation fetch result.

    A reservation the parser cannot use is skipped — the parser
    logs why — rather than failing the refresh, because one
    unusable record must not hide every other reservation.

    Args:
        pages: One list of raw reservation dicts per fetched page.

    Returns:
        The mapped reservations and the diagnostics counters.
    """
    reservations: list[GuestyReservation] = []
    status_counts: Counter[str] = Counter()
    segment_histogram: Counter[int] = Counter()

    for page in pages:
        for item in page:
            raw_status = item.get("status")
            if isinstance(raw_status, str):
                status_counts[raw_status] += 1

            reservation = GuestyReservation.from_v3_api_dict(item)
            if reservation is None:
                continue

            reservations.append(reservation)
            segment_histogram[len(reservation.stay)] += 1

    return ReservationFetchResult(
        reservations=tuple(reservations),
        pages=len(pages),
        status_counts=MappingProxyType(dict(status_counts)),
        segment_histogram=MappingProxyType(dict(segment_histogram)),
    )
