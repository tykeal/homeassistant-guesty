# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the bounded guest lookup cache.

Every payload here is a synthetic dict; no live Guesty payload
exists and the ``guests-crud`` resource itself is documentation
derived and unverified (OQ-004, NFR-008).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    BASE_URL,
    GUEST_CACHE_MAX_ENTRIES,
    GUEST_CACHE_MISS_TTL_SECONDS,
    GUEST_CACHE_TTL_SECONDS,
    GUEST_FIELDS,
    GUEST_LOOKUP_CONCURRENCY,
    MAX_GUEST_LOOKUPS_PER_REFRESH,
    RESERVATIONS_API_V3,
    TOKEN_URL,
)
from custom_components.guesty.api.guest_cache import GuestCache
from custom_components.guesty.api.models import GuestyGuest
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeTokenStorage,
    make_token_response,
)


class _FakeClock:
    """A monotonic clock the tests advance by hand.

    Attributes:
        now: The current reading, in seconds.
    """

    def __init__(self, start: float = 1000.0) -> None:
        """Initialize the clock.

        Args:
            start: The initial reading.
        """
        self.now = start

    def __call__(self) -> float:
        """Return the current reading.

        Returns:
            The clock's current value in seconds.
        """
        return self.now

    def advance(self, seconds: float) -> None:
        """Advance the clock.

        Args:
            seconds: Seconds to add to the reading.
        """
        self.now += seconds


def _mock_token() -> None:
    """Register the OAuth token route used by every request."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


def _make_cache() -> tuple[GuestCache, _FakeClock]:
    """Create a guest cache backed by a v3 test client.

    Returns:
        Tuple of the cache and the clock it reads.
    """
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=http,
        storage=FakeTokenStorage(),
        refresh_buffer=0,
    )
    client = GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        reservations_api=RESERVATIONS_API_V3,
    )
    clock = _FakeClock()
    return GuestCache(client, clock=clock), clock


def _guest_url(guest_id: str) -> str:
    """Build the guest lookup URL for one guest.

    Args:
        guest_id: The guest identifier being looked up.

    Returns:
        The absolute URL the cache is expected to request.
    """
    return f"{BASE_URL}/guests-crud/{guest_id}"


def _guest_body(guest_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a synthetic guests-crud response body.

    Args:
        guest_id: The guest identifier echoed by the fixture.
        **overrides: Fields to override on the defaults.

    Returns:
        A dict matching the consumed guests-crud response.
    """
    body: dict[str, Any] = {
        "_id": guest_id,
        "fullName": "Ada Lovelace",
        "phone": "+1-555-0100",
        "email": "ada@example.com",
    }
    body.update(overrides)
    return body


class TestGuestCacheFreshness:
    """Tests for the hit, expiry and negative-entry TTLs (T053)."""

    @respx.mock
    async def test_fresh_hit_issues_no_request(self) -> None:
        """A hit inside the TTL is served without a request."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            return_value=Response(200, json=_guest_body("guest-001")),
        )
        cache, clock = _make_cache()

        first = await cache.resolve(["guest-001"])
        clock.advance(GUEST_CACHE_TTL_SECONDS - 1)
        second = await cache.resolve(["guest-001"])

        assert route.call_count == 1
        assert first.lookups == 1
        assert second.lookups == 0
        assert second.hits == 1
        assert second.guests["guest-001"].full_name == "Ada Lovelace"

    @respx.mock
    async def test_expired_hit_refetches_and_replaces(self) -> None:
        """An expired entry is refetched and its value replaced."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            side_effect=[
                Response(200, json=_guest_body("guest-001")),
                Response(
                    200,
                    json=_guest_body("guest-001", fullName="Ada Byron"),
                ),
            ],
        )
        cache, clock = _make_cache()

        await cache.resolve(["guest-001"])
        clock.advance(GUEST_CACHE_TTL_SECONDS + 1)
        second = await cache.resolve(["guest-001"])

        assert route.call_count == 2
        assert second.guests["guest-001"].full_name == "Ada Byron"

    @respx.mock
    async def test_not_found_stores_a_negative_entry(self) -> None:
        """A 404 is cached for the miss TTL and not refetched."""
        _mock_token()
        route = respx.get(_guest_url("guest-404")).mock(
            return_value=Response(404, json={"message": "not found"}),
        )
        cache, clock = _make_cache()

        first = await cache.resolve(["guest-404"])
        clock.advance(GUEST_CACHE_MISS_TTL_SECONDS - 1)
        second = await cache.resolve(["guest-404"])

        assert route.call_count == 1
        assert first.guests == {}
        assert second.guests == {}
        assert second.lookups == 0

    @respx.mock
    async def test_negative_entry_expires_after_the_miss_ttl(
        self,
    ) -> None:
        """A negative entry older than the miss TTL is retried."""
        _mock_token()
        route = respx.get(_guest_url("guest-404")).mock(
            side_effect=[
                Response(404, json={"message": "not found"}),
                Response(200, json=_guest_body("guest-404")),
            ],
        )
        cache, clock = _make_cache()

        await cache.resolve(["guest-404"])
        clock.advance(GUEST_CACHE_MISS_TTL_SECONDS + 1)
        second = await cache.resolve(["guest-404"])

        assert route.call_count == 2
        assert second.guests["guest-404"].full_name == "Ada Lovelace"


class TestGuestCacheBounds:
    """Tests for the LRU bound, burst cap and dedup (T053)."""

    @respx.mock
    async def test_lru_evicts_rather_than_growing(self) -> None:
        """The cache never holds more than its maximum entries."""
        _mock_token()
        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=lambda request: Response(
                200,
                json=_guest_body(request.url.path.rsplit("/", 1)[-1]),
            ),
        )
        cache, _clock = _make_cache()

        total = GUEST_CACHE_MAX_ENTRIES + 10
        ids = [f"guest-{index:05d}" for index in range(total)]
        for start in range(0, total, MAX_GUEST_LOOKUPS_PER_REFRESH):
            await cache.resolve(
                ids[start : start + MAX_GUEST_LOOKUPS_PER_REFRESH],
            )

        assert len(cache) == GUEST_CACHE_MAX_ENTRIES
        assert ids[0] not in cache
        assert ids[-1] in cache

    @respx.mock
    async def test_burst_cap_defers_the_remainder(self) -> None:
        """Only the capped number of lookups is issued per refresh."""
        _mock_token()
        route = respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=lambda request: Response(
                200,
                json=_guest_body(request.url.path.rsplit("/", 1)[-1]),
            ),
        )
        cache, _clock = _make_cache()

        over = MAX_GUEST_LOOKUPS_PER_REFRESH + 7
        ids = [f"guest-{index:05d}" for index in range(over)]

        first = await cache.resolve(ids)
        assert route.call_count == MAX_GUEST_LOOKUPS_PER_REFRESH
        assert first.lookups == MAX_GUEST_LOOKUPS_PER_REFRESH
        assert first.deferrals == 7

        second = await cache.resolve(ids)
        assert second.lookups == 7
        assert second.deferrals == 0
        assert len(second.guests) == over

    @respx.mock
    async def test_deferred_guests_are_not_starved(self) -> None:
        """A deferred guest is served before one already cached."""
        _mock_token()
        requested: list[str] = []

        def _handler(request: httpx.Request) -> Response:
            """Record and serve one guest lookup.

            Args:
                request: The outgoing guest lookup request.

            Returns:
                A synthetic guests-crud response.
            """
            guest_id = request.url.path.rsplit("/", 1)[-1]
            requested.append(guest_id)
            return Response(200, json=_guest_body(guest_id))

        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=_handler,
        )
        cache, clock = _make_cache()

        ids = [
            f"guest-{index:05d}" for index in range(MAX_GUEST_LOOKUPS_PER_REFRESH + 3)
        ]
        await cache.resolve(ids)
        requested.clear()

        # Every cached entry is now stale, so the three guests that
        # were deferred compete with 50 expired entries.
        clock.advance(GUEST_CACHE_TTL_SECONDS + 1)
        await cache.resolve(ids)

        assert set(ids[-3:]) <= set(requested)

    @respx.mock
    async def test_one_lookup_per_distinct_guest(self) -> None:
        """Repeated ids in one refresh cost a single request."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            return_value=Response(200, json=_guest_body("guest-001")),
        )
        cache, _clock = _make_cache()

        result = await cache.resolve(
            ["guest-001", "guest-001", "guest-001"],
        )

        assert route.call_count == 1
        assert result.lookups == 1

    @respx.mock
    async def test_concurrency_is_bounded(self) -> None:
        """No more than the configured lookups are ever in flight."""
        _mock_token()
        in_flight = 0
        peak = 0

        async def _handler(request: httpx.Request) -> Response:
            """Track concurrent lookups while serving one guest.

            Args:
                request: The outgoing guest lookup request.

            Returns:
                A synthetic guests-crud response.
            """
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return Response(
                200,
                json=_guest_body(request.url.path.rsplit("/", 1)[-1]),
            )

        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=_handler,
        )
        cache, _clock = _make_cache()

        await cache.resolve([f"guest-{index:05d}" for index in range(12)])

        assert peak == GUEST_LOOKUP_CONCURRENCY


class TestGuestCacheContract:
    """Tests for the guests-crud request and mapping (T054)."""

    @respx.mock
    async def test_request_path_and_fields(self) -> None:
        """The documented path and fields projection are sent."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            return_value=Response(200, json=_guest_body("guest-001")),
        )
        cache, _clock = _make_cache()

        await cache.resolve(["guest-001"])

        request = route.calls[0].request
        assert request.url.path == "/v1/guests-crud/guest-001"
        assert request.url.params["fields"] == " ".join(GUEST_FIELDS)

    @respx.mock
    async def test_success_maps_into_the_guest_dataclass(self) -> None:
        """A 200 maps onto GuestyGuest with the requested id."""
        _mock_token()
        respx.get(_guest_url("guest-001")).mock(
            return_value=Response(
                200,
                json=_guest_body("guest-001", _id="other-id"),
            ),
        )
        cache, _clock = _make_cache()

        result = await cache.resolve(["guest-001"])

        assert result.guests["guest-001"] == GuestyGuest(
            full_name="Ada Lovelace",
            phone="+1-555-0100",
            email="ada@example.com",
            guest_id="guest-001",
        )

    @respx.mock
    async def test_failure_retains_the_previous_value(self) -> None:
        """A 500 keeps the last known guest and never raises."""
        _mock_token()
        respx.get(_guest_url("guest-001")).mock(
            side_effect=[
                Response(200, json=_guest_body("guest-001")),
                *[Response(500, text="boom") for _ in range(4)],
            ],
        )
        cache, clock = _make_cache()

        await cache.resolve(["guest-001"])
        clock.advance(GUEST_CACHE_TTL_SECONDS + 1)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cache.resolve(["guest-001"])

        assert result.failures == 1
        assert result.guests["guest-001"].full_name == "Ada Lovelace"

    @respx.mock
    async def test_transport_error_retains_the_previous_value(
        self,
    ) -> None:
        """A transport error degrades nothing but the lookup."""
        _mock_token()
        respx.get(_guest_url("guest-001")).mock(
            side_effect=[
                Response(200, json=_guest_body("guest-001")),
                httpx.ConnectError("no route to host"),
                httpx.ConnectError("no route to host"),
                httpx.ConnectError("no route to host"),
                httpx.ConnectError("no route to host"),
            ],
        )
        cache, clock = _make_cache()

        await cache.resolve(["guest-001"])
        clock.advance(GUEST_CACHE_TTL_SECONDS + 1)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cache.resolve(["guest-001"])

        assert result.failures == 1
        assert result.guests["guest-001"].full_name == "Ada Lovelace"

    @respx.mock
    async def test_rate_limited_lookup_retains_the_previous_value(
        self,
    ) -> None:
        """A post-retry 429 never fails the caller."""
        _mock_token()
        respx.get(_guest_url("guest-001")).mock(
            side_effect=[
                Response(200, json=_guest_body("guest-001")),
                *[Response(429, headers={"Retry-After": "0"}) for _ in range(4)],
            ],
        )
        cache, clock = _make_cache()

        await cache.resolve(["guest-001"])
        clock.advance(GUEST_CACHE_TTL_SECONDS + 1)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cache.resolve(["guest-001"])

        assert result.failures == 1
        assert result.guests["guest-001"].full_name == "Ada Lovelace"

    @respx.mock
    async def test_unparseable_body_is_a_failure(self) -> None:
        """A non-JSON body is a failure, not a cached entry."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            return_value=Response(200, text="<html>"),
        )
        cache, _clock = _make_cache()

        result = await cache.resolve(["guest-001"])

        assert route.call_count == 1
        assert result.failures == 1
        assert result.guests == {}

    @respx.mock
    async def test_failed_lookup_is_retried_next_refresh(self) -> None:
        """A failure is not cached as a negative entry."""
        _mock_token()
        route = respx.get(_guest_url("guest-001")).mock(
            side_effect=[
                *[Response(500, text="boom") for _ in range(4)],
                Response(200, json=_guest_body("guest-001")),
            ],
        )
        cache, _clock = _make_cache()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await cache.resolve(["guest-001"])
            result = await cache.resolve(["guest-001"])

        assert route.call_count == 5
        assert result.guests["guest-001"].full_name == "Ada Lovelace"

    @respx.mock
    async def test_changed_guest_id_never_reuses_the_previous(
        self,
    ) -> None:
        """A different guest id is a different key (FR-019)."""
        _mock_token()
        respx.get(_guest_url("guest-001")).mock(
            return_value=Response(200, json=_guest_body("guest-001")),
        )
        respx.get(_guest_url("guest-002")).mock(
            return_value=Response(500, text="boom"),
        )
        cache, _clock = _make_cache()

        await cache.resolve(["guest-001"])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await cache.resolve(["guest-002"])

        assert "guest-002" not in result.guests
        assert result.failures == 1
