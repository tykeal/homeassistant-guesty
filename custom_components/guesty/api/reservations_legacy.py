# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Legacy Open API reservation source.

Holds the reservation retrieval that shipped before the v3
migration, moved verbatim behind the ``ReservationSource`` seam so
the legacy path stays behaviourally identical to the previous
release. Deleted as a unit once Guesty retires the legacy
resources on ``LEGACY_RESERVATIONS_RETIREMENT_DATE``.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_FIELDS,
    RESERVATIONS_PAGE_SIZE,
)
from custom_components.guesty.api.exceptions import GuestyResponseError
from custom_components.guesty.api.models import (
    GuestyReservation,
    GuestyReservationsResponse,
    ReservationFetchResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.guesty.api.client import GuestyApiClient
    from custom_components.guesty.api.models import FallbackCandidate

_LOGGER = logging.getLogger(__name__)


class LegacyReservationSource:
    """Reservation source backed by the legacy Open API resources.

    Attributes:
        _client: API client issuing the authenticated requests.
    """

    def __init__(self, client: GuestyApiClient) -> None:
        """Initialize the legacy reservation source.

        Args:
            client: API client used to issue HTTP requests.
        """
        self._client = client

    async def fetch(
        self,
        *,
        past_days: int = DEFAULT_PAST_DAYS,
        future_days: int = DEFAULT_FUTURE_DAYS,
        statuses: frozenset[str] | None = None,
        candidates: Mapping[str, FallbackCandidate] | None = None,
    ) -> ReservationFetchResult:
        """Fetch reservations with date and status filters.

        Makes two paginated requests to the Guesty reservations
        endpoint:
        1. Primary: checkIn date-range filter with status filter
           for the configurable window.
        2. Secondary: checked_in status only (no date filter)
           to capture long-stay active reservations whose
           checkIn predates the date window.

        Results are merged and de-duplicated by reservation ID.
        Reservations missing required fields (_id, listingId,
        status, checkIn, checkOut) are skipped with a warning.

        Args:
            past_days: Days in the past for the check-in
                date filter window. Default 30.
            future_days: Days in the future for the check-in
                date filter window. Default 365.
            statuses: Set of reservation statuses to include.
                Defaults to ACTIONABLE_STATUSES if None.
            candidates: Accepted for the source seam and ignored.
                The not-found fallback corrects the v3 path only;
                on the legacy path this search already is the
                legacy resource (FR-050).

        Returns:
            The de-duplicated reservations from both requests and
            the diagnostics counters the legacy payloads support.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        if statuses is None:
            statuses = ACTIONABLE_STATUSES

        now = datetime.now(UTC)
        past_boundary = (now - timedelta(days=past_days)).strftime(
            "%Y-%m-%d",
        )
        future_boundary = (now + timedelta(days=future_days)).strftime(
            "%Y-%m-%d",
        )

        primary_params = _build_reservation_params(
            past_boundary=past_boundary,
            future_boundary=future_boundary,
            statuses=statuses,
        )
        primary, primary_pages = await self._fetch_all_reservations(
            primary_params,
        )

        secondary_params = _build_checked_in_params()
        secondary, secondary_pages = await self._fetch_all_reservations(
            secondary_params,
        )

        merged = _merge_reservations(primary, secondary)
        return ReservationFetchResult(
            reservations=tuple(merged),
            pages=primary_pages + secondary_pages,
            status_counts=MappingProxyType(
                dict(
                    Counter(
                        res.status for res in merged if isinstance(res.status, str)
                    ),
                ),
            ),
        )

    async def _fetch_all_reservations(
        self,
        params: dict[str, str | int],
    ) -> tuple[list[GuestyReservation], int]:
        """Paginate through all reservation pages.

        Args:
            params: Query parameters for the Guesty API
                reservations endpoint.

        Returns:
            Tuple of the parsed reservations and the page count.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        all_reservations: list[GuestyReservation] = []
        skip = 0
        pages = 0
        fields = " ".join(RESERVATIONS_FIELDS)

        while True:
            page_params: dict[str, str | int] = {
                **params,
                "limit": RESERVATIONS_PAGE_SIZE,
                "skip": skip,
                "fields": fields,
                "sort": "_id",
            }
            response = await self._client._request(
                "GET",
                RESERVATIONS_ENDPOINT,
                params=page_params,
            )
            pages += 1

            if not response.is_success:
                raise GuestyResponseError(
                    f"Reservations fetch failed: status {response.status_code}",
                )

            try:
                data = response.json()
            except Exception as exc:
                raise GuestyResponseError(
                    "Reservations response is not valid JSON",
                ) from exc

            if not isinstance(data, dict):
                raise GuestyResponseError(
                    "Reservations response must be a JSON object",
                )

            results = data.get("results")
            if results is None:
                raise GuestyResponseError(
                    "Reservations response missing results",
                )

            if not isinstance(results, list):
                raise GuestyResponseError(
                    "Reservations results must be a list",
                )

            page = GuestyReservationsResponse.from_api_dict(data)
            all_reservations.extend(page.results)

            if skip == 0 and results and _LOGGER.isEnabledFor(logging.DEBUG):
                sample_item = results[0]
                sample_keys: list[str] | str = (
                    sorted(sample_item.keys())
                    if isinstance(sample_item, dict)
                    else "non-dict"
                )
                guest_item = (
                    sample_item.get("guest", {})
                    if isinstance(sample_item, dict)
                    else "non-dict"
                )
                guest_keys: list[str] | str = (
                    sorted(guest_item.keys())
                    if isinstance(guest_item, dict)
                    else "non-dict"
                )
                _LOGGER.debug(
                    "Sample reservation keys: %s, guest keys: %s",
                    sample_keys,
                    guest_keys,
                )

            if len(results) < RESERVATIONS_PAGE_SIZE:
                break

            skip += RESERVATIONS_PAGE_SIZE

        return all_reservations, pages


def _build_reservation_params(
    *,
    past_boundary: str,
    future_boundary: str,
    statuses: frozenset[str],
) -> dict[str, str | int]:
    """Build query parameters for the primary reservation request.

    Args:
        past_boundary: YYYY-MM-DD start of the date window.
        future_boundary: YYYY-MM-DD end of the date window.
        statuses: Set of statuses to include.

    Returns:
        Dict of query parameters for the Guesty API.
    """
    return {
        "checkIn": past_boundary,
        "checkOut": future_boundary,
        "status": ",".join(sorted(statuses)),
    }


def _build_checked_in_params() -> dict[str, str | int]:
    """Build query parameters for the secondary checked_in request.

    Returns:
        Dict of query parameters selecting only checked_in status.
    """
    return {
        "status": "checked_in",
    }


def _merge_reservations(
    primary: list[GuestyReservation],
    secondary: list[GuestyReservation],
) -> list[GuestyReservation]:
    """Merge and de-duplicate two reservation lists by ID.

    Primary results take precedence. Secondary results are
    added only if their ID is not already present.

    Args:
        primary: Reservations from the date-range request.
        secondary: Reservations from the checked_in request.

    Returns:
        De-duplicated list of reservations.
    """
    seen: set[str] = set()
    merged: list[GuestyReservation] = []
    for reservation in primary:
        if reservation.id not in seen:
            seen.add(reservation.id)
            merged.append(reservation)
    for reservation in secondary:
        if reservation.id not in seen:
            seen.add(reservation.id)
            merged.append(reservation)
    return merged
