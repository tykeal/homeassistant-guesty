# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Guesty API requests."""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime, timedelta

import httpx

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    BACKOFF_MULTIPLIER,
    BASE_URL,
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    INITIAL_BACKOFF,
    LISTINGS_ENDPOINT,
    LISTINGS_FIELDS,
    LISTINGS_PAGE_SIZE,
    MAX_BACKOFF,
    MAX_RETRIES,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_FIELDS,
    RESERVATIONS_PAGE_SIZE,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyRateLimitError,
    GuestyResponseError,
)
from custom_components.guesty.api.models import (
    GuestyListing,
    GuestyListingsResponse,
    GuestyReservation,
    GuestyReservationsResponse,
)

_LOGGER = logging.getLogger(__name__)


class GuestyApiClient:
    """HTTP client for authenticated Guesty API requests.

    Wraps httpx.AsyncClient (injected) with automatic token
    management, exponential backoff on 429 responses, and
    reactive token refresh on 401 responses.

    Attributes:
        _token_manager: Token provider for authentication.
        _http: Injected async HTTP client.
        _base_url: Guesty API base URL.
    """

    def __init__(
        self,
        token_manager: GuestyTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        """Initialize GuestyApiClient.

        Args:
            token_manager: Token manager for authentication.
            http_client: Async HTTP client for API requests.
            base_url: Guesty API base URL.
        """
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url

    async def test_connection(self) -> bool:
        """Validate credentials and API access.

        Acquires a token and makes a lightweight API call to verify
        the credentials work end-to-end.

        Returns:
            True if connection test succeeds.

        Raises:
            GuestyAuthError: If credentials are invalid.
            GuestyConnectionError: If the API is unreachable.
            GuestyRateLimitError: If rate limited.
            GuestyResponseError: If the API returns unexpected data.
        """
        response = await self._request(
            "GET",
            "/listings",
            params={"limit": 1, "fields": "_id"},
        )
        if not response.is_success:
            raise GuestyResponseError(
                f"Connection test failed: status {response.status_code}",
            )
        return True

    async def get_listings(self) -> list[GuestyListing]:
        """Fetch all listings with automatic pagination.

        Iterates through all pages of the Guesty listings
        endpoint, requesting ``LISTINGS_PAGE_SIZE`` listings per
        page. Listings missing a valid ``_id`` field are skipped
        with a warning log.

        Returns:
            Complete list of valid GuestyListing objects.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        all_listings: list[GuestyListing] = []
        skip = 0
        fields = " ".join(LISTINGS_FIELDS)

        while True:
            response = await self._request(
                "GET",
                LISTINGS_ENDPOINT,
                params={
                    "limit": LISTINGS_PAGE_SIZE,
                    "skip": skip,
                    "fields": fields,
                },
            )

            if not response.is_success:
                raise GuestyResponseError(
                    f"Listings fetch failed: status {response.status_code}",
                )

            try:
                data = response.json()
            except Exception as exc:
                raise GuestyResponseError(
                    "Listings response is not valid JSON",
                ) from exc

            if not isinstance(data, dict):
                raise GuestyResponseError(
                    "Listings response must be a JSON object",
                )

            results = data.get("results")
            if results is None:
                raise GuestyResponseError(
                    "Listings response missing results",
                )

            if not isinstance(results, list):
                raise GuestyResponseError(
                    "Listings results must be a list",
                )

            page = GuestyListingsResponse.from_api_dict(data)
            all_listings.extend(page.results)

            if len(results) < LISTINGS_PAGE_SIZE:
                break

            skip += LISTINGS_PAGE_SIZE

        return all_listings

    async def get_reservations(
        self,
        *,
        past_days: int = DEFAULT_PAST_DAYS,
        future_days: int = DEFAULT_FUTURE_DAYS,
        statuses: frozenset[str] | None = None,
    ) -> list[GuestyReservation]:
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

        Returns:
            De-duplicated list of valid GuestyReservation
            objects from both requests.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        if statuses is None:
            statuses = ACTIONABLE_STATUSES

        now = datetime.now(UTC)
        past_boundary = (now - timedelta(days=past_days)).isoformat()
        future_boundary = (now + timedelta(days=future_days)).isoformat()

        primary_filters = _build_reservation_filters(
            past_boundary=past_boundary,
            future_boundary=future_boundary,
            statuses=statuses,
        )
        primary = await self._fetch_all_reservations(
            primary_filters,
        )

        secondary_filters = _build_checked_in_filters()
        secondary = await self._fetch_all_reservations(
            secondary_filters,
        )

        return _merge_reservations(primary, secondary)

    async def _fetch_all_reservations(
        self,
        filters: list[dict[str, object]],
    ) -> list[GuestyReservation]:
        """Paginate through all reservation pages.

        Args:
            filters: JSON-serializable filter list for the
                Guesty API filters parameter.

        Returns:
            List of parsed GuestyReservation objects.

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        all_reservations: list[GuestyReservation] = []
        skip = 0
        fields = " ".join(RESERVATIONS_FIELDS)
        filters_json = json.dumps(filters)

        while True:
            response = await self._request(
                "GET",
                RESERVATIONS_ENDPOINT,
                params={
                    "limit": RESERVATIONS_PAGE_SIZE,
                    "skip": skip,
                    "fields": fields,
                    "sort": "_id",
                    "filters": filters_json,
                },
            )

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

            if len(results) < RESERVATIONS_PAGE_SIZE:
                break

            skip += RESERVATIONS_PAGE_SIZE

        return all_reservations

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_data: dict[str, object] | None = None,
        _retried_auth: bool = False,
    ) -> httpx.Response:
        """Make an authenticated API request with retry logic.

        Adds Authorization header, retries on 429 and transient
        failures (connect/timeout errors, 5xx responses) with
        exponential backoff, and reactively refreshes token on 401.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path relative to base URL.
            params: Optional query parameters.
            json_data: Optional JSON body.
            _retried_auth: Internal flag to prevent infinite 401 retry.

        Returns:
            The httpx.Response from the API.

        Raises:
            GuestyAuthError: On persistent 401 after token refresh.
            GuestyConnectionError: On network failures after retries.
            GuestyRateLimitError: On 429 after max retries.
            GuestyResponseError: On unexpected response formats.
        """
        import asyncio

        url = f"{self._base_url}{path}"
        backoff = INITIAL_BACKOFF

        for attempt in range(MAX_RETRIES + 1):
            token = await self._token_manager.get_token()

            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json_data,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt >= MAX_RETRIES:
                    raise GuestyConnectionError(
                        f"Failed to connect to Guesty API "
                        f"after {MAX_RETRIES} retries: {exc}",
                    ) from exc

                delay = _calculate_transient_backoff(backoff)
                _LOGGER.warning(
                    "Transient error, retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(delay)
                backoff = min(
                    backoff * BACKOFF_MULTIPLIER,
                    MAX_BACKOFF,
                )
                continue

            if response.status_code == 401:
                if _retried_auth:
                    raise GuestyAuthError(
                        "Authentication failed after token refresh",
                    )
                self._token_manager.invalidate()
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_data=json_data,
                    _retried_auth=True,
                )

            if response.status_code == 429:
                if attempt >= MAX_RETRIES:
                    retry_after = _parse_retry_after(response)
                    raise GuestyRateLimitError(
                        "Rate limit exceeded after max retries",
                        retry_after=retry_after,
                    )

                delay = _calculate_backoff(
                    backoff,
                    response,
                )
                _LOGGER.warning(
                    "Rate limited, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                backoff = min(
                    backoff * BACKOFF_MULTIPLIER,
                    MAX_BACKOFF,
                )
                continue

            if response.status_code == 403:
                raise GuestyAuthError(
                    "Insufficient permissions for this API endpoint",
                )

            if _is_transient_5xx(response.status_code):
                if attempt >= MAX_RETRIES:
                    raise GuestyConnectionError(
                        f"Server error {response.status_code} "
                        f"after {MAX_RETRIES} retries",
                    )

                delay = _calculate_transient_backoff(backoff)
                _LOGGER.warning(
                    "Server error %d, retrying in %.1fs (attempt %d/%d)",
                    response.status_code,
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                backoff = min(
                    backoff * BACKOFF_MULTIPLIER,
                    MAX_BACKOFF,
                )
                continue

            return response

        # Should not reach here, but just in case
        raise GuestyResponseError(  # pragma: no cover
            "Request loop exited without returning",
        )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After header from a 429 response.

    Args:
        response: The HTTP response to parse.

    Returns:
        The retry-after value in seconds, or None if not present.
    """
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _calculate_backoff(
    base_backoff: float,
    response: httpx.Response,
) -> float:
    """Calculate backoff delay with jitter and Retry-After support.

    The base_backoff is already the desired delay for this attempt
    (the caller handles exponential growth). We just apply jitter
    and Retry-After override.

    Args:
        base_backoff: Current backoff delay in seconds.
        response: The HTTP response (may contain Retry-After).

    Returns:
        Delay in seconds before next retry.
    """
    retry_after = _parse_retry_after(response)
    if retry_after is not None:
        return max(0.1, min(retry_after, MAX_BACKOFF))

    return _jittered_delay(base_backoff)


def _jittered_delay(base_backoff: float) -> float:
    """Apply ±25% jitter to a base backoff delay.

    Args:
        base_backoff: Current backoff delay in seconds.

    Returns:
        Delay in seconds with jitter applied, minimum 0.1s.
    """
    delay = min(base_backoff, MAX_BACKOFF)
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return max(0.1, delay + jitter)


def _calculate_transient_backoff(base_backoff: float) -> float:
    """Calculate backoff delay with jitter for transient errors.

    Args:
        base_backoff: Current backoff delay in seconds.

    Returns:
        Delay in seconds before next retry.
    """
    return _jittered_delay(base_backoff)


_TRANSIENT_STATUS_CODES: frozenset[int] = frozenset(
    {500, 502, 503, 504},
)


def _is_transient_5xx(status_code: int) -> bool:
    """Check if an HTTP status code is a transient server error.

    Args:
        status_code: HTTP response status code.

    Returns:
        True if the status code is a retryable 5xx error.
    """
    return status_code in _TRANSIENT_STATUS_CODES


def _build_reservation_filters(
    *,
    past_boundary: str,
    future_boundary: str,
    statuses: frozenset[str],
) -> list[dict[str, object]]:
    """Build filter list for the primary reservation request.

    Args:
        past_boundary: ISO 8601 start of the date window.
        future_boundary: ISO 8601 end of the date window.
        statuses: Set of statuses to include.

    Returns:
        List of filter dicts for the Guesty filters parameter.
    """
    return [
        {
            "field": "checkIn",
            "operator": "$between",
            "from": past_boundary,
            "to": future_boundary,
        },
        {
            "field": "status",
            "operator": "$contains",
            "value": sorted(statuses),
        },
    ]


def _build_checked_in_filters() -> list[dict[str, object]]:
    """Build filter list for the secondary checked_in request.

    Returns:
        List of filter dicts selecting only checked_in status.
    """
    return [
        {
            "field": "status",
            "operator": "$eq",
            "value": "checked_in",
        },
    ]


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
