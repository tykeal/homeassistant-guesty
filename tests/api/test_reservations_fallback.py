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
from typing import TYPE_CHECKING, Any, Literal
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
    RejectedCode,
)
from custom_components.guesty.api.reservations_fallback import (
    ReservationFallbackSource,
    ServedRecord,
)
from custom_components.guesty.api.validation import ValidationReason
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
from custom_components.guesty.identity import (
    CandidateHint,
    IdentityBaseline,
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
    rejected: tuple[str, ...] = (),
) -> Any:
    """Run one fallback resolution with test defaults.

    Args:
        source: The fallback source under test.
        retrieved: Reservations the v3 result carried.
        candidates: Durable candidate hints by identifier.
        past_days: Configured days of history.
        future_days: Configured days ahead.
        statuses: Configured status set.
        rejected: Identifiers v3 returned but did not fully verify;
            passed only when non-empty.

    Returns:
        The fallback outcome.
    """
    extra: dict[str, Any] = {"rejected": rejected} if rejected else {}
    return await source.resolve(
        retrieved=retrieved,
        candidates=candidates or {},
        past_days=past_days,
        future_days=future_days,
        statuses=statuses,
        **extra,
    )


class TestCandidateSelection:
    """Who the fallback will and will not look up (T115)."""

    async def test_unknown_candidate_is_not_eligible_for_fallback(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Unknown current timing cannot reuse stale endpoints for lookup."""
        coordinator = _coordinator(hass)
        await coordinator.identity.async_load()
        await coordinator.identity.async_store(
            IdentityBaseline(
                path=RESERVATIONS_API_V3,
                candidates={
                    "res-unknown": CandidateHint(
                        timing_status="unknown",
                        observed_at="2026-01-01T00:00:00+00:00",
                    ),
                },
            ),
        )

        assert coordinator._fallback_candidates() == {}

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
        assert outcome.observed == ("res-1",)

    @respx.mock
    async def test_out_of_status_result_is_not_republished(
        self,
    ) -> None:
        """A status outside the configured set stays unpublished.

        Updated for feature 013 (research R8, R2): the former
        ``inquiry`` status is now unrecognized and makes the document
        ``_INVALID`` (see ``TestInvalidLookup``), so a recognized
        status outside the configured set keeps this testing the
        out-of-selection behavior.
        """
        _mock_token()
        respx.get(by_id_url("res-x")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-x", status="canceled"),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-x": _candidate()},
            statuses=frozenset({"confirmed", "checked_in"}),
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
        """Attribution follows the payload's own provenance.

        Updated for feature 013 (FR-008b, research R18): the rejected
        segmentless record now takes part in conflict detection, so
        it carries its own confirmation code. Sharing the fixture's
        default code with ``res-000001`` would be a genuine
        within-refresh collision and correctly abort the refresh.
        """
        _mock_token()
        segmentless_v3 = make_v3_reservation("res-000002", segments=[])
        segmentless_v3["confirmationCode"] = "CONF-SEGMENTLESS"
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
        assert coordinator.identity.baseline is not None
        primary_observation = coordinator.identity.baseline.candidates[
            "res-000001"
        ].observed_at

        await coordinator.async_refresh()

        assert coordinator.last_update_success
        published = [res.id for res in coordinator.data["listing-001"]]
        assert published == ["res-000001"]
        assert coordinator.fallback_sourced_ids == frozenset({"res-000001"})
        assert coordinator.fallback_served_ids == frozenset({"res-000001"})
        assert coordinator.identity.baseline is not None
        by_id_observation = coordinator.identity.baseline.candidates[
            "res-000001"
        ].observed_at
        assert datetime.fromisoformat(by_id_observation) > datetime.fromisoformat(
            primary_observation,
        )


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
    async def test_failed_lookup_preserves_retained_v3_lifecycle(
        self,
    ) -> None:
        """Unknown V3 timing must not overwrite the retained lifecycle."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        v3 = GuestyReservation.from_v3_api_dict(
            _v3_in_window("res-1"),
        )
        assert v3 is not None
        assert v3.status == "confirmed"
        assert v3.timing_status == "unknown"
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        await _resolve(source, retrieved=(v3,), candidates=candidates)
        outcome = await _resolve(source, candidates=candidates)

        assert len(outcome.reservations) == 1
        retained = outcome.reservations[0]
        assert retained.status == "confirmed"
        assert retained.reservation_status is None
        assert retained.timing_status == "unknown"

    @respx.mock
    async def test_successful_filtered_lookup_retains_observed_record(
        self,
    ) -> None:
        """A non-published successful lookup still carries fresh evidence.

        Updated for feature 013 (research R2; data-model
        ``GuestyReservation.status`` is always the recognized mapped
        value): ``cancelled`` now maps to ``canceled``, which is in
        ``ACTIONABLE_STATUSES``, so the configured set excludes it to
        keep the record filtered from publication.
        """
        _mock_token()
        respx.get(by_id_url("res-filtered")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-filtered", status="cancelled"),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-filtered": _candidate()},
            statuses=frozenset({"confirmed", "checked_in"}),
        )

        assert outcome.reservations == ()
        assert outcome.observed == ("res-filtered",)
        assert len(outcome.observed_reservations) == 1
        observed = outcome.observed_reservations[0]
        assert observed.id == "res-filtered"
        assert observed.status == "canceled"

    @pytest.mark.parametrize(
        "malformed",
        [
            {"status": ["confirmed"]},
            {"listingId": {"_id": "listing-1"}},
        ],
    )
    @respx.mock
    async def test_malformed_document_degrades_to_a_failed_lookup(
        self,
        malformed: dict[str, Any],
    ) -> None:
        """A non-string required field never aborts the refresh.

        Updated for feature 013 (research R8): a dict with the
        requested ``_id`` and a malformed required field is now
        ``_INVALID``: it is purged, neither counted as failed nor
        retained, and the refresh still never aborts. The name is
        kept for history.
        """
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1", **malformed),
            ),
        )
        v3 = GuestyReservation.from_v3_api_dict(
            _v3_in_window("res-1"),
        )
        assert v3 is not None
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        await _resolve(source, retrieved=(v3,), candidates=candidates)
        outcome = await _resolve(source, candidates=candidates)

        assert outcome.counters.failed == 0
        assert outcome.counters.succeeded == 0
        assert outcome.reservations == ()
        assert outcome.served == ()
        assert outcome.invalid == ("res-1",)
        assert "res-1" not in source._retained

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
                    checkInDateLocalized=_rel(1).date().isoformat(),
                    checkOutDateLocalized=_rel(2).date().isoformat(),
                ),
                make_v3_segment(
                    listingId="listing-002",
                    checkIn=_rel(2).isoformat(),
                    checkOut=_rel(4).isoformat(),
                    checkInDateLocalized=_rel(2).date().isoformat(),
                    checkOutDateLocalized=_rel(4).date().isoformat(),
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
        assert coordinator.identity.baseline is not None
        primary_observation = coordinator.identity.baseline.candidates[
            "res-000001"
        ].observed_at
        await coordinator.async_refresh()

        assert coordinator.last_update_success
        assert [res.id for res in coordinator.data["listing-001"]] == [
            "res-000001",
        ]
        assert [res.id for res in coordinator.data["listing-002"]] == [
            "res-000001",
        ]
        assert coordinator.identity.baseline is not None
        assert (
            coordinator.identity.baseline.candidates["res-000001"].observed_at
            == primary_observation
        )
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

    @respx.mock
    async def test_route_level_not_found_is_treated_as_retirement(
        self,
    ) -> None:
        """A missing route never retires a reservation."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                404,
                json={
                    "statusCode": 404,
                    "error": "Not Found",
                    "message": "Cannot GET /v1/reservations/res-1",
                },
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={"res-1": _candidate()},
        )

        assert source.disabled
        assert outcome.absent == ()
        assert outcome.counters.confirmed_absent == 0

    @respx.mock
    async def test_retirement_still_reports_earlier_absences(
        self,
    ) -> None:
        """Absences confirmed before retirement are not lost."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                404,
                json=make_legacy_not_found_body("res-1"),
            ),
        )
        respx.get(by_id_url("res-2")).mock(
            return_value=Response(410, json=make_legacy_retired_body()),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={
                "res-1": _candidate(check_out_days=2),
                "res-2": _candidate(check_out_days=3),
            },
        )

        assert source.disabled
        assert outcome.absent == ("res-1",)
        assert outcome.counters.confirmed_absent == 1


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
    check_in = _rel(1)
    check_out = _rel(3)
    return make_v3_reservation(
        reservation_id,
        segments=[
            make_v3_segment(
                checkIn=check_in.isoformat(),
                checkOut=check_out.isoformat(),
                checkInDateLocalized=check_in.date().isoformat(),
                checkOutDateLocalized=check_out.date().isoformat(),
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
    listing_data = make_listing_dict(_id=listing_id)
    listing_data.update(
        {
            "timezone": "UTC",
            "defaultCheckInTime": "16:00",
            "defaultCheckOutTime": "11:00",
        },
    )
    listing = GuestyListing.from_api_dict(listing_data)
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
    listings.timing_listings = dict(listings.data)
    return ReservationsCoordinator(
        hass=hass,
        entry=entry,
        api_client=api_client,
        listings_coordinator=listings,
    )


class TestWriteConfirmedAbsenceIsReported:
    """A write-discovered deletion reaches the refresh (FR-052)."""

    @respx.mock
    async def test_the_next_refresh_reports_the_absence(self) -> None:
        """The identifier appears once in ``absent``."""
        _mock_token()
        source = ReservationFallbackSource(_make_client())
        source.retire_candidate("res-gone")

        first = await _resolve(source)
        source.acknowledge_absent(first.absent)
        second = await _resolve(source)

        assert first.absent == ("res-gone",)
        assert second.absent == ()

    @respx.mock
    async def test_a_retired_fallback_still_reports_it(self) -> None:
        """Self-disabling must not swallow the absence."""
        _mock_token()
        source = ReservationFallbackSource(_make_client())
        source.retire_candidate("res-gone")
        source.note_retirement()

        outcome = await _resolve(source)

        assert outcome.absent == ("res-gone",)

    @respx.mock
    async def test_an_unacknowledged_absence_is_reported_again(
        self,
    ) -> None:
        """A failed baseline save must not lose the deletion."""
        _mock_token()
        source = ReservationFallbackSource(_make_client())
        source.retire_candidate("res-gone")

        first = await _resolve(source)
        # The refresh could not persist its baseline, so it never
        # acknowledges the retirement.
        second = await _resolve(source)
        source.acknowledge_absent(second.absent)
        third = await _resolve(source)

        assert first.absent == ("res-gone",)
        assert second.absent == ("res-gone",)
        assert third.absent == ()


# ── Feature 014: session legacy evidence (T012) ─────────────────────


class TestSessionLegacyEvidence:
    """Legacy observations and retirements are held for the session."""

    @respx.mock
    async def test_out_of_selection_lookup_is_recorded_as_observed(self) -> None:
        """A record legacy returned out of selection is still observed.

        Updated for feature 013 (research R8, R2): ``inquiry`` is now
        unrecognized (an ``_INVALID`` case in ``TestInvalidLookup``),
        so a recognized status outside the configured set keeps this
        testing out-of-selection behavior.
        """
        _mock_token()
        respx.get(by_id_url("res-x")).mock(
            return_value=Response(
                200, json=_legacy_document("res-x", status="canceled")
            ),
        )
        source = ReservationFallbackSource(_make_client())

        await _resolve(
            source,
            candidates={"res-x": _candidate()},
            statuses=frozenset({"confirmed", "checked_in"}),
        )

        assert source.legacy_observed == frozenset({"res-x"})
        assert "res-x" not in source._served
        assert "res-x" not in source._retained

    @respx.mock
    async def test_in_selection_lookup_is_recorded_as_observed(self) -> None:
        """A served record is observed too."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        source = ReservationFallbackSource(_make_client())

        await _resolve(source, candidates={"res-1": _candidate()})

        assert source.legacy_observed == frozenset({"res-1"})

    @respx.mock
    async def test_failed_lookup_is_not_observed(self) -> None:
        """A failed lookup is no observation."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(return_value=Response(500, json={}))
        source = ReservationFallbackSource(_make_client())

        await _resolve(source, candidates={"res-1": _candidate()})

        assert source.legacy_observed == frozenset()

    @respx.mock
    async def test_retire_and_retire_candidate_record_retired(self) -> None:
        """Both a lookup retirement and a write retirement are recorded."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(404, json=make_legacy_not_found_body("res-1")),
        )
        source = ReservationFallbackSource(_make_client())

        await _resolve(source, candidates={"res-1": _candidate()})
        source.retire_candidate("res-2")

        assert source.legacy_retired == frozenset({"res-1", "res-2"})
        assert source.legacy_observed == frozenset()

    @respx.mock
    async def test_prune_never_shrinks_session_retired_set(self) -> None:
        """A retirement pruned from confirmed_absent stays retired."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(404, json=make_legacy_not_found_body("res-1")),
        )
        source = ReservationFallbackSource(_make_client())
        await _resolve(source, candidates={"res-1": _candidate()})
        assert "res-1" in source.confirmed_absent

        await _resolve(source, candidates={})

        assert "res-1" not in source.confirmed_absent
        assert source.legacy_retired == frozenset({"res-1"})

    @pytest.mark.parametrize("which", ["observed", "retired"])
    def test_overflow_marks_set_incomplete(
        self, which: Literal["observed", "retired"]
    ) -> None:
        """Exceeding the bound reports the set incomplete, never truncated."""
        source = ReservationFallbackSource(_make_client())
        with patch(
            "custom_components.guesty.api.reservations_fallback.MAX_LEGACY_SESSION_IDS",
            2,
        ):
            for reservation_id in ("res-1", "res-2", "res-1"):
                source._note_session(which, reservation_id)
            assert getattr(source, f"legacy_{which}") == frozenset({"res-1", "res-2"})
            source._note_session(which, "res-3")
            assert getattr(source, f"legacy_{which}") is None
            source._note_session(which, "res-1")
            assert getattr(source, f"legacy_{which}") is None
        other = "retired" if which == "observed" else "observed"
        assert getattr(source, f"legacy_{other}") == frozenset()

    @pytest.mark.parametrize("reservations_api", [RESERVATIONS_API_V3, "legacy"])
    def test_client_properties_are_read_only_frozensets(
        self, reservations_api: str
    ) -> None:
        """The client exposes both sets read-only, empty and complete."""
        client = _make_client()
        if reservations_api != RESERVATIONS_API_V3:
            client = GuestyApiClient(
                token_manager=client._token_manager,
                http_client=client._http,
                reservations_api=reservations_api,
            )

        assert client.legacy_observed_reservations == frozenset()
        assert client.legacy_retired_reservations == frozenset()
        assert isinstance(client.legacy_observed_reservations, frozenset)
        with pytest.raises(AttributeError):
            client.legacy_observed_reservations = frozenset()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            client.legacy_retired_reservations = frozenset()  # type: ignore[misc]

    def test_client_properties_reflect_the_fallback(self) -> None:
        """The client reports the fallback's sets, including incomplete."""
        client = _make_client()
        fallback = client._fallback
        assert fallback is not None
        fallback._note_session("observed", "res-1")
        fallback._note_session("retired", "res-2")

        assert client.legacy_observed_reservations == frozenset({"res-1"})
        assert client.legacy_retired_reservations == frozenset({"res-2"})
        with patch(
            "custom_components.guesty.api.reservations_fallback.MAX_LEGACY_SESSION_IDS",
            1,
        ):
            fallback._note_session("observed", "res-3")
        assert client.legacy_observed_reservations is None


# ── Feature 013: rejected records and invalid lookups (T012) ────────


def _partial_v3(reservation_id: str) -> GuestyReservation:
    """Build a partially valid v3 reservation in the window.

    Args:
        reservation_id: The ``reservationId`` value.

    Returns:
        The published model of a record with one rejected segment.
    """
    raw = _v3_in_window(reservation_id)
    raw["stay"] = [*raw["stay"], make_v3_segment(listingId="")]
    reservation = GuestyReservation.from_v3_api_dict(raw)
    assert reservation is not None
    assert reservation.partial
    return reservation


def _accepted_v3(reservation_id: str) -> GuestyReservation:
    """Build a fully verified v3 reservation in the window.

    Args:
        reservation_id: The ``reservationId`` value.

    Returns:
        The published model.
    """
    reservation = GuestyReservation.from_v3_api_dict(
        _v3_in_window(reservation_id),
    )
    assert reservation is not None
    return reservation


class TestRejectedExclusion:
    """IDs v3 returned but did not fully verify are excluded (R8)."""

    @respx.mock
    async def test_rejected_ids_are_never_looked_up(self) -> None:
        """Rejected and partial IDs are present, so never eligible."""
        _mock_token()
        routes = [
            respx.get(by_id_url(res_id)).mock(
                return_value=Response(200, json=_legacy_document(res_id)),
            )
            for res_id in ("res-1", "res-2")
        ]
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            retrieved=(_partial_v3("res-2"),),
            candidates={"res-1": _candidate(), "res-2": _candidate()},
            rejected=("res-1", "res-2"),
        )

        assert not any(route.called for route in routes)
        assert outcome.counters.attempted == 0
        assert outcome.reservations == ()
        assert outcome.absent == ()

    @respx.mock
    async def test_rejected_ids_are_purged(self) -> None:
        """Every cache drops a rejected ID; it is never absent."""
        _mock_token()
        source = ReservationFallbackSource(_make_client())
        held = _accepted_v3("res-1")
        source._served["res-1"] = ServedRecord(held, datetime.now(UTC))
        source._retained["res-1"] = held
        source._last_attempt["res-1"] = datetime.now(UTC)
        source._stale = {"res-1"}
        source._confirmed_absent.add("res-1")
        source._unreported_absent.add("res-1")

        outcome = await _resolve(
            source,
            candidates={"res-1": _candidate()},
            rejected=("res-1",),
        )

        assert "res-1" not in source._served
        assert "res-1" not in source._retained
        assert "res-1" not in source._last_attempt
        assert "res-1" not in source._stale
        assert "res-1" not in source._confirmed_absent
        assert "res-1" not in source._unreported_absent
        assert outcome.absent == ()
        assert outcome.reservations == ()

    @respx.mock
    async def test_partial_record_is_not_retained(self) -> None:
        """Nothing is retained for a partially valid record."""
        _mock_token()
        source = ReservationFallbackSource(_make_client())
        source._retained["res-2"] = _accepted_v3("res-2")

        await _resolve(
            source,
            retrieved=(_accepted_v3("res-1"), _partial_v3("res-2")),
            rejected=("res-2",),
        )

        assert "res-1" in source._retained
        assert "res-2" not in source._retained


class TestInvalidLookup:
    """A requested document that fails validation is ``_INVALID`` (R8)."""

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            pytest.param(
                {"status": "inquiry"},
                ValidationReason.UNRECOGNIZED_STATUS,
                id="inquiry",
            ),
            pytest.param(
                {"status": ["confirmed"]},
                ValidationReason.MALFORMED_STATUS,
                id="malformed-status",
            ),
            pytest.param(
                {"listingId": {"_id": "listing-1"}},
                ValidationReason.INVALID_LISTING,
                id="object-listing",
            ),
            pytest.param(
                {"checkIn": "2025-08-01T15:00:00"},
                ValidationReason.UNVERIFIED_INSTANT,
                id="naive-check-in",
            ),
            pytest.param(
                {"checkIn": _rel(4).isoformat()},
                ValidationReason.INVALID_INTERVAL,
                id="reversed-interval",
            ),
        ],
    )
    @respx.mock
    async def test_invalid_document_is_purged_and_reported(
        self,
        overrides: dict[str, Any],
        reason: ValidationReason,
    ) -> None:
        """The ID is purged, observed and reported, never absent."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1", **overrides),
            ),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}
        await _resolve(
            source,
            retrieved=(_accepted_v3("res-1"),),
            candidates=candidates,
        )
        source._stale = {"res-1"}

        outcome = await _resolve(source, candidates=candidates)

        assert outcome.reservations == ()
        assert outcome.served == ()
        assert outcome.absent == ()
        assert outcome.invalid == ("res-1",)
        assert outcome.observed == ("res-1",)
        assert outcome.exclusions == ((reason, "res-1"),)
        assert outcome.counters.failed == 0
        assert source.legacy_observed == frozenset({"res-1"})
        assert outcome.validation.rejected_records == 1
        assert dict(outcome.validation.reasons) == {reason: 1}
        for cache in (source._served, source._retained):
            assert "res-1" not in cache
        assert "res-1" in source._last_attempt
        assert "res-1" not in source._stale
        assert "res-1" not in source._confirmed_absent

    @respx.mock
    async def test_invalid_lookup_keeps_the_recheck_interval(
        self,
        freezer: Any,
    ) -> None:
        """An ``_INVALID`` ID is looked up again only once it is due."""
        freezer.move_to("2026-11-13T12:00:00+00:00")
        _mock_token()
        route = respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1", status="inquiry"),
            ),
        )
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}

        first = await _resolve(source, candidates=candidates)
        second = await _resolve(source, candidates=candidates)

        assert route.call_count == 1
        assert first.invalid == ("res-1",)
        assert first.counters.attempted == 1
        assert second.counters.attempted == 0
        assert second.invalid == ()

        freezer.tick(timedelta(seconds=LEGACY_FALLBACK_RECHECK_SECONDS + 1))
        third = await _resolve(source, candidates=candidates)

        assert route.call_count == 2
        assert third.counters.attempted == 1
        assert third.invalid == ("res-1",)

    @respx.mock
    async def test_coded_invalid_document_reports_code_and_bound(self) -> None:
        """A non-hold string code gives a pair and its bound."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    status="inquiry",
                    checkOut="2025-08-09T23:00:00-05:00",
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.rejected_codes == (RejectedCode("res-1", "CONF-ABC"),)
        assert dict(outcome.rejected_check_out_bounds) == {"res-1": "2025-08-11"}

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"status": "alteration", "listingId": 7}, id="hold"),
            pytest.param({"status": "inquiry", "confirmationCode": 5}, id="non-string"),
            pytest.param(
                {"status": "inquiry", "confirmationCode": None}, id="codeless"
            ),
        ],
    )
    @respx.mock
    async def test_codeless_or_hold_invalid_document_is_still_observed(
        self,
        overrides: dict[str, Any],
    ) -> None:
        """No pair is reported, but the session still observes the ID."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1", **overrides),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.rejected_codes == ()
        assert dict(outcome.rejected_check_out_bounds) == {}
        assert outcome.invalid == ("res-1",)
        assert source.legacy_observed == frozenset({"res-1"})

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(Response(200, json=["not", "a", "dict"]), id="non-dict"),
            pytest.param(
                Response(200, json=make_legacy_reservation_by_id("res-other")),
                id="other-id",
            ),
            pytest.param(Response(500, json={"error": "boom"}), id="http"),
            pytest.param(Response(200, text="<html>not json</html>"), id="non-json"),
            pytest.param(httpx.ConnectError("down"), id="transport"),
        ],
    )
    @respx.mock
    async def test_unproven_answers_stay_failed(
        self,
        failure: Response | Exception,
    ) -> None:
        """Baseline control: nothing proven about the record is ``_FAILED``."""
        _mock_token()
        route = respx.get(by_id_url("res-1"))
        if isinstance(failure, Exception):
            route.mock(side_effect=failure)
        else:
            route.mock(return_value=failure)
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}
        await _resolve(
            source,
            retrieved=(_accepted_v3("res-1"),),
            candidates=candidates,
        )

        outcome = await _resolve(source, candidates=candidates)

        assert outcome.counters.failed == 1
        assert [res.id for res in outcome.reservations] == ["res-1"]
        assert "res-1" in source._retained
        assert source.legacy_observed == frozenset()

    @pytest.mark.parametrize(
        "check_out",
        [
            pytest.param("9999-12-31T23:00:00Z", id="utc-max-plus-one"),
            pytest.param("9999-12-31T20:00:00-05:00", id="negative-offset-max"),
            pytest.param("0001-01-01T00:00:00+05:00", id="positive-offset-min"),
        ],
    )
    @respx.mock
    async def test_extreme_check_out_is_invalid_without_bound(
        self,
        check_out: str,
    ) -> None:
        """An unrepresentable bound never escapes ``resolve``."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    status="inquiry",
                    checkOut=check_out,
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.invalid == ("res-1",)
        assert outcome.rejected_codes == (RejectedCode("res-1", "CONF-ABC"),)
        assert dict(outcome.rejected_check_out_bounds) == {}

    @respx.mock
    async def test_unrepresentable_instant_is_invalid(self) -> None:
        """An instant that cannot be expressed in UTC is not verified."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    checkIn="9999-12-30T00:00:00Z",
                    checkOut="9999-12-31T20:00:00-05:00",
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.invalid == ("res-1",)
        assert outcome.exclusions == ((ValidationReason.UNVERIFIED_INSTANT, "res-1"),)

    @respx.mock
    async def test_invalid_lookup_counts_as_attempted_before_retirement(
        self,
    ) -> None:
        """An invalid lookup is attempted even when retirement follows."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document("res-1", status="inquiry"),
            ),
        )
        respx.get(by_id_url("res-2")).mock(
            return_value=Response(410, json=make_legacy_retired_body()),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(
            source,
            candidates={
                "res-1": _candidate(check_out_days=2),
                "res-2": _candidate(check_out_days=3),
            },
        )

        assert source.disabled
        assert outcome.invalid == ("res-1",)
        assert outcome.counters.attempted == 2
        assert outcome.counters.succeeded == 0
        assert outcome.counters.failed == 0
        assert outcome.counters.confirmed_absent == 0

    @respx.mock
    async def test_write_absence_is_not_an_attempt_before_retirement(
        self,
    ) -> None:
        """A drained write-discovered absence costs no lookup."""
        _mock_token()
        respx.get(by_id_url("res-2")).mock(
            return_value=Response(410, json=make_legacy_retired_body()),
        )
        source = ReservationFallbackSource(_make_client())
        source.retire_candidate("res-9")

        outcome = await _resolve(
            source,
            candidates={"res-2": _candidate(), "res-9": _candidate()},
        )

        assert outcome.absent == ("res-9",)
        assert outcome.counters.attempted == 1


class TestLegacyDocumentSanitizing:
    """An accepted legacy document keeps only verified values (C6)."""

    @respx.mock
    async def test_invalid_optional_fields_are_not_served(self) -> None:
        """Bad optional values become None instead of being published."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    checkInDateLocalized={"d": 1},
                    checkOutDateLocalized="2025-8-22",
                    nightsCount=-2,
                    guestsCount=True,
                    plannedArrival=1600,
                    plannedDeparture=["10:00"],
                    source={"x": 1},
                    note=7,
                    guest={"fullName": 5, "_id": ["g"]},
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.invalid == ()
        (reservation,) = outcome.reservations
        assert reservation.check_in_local is None
        assert reservation.check_out_local is None
        assert reservation.nights_count is None
        assert reservation.guests_count is None
        assert reservation.planned_arrival is None
        assert reservation.planned_departure is None
        assert reservation.source is None
        assert reservation.note is None
        assert reservation.guest is None

    @respx.mock
    async def test_huge_money_integer_never_escapes_resolve(self) -> None:
        """An amount too large for a float drops only that field."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    money={"totalPaid": 10**400, "currency": "USD"},
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.invalid == ()
        (reservation,) = outcome.reservations
        assert reservation.money is not None
        assert reservation.money.total_paid is None
        assert reservation.money.currency == "USD"

    @respx.mock
    async def test_reversed_local_dates_are_not_served(self) -> None:
        """A non-chronological local date pair is dropped as a whole."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(
                200,
                json=_legacy_document(
                    "res-1",
                    checkInDateLocalized="2025-08-22",
                    checkOutDateLocalized="2025-08-17",
                ),
            ),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        (reservation,) = outcome.reservations
        assert reservation.check_in_local is None
        assert reservation.check_out_local is None


class TestPerRecordStale:
    """Per-record staleness of fallback-published records (FR-012g)."""

    async def _stale_source(
        self,
        *,
        served: bool,
    ) -> tuple[ReservationFallbackSource, dict[str, FallbackCandidate], Any]:
        """Make ``res-1`` published and stale after a failed lookup.

        Args:
            served: Whether the record was served by a lookup first,
                rather than retained from v3 only.

        Returns:
            The source, its candidates and the failing outcome.
        """
        _mock_token()
        route = respx.get(by_id_url("res-1"))
        source = ReservationFallbackSource(_make_client())
        candidates = {"res-1": _candidate()}
        if served:
            route.mock(return_value=Response(200, json=_legacy_document("res-1")))
            await _resolve(source, candidates=candidates)
            source._last_attempt["res-1"] = _rel(-1)
        else:
            await _resolve(
                source,
                retrieved=(_accepted_v3("res-1"),),
                candidates=candidates,
            )
        route.mock(return_value=Response(500, json={"error": "boom"}))
        outcome = await _resolve(source, candidates=candidates)
        return source, candidates, outcome

    @respx.mock
    async def test_failed_due_lookup_of_published_id_is_stale(self) -> None:
        """A failed lookup of a published ID marks it stale."""
        source, _, outcome = await self._stale_source(served=False)

        assert [res.id for res in outcome.reservations] == ["res-1"]
        assert outcome.stale == ("res-1",)
        assert source._stale == {"res-1"}

    @respx.mock
    async def test_unpublished_failure_is_not_stale(self) -> None:
        """A cold-start failure publishes nothing and is not stale."""
        _mock_token()
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(500, json={"error": "boom"}),
        )
        source = ReservationFallbackSource(_make_client())

        outcome = await _resolve(source, candidates={"res-1": _candidate()})

        assert outcome.stale == ()
        assert source._stale == set()

    @respx.mock
    async def test_deferred_lookup_changes_nothing(self) -> None:
        """A lookup that is not due leaves the stale mark alone."""
        source, candidates, _ = await self._stale_source(served=False)

        outcome = await _resolve(source, candidates=candidates)

        assert outcome.counters.attempted == 0
        assert outcome.stale == ("res-1",)

    @respx.mock
    async def test_ok_lookup_clears_stale(self) -> None:
        """A successful lookup reverifies the record."""
        source, candidates, _ = await self._stale_source(served=False)
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(200, json=_legacy_document("res-1")),
        )
        source._last_attempt["res-1"] = _rel(-1)

        outcome = await _resolve(source, candidates=candidates)

        assert outcome.stale == ()
        assert source._stale == set()

    @respx.mock
    async def test_absorb_clears_stale(self) -> None:
        """v3 returning the record accepted clears the stale mark."""
        source, candidates, _ = await self._stale_source(served=False)

        outcome = await _resolve(
            source,
            retrieved=(_accepted_v3("res-1"),),
            candidates=candidates,
        )

        assert outcome.stale == ()
        assert source._stale == set()

    @respx.mock
    async def test_retirement_clears_stale(self) -> None:
        """A confirmed absence clears the stale mark."""
        source, candidates, _ = await self._stale_source(served=False)
        respx.get(by_id_url("res-1")).mock(
            return_value=Response(404, json=make_legacy_not_found_body("res-1")),
        )
        source._last_attempt["res-1"] = _rel(-1)

        outcome = await _resolve(source, candidates=candidates)

        assert outcome.absent == ("res-1",)
        assert source._stale == set()

    @respx.mock
    async def test_purge_clears_stale(self) -> None:
        """A rejected ID is purged, stale mark included."""
        source, candidates, _ = await self._stale_source(served=False)

        await _resolve(source, candidates=candidates, rejected=("res-1",))

        assert source._stale == set()

    @respx.mock
    async def test_routing_is_unchanged_by_staleness(self) -> None:
        """A served target stays routable; retained-only stays unroutable."""
        served_source, _, served_outcome = await self._stale_source(served=True)

        assert served_outcome.stale == ("res-1",)
        assert served_source.served_ids == frozenset({"res-1"})
        assert served_outcome.served == ("res-1",)

    @respx.mock
    async def test_retained_only_target_stays_unroutable(self) -> None:
        """A stale retained-only record never becomes routable."""
        source, _, outcome = await self._stale_source(served=False)

        assert outcome.stale == ("res-1",)
        assert source.served_ids == frozenset()
        assert outcome.served == ()
