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
from typing import TYPE_CHECKING, Any

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
    ReservationFetchResult,
)
from custom_components.guesty.api.validation import (
    ValidationTally,
    assess_legacy_record,
    build_legacy_reservation,
    recognize_v3_status,
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

        Raw items are merged and de-duplicated first-wins by ``_id``
        before assessment. An item without a non-empty string ``_id``
        fails the refresh closed. Every other record is assessed: a
        rejected record is isolated and reported by identifier in
        ``rejected_ids``, never logged by value.

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
        reservations: list[GuestyReservation] = []
        status_counts: Counter[str] = Counter()
        tally = ValidationTally()
        for item in merged:
            status_counts[
                recognize_v3_status(item.get("status")) or "unrecognized"
            ] += 1
            assessment = assess_legacy_record(item)
            reservation = build_legacy_reservation(item, assessment)
            if reservation is not None:
                reservations.append(reservation)
                continue
            tally.add(
                assessment.reservation_id,
                reason=assessment.reason,
                partial=False,
                segments=(),
                conflict_code=assessment.conflict_code,
                raw_check_out=item.get("checkOut"),
            )
        return ReservationFetchResult(
            reservations=tuple(reservations),
            pages=primary_pages + secondary_pages,
            status_counts=MappingProxyType(dict(status_counts)),
            observed_ids=tuple(reservation.id for reservation in reservations),
            rejected_ids=tally.sorted_rejected_ids(),
            rejected_codes=tally.sorted_rejected_codes(),
            rejected_check_out_bounds=tally.check_out_bounds(),
            validation=tally.summary(),
            exclusions=tally.sorted_exclusions(),
        )

    async def _fetch_all_reservations(
        self,
        params: dict[str, str | int],
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginate through all reservation pages.

        Every item must be a dict with a non-empty string ``_id``;
        otherwise the refresh fails closed, because a record without a
        verified identifier cannot be isolated.

        Args:
            params: Query parameters for the Guesty API
                reservations endpoint.

        Returns:
            Tuple of the raw identifier-verified items and the page
            count.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        all_reservations: list[dict[str, Any]] = []
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

            for item in results:
                if not isinstance(item, dict):
                    raise GuestyResponseError(
                        "Reservations results must be objects",
                    )
                reservation_id = item.get("_id")
                if not isinstance(reservation_id, str) or not reservation_id:
                    raise GuestyResponseError(
                        "Reservation is missing a string _id",
                    )
                all_reservations.append(item)

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
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge and de-duplicate two raw item lists by ``_id``.

    The first item per ``_id`` wins, primary before secondary. It runs
    before assessment, so a rejected record and an accepted duplicate
    can never both survive.

    Args:
        primary: Raw items from the date-range request.
        secondary: Raw items from the checked_in request.

    Returns:
        De-duplicated list of raw items.
    """
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for item in (*primary, *secondary):
        reservation_id = item["_id"]
        if reservation_id not in seen:
            seen.add(reservation_id)
            merged.append(item)
    return merged
