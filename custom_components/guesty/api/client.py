# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""HTTP client for authenticated Guesty API requests."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.const import (
    ACCOUNTS_ME_ENDPOINT,
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
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
)
from custom_components.guesty.api.exceptions import (
    GuestyAuthError,
    GuestyConnectionError,
    GuestyPermissionError,
    GuestyRateLimitError,
    GuestyRecoveryAuthError,
    GuestyRecoveryConnectionError,
    GuestyRecoveryPermissionError,
    GuestyRecoveryPreSendConnectionError,
    GuestyRecoveryTokenConnectionError,
    GuestyRecoveryTokenError,
    GuestyResponseError,
)
from custom_components.guesty.api.guest_cache import (
    GuestCache,
    GuestLookupResult,
)
from custom_components.guesty.api.models import (
    FallbackCandidate,
    GuestyListing,
    GuestyListingsResponse,
    ReservationFetchResult,
    ReservationSource,
)
from custom_components.guesty.api.reservations_fallback import (
    ReservationFallbackSource,
)
from custom_components.guesty.api.reservations_legacy import (
    LegacyReservationSource,
)
from custom_components.guesty.api.reservations_v3 import (
    V3ReservationSource,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _PrivateHttpScope:
    """Track a privacy scope shared by copied asynchronous contexts."""

    active: bool = True


_PRIVATE_HTTP_LOGS: ContextVar[tuple[_PrivateHttpScope, ...]] = ContextVar(
    "guesty_private_http_logs", default=()
)


def _recovery_transport_failure(
    exc: httpx.TransportError,
) -> GuestyRecoveryConnectionError:
    """Classify safe connection setup failures separately from uncertain writes."""
    if isinstance(
        exc,
        (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ProxyError),
    ):
        return GuestyRecoveryPreSendConnectionError(
            "The Guesty connection failed; the reservation write was not sent "
            "and was not retried.",
        )
    return GuestyRecoveryConnectionError(
        "The reservation write may have reached Guesty; it was "
        "not retried. Check Guesty before retrying.",
    )


class _PrivateHttpLogFilter(logging.Filter):
    """Keep HTTP transport details out of privacy-scoped logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Allow unrelated tasks' transport logs through unchanged."""
        return not any(scope.active for scope in _PRIVATE_HTTP_LOGS.get())


_PRIVATE_HTTP_LOG_FILTER = _PrivateHttpLogFilter()


@contextmanager
def private_http_logging(enabled: bool) -> Iterator[None]:
    """Suppress sensitive HTTP library logs only in this asynchronous context."""
    if not enabled:
        yield
        return
    for name in (
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logging.getLogger(name).addFilter(_PRIVATE_HTTP_LOG_FILTER)
    scope = _PrivateHttpScope()
    token = _PRIVATE_HTTP_LOGS.set((*_PRIVATE_HTTP_LOGS.get(), scope))
    try:
        yield
    finally:
        scope.active = False
        _PRIVATE_HTTP_LOGS.reset(token)


_RESERVATION_API_VALUES: frozenset[str] = frozenset(
    {RESERVATIONS_API_V3, RESERVATIONS_API_LEGACY},
)


def resolve_reservations_api(value: object) -> str:
    """Resolve a stored reservation API selection to a known value.

    Args:
        value: The stored selection, from config or elsewhere.

    Returns:
        The value itself when recognized, otherwise ``v3``.
    """
    if isinstance(value, str) and value in _RESERVATION_API_VALUES:
        return value
    _LOGGER.warning(
        "Unrecognized reservations_api value %r; using %s",
        value,
        RESERVATIONS_API_V3,
    )
    return RESERVATIONS_API_V3


class GuestyApiClient:
    """HTTP client for authenticated Guesty API requests.

    Wraps httpx.AsyncClient (injected) with automatic token
    management, exponential backoff on 429 responses, and
    reactive token refresh on 401 responses.

    Attributes:
        _token_manager: Token provider for authentication.
        _http: Injected async HTTP client.
        _base_url: Guesty API base URL.
        _reservations_api: Selected reservation API generation.
        _reservation_source: Strategy serving reservation reads.
        _guest_cache: Guest record cache, on the v3 path only.
    """

    def __init__(
        self,
        token_manager: GuestyTokenManager,
        http_client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
        reservations_api: str = RESERVATIONS_API_LEGACY,
    ) -> None:
        """Initialize GuestyApiClient.

        Args:
            token_manager: Token manager for authentication.
            http_client: Async HTTP client for API requests.
            base_url: Guesty API base URL.
            reservations_api: Reservation API generation to use,
                either ``v3`` or ``legacy``. An unrecognized value
                resolves to ``v3`` with a warning.
        """
        self._token_manager = token_manager
        self._http = http_client
        self._base_url = base_url
        self._reservations_api = resolve_reservations_api(
            reservations_api,
        )
        self._reservation_source: ReservationSource = self._build_reservation_source()
        self._guest_cache: GuestCache | None = (
            GuestCache(self) if self._reservations_api == RESERVATIONS_API_V3 else None
        )

    @property
    def reservations_api(self) -> str:
        """Return the resolved reservation API generation.

        Returns:
            Either ``v3`` or ``legacy``.
        """
        return self._reservations_api

    @property
    def _fallback(self) -> ReservationFallbackSource | None:
        """Return the not-found fallback, when one exists.

        Returns:
            The v3 source's fallback, or None on the legacy path
            where the legacy resource is already primary.
        """
        source = self._reservation_source
        if isinstance(source, V3ReservationSource):
            return source.fallback
        return None

    @property
    def legacy_write_routing_available(self) -> bool:
        """Report whether a write may still be routed to legacy.

        Routing shares the fallback's self-disable: once a legacy
        answer is read as retirement, neither reads nor writes use
        that generation again this session (FR-056, FR-061).

        On the legacy path there is no fallback and the legacy
        resource is the primary one, so nothing is disabled.

        Returns:
            True while a legacy-routed write may be issued.
        """
        fallback = self._fallback
        return fallback is None or not fallback.disabled

    def disable_legacy_write_routing(self) -> None:
        """Self-disable the legacy generation for this session."""
        fallback = self._fallback
        if fallback is not None:
            fallback.note_retirement()

    def retire_fallback_candidate(self, reservation_id: str) -> None:
        """Retire a candidate a legacy write reported is gone.

        Args:
            reservation_id: The reservation legacy denied.
        """
        fallback = self._fallback
        if fallback is not None:
            fallback.retire_candidate(reservation_id)

    @property
    def confirmed_absent_reservations(self) -> frozenset[str]:
        """Return the identifiers legacy confirmed no longer exist.

        Returns:
            The retired fallback candidates of this session.
        """
        fallback = self._fallback
        return fallback.confirmed_absent if fallback is not None else frozenset()

    @property
    def legacy_observed_reservations(self) -> frozenset[str] | None:
        """Return the IDs a legacy by-ID lookup returned this session.

        Returns:
            The session set, empty when no fallback exists, or None
            when it overflowed and is incomplete.
        """
        fallback = self._fallback
        return fallback.legacy_observed if fallback is not None else frozenset()

    @property
    def legacy_retired_reservations(self) -> frozenset[str] | None:
        """Return the IDs legacy retired this session.

        Returns:
            The session set, empty when no fallback exists, or None
            when it overflowed and is incomplete.
        """
        fallback = self._fallback
        return fallback.legacy_retired if fallback is not None else frozenset()

    def acknowledge_absent_reservations(
        self,
        reservation_ids: Collection[str],
    ) -> None:
        """Accept that these absences are durably retired.

        Args:
            reservation_ids: Identifiers whose retirement reached
                the persisted identity baseline.
        """
        fallback = self._fallback
        if fallback is not None:
            fallback.acknowledge_absent(reservation_ids)

    def _build_reservation_source(self) -> ReservationSource:
        """Select the reservation source for the resolved path.

        Returns:
            The reservation source to serve reads from.
        """
        if self._reservations_api == RESERVATIONS_API_V3:
            return V3ReservationSource(self)
        return LegacyReservationSource(self)

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

    async def get_account_id(self) -> str:
        """Fetch the authenticated account ID from the Guesty API.

        Returns:
            The account ID string.

        Raises:
            GuestyResponseError: If the API response is not
                successful, not valid JSON, or missing _id.
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
        """
        response = await self._request(
            "GET",
            ACCOUNTS_ME_ENDPOINT,
        )
        if not response.is_success:
            raise GuestyResponseError(
                f"Account lookup failed: status {response.status_code}",
            )
        try:
            data = response.json()
        except Exception as exc:
            raise GuestyResponseError(
                "Account response is not valid JSON",
            ) from exc
        if not isinstance(data, dict):
            raise GuestyResponseError(
                "Account response must be a JSON object",
            )
        account_id = data.get("_id")
        if not isinstance(account_id, str):
            raise GuestyResponseError(
                "Account response missing _id field",
            )
        normalized = account_id.strip()
        if not normalized:
            raise GuestyResponseError(
                "Account response missing _id field",
            )
        result: str = normalized
        return result

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

            if skip == 0 and results and _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Sample listing API keys: %s",
                    sorted(results[0].keys())
                    if isinstance(results[0], dict)
                    else "non-dict",
                )

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
        candidates: Mapping[str, FallbackCandidate] | None = None,
    ) -> ReservationFetchResult:
        """Fetch reservations from the selected reservation source.

        Delegates to the source chosen by ``reservations_api``;
        the retrieval behaviour is defined by that source.

        Args:
            past_days: Days in the past for the check-in
                date filter window. Default 30.
            future_days: Days in the future for the check-in
                date filter window. Default 365.
            statuses: Set of reservation statuses to include.
                Defaults to the source's own status set if None.
            candidates: Durable stay dates of every reservation the
                integration already knows, keyed by identifier.
                Used by the v3 source alone, to recover a
                reservation Guesty has not finished backfilling
                (FR-050); the legacy source ignores them.

        Returns:
            The de-duplicated reservations together with the
            per-refresh diagnostics counters (FR-042).

        Raises:
            GuestyAuthError: On authentication failure.
            GuestyConnectionError: On network failure.
            GuestyRateLimitError: On rate limit exhaustion.
            GuestyResponseError: On malformed API response.
        """
        return await self._reservation_source.fetch(
            past_days=past_days,
            future_days=future_days,
            statuses=statuses,
            candidates=candidates,
        )

    async def resolve_guests(
        self,
        guest_ids: Iterable[str],
    ) -> GuestLookupResult:
        """Resolve guest records for the ids of one refresh.

        Guest lookups exist on the v3 path only: the legacy
        reservation payloads embed the guest, so the legacy path
        resolves nothing and issues no request.

        A lookup failure never propagates, because a missing guest
        name must not cost a refresh (FR-020, NFR-005).

        Args:
            guest_ids: Guest ids the refresh needs, with repeats.

        Returns:
            The known guest records and this refresh's counters.
        """
        if self._guest_cache is None:
            return GuestLookupResult()
        return await self._guest_cache.resolve(guest_ids)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json_data: dict[str, object] | list[object] | None = None,
        single_attempt: bool = False,
        before_send: Callable[[], None] | None = None,
        recovery_write: bool = False,
        _retried_auth: bool = False,
    ) -> httpx.Response:
        """Make an authenticated API request with retry logic.

        Adds Authorization header, retries on 429 and transient
        failures (connect/timeout errors, 5xx responses) with
        exponential backoff, and reactively refreshes token on 401.

        ``single_attempt`` is the opt-in mode FR-063 requires of a
        legacy-routed reservation write: the request is issued once
        and its answer returned whatever it is, so one invocation
        can never become several writes against a third-party
        system. It defaults to off, so no other caller's retry
        behaviour changes.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: API path relative to base URL.
            params: Optional query parameters.
            json_data: Optional JSON body (dict or list).
            single_attempt: Issue the request exactly once, with no
                transient, rate-limit or token retry.
            before_send: Recheck authorization after token acquisition.
            recovery_write: Apply recovery-only failure handling and no retries.
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
        single_attempt = single_attempt or recovery_write
        max_attempts = 0 if single_attempt else MAX_RETRIES

        for attempt in range(max_attempts + 1):
            try:
                with private_http_logging(recovery_write):
                    token = await self._token_manager.get_token()
            except GuestyPermissionError:
                if not recovery_write:
                    raise
                raise GuestyRecoveryPermissionError(
                    "Insufficient permission to acquire a Guesty token; "
                    "the write was not sent.",
                    stage="token",
                ) from None
            except GuestyAuthError:
                if not recovery_write:
                    raise
                raise GuestyRecoveryAuthError(
                    "Guesty authentication failed; the write was not sent.",
                    stage="token",
                ) from None
            except GuestyConnectionError:
                if not recovery_write:
                    raise
                raise GuestyRecoveryTokenConnectionError(
                    "Guesty token connection failed; the write was not sent.",
                ) from None
            except GuestyRateLimitError:
                if not recovery_write:
                    raise
                raise GuestyRecoveryTokenError(rate_limited=True) from None
            except httpx.TransportError:
                if not recovery_write:
                    raise
                raise GuestyRecoveryTokenConnectionError(
                    "Guesty token connection failed; the write was not sent.",
                ) from None
            except Exception:
                if not recovery_write:
                    raise
                raise GuestyRecoveryTokenError() from None
            if recovery_write and (
                not token
                or not token.isascii()
                or token[-1] in " \t"
                or any(ord(char) < 32 or ord(char) == 127 for char in token)
            ):
                raise GuestyRecoveryAuthError(
                    "Guesty supplied an invalid token; the write was not sent.",
                    stage="token",
                ) from None
            if before_send is not None:
                before_send()

            try:
                with private_http_logging(recovery_write):
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
                if recovery_write:
                    raise _recovery_transport_failure(exc) from None
                if single_attempt:
                    raise GuestyConnectionError(
                        f"Failed to connect to Guesty API: {exc}",
                    ) from exc
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
                    type(exc).__name__,
                )
                await asyncio.sleep(delay)
                backoff = min(
                    backoff * BACKOFF_MULTIPLIER,
                    MAX_BACKOFF,
                )
                continue
            except httpx.TransportError as exc:
                if not recovery_write:
                    raise
                raise _recovery_transport_failure(exc) from None

            if response.status_code == 401:
                if recovery_write:
                    raise GuestyRecoveryAuthError(
                        "Guesty authentication failed for the reservation write; "
                        "the write was not retried.",
                        stage="write",
                    ) from None
                if single_attempt:
                    return response
                if _retried_auth:
                    raise GuestyAuthError(
                        "Authentication failed after token refresh",
                    )
                self._token_manager.invalidate(token)
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_data=json_data,
                    _retried_auth=True,
                )

            if response.status_code == 429:
                if single_attempt:
                    return response
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
                if recovery_write:
                    raise GuestyRecoveryPermissionError(
                        "Insufficient permission for the reservation write; "
                        "the write was not retried.",
                        stage="write",
                    ) from None
                raise GuestyPermissionError(
                    "Insufficient permissions for this API endpoint",
                )

            if _is_transient_5xx(response.status_code):
                if single_attempt:
                    return response
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
