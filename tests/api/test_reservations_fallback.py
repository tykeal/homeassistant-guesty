# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the legacy not-found fallback (FR-050 to FR-057).

Guesty Support confirmed on issue #223 that their v3 backfill is
still running, so a reservation absent from a v3 result may simply
not have been migrated yet. These tests pin the behaviour that
stops such a reservation silently vanishing, and equally the
behaviour that stops the fallback resurrecting a reservation legacy
agrees is gone.

Every payload here is a synthetic dict built by the builders in
``tests/conftest.py``; no live Guesty payload exists (NFR-008).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from homeassistant.core import HomeAssistant
from httpx import Response
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.guesty.api.auth import GuestyTokenManager
from custom_components.guesty.api.client import GuestyApiClient
from custom_components.guesty.api.const import (
    ACTIONABLE_STATUSES,
    BASE_URL,
    LEGACY_FALLBACK_RECHECK_SECONDS,
    LEGACY_RESERVATIONS_RETIREMENT_DATE,
    MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH,
    MAX_RETRIES,
    RESERVATION_BY_ID_LEGACY_PATH,
    RESERVATIONS_API_V3,
    RESERVATIONS_V3_SEARCH_ENDPOINT,
    TOKEN_URL,
)
from custom_components.guesty.api.models import (
    FallbackCandidate,
    GuestyListing,
    GuestyReservation,
)
from custom_components.guesty.api.reservations_fallback import (
    ReservationFallbackSource,
)
from custom_components.guesty.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FUTURE_DAYS,
    CONF_PAST_DAYS,
    CONF_RESERVATIONS_API,
    DOMAIN,
)
from custom_components.guesty.coordinator import (
    ListingsCoordinator,
    ReservationsCoordinator,
)
from tests.conftest import (
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeTokenStorage,
    make_guest_dict,
    make_legacy_not_found_body,
    make_legacy_reservation_by_id,
    make_legacy_retired_body,
    make_listing_dict,
    make_token_response,
    make_v3_reservation,
    make_v3_search_page,
    make_v3_segment,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

SEARCH_URL = f"{BASE_URL}{RESERVATIONS_V3_SEARCH_ENDPOINT}"


@pytest.fixture(autouse=True)
def _no_retry_backoff() -> Iterator[None]:
    """Remove the client's retry backoff from these tests.

    Several cases drive many failing lookups on purpose; waiting
    out the real backoff would make them take minutes without
    testing anything about the fallback.

    Yields:
        Nothing; the patch is active for the test.
    """
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


def by_id_url(reservation_id: str) -> str:
    """Build the legacy by-id URL for one reservation.

    Args:
        reservation_id: The reservation the lookup targets.

    Returns:
        The absolute legacy by-id URL.
    """
    path = RESERVATION_BY_ID_LEGACY_PATH.format(
        reservation_id=reservation_id,
    )
    return f"{BASE_URL}{path}"


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


def _mock_token() -> None:
    """Register the OAuth token route used by every request."""
    respx.post(TOKEN_URL).mock(
        return_value=Response(200, json=make_token_response()),
    )


def _rel(days: int) -> datetime:
    """Build a UTC datetime offset from now by whole days.

    Args:
        days: Day offset from now.

    Returns:
        The offset datetime, in UTC.
    """
    return datetime.now(UTC) + timedelta(days=days)


def _candidate(
    check_in_days: int = 1,
    check_out_days: int = 3,
) -> FallbackCandidate:
    """Build a durable candidate hint for the fallback.

    Args:
        check_in_days: Check-in offset from now, in days.
        check_out_days: Check-out offset from now, in days.

    Returns:
        The reconstructed candidate hint.
    """
    return FallbackCandidate(
        check_in=_rel(check_in_days),
        check_out=_rel(check_out_days),
    )


def _legacy_document(
    reservation_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """Build an in-window legacy document for one reservation.

    Args:
        reservation_id: The reservation the document describes.
        **overrides: Fields to override on the defaults.

    Returns:
        A legacy reservation document dict.
    """
    defaults: dict[str, Any] = {
        "checkIn": _rel(1).isoformat(),
        "checkOut": _rel(3).isoformat(),
    }
    defaults.update(overrides)
    return make_legacy_reservation_by_id(reservation_id, **defaults)


async def _resolve(
    source: ReservationFallbackSource,
    *,
    retrieved: tuple[GuestyReservation, ...] = (),
    candidates: dict[str, FallbackCandidate] | None = None,
    past_days: int = 30,
    future_days: int = 365,
    statuses: frozenset[str] = ACTIONABLE_STATUSES,
) -> Any:
    """Run one fallback resolution with test defaults.

    Args:
        source: The fallback source under test.
        retrieved: Reservations the v3 result carried.
        candidates: Durable candidate hints by identifier.
        past_days: Configured days of history.
        future_days: Configured days ahead.
        statuses: Configured status set.

    Returns:
        The fallback outcome.
    """
    return await source.resolve(
        retrieved=retrieved,
        candidates=candidates or {},
        past_days=past_days,
        future_days=future_days,
        statuses=statuses,
    )


class TestCandidateSelection:
    """Who the fallback will and will not look up (T115)."""

    @respx.mock
    async def test_previously_published_absentee_is_a_candidate(
        self,
    ) -> None:
        """A known reservation missing from v3 is looked up."""
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1"),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-1": _candidate()},
        )

        assert route.call_count == 1
        assert [res.id for res in outcome.reservations] == ["res-1"]
        assert outcome.served == ("res-1",)

    @respx.mock
    async def test_never_seen_identifier_is_not_a_candidate(
        self,
    ) -> None:
        """A reservation the integration never saw is not fetched."""
        _mock_token()
        route = respx.get(by_id_url("res-unknown")).mock(
            return_value=Response(200, json=_legacy_document("res-unknown")),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source)

        assert route.call_count == 0
        assert outcome.reservations == ()

    @respx.mock
    async def test_out_of_window_candidate_costs_no_request(
        self,
    ) -> None:
        """A stay outside the window is dropped without a lookup."""
        _mock_token()
        route = respx.get(by_id_url("res-old")).mock(
            return_value=Response(200, json=_legacy_document("res-old")),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={
                "res-old": _candidate(
                    check_in_days=-400,
                    check_out_days=-390,
                ),
            },
        )

        assert route.call_count == 0
        assert outcome.reservations == ()

    @respx.mock
    async def test_confirmed_absent_candidate_is_never_retried(
        self,
        freezer: Any,
    ) -> None:
        """A legacy not-found retires the candidate for good."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-gone")).mock(
            return_value=Response(
                404,
                json=make_legacy_not_found_body("res-gone"),
            ),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-gone": _candidate()}

        first = await _resolve(source, candidates=candidates)
        freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS * 3))
        second = await _resolve(source, candidates=candidates)

        assert route.call_count == 1
        assert first.absent == ("res-gone",)
        assert first.reservations == ()
        assert second.reservations == ()
        assert second.counters.attempted == 0

    @respx.mock
    async def test_fallback_only_reservation_survives_a_reload(
        self,
    ) -> None:
        """A reload rebuilds candidacy from the durable hints."""
        _mock_token()
        route = respx.get(by_id_url("res-fb")).mock(
            return_value=Response(200, json=_legacy_document("res-fb")),
        )
        # A brand-new source stands in for the cleared in-memory
        # caches of a reload; only the hint survives.
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-fb": _candidate()},
        )

        assert route.call_count == 1
        assert [res.id for res in outcome.reservations] == ["res-fb"]

    @respx.mock
    async def test_codeless_reservation_survives_a_reload(
        self,
    ) -> None:
        """Candidacy never depends on confirmation-code evidence."""
        _mock_token()
        route = respx.get(by_id_url("res-nocode")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-nocode",
                    confirmationCode=None,
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-nocode": _candidate()},
        )

        assert route.call_count == 1
        assert [res.id for res in outcome.reservations] == ["res-nocode"]
        assert outcome.reservations[0].confirmation_code is None

    @respx.mock
    async def test_narrowed_future_days_drops_a_candidate(
        self,
    ) -> None:
        """A narrowed window is enforced from the stored check-in."""
        _mock_token()
        route = respx.get(by_id_url("res-far")).mock(
            return_value=Response(200, json=_legacy_document("res-far")),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={
                "res-far": _candidate(
                    check_in_days=200,
                    check_out_days=205,
                ),
            },
            future_days=7,
        )

        assert route.call_count == 0
        assert outcome.reservations == ()


class TestRepublication:
    """What a successful fallback lookup publishes (T116)."""

    @respx.mock
    async def test_successful_lookup_is_flagged_fallback_sourced(
        self,
    ) -> None:
        """A legacy document maps to the same fields v3 publishes."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-1": _candidate()},
        )

        reservation = outcome.reservations[0]
        assert reservation.id == "res-1"
        assert reservation.listing_id == "listing-001"
        assert reservation.status == "confirmed"
        assert reservation.confirmation_code == "CONF-ABC"
        assert outcome.sourced == ("res-1",)
        assert outcome.served == ("res-1",)

    @respx.mock
    async def test_out_of_status_result_is_not_republished(
        self,
    ) -> None:
        """A status outside the configured set stays unpublished."""
        _mock_token()
        respx.get(by_id_url("res-x")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-x", status="inquiry"),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-x": _candidate()},
        )

        assert outcome.reservations == ()
        assert outcome.served == ()
        assert outcome.counters.succeeded == 1

    @respx.mock
    async def test_reappearing_in_v3_drops_the_served_record(
        self,
    ) -> None:
        """A v3 result reclaims the reservation immediately."""
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}
        await _resolve(source, candidates=candidates)
        assert source.served_ids == frozenset({"res-1"})

        v3 = GuestyReservation.from_v3_api_dict(
            make_v3_reservation("res-1"),
        )
        assert v3 is not None
        outcome = await _resolve(
            source,
            retrieved=(v3,),
            candidates=candidates,
        )

        assert route.call_count == 1
        assert outcome.reservations == ()
        assert outcome.served == ()
        assert source.served_ids == frozenset()

    @respx.mock
    async def test_coordinator_publishes_a_segmentless_legacy_record(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Attribution follows the payload's own provenance."""
        _mock_token()
        segmentless_v3 = make_v3_reservation("res-000002", segments=[])
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(
                    200,
                    json=make_v3_search_page(
                        [
                            _v3_in_window("res-000001"),
                            segmentless_v3,
                        ],
                    ),
                ),
                Response(200, json=make_v3_search_page([])),
            ],
        )
        respx.get(by_id_url("res-000001")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-000001"),
            ),
        )
        respx.get(by_id_url("res-000002")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-000002"),
            ),
        )
        coordinator = _coordinator(hass)

        await coordinator.async_refresh()
        assert [res.id for res in coordinator.data["listing-001"]] == [
            "res-000001",
        ]

        await coordinator.async_refresh()

        assert coordinator.last_update_success
        published = [res.id for res in coordinator.data["listing-001"]]
        assert published == ["res-000001"]
        assert coordinator.fallback_sourced_ids == frozenset({"res-000001"})
        assert coordinator.fallback_served_ids == frozenset({"res-000001"})


class TestRequestBudget:
    """The bounds that keep the fallback affordable (T117)."""

    @respx.mock
    async def test_nothing_missing_issues_no_legacy_request(
        self,
    ) -> None:
        """A complete v3 result costs nothing extra."""
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        v3 = GuestyReservation.from_v3_api_dict(
            make_v3_reservation("res-1"),
        )
        assert v3 is not None
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            retrieved=(v3,),
            candidates={"res-1": _candidate()},
        )

        assert route.call_count == 0
        assert outcome.counters.attempted == 0

    @respx.mock
    async def test_cap_bounds_one_refresh_and_defers_the_rest(
        self,
    ) -> None:
        """Only the cap is spent; the remainder is counted deferred."""
        _mock_token()
        total = MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH + 7
        candidates = {}
        routes = []
        for index in range(total):
            reservation_id = f"res-{index:03d}"
            candidates[reservation_id] = _candidate()
            routes.append(
                respx.get(by_id_url(reservation_id)).mock(
                    return_value=Response(
                        200,
                        json=_legacy_document(reservation_id),
                    ),
                ),
            )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates=candidates)

        issued = sum(route.call_count for route in routes)
        assert issued == MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH
        assert outcome.counters.attempted == (MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH)
        assert outcome.counters.deferred == 7

    @respx.mock
    async def test_recheck_interval_runs_from_the_last_attempt(
        self,
        freezer: Any,
    ) -> None:
        """Even a failing lookup resets the re-check clock."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        await _resolve(source, candidates=candidates)
        attempts_after_first = route.call_count
        freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS // 2))
        await _resolve(source, candidates=candidates)
        attempts_before_due = route.call_count
        freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS))
        await _resolve(source, candidates=candidates)

        assert attempts_after_first > 0
        assert attempts_before_due == attempts_after_first
        assert route.call_count > attempts_before_due

    @respx.mock
    async def test_success_is_served_without_a_second_lookup(
        self,
        freezer: Any,
    ) -> None:
        """Between re-checks the stored record is served."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        await _resolve(source, candidates=candidates)
        freezer.tick(timedelta(seconds=60))
        outcome = await _resolve(source, candidates=candidates)

        assert route.call_count == 1
        assert [res.id for res in outcome.reservations] == ["res-1"]
        assert outcome.counters.served == 1
        assert outcome.counters.attempted == 0

    @respx.mock
    async def test_backlog_drains_within_a_bounded_number_of_refreshes(
        self,
        freezer: Any,
    ) -> None:
        """Fair ordering starves nobody, even as re-checks fall due."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        total = MAX_LEGACY_FALLBACK_LOOKUPS_PER_REFRESH * 5
        candidates = {}
        routes = {}
        for index in range(total):
            reservation_id = f"res-{index:03d}"
            candidates[reservation_id] = _candidate()
            routes[reservation_id] = respx.get(
                by_id_url(reservation_id),
            ).mock(
                return_value=Response(500, json={"error": "boom"}),
            )
        source = ReservationFallbackSource(_make_client())

        for _ in range(12):
            await _resolve(source, candidates=candidates)
            freezer.tick(
                timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS // 3),
            )

        never_attempted = [
            reservation_id
            for reservation_id, route in routes.items()
            if route.call_count == 0
        ]
        assert never_attempted == []


class TestFailureModes:
    """Degrading one reservation, never the refresh (T118)."""

    @pytest.mark.parametrize(
        "failure",
        [
            Response(500, json={"error": "boom"}),
            Response(200, text="<html>not json</html>"),
        ],
    )
    @respx.mock
    async def test_failure_republishes_retained_v3_values(
        self,
        failure: Response,
    ) -> None:
        """The first failed lookup still republishes last values."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(return_value=failure)
        v3 = GuestyReservation.from_v3_api_dict(
            _v3_in_window("res-1"),
        )
        assert v3 is not None
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        await _resolve(source, retrieved=(v3,), candidates=candidates)
        outcome = await _resolve(source, candidates=candidates)

        assert [res.id for res in outcome.reservations] == ["res-1"]
        assert outcome.counters.failed == 1
        assert outcome.sourced == ("res-1",)
        assert outcome.served == ()

    @respx.mock
    async def test_timeout_degrades_only_that_reservation(
        self,
    ) -> None:
        """A timeout never raises out of the fetch."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            side_effect=httpx.TimeoutException("too slow"),
        )
        respx.get(by_id_url("res-2")).mock(
            return_value=Response(200, json=_legacy_document("res-2")),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={
                "res-1": _candidate(),
                "res-2": _candidate(),
            },
        )

        assert [res.id for res in outcome.reservations] == ["res-2"]
        assert outcome.counters.failed == 1

    @respx.mock
    async def test_retained_values_keep_every_projected_listing(
        self,
        hass: HomeAssistant,
    ) -> None:
        """A multi-segment stay keeps all of its listings."""
        _mock_token()
        multi = make_v3_reservation(
            "res-000001",
            segments=[
                make_v3_segment(
                    listingId="listing-001",
                    checkIn=_rel(1).isoformat(),
                    checkOut=_rel(2).isoformat(),
                ),
                make_v3_segment(
                    listingId="listing-002",
                    checkIn=_rel(2).isoformat(),
                    checkOut=_rel(4).isoformat(),
                ),
            ],
        )
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(200, json=make_v3_search_page([multi])),
                Response(200, json=make_v3_search_page([])),
            ],
        )
        respx.get(by_id_url("res-000001")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        coordinator = _coordinator(
            hass,
            listing_ids=("listing-001", "listing-002"),
        )

        await coordinator.async_refresh()
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert [res.id for res in coordinator.data["listing-001"]] == [
            "res-000001",
        ]
        assert [res.id for res in coordinator.data["listing-002"]] == [
            "res-000001",
        ]
        assert coordinator.fallback_served_ids == frozenset()

    @respx.mock
    async def test_cold_start_failure_leaves_it_unpublished(
        self,
        freezer: Any,
    ) -> None:
        """Nothing retained means nothing published, and no retirement."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        first = await _resolve(source, candidates=candidates)
        freezer.tick(timedelta(seconds=60))
        second = await _resolve(source, candidates=candidates)
        freezer.tick(
            timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS + 60),
        )
        third = await _resolve(source, candidates=candidates)

        assert first.reservations == ()
        assert first.absent == ()
        assert second.counters.attempted == 0
        assert third.counters.attempted == 1
        assert route.call_count == 2 * (MAX_RETRIES + 1)
        assert not source.disabled


class TestRetirement:
    """Self-disabling when legacy answers as retired (T119)."""

    @respx.mock
    async def test_retirement_disables_the_fallback_for_the_session(
        self,
        caplog: pytest.LogCaptureFixture,
        freezer: Any,
    ) -> None:
        """One warning names the date and no lookup follows."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(410, json=make_legacy_retired_body()),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        with caplog.at_level(logging.WARNING):
            first = await _resolve(source, candidates=candidates)
            freezer.tick(
                timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS * 2),
            )
            await _resolve(source, candidates=candidates)

        warnings = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and LEGACY_RESERVATIONS_RETIREMENT_DATE in record.getMessage()
        ]
        assert len(warnings) == 1
        assert route.call_count == 1
        assert source.disabled
        assert first.reservations == ()
        assert first.absent == ()

    @respx.mock
    async def test_unclassifiable_answer_is_treated_as_retirement(
        self,
    ) -> None:
        """Ambiguity self-disables rather than erasing a reservation."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(404, text="<html>Not Found</html>"),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-1": _candidate()},
        )

        assert source.disabled
        assert outcome.absent == ()
        assert outcome.counters.confirmed_absent == 0


class TestPrivacyAndObservability:
    """Counters without personal data (T120)."""

    @respx.mock
    async def test_counters_and_ids_reach_the_debug_line(
        self,
        hass: HomeAssistant,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The refresh line carries the fallback counters."""
        _mock_token()
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(
                    200,
                    json=make_v3_search_page([_v3_in_window("res-000001")]),
                ),
                Response(200, json=make_v3_search_page([])),
            ],
        )
        respx.get(by_id_url("res-000001")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-000001"),
            ),
        )
        coordinator = _coordinator(hass)

        await coordinator.async_refresh()
        with caplog.at_level(logging.DEBUG):
            await coordinator.async_refresh()

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("Reservation refresh:")
        ]
        assert lines
        assert "fallback_attempted=1" in lines[-1]
        assert "fallback_succeeded=1" in lines[-1]

    @respx.mock
    async def test_no_personal_data_is_logged_at_any_level(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No guest detail nor code value appears in any record."""
        _mock_token()
        guest = make_guest_dict(
            fullName="Ada Lovelace",
            phone="+15559998888",
            email="ada@example.com",
        )
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    guest=guest,
                    confirmationCode="SECRET-CODE",
                ),
            ),
        )
        respx.get(by_id_url("res-2")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        source = ReservationFallbackSource(_make_client())

        with caplog.at_level(logging.DEBUG):
            await _resolve(
                source,
                candidates={
                    "res-1": _candidate(),
                    "res-2": _candidate(),
                },
            )

        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "Ada Lovelace" not in logged
        assert "+15559998888" not in logged
        assert "ada@example.com" not in logged
        assert "SECRET-CODE" not in logged


class TestIdentifierProvenance:
    """No external reference is ever a lookup key (T121)."""

    @respx.mock
    async def test_no_request_uses_an_external_reference(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Every path parameter and filter is a Guesty identifier."""
        _mock_token()
        v3_record = _v3_in_window("res-000001")
        v3_record["confirmationCode"] = "CONF-EXTERNAL"
        v3_record["originId"] = "ORIGIN-EXTERNAL"
        respx.get(SEARCH_URL).mock(
            side_effect=[
                Response(200, json=make_v3_search_page([v3_record])),
                Response(200, json=make_v3_search_page([])),
            ],
        )
        respx.get(by_id_url("res-000001")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-000001",
                    confirmationCode="CONF-EXTERNAL",
                    originId="ORIGIN-EXTERNAL",
                ),
            ),
        )
        coordinator = _coordinator(hass)

        await coordinator.async_refresh()
        await coordinator.async_refresh()

        urls = [str(call.request.url) for call in respx.calls]
        assert urls
        for url in urls:
            assert "CONF-EXTERNAL" not in url
            assert "ORIGIN-EXTERNAL" not in url
        assert any("/reservations/res-000001" in url for url in urls)


# ── Shared helpers for the coordinator-level tests ──────────────────


def _v3_in_window(reservation_id: str) -> dict[str, Any]:
    """Build a v3 reservation whose stay is inside the window.

    Args:
        reservation_id: The ``reservationId`` value.

    Returns:
        A v3 search ``results[]`` entry dict.
    """
    return make_v3_reservation(
        reservation_id,
        segments=[
            make_v3_segment(
                checkIn=_rel(1).isoformat(),
                checkOut=_rel(3).isoformat(),
            ),
        ],
    )


def _listing(listing_id: str) -> GuestyListing:
    """Build a minimal tracked listing.

    Args:
        listing_id: Listing identifier.

    Returns:
        A GuestyListing the coordinator will treat as tracked.
    """
    listing = GuestyListing.from_api_dict(make_listing_dict(_id=listing_id))
    assert listing is not None
    return listing


def _coordinator(
    hass: HomeAssistant,
    *,
    listing_ids: tuple[str, ...] = ("listing-001",),
) -> ReservationsCoordinator:
    """Build a reservations coordinator over the respx-backed client.

    Args:
        hass: Home Assistant instance.
        listing_ids: Listing identifiers to treat as tracked.

    Returns:
        A ReservationsCoordinator ready for a refresh.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Guesty (test)",
        data={
            CONF_CLIENT_ID: FAKE_CLIENT_ID,
            CONF_CLIENT_SECRET: FAKE_CLIENT_SECRET,
        },
        options={
            CONF_RESERVATIONS_API: RESERVATIONS_API_V3,
            CONF_PAST_DAYS: 30,
            CONF_FUTURE_DAYS: 365,
        },
        unique_id=FAKE_CLIENT_ID,
    )
    entry.add_to_hass(hass)
    api_client = _make_client()
    listings = ListingsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
    )
    listings.data = {lid: _listing(lid) for lid in listing_ids}
    return ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=listings,
    )
