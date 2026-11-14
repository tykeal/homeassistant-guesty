# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty reservations v3 search source.

Every payload here is a synthetic dict built by the ``make_v3_*``
builders in ``tests/conftest.py``; no live Guesty payload exists
(NFR-008).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    BASE_URL,
    MAX_V3_RESERVATION_PAGES,
    RESERVATIONS_API_LEGACY,
    RESERVATIONS_API_V3,
    RESERVATIONS_ENDPOINT,
    RESERVATIONS_PAGE_SIZE,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.exceptions import (
    GuestyFilterRejectedError,
    GuestyResponseError,
)
from custom_components.guesty.api.reservations_v3 import V3ReservationSource
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeTokenStorage,
    make_reservation_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
)

SEARCH_URL = f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}"
LEGACY_URL = f"{BASE_URL}{RESERVATIONS_ENDPOINT}"


def _make_client() -> GuestyApiClient:
    """Create a v3-selected API client for tests.

    Returns:
        A GuestyApiClient configured for the v3 reservation path.
    """
    http = httpx.AsyncClient()
    token_manager = GuestyTokenManager(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        http_client=http,
        storage=FakeTokenStorage(),
        refresh_buffer=0,
    )
    return GuestyApiClient(
        token_manager=token_manager,
        http_client=http,
        reservations_api=RESERVATIONS_API_V3,
    )


def _make_source() -> V3ReservationSource:
    """Create a V3ReservationSource backed by a test client.

    Returns:
        A V3ReservationSource ready to fetch.
    """
    return V3ReservationSource(_make_client())


def _mock_token() -> None:
    """Register the OAuth token route used by every request."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


def _page_of(count: int, *, start: int = 0) -> list[dict[str, Any]]:
    """Build a page of reservations with ascending identifiers.

    Args:
        count: Number of reservation dicts to build.
        start: Starting index for the identifier sequence.

    Returns:
        A list of v3 reservation dicts.
    """
    return [make_v3_reservation(f"res-{start + i:06d}") for i in range(count)]


class TestV3RequestConstruction:
    """Tests pinning the exact outgoing v3 search query (T029)."""

    @respx.mock
    async def test_first_page_query_parameters(
        self,
        freezer: Any,
    ) -> None:
        """The first page sends the padded overlap filter and paging."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(SEARCH_URL).mock(
            return_value=Response(200, json=make_v3_search_page([])),
        )

        await _make_source().fetch(
            past_days=30,
            future_days=365,
            statuses=ACTIONABLE_STATUSES,
        )

        params = route.calls[0].request.url.params
        assert params["filter[checkOut][gte]"] == "2026-10-13"
        assert params["filter[checkIn][lte]"] == "2027-11-14"
        assert params["filter[status][in]"] == (
            "canceled,checked_in,checked_out,completed,confirmed"
        )
        assert params["sort"] == "_id"
        assert params["limit"] == str(RESERVATIONS_PAGE_SIZE)
        assert params["skip"] == "0"

    @respx.mock
    async def test_no_fields_or_listing_filter(
        self,
        freezer: Any,
    ) -> None:
        """No projection and no listing filter is ever sent (FR-007, FR-015)."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(SEARCH_URL).mock(
            return_value=Response(200, json=make_v3_search_page([])),
        )

        await _make_source().fetch(past_days=30, future_days=365)

        params = route.calls[0].request.url.params
        assert "fields" not in params
        assert not any("listingId" in key for key in params)
        assert not any("listing" in key.lower() for key in params)

    @respx.mock
    async def test_default_statuses_are_translated(
        self,
        freezer: Any,
    ) -> None:
        """Omitting statuses requests the translated actionable set."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(SEARCH_URL).mock(
            return_value=Response(200, json=make_v3_search_page([])),
        )

        await _make_source().fetch(past_days=30, future_days=365)

        params = route.calls[0].request.url.params
        assert params["filter[status][in]"] == (
            "canceled,checked_in,checked_out,completed,confirmed"
        )

    @respx.mock
    async def test_skip_steps_by_page_size(self) -> None:
        """Paging steps skip by the page size on each request."""
        _mock_token()
        pages = [
            Response(
                200,
                json=make_v3_search_page(
                    _page_of(RESERVATIONS_PAGE_SIZE),
                    skip=0,
                    has_more=True,
                ),
            ),
            Response(
                200,
                json=make_v3_search_page(
                    _page_of(5, start=RESERVATIONS_PAGE_SIZE),
                    skip=RESERVATIONS_PAGE_SIZE,
                ),
            ),
        ]
        route = respx.get(SEARCH_URL).mock(side_effect=pages)

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert route.call_count == 2
        assert route.calls[0].request.url.params["skip"] == "0"
        assert route.calls[1].request.url.params["skip"] == str(
            RESERVATIONS_PAGE_SIZE,
        )
        assert result.pages == 2
        assert len(result.reservations) == RESERVATIONS_PAGE_SIZE + 5


class TestV3PageGuard:
    """Tests for paging and the nine-check page guard (T030)."""

    @respx.mock
    async def test_missing_results_aborts(self) -> None:
        """A page without results raises and publishes nothing."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json={"pagination": {"skip": 0, "limit": 100, "hasMore": False}},
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_missing_pagination_aborts(self) -> None:
        """A page without a pagination block raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(200, json={"results": []}),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_non_boolean_has_more_aborts(self) -> None:
        """A non-boolean hasMore raises."""
        _mock_token()
        page = make_v3_search_page([])
        page["pagination"]["hasMore"] = "false"
        respx.get(SEARCH_URL).mock(return_value=Response(200, json=page))

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_skip_mismatch_aborts(self) -> None:
        """A pagination.skip that differs from the request raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(_page_of(1), skip=50),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_oversized_page_aborts(self) -> None:
        """More results than the requested limit raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    _page_of(RESERVATIONS_PAGE_SIZE + 1),
                ),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_has_more_on_empty_page_aborts(self) -> None:
        """hasMore true on an empty page raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page([], has_more=True),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_has_more_on_short_page_aborts(self) -> None:
        """hasMore true on a short page raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(_page_of(3), has_more=True),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_missing_reservation_id_aborts(self) -> None:
        """A result without a reservationId raises."""
        _mock_token()
        broken = make_v3_reservation("res-0000")
        del broken["reservationId"]
        respx.get(SEARCH_URL).mock(
            return_value=Response(200, json=make_v3_search_page([broken])),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_non_string_reservation_id_aborts(self) -> None:
        """A non-string reservationId raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [make_v3_reservation("res-0000", reservationId=17)],
                ),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_duplicate_reservation_id_aborts(self) -> None:
        """A reservationId repeated across pages raises."""
        _mock_token()
        first = _page_of(RESERVATIONS_PAGE_SIZE)
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(
                    200,
                    json=make_v3_search_page(first, skip=0, has_more=True),
                ),
                Response(
                    200,
                    json=make_v3_search_page(
                        [make_v3_reservation("res-000000")],
                        skip=RESERVATIONS_PAGE_SIZE,
                    ),
                ),
            ],
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_non_ascending_reservation_id_aborts(self) -> None:
        """Identifiers that are not strictly ascending raise."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-0009"),
                        make_v3_reservation("res-0001"),
                    ],
                ),
            ),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_page_cap_aborts(self) -> None:
        """hasMore still true after the page cap raises."""
        _mock_token()

        def _endless(request: httpx.Request) -> Response:
            """Return a full page that always claims another follows.

            Args:
                request: The outgoing search request.

            Returns:
                A full page response with hasMore true.
            """
            skip = int(request.url.params["skip"])
            return Response(
                200,
                json=make_v3_search_page(
                    _page_of(RESERVATIONS_PAGE_SIZE, start=skip),
                    skip=skip,
                    has_more=True,
                ),
            )

        route = respx.get(SEARCH_URL).mock(side_effect=_endless)

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

        assert route.call_count == MAX_V3_RESERVATION_PAGES

    @respx.mock
    async def test_non_json_body_aborts(self) -> None:
        """A non-JSON search response raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(200, text="not json"),
        )

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)

    @respx.mock
    async def test_error_status_aborts(self) -> None:
        """A non-success, non-400 status raises."""
        _mock_token()
        respx.get(SEARCH_URL).mock(return_value=Response(404, json={}))

        with pytest.raises(GuestyResponseError):
            await _make_source().fetch(past_days=30, future_days=365)


class TestV3FilterRejection:
    """Tests for the HTTP 400 filter rejection (T031)."""

    @respx.mock
    async def test_http_400_raises_filter_rejected(self) -> None:
        """An HTTP 400 raises GuestyFilterRejectedError with detail."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(400, text="unsupported filter checkIn"),
        )

        with pytest.raises(GuestyFilterRejectedError) as excinfo:
            await _make_source().fetch(past_days=30, future_days=365)

        error = excinfo.value
        assert error.endpoint == RESERVATIONS_V3_SEARCH_ENDPOINT
        assert error.status_code == 400
        assert error.body_excerpt is not None
        assert "unsupported filter" in error.body_excerpt
        assert "unsupported filter" in error.message

    @respx.mock
    async def test_http_400_is_never_retried_unfiltered(self) -> None:
        """A rejected search is never retried with filters removed."""
        _mock_token()
        route = respx.get(SEARCH_URL).mock(
            return_value=Response(400, text="bad filter"),
        )

        with pytest.raises(GuestyFilterRejectedError):
            await _make_source().fetch(past_days=30, future_days=365)

        assert route.call_count == 1
        params = route.calls[0].request.url.params
        assert "filter[status][in]" in params
        assert "filter[checkIn][lte]" in params
        assert "filter[checkOut][gte]" in params


class TestV3Window:
    """Frozen-clock window tests against a fake backend (T032)."""

    @respx.mock
    async def test_window_excludes_out_of_window_reservations(
        self,
        freezer: Any,
    ) -> None:
        """Out-of-window fixtures are not returned by the overlap filter."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        backend = [
            make_v3_reservation(
                "res-0001",
                segments=[
                    make_v3_segment(
                        checkIn="2024-01-01T15:00:00+00:00",
                        checkOut="2024-01-05T11:00:00+00:00",
                        checkInDateLocalized="2024-01-01",
                        checkOutDateLocalized="2024-01-05",
                    ),
                ],
            ),
            make_v3_reservation(
                "res-0002",
                segments=[
                    make_v3_segment(
                        checkIn="2026-12-01T15:00:00+00:00",
                        checkOut="2026-12-05T11:00:00+00:00",
                        checkInDateLocalized="2026-12-01",
                        checkOutDateLocalized="2026-12-05",
                    ),
                ],
            ),
            make_v3_reservation(
                "res-0003",
                segments=[
                    make_v3_segment(
                        checkIn="2029-01-01T15:00:00+00:00",
                        checkOut="2029-01-05T11:00:00+00:00",
                        checkInDateLocalized="2029-01-01",
                        checkOutDateLocalized="2029-01-05",
                    ),
                ],
            ),
        ]
        respx.get(SEARCH_URL).mock(side_effect=_fake_backend(backend))

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert [res.id for res in result.reservations] == ["res-0002"]

    @respx.mock
    async def test_long_in_progress_stay_is_returned(
        self,
        freezer: Any,
    ) -> None:
        """A 200-day-old in-progress stay survives the overlap filter."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        backend = [
            make_v3_reservation(
                "res-0010",
                status="checked_in",
                segments=[
                    make_v3_segment(
                        checkIn="2026-04-27T15:00:00+00:00",
                        checkOut="2026-12-20T11:00:00+00:00",
                        checkInDateLocalized="2026-04-27",
                        checkOutDateLocalized="2026-12-20",
                    ),
                ],
            ),
        ]
        respx.get(SEARCH_URL).mock(side_effect=_fake_backend(backend))

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert [res.id for res in result.reservations] == ["res-0010"]


class TestV3NoDeprecatedRequests:
    """Tests that the v3 path never calls deprecated resources (T033).

    The one-time identity bootstrap sweep (T073) is the single
    qualified exception to this rule, and it does not exist yet;
    it is covered by its own phase pending the T002 amendment.
    """

    @respx.mock
    async def test_refresh_touches_only_the_v3_search(self) -> None:
        """A full refresh never requests /v1/reservations."""
        _mock_token()
        legacy = respx.get(LEGACY_URL).mock(
            return_value=Response(200, json={"results": []}),
        )
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(
                    200,
                    json=make_v3_search_page(
                        _page_of(RESERVATIONS_PAGE_SIZE),
                        skip=0,
                        has_more=True,
                    ),
                ),
                Response(
                    200,
                    json=make_v3_search_page(
                        _page_of(2, start=RESERVATIONS_PAGE_SIZE),
                        skip=RESERVATIONS_PAGE_SIZE,
                    ),
                ),
            ],
        )

        client = _make_client()
        await client.get_reservations(past_days=30, future_days=365)

        assert legacy.call_count == 0
        paths = {
            call.request.url.path
            for call in respx.calls
            if call.request.url.host != "open-api.guesty.com"
            or not call.request.url.path.endswith("/oauth2/token")
        }
        assert paths == {f"/v1{RESERVATIONS_V3_SEARCH_ENDPOINT}"}


class TestV3Diagnostics:
    """Tests for the ReservationFetchResult counters (T034)."""

    @respx.mock
    async def test_counters_report_pages_statuses_and_segments(
        self,
    ) -> None:
        """Pages, raw status and segment histograms are reported."""
        _mock_token()
        two_segments = [
            make_v3_segment(
                listingId="listing-001",
                checkIn="2026-08-01T15:00:00+00:00",
                checkOut="2026-08-03T11:00:00+00:00",
                checkInDateLocalized="2026-08-01",
                checkOutDateLocalized="2026-08-03",
            ),
            make_v3_segment(
                listingId="listing-002",
                checkIn="2026-08-03T15:00:00+00:00",
                checkOut="2026-08-05T11:00:00+00:00",
                checkInDateLocalized="2026-08-03",
                checkOutDateLocalized="2026-08-05",
            ),
        ]
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-0001", status="confirmed"),
                        make_v3_reservation("res-0002", status="completed"),
                        make_v3_reservation(
                            "res-0003",
                            status="confirmed",
                            segments=two_segments,
                        ),
                    ],
                ),
            ),
        )

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert result.pages == 1
        assert dict(result.status_counts) == {"confirmed": 2, "completed": 1}
        assert dict(result.segment_histogram) == {1: 2, 2: 1}
        assert {res.status for res in result.reservations} == {
            "confirmed",
            "checked_out",
        }

    @respx.mock
    async def test_unusable_reservation_is_skipped(self) -> None:
        """A reservation with no usable segment is skipped, not fatal."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-0001"),
                        make_v3_reservation("res-0002", segments=[]),
                    ],
                ),
            ),
        )

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert [res.id for res in result.reservations] == ["res-0001"]
        assert dict(result.status_counts) == {"confirmed": 2}


class TestV3SourceSelection:
    """Tests for the client selecting the v3 source (T040)."""

    def test_v3_client_uses_the_v3_source(self) -> None:
        """A v3 client builds a V3ReservationSource."""
        client = _make_client()
        assert isinstance(client._reservation_source, V3ReservationSource)


def _fake_backend(
    reservations: list[dict[str, Any]],
) -> Any:
    """Build a respx side effect filtering fixtures server-side.

    The fake applies the documented v3 overlap semantics: a
    reservation matches when its localized check-out is at or after
    ``filter[checkOut][gte]`` and its localized check-in is at or
    before ``filter[checkIn][lte]``.

    Args:
        reservations: The fixtures held by the fake backend.

    Returns:
        A callable usable as a respx side effect.
    """

    def _handler(request: httpx.Request) -> Response:
        """Return the matching fixtures for one search request.

        Args:
            request: The outgoing search request.

        Returns:
            A single-page search response.
        """
        params = request.url.params
        window_start = params["filter[checkOut][gte]"]
        window_end = params["filter[checkIn][lte]"]
        matched = [
            res
            for res in reservations
            if res["checkOutDateLocalized"] >= window_start
            and res["checkInDateLocalized"] <= window_end
        ]
        return Response(
            200,
            json=make_v3_search_page(
                matched,
                skip=int(params["skip"]),
            ),
        )

    return _handler


class TestV3RequestBudget:
    """Tests for the per-refresh request budget (T056).

    A refresh is modelled here as the search followed by the guest
    lookups it implies, which is what the coordinator issues once
    attribution has run (NFR-001, SC-003).
    """

    @respx.mock
    async def test_warm_cache_costs_no_more_than_legacy(self) -> None:
        """A warm v3 refresh is never dearer than the legacy one."""
        _mock_token()
        reservations = _guest_page(10)
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(reservations, skip=0),
            ),
        )
        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=_guest_response,
        )
        respx.get(LEGACY_URL).mock(
            return_value=Response(
                200,
                json={
                    "results": [
                        make_reservation_dict(_id=f"res-{index:06d}")
                        for index in range(10)
                    ],
                    "count": 10,
                },
            ),
        )

        client = _make_client()
        await _v3_refresh(client)
        warm = await _count_requests(lambda: _v3_refresh(client))

        legacy_client = GuestyApiClient(
            token_manager=client._token_manager,
            http_client=client._http,
            reservations_api=RESERVATIONS_API_LEGACY,
        )
        legacy = await _count_requests(legacy_client.get_reservations)

        assert warm == 1
        assert warm <= legacy

    @respx.mock
    async def test_cold_cache_costs_the_pages_plus_one_per_guest(
        self,
    ) -> None:
        """Ten reservations for ten guests cost exactly eleven."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(_guest_page(10), skip=0),
            ),
        )
        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=_guest_response,
        )

        client = _make_client()
        cold = await _count_requests(lambda: _v3_refresh(client))

        assert cold == 11

    @respx.mock
    async def test_repeated_guest_costs_one_lookup(self) -> None:
        """Ten reservations sharing a guest cost a single lookup."""
        _mock_token()
        shared = [
            make_v3_reservation(f"res-{index:06d}", guest_id="guest-same")
            for index in range(10)
        ]
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(shared, skip=0),
            ),
        )
        respx.get(url__regex=rf"{BASE_URL}/guests-crud/.+").mock(
            side_effect=_guest_response,
        )

        client = _make_client()
        cold = await _count_requests(lambda: _v3_refresh(client))

        assert cold == 2


def _guest_page(count: int) -> list[dict[str, Any]]:
    """Build reservations with one distinct guest each.

    Args:
        count: Number of reservation dicts to build.

    Returns:
        A list of v3 reservation dicts.
    """
    return [
        make_v3_reservation(
            f"res-{index:06d}",
            guest_id=f"guest-{index:06d}",
        )
        for index in range(count)
    ]


def _guest_response(request: httpx.Request) -> Response:
    """Serve one synthetic guests-crud lookup.

    Args:
        request: The outgoing guest lookup request.

    Returns:
        A guests-crud response for the requested guest.
    """
    guest_id = request.url.path.rsplit("/", 1)[-1]
    return Response(
        200,
        json={
            "_id": guest_id,
            "fullName": "Ada Lovelace",
            "phone": None,
            "email": None,
        },
    )


async def _v3_refresh(client: GuestyApiClient) -> None:
    """Run a full v3 refresh: the search and its guest lookups.

    Args:
        client: The client under test.
    """
    result = await client.get_reservations()
    await client.resolve_guests(
        [
            reservation.guest.guest_id
            for reservation in result.reservations
            if reservation.guest is not None and reservation.guest.guest_id is not None
        ],
    )


async def _count_requests(
    refresh: Callable[[], Awaitable[Any]],
) -> int:
    """Count the non-token requests one refresh issues.

    Args:
        refresh: The refresh to run.

    Returns:
        The number of API requests the refresh issued.
    """
    before = len(respx.calls)
    await refresh()
    return sum(
        1
        for call in list(respx.calls)[before:]
        if not call.request.url.path.endswith("/oauth2/token")
    )
