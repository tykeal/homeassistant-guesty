# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Guesty reservations v3 search source.

Every payload here is a synthetic dict built by the ``make_v3_*``
builders in ``tests/conftest.py``; no live Guesty payload exists
(NFR-008).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, patch

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
from custom_components.guesty.api.models import RejectedCode, ValidationSummary
from custom_components.guesty.api.reservations_fallback import FallbackOutcome
from custom_components.guesty.api.reservations_v3 import (
    V3ReservationSource,
    _map_results,
)
from custom_components.guesty.api.validation import ValidationReason
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
        """Pages, sanitized status and segment histograms are reported.

        Feature 013 (research R2): ``status_counts`` is keyed by the
        mapped recognized status, so ``completed`` counts as
        ``checked_out``; no raw value is ever a key.
        """
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
        assert dict(result.status_counts) == {"confirmed": 2, "checked_out": 1}
        assert dict(result.segment_histogram) == {1: 2, 2: 1}
        assert {res.status for res in result.reservations} == {
            "confirmed",
            "checked_out",
        }

    @respx.mock
    async def test_unusable_reservation_is_skipped(self) -> None:
        """A reservation with no usable segment is skipped, not fatal.

        Audited for feature 013 (research R2, R3): the rejected record's
        recognized status is still counted under its mapped key, and it
        is reported in ``rejected_ids``.
        """
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
        assert result.rejected_ids == ("res-0002",)


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


# ── Feature 014: returned and rejected identifiers (T010) ───────────


class TestV3ReturnedAndRejectedIds:
    """The raw returned and rejected v3 identifiers reach the result."""

    def test_map_results_records_every_raw_string_id(self) -> None:
        """Accepted and rejected string IDs are returned; others are not."""
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1"),
                    make_v3_reservation("res-2", segments=[]),
                    {"reservationId": 7, "status": "confirmed"},
                    {"status": "confirmed"},
                ],
            ],
        )

        assert result.returned_ids == ("res-1", "res-2")

    def test_rejected_ids_are_assessment_based(self) -> None:
        """Rejected and partially valid records are rejected, by identifier.

        Renamed from ``test_rejected_ids_are_raw_minus_parsed``: feature
        013 (research R16; 014 T010 note) computes ``rejected_ids`` from
        the record assessment, so a partially valid record is both
        published and rejected.
        """
        no_status = make_v3_reservation("res-2")
        del no_status["status"]
        partial = make_v3_reservation(
            "res-4",
            segments=[make_v3_segment(), make_v3_segment(listingId="")],
        )
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1"),
                    no_status,
                    make_v3_reservation("res-3", segments=[]),
                    partial,
                ],
            ],
        )

        assert [r.id for r in result.reservations] == ["res-1", "res-4"]
        assert result.rejected_ids == ("res-2", "res-3", "res-4")

    def test_rejected_ids_include_013_raw_segment_rejections(self) -> None:
        """Raw segment defects reject a record by its identifier (014 T050).

        Feature 014's by-ID fallback must never read a reservation the
        qualifying search rejected, so 013's raw segment rejections
        (a non-string parent listing, an empty listing, an unparsable
        date) all reach ``rejected_ids`` whether the record is dropped
        or still published from its valid segment.
        """
        bad_parent = make_v3_reservation(
            "res-parent",
            segments=[make_v3_segment(parentListingId=7)],
        )
        bad_date = make_v3_reservation(
            "res-date",
            segments=[make_v3_segment(checkOutDateLocalized="not-a-date")],
        )
        partial = make_v3_reservation(
            "res-partial",
            segments=[make_v3_segment(), make_v3_segment(parentListingId=7)],
        )
        result = _map_results(
            [[make_v3_reservation("res-ok"), bad_parent, bad_date, partial]],
        )

        assert result.rejected_ids == ("res-date", "res-parent", "res-partial")
        assert result.returned_ids == (
            "res-date",
            "res-ok",
            "res-parent",
            "res-partial",
        )

    def test_ids_are_sorted_deduplicated_tuples(self) -> None:
        """Both fields are sorted tuples without duplicates."""
        result = _map_results(
            [
                [make_v3_reservation("res-3"), make_v3_reservation("res-9", stay=[])],
                [make_v3_reservation("res-1"), make_v3_reservation("res-9", stay=[])],
            ],
        )

        assert result.returned_ids == ("res-1", "res-3", "res-9")
        assert result.rejected_ids == ("res-9",)

    @respx.mock
    async def test_fetch_carries_fallback_observed_from_outcome(self) -> None:
        """fallback_observed equals the fallback outcome's observed IDs."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200, json=make_v3_search_page([make_v3_reservation("res-1")])
            ),
        )
        source = _make_source()
        outcome = FallbackOutcome(observed=("res-out", "res-served"))

        with patch.object(source.fallback, "resolve", AsyncMock(return_value=outcome)):
            result = await source.fetch(past_days=30, future_days=365)

        assert result.fallback_observed == ("res-out", "res-served")
        assert result.returned_ids == ("res-1",)
        assert result.rejected_ids == ()

    @respx.mock
    async def test_observed_ids_merge_is_unchanged(self) -> None:
        """Baseline control: observed_ids merges parsed and legacy IDs."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200, json=make_v3_search_page([make_v3_reservation("res-2")])
            ),
        )
        source = _make_source()

        with patch.object(
            source.fallback,
            "resolve",
            AsyncMock(return_value=FallbackOutcome(observed=("res-1",))),
        ):
            result = await source.fetch(past_days=30, future_days=365)

        assert result.observed_ids == ("res-1", "res-2")


# ── Feature 013: record isolation and rejection reporting (T008) ────


def _rejected(reservation_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a record rejected by an unrecognized status.

    Args:
        reservation_id: The ``reservationId`` value.
        **overrides: Fields to override on the record.

    Returns:
        A v3 search result dict whose status is not recognized.
    """
    overrides.setdefault("status", "inquiry")
    return make_v3_reservation(reservation_id, **overrides)


def _partial(reservation_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a partially valid record: one verified, one bad segment.

    Args:
        reservation_id: The ``reservationId`` value.
        **overrides: Fields to override on the record.

    Returns:
        A v3 search result dict with one rejected segment.
    """
    return make_v3_reservation(
        reservation_id,
        segments=[make_v3_segment(), make_v3_segment(listingId="")],
        **overrides,
    )


class TestV3RecordIsolation:
    """A malformed record is isolated from a verified batch."""

    @respx.mock
    async def test_rejected_record_is_isolated(self) -> None:
        """Other records publish; the rejected one is only returned."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-1"),
                        _rejected("res-2"),
                        make_v3_reservation("res-3"),
                    ],
                ),
            ),
        )

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert [r.id for r in result.reservations] == ["res-1", "res-3"]
        assert result.returned_ids == ("res-1", "res-2", "res-3")
        assert result.rejected_ids == ("res-2",)

    @pytest.mark.parametrize(
        "pages",
        [
            pytest.param([["not-a-dict"]], id="non-dict"),
            pytest.param([[{"status": "confirmed"}]], id="missing-id"),
            pytest.param(
                [[make_v3_reservation("res-1", reservationId=5)]],
                id="non-string-id",
            ),
            pytest.param(
                [[make_v3_reservation("res-1"), make_v3_reservation("res-1")]],
                id="duplicate",
            ),
            pytest.param(
                [[make_v3_reservation("res-2"), make_v3_reservation("res-1")]],
                id="non-ascending",
            ),
        ],
    )
    @respx.mock
    async def test_unverified_identifier_fails_the_refresh(
        self,
        pages: list[list[Any]],
    ) -> None:
        """Baseline control: an unprovable batch still fails closed."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(200, json=make_v3_search_page(pages[0])),
        )
        source = _make_source()

        with (
            patch(
                "custom_components.guesty.api.reservations_v3._map_results",
            ) as mapper,
            pytest.raises(GuestyResponseError),
        ):
            await source.fetch(past_days=30, future_days=365)

        mapper.assert_not_called()

    def test_rejected_ids_contents_order_and_exclusions(self) -> None:
        """Rejected and partial records, holds included, are listed."""
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1"),
                    _partial("res-2"),
                    _rejected("res-3", stay=[]),
                    _rejected("res-4", status="alteration", stay=[]),
                    _partial("res-5", status="alteration"),
                    make_v3_reservation("res-6", status="alteration"),
                ],
                [_rejected("res-0")],
            ],
        )

        assert result.rejected_ids == ("res-0", "res-2", "res-3", "res-4", "res-5")
        assert set(result.rejected_ids) <= set(result.returned_ids)
        assert len(set(result.rejected_ids)) == len(result.rejected_ids)
        assert sorted(r.id for r in result.reservations) == [
            "res-1",
            "res-2",
            "res-5",
            "res-6",
        ]

    def test_partial_record_is_published_and_rejected(self) -> None:
        """A partial record publishes verified segments and is rejected."""
        result = _map_results([[_partial("res-1")]])

        assert [r.id for r in result.reservations] == ["res-1"]
        published = result.reservations[0]
        assert published.partial is True
        assert len(published.stay) == 1
        assert result.rejected_ids == ("res-1",)
        assert result.observed_ids == ("res-1",)

    def test_status_counts_are_sanitized(self) -> None:
        """Only mapped statuses and ``unrecognized`` are keys."""
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1", status="confirmed"),
                    make_v3_reservation("res-2", status="Completed"),
                    _rejected("res-3", status="SENTINEL-STATUS"),
                    _rejected("res-4", status=["confirmed"]),
                    make_v3_reservation("res-5", status="alteration"),
                ],
            ],
        )

        assert dict(result.status_counts) == {
            "confirmed": 1,
            "checked_out": 1,
            "alteration": 1,
            "unrecognized": 2,
        }

    def test_rejected_codes_pairs(self) -> None:
        """Only non-hold coded rejected or partial records give pairs."""
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1", confirmationCode="CODE-OK"),
                    _rejected("res-2", confirmationCode="CODE-SHARED"),
                    _partial("res-3", confirmationCode="CODE-P"),
                    _rejected("res-4", confirmationCode="CODE-SHARED"),
                    _rejected(
                        "res-5",
                        status="alteration",
                        stay=[],
                        confirmationCode="CODE-HOLD",
                    ),
                    _rejected("res-6", confirmationCode=123),
                    _rejected("res-7", confirmationCode=""),
                ],
            ],
        )

        assert result.rejected_codes == (
            RejectedCode("res-2", "CODE-SHARED"),
            RejectedCode("res-3", "CODE-P"),
            RejectedCode("res-4", "CODE-SHARED"),
        )

    @pytest.mark.parametrize(
        ("record", "expected"),
        [
            pytest.param(
                _rejected(
                    "res-1",
                    segments=[
                        make_v3_segment(),
                        make_v3_segment(
                            checkInDateLocalized="2025-08-05",
                            checkOutDateLocalized="2025-08-07",
                        ),
                    ],
                    checkOut="2025-08-07T11:00:00",
                ),
                "2025-08-07",
                id="segments",
            ),
            pytest.param(
                _partial("res-1", checkOut="2025-08-09T23:00:00-05:00"),
                "2025-08-11",
                id="explicit-offset",
            ),
            pytest.param(
                _partial("res-1", checkOut="2025-08-09T11:00:00Z"),
                "2025-08-10",
                id="zulu",
            ),
            pytest.param(
                _partial("res-1", checkOut="2025-08-09T11:00:00"),
                "2025-08-05",
                id="naive",
            ),
            pytest.param(
                _rejected("res-1", checkOut="2025-08-02T10:00:00+00:00"),
                "2025-08-05",
                id="segments-later",
            ),
            pytest.param(
                _rejected("res-1", checkOut="2025-08-20T10:00:00+00:00"),
                "2025-08-21",
                id="top-level-later",
            ),
            pytest.param(
                _partial("res-1", checkOut=None, stay=[make_v3_segment(), "bad"]),
                None,
                id="neither",
            ),
            pytest.param(
                _rejected("res-1", checkOut="9999-12-31T23:00:00Z"),
                "2025-08-05",
                id="overflow-utc",
            ),
            pytest.param(
                _partial("res-1", checkOut="9999-12-31T20:00:00-05:00"),
                "2025-08-05",
                id="overflow-negative-offset",
            ),
            pytest.param(
                _rejected(
                    "res-1",
                    checkOut="0001-01-01T00:00:00+05:00",
                    stay=[make_v3_segment(), "bad"],
                ),
                None,
                id="overflow-positive-offset",
            ),
        ],
    )
    def test_rejected_check_out_bounds(
        self,
        record: dict[str, Any],
        expected: str | None,
    ) -> None:
        """The later verified candidate bounds a coded rejected record.

        Updated for the feature 013 review (research R7 decision 4):
        the segment candidate needs every raw segment to have a valid
        date-level interval, not full verification. The ``naive``
        partial record's second segment only lacks a listing, so its
        dates now give the bound; ``neither`` uses a non-dict segment
        to keep no candidate at all. An unrepresentable top-level
        bound is no candidate and never fails the batch.
        """
        result = _map_results([[record]])

        assert result.rejected_codes == (RejectedCode("res-1", "CONF-ABC"),)
        if expected is None:
            assert dict(result.rejected_check_out_bounds) == {}
        else:
            assert dict(result.rejected_check_out_bounds) == {"res-1": expected}

    @respx.mock
    async def test_extreme_check_out_never_fails_the_refresh(self) -> None:
        """An unrepresentable rejected bound leaves the batch intact."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-1"),
                        _rejected(
                            "res-2",
                            checkOut="9999-12-31T20:00:00-05:00",
                            stay=["bad"],
                        ),
                        _rejected(
                            "res-3",
                            checkOut="9999-12-31T23:00:00Z",
                            stay=["bad"],
                        ),
                    ],
                ),
            ),
        )

        result = await _make_source().fetch(past_days=30, future_days=365)

        assert [r.id for r in result.reservations] == ["res-1"]
        assert result.rejected_ids == ("res-2", "res-3")
        assert dict(result.rejected_check_out_bounds) == {}

    def test_validation_summary_counts(self) -> None:
        """The summary holds counts only."""
        no_verified = make_v3_reservation(
            "res-4",
            segments=[make_v3_segment(checkInDateLocalized=None)],
        )
        no_verified["stay"].append("bad")
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1"),
                    _partial("res-2"),
                    _rejected("res-3"),
                    no_verified,
                ],
            ],
        )

        assert result.validation == ValidationSummary(
            rejected_records=2,
            partial_records=1,
            rejected_segments=3,
            reasons=MappingProxyType(
                {
                    ValidationReason.PARTIAL_STAY: 1,
                    ValidationReason.UNRECOGNIZED_STATUS: 1,
                    ValidationReason.NO_VERIFIED_SEGMENT: 1,
                    ValidationReason.SEGMENT_MISSING_LISTING: 1,
                    ValidationReason.SEGMENT_UNVERIFIED_DATES: 1,
                    ValidationReason.SEGMENT_MALFORMED: 1,
                },
            ),
        )

    def test_exclusion_keys(self) -> None:
        """Exclusions are sorted unique reason and identifier pairs."""
        sentinel = "SENTINEL-EXCL"
        result = _map_results(
            [
                [
                    make_v3_reservation("res-1"),
                    make_v3_reservation(
                        "res-2",
                        confirmationCode=sentinel,
                        segments=[
                            make_v3_segment(),
                            make_v3_segment(listingId=""),
                            make_v3_segment(listingId=None),
                        ],
                    ),
                    _rejected("res-3", status=sentinel),
                ],
            ],
        )

        assert result.exclusions == (
            (ValidationReason.PARTIAL_STAY, "res-2"),
            (ValidationReason.SEGMENT_MISSING_LISTING, "res-2"),
            (ValidationReason.UNRECOGNIZED_STATUS, "res-3"),
        )
        assert sentinel not in repr(result.exclusions)
        assert sentinel not in repr(result.validation)
        assert sentinel not in repr(dict(result.status_counts))


# ── Feature 013: fallback outcome merge (T012) ──────────────────────


class TestV3FallbackMerge:
    """``fetch`` hands rejections to the fallback and merges its report."""

    @respx.mock
    async def test_fetch_passes_rejected_and_merges_outcome(self) -> None:
        """All of ``rejected_ids`` reach ``resolve``; outcome fields merge."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-1"),
                        _rejected("res-2", confirmationCode="CODE-V3"),
                        _partial("res-3"),
                    ],
                ),
            ),
        )
        source = _make_source()
        outcome = FallbackOutcome(
            observed=("res-9",),
            invalid=("res-9",),
            stale=("res-8",),
            exclusions=((ValidationReason.UNRECOGNIZED_STATUS, "res-9"),),
            rejected_codes=(RejectedCode("res-9", "CODE-LEGACY"),),
            rejected_check_out_bounds=MappingProxyType({"res-9": "2025-08-10"}),
        )
        resolve = AsyncMock(return_value=outcome)

        with patch.object(source.fallback, "resolve", resolve):
            result = await source.fetch(past_days=30, future_days=365)

        assert resolve.await_args is not None
        assert tuple(resolve.await_args.kwargs["rejected"]) == ("res-2", "res-3")
        assert result.rejected_ids == ("res-2", "res-3")
        assert result.rejected_codes == (
            RejectedCode("res-2", "CODE-V3"),
            RejectedCode("res-3", "CONF-ABC"),
            RejectedCode("res-9", "CODE-LEGACY"),
        )
        assert dict(result.rejected_check_out_bounds) == {
            "res-2": "2025-08-06",
            "res-3": "2025-08-06",
            "res-9": "2025-08-10",
        }
        assert result.exclusions == (
            (ValidationReason.PARTIAL_STAY, "res-3"),
            (ValidationReason.SEGMENT_MISSING_LISTING, "res-3"),
            (ValidationReason.UNRECOGNIZED_STATUS, "res-2"),
            (ValidationReason.UNRECOGNIZED_STATUS, "res-9"),
        )
        assert result.fallback_invalid_ids == ("res-9",)
        assert result.stale_ids == ("res-8",)
        assert "res-9" not in result.rejected_ids

    @respx.mock
    async def test_fallback_validation_counts_merge(self) -> None:
        """Invalid lookups count in the refresh's validation summary.

        Every summary field is summed and the reasons are combined,
        including one only the fallback reports.
        """
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page(
                    [
                        make_v3_reservation("res-1"),
                        _rejected("res-2"),
                        _partial("res-3"),
                    ],
                ),
            ),
        )
        source = _make_source()
        outcome = FallbackOutcome(
            invalid=("res-8", "res-9"),
            exclusions=(
                (ValidationReason.UNVERIFIED_INSTANT, "res-8"),
                (ValidationReason.UNRECOGNIZED_STATUS, "res-9"),
            ),
            validation=ValidationSummary(
                rejected_records=2,
                partial_records=1,
                rejected_segments=2,
                reasons=MappingProxyType(
                    {
                        ValidationReason.UNRECOGNIZED_STATUS: 1,
                        ValidationReason.UNVERIFIED_INSTANT: 1,
                    }
                ),
            ),
        )

        with patch.object(
            source.fallback,
            "resolve",
            AsyncMock(return_value=outcome),
        ):
            result = await source.fetch(past_days=30, future_days=365)

        assert result.validation.rejected_records == 3
        assert result.validation.partial_records == 2
        assert result.validation.rejected_segments == 3
        assert dict(result.validation.reasons) == {
            ValidationReason.UNRECOGNIZED_STATUS: 2,
            ValidationReason.UNVERIFIED_INSTANT: 1,
            ValidationReason.PARTIAL_STAY: 1,
            ValidationReason.SEGMENT_MISSING_LISTING: 1,
        }

    @respx.mock
    async def test_empty_outcome_keeps_the_v3_report(self) -> None:
        """Baseline: an empty outcome leaves the v3 report unchanged."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            return_value=Response(
                200,
                json=make_v3_search_page([make_v3_reservation("res-1")]),
            ),
        )
        source = _make_source()

        with patch.object(
            source.fallback,
            "resolve",
            AsyncMock(return_value=FallbackOutcome()),
        ):
            result = await source.fetch(past_days=30, future_days=365)

        assert result.rejected_codes == ()
        assert result.fallback_invalid_ids == ()
        assert result.stale_ids == ()
        assert result.exclusions == ()
